// SPDX-License-Identifier: GPL-2.0-or-later
// Experimental live host visibility, based on ccv's system-coherent spelling.
// See docs/persistent.md and vendor/licenses/ccv-BSD-3-Clause.txt.
#pragma METAL internals : enable
#ifndef __METAL_MEMORY_SCOPE_SYSTEM__
#define __METAL_MEMORY_SCOPE_SYSTEM__ 3
#endif
inline void doom_system_fence() {
  atomic_thread_fence(mem_flags::mem_device, memory_order_seq_cst,
                     static_cast<thread_scope>(__METAL_MEMORY_SCOPE_SYSTEM__));
}
void doom_publish(device DoomState *s, volatile coherent(system) device DoomLiveOutput *out,
                  uint sequence, uint tick, uint result, uint renders, uint keys, uint lane, uint lanes) {
  // The host acknowledges only after copying. Until then this payload is immutable.
  uint frame = 0, diag = 0, log = 0, logBytes = 0;
  // engine_diagnostics refreshes its array: only the owner may call it.
  if (lane == 0) {
    frame = doom_framebuffer_offset(s); diag = doom_diagnostics_offset(s);
    log = doom_log_offset(s); logBytes = min(doom_log_size(s), 4096u);
  }
  simdgroup_barrier(mem_flags::mem_device);
  frame = simd_broadcast_first(frame); diag = simd_broadcast_first(diag);
  log = simd_broadcast_first(log); logBytes = simd_broadcast_first(logBytes);
  device u8 *base = s->w2c_memory.data;
  if (ulong(frame) + DoomWidth * DoomHeight * 4 > s->w2c_memory.size ||
      ulong(diag) + 64 > s->w2c_memory.size || ulong(log) + logBytes > s->w2c_memory.size) {
    if (lane == 0) s->trap = 3;
    return;
  }
  const device uint *pixels = (const device uint *)(base + frame);
  const device uint *diagnostics = (const device uint *)(base + diag);
  for (uint i = lane; i < DoomWidth * DoomHeight; i += lanes) out->pixels[i] = pixels[i];
  for (uint i = lane; i < 16; i += lanes) out->diagnostics[i] = diagnostics[i];
  // Avoid aligned reads past the log allocation's end.
  for (uint i = lane; i < (logBytes + 3) / 4; i += lanes) {
    uint word = 0;
    for (uint j = 0; j < 4 && i * 4 + j < logBytes; ++j)
      word |= uint(base[log + i * 4 + j]) << (j * 8);
    out->log[i] = word;
  }
  doom_system_fence();
  simdgroup_barrier(mem_flags::mem_device);
  if (lane == 0) {
    out->tick = tick; out->keys = keys; out->result = result; out->logBytes = logBytes; out->renders = renders;
    doom_system_fence();
    out->sequence = sequence;
    doom_system_fence();
  }
}

// Call collectively from exactly one complete SIMD group. All synchronization
// stays within that group, so other groups in an outer kernel can keep working.
inline void doom_persistent_job(
    device u8 *memory, device DoomState *instance, const device u8 *wad,
    const volatile coherent(system) device DoomHostControl *host,
    volatile coherent(system) device DoomLiveOutput *out,
    constant DoomPersistentConfig &config,
    const device DoomReplayOperation *operations, uint lane, uint lanes) {
  uint offset = 0;
  if (lane == 0) {
    doom_bind(instance, memory);
    offset = doom_allocate(instance, config.wadBytes);
  }
  simdgroup_barrier(mem_flags::mem_device);
  offset = simd_broadcast_first(offset);
  if (!offset || ulong(offset) + config.wadBytes > instance->w2c_memory.size) {
    if (lane == 0) { out->fault = 4; doom_system_fence(); out->done = 3; doom_system_fence(); }
    return;
  }
  for (uint i = lane; i < config.wadBytes; i += lanes) memory[offset + i] = wad[i];
  simdgroup_barrier(mem_flags::mem_device);
  uint result = doom_initialize(instance, offset, config.wadBytes, lane, lanes);
  uint sequence = 1, tick = 0, renders = 1, next = 0, previousKeys = 0;
  uint lastMS = 0, credit = 0;
  doom_system_fence();
  lastMS = simd_broadcast_first(lane == 0 ? host->timeMS : 0);
  if (!result && !instance->trap && !instance->w2c_memory.fault)
    doom_publish(instance, out, sequence, tick, result, renders, previousKeys, lane, lanes);
  else if (lane == 0) out->result = result;
  uint reason = 1;
  while (!result && !instance->trap && !instance->w2c_memory.fault) {
    // A system fence is required on *every* wait path, not only after writes.
    doom_system_fence();
    uint stop = simd_broadcast_first(lane == 0 ? host->stop : 0);
    if (stop) break;
    uint ack = simd_broadcast_first(lane == 0 ? host->ack : 0);
    if (ack != sequence) continue;
    if (config.replay) {
      if (next == config.operationCount) { reason = 2; break; }
      // Replay is preloaded before this single dispatch. No host tick commands.
      DoomReplayOperation op = operations[next++];
      if (op.code == 4 && lane == 0) doom_key(instance, op.a, op.b);
      if (op.code == 1) {
        if (lane == 0) doom_tick(instance);
        ++tick;
      }
      simdgroup_barrier(mem_flags::mem_device);
      if (op.code == 2) {
        if (config.serialRender) doom_render_serial(instance, lane, lanes);
        else doom_render(instance, lane, lanes);
        ++renders;
      }
      if (op.code != 9) continue;
    } else {
      uint now = simd_broadcast_first(lane == 0 ? host->timeMS : 0);
      uint elapsed = min(now - lastMS, 228u);
      lastMS = now;
      credit = min(credit + elapsed * 35u, 8000u);
      if (credit < 1000) continue;
      uint keys = simd_broadcast_first(lane == 0 ? host->keys : 0);
      if (lane == 0) {
        const uint codes[] = {0xad,0xaf,0xac,0xae,0xa0,0xa1,0xa3,0xa2,13,0xb6,
                              49,50,51,52,53,54,55,9,27};
        for (uint bit = 0; bit < 19; ++bit)
          if ((keys ^ previousKeys) & (1u << bit)) doom_key(instance, (keys >> bit) & 1, codes[bit]);
      }
      previousKeys = keys;
      uint count = credit / 1000;
      credit -= count * 1000;
      if (lane == 0) for (uint i = 0; i < count; ++i) doom_tick(instance);
      tick += count;
      simdgroup_barrier(mem_flags::mem_device);
      if (config.serialRender) doom_render_serial(instance, lane, lanes);
      else doom_render(instance, lane, lanes);
      ++renders;
    }
    if (instance->trap || instance->w2c_memory.fault) break;
    doom_publish(instance, out, ++sequence, tick, result, renders, previousKeys, lane, lanes);
  }
  if (lane == 0) {
    out->fault = instance->trap ? instance->trap : instance->w2c_memory.fault;
    if (result || out->fault) reason = 3;
    doom_system_fence();
    out->done = reason; // 1 host stop, 2 replay exhausted, 3 engine failure.
    doom_system_fence();
  }
}

#ifndef DOOM_EMBEDDED
kernel void doom_persistent(
    device u8 *memory [[buffer(0)]], device DoomState *instance [[buffer(1)]],
    const device u8 *wad [[buffer(2)]],
    const volatile coherent(system) device DoomHostControl *host [[buffer(3)]],
    volatile coherent(system) device DoomLiveOutput *out [[buffer(4)]],
    constant DoomPersistentConfig &config [[buffer(5)]],
    const device DoomReplayOperation *operations [[buffer(6)]],
    uint lane [[thread_index_in_simdgroup]], uint lanes [[threads_per_simdgroup]]) {
  doom_persistent_job(memory, instance, wad, host, out, config, operations, lane, lanes);
}
#endif
