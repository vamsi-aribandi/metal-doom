// SPDX-License-Identifier: GPL-2.0-or-later
using DoomState = w2c_doom;
constant uint DoomWidth = 320, DoomHeight = 200;
constant uint DoomMemoryBytes = 100663296, DoomStackTop = 1048576;

// Bind a fresh, uploaded program image. Call on the instance owner lane.
void doom_bind(device DoomState *s, device u8 *memory) {
  s->w2c_memory.data = memory;
  s->w2c_memory.size = DoomMemoryBytes;
  s->w2c_0x5F_stack_pointer = DoomStackTop;
  s->trap = 0;
  s->w2c_memory.fault = 0;
}
uint doom_allocate(device DoomState *s, uint bytes) { return w2c_doom_engine_alloc_0(s, bytes); }
void doom_key(device DoomState *s, uint pressed, uint key) {
  w2c_doom_engine_key_0(s, pressed, key);
}
uint doom_framebuffer_offset(device DoomState *s) { return w2c_doom_engine_framebuffer_0(s); }
uint doom_diagnostics_offset(device DoomState *s) { return w2c_doom_engine_diagnostics_0(s); }
uint doom_log_offset(device DoomState *s) { return w2c_doom_engine_log_0(s); }
uint doom_log_size(device DoomState *s) { return w2c_doom_engine_log_size_0(s); }

// Call render collectively within one SIMD group; initialization and gameplay
// are owned by lane zero. No threadgroup-wide barrier or separate launch is used.
void doom_present(device DoomState *s, uint lane, uint lanes) {
  const uint source = w2c_doom_engine_index_buffer(s);
  const uint palette = w2c_doom_engine_palette(s);
  const uint output = w2c_doom_engine_framebuffer_0(s);
  device u8 *base = s->w2c_memory.data;
  const ulong size = s->w2c_memory.size;
  if (ulong(source) + DoomWidth * DoomHeight > size || ulong(palette) + 1024 > size ||
      ulong(output) + DoomWidth * DoomHeight * 4 > size || (output & 3) || (palette & 3)) {
    if (lane == 0)
      s->trap = 3;
    return;
  }
  const device u32 *colors = (const device u32 *)(base + palette);
  device u32 *pixels = (device u32 *)(base + output);
  for (uint i = lane; i < DoomWidth * DoomHeight; i += lanes)
    pixels[i] = colors[base[source + i]] & 0x00ffffffu;
  simdgroup_barrier(mem_flags::mem_device);
}
uint doom_initialize(device DoomState *s, uint offset, uint length, uint lane, uint lanes) {
  uint result = 0;
  if (lane == 0)
    result = w2c_doom_engine_init_0(s, offset, length);
  simdgroup_barrier(mem_flags::mem_device);
  result = simd_broadcast_first(result);
  if (result == 0 && !s->trap && !s->w2c_memory.fault)
    doom_present(s, lane, lanes);
  return result;
}
void doom_tick(device DoomState *s) { w2c_doom_engine_tick_0(s); }
// Queue layout mirrors engine/parallel_draw.c (wasm32 offsets, 28-byte records).
struct DoomDrawCommand { uint dest, source, colors, position, step, count; int next; };
struct DoomDrawQueue {
  uint columns, spans;
  int head[320], tail[320];
  DoomDrawCommand column[4096], span[4096];
};
void doom_draw_command(device DoomState *s, const device DoomDrawCommand &c, bool span) {
  device u8 *base = s->w2c_memory.data;
  ulong size = s->w2c_memory.size;
  uint stride = span ? 1 : DoomWidth;
  if (c.count >= DoomWidth * DoomHeight || ulong(c.dest) + ulong(c.count) * stride >= size ||
      ulong(c.source) + (span ? 4096 : 128) > size || ulong(c.colors) + 256 > size) {
    atomic_store_explicit((device atomic_uint *)&s->w2c_memory.fault, 3u, memory_order_relaxed);
    return;
  }
  uint position = c.position;
  device u8 *dest = base + c.dest;
  const device u8 *source = base + c.source, *colors = base + c.colors;
  for (uint i = 0; i <= c.count; ++i) {
    uint spot = span ? ((position >> 4) & 0xfc0) | (position >> 26)
                     : uint(int(position) >> 16) & 127;
    *dest = colors[source[spot]];
    dest += stride;
    position += c.step;
  }
}
void doom_draw_opaque(device DoomState *s, uint lane, uint lanes) {
  uint offset = w2c_doom_engine_draw_queue(s);
  if ((offset & 3) || ulong(offset) + sizeof(DoomDrawQueue) > s->w2c_memory.size) {
    if (lane == 0) s->trap = 3;
    return;
  }
  const device DoomDrawQueue &q = *(const device DoomDrawQueue *)(s->w2c_memory.data + offset);
  if (q.columns > 4096 || q.spans > 4096) {
    if (lane == 0) s->trap = 3;
    return;
  }
  // Each screen column has one owner, preserving every overwrite in draw order.
  for (uint x = lane; x < DoomWidth; x += lanes) {
    int index = q.head[x];
    for (uint visited = 0; index >= 0; ++visited) {
      if (uint(index) >= q.columns || visited >= q.columns) {
        atomic_store_explicit((device atomic_uint *)&s->w2c_memory.fault, 3u, memory_order_relaxed);
        break;
      }
      const device DoomDrawCommand &c = q.column[index];
      doom_draw_command(s, c, false);
      index = c.next;
    }
  }
  simdgroup_barrier(mem_flags::mem_device);
  // Original opaque floor/ceiling spans do not overlap one another.
  for (uint i = lane; i < q.spans; i += lanes)
    doom_draw_command(s, q.span[i], true);
  simdgroup_barrier(mem_flags::mem_device);
}
void doom_render_serial(device DoomState *s, uint lane, uint lanes) {
  if (lane == 0) w2c_doom_engine_render_0(s);
  simdgroup_barrier(mem_flags::mem_device);
  if (!s->trap && !s->w2c_memory.fault) doom_present(s, lane, lanes);
}
void doom_render(device DoomState *s, uint lane, uint lanes) {
  if (lane == 0) w2c_doom_engine_render_begin(s);
  simdgroup_barrier(mem_flags::mem_device);
  if (s->trap || s->w2c_memory.fault) return;
  doom_draw_opaque(s, lane, lanes);
  if (lane == 0) w2c_doom_engine_render_end(s);
  simdgroup_barrier(mem_flags::mem_device);
  if (!s->trap && !s->w2c_memory.fault) doom_present(s, lane, lanes);
}
void doom_profile_world(device DoomState *s, uint lane, uint lanes) {
  if (lane == 0) w2c_doom_engine_world_begin(s);
  simdgroup_barrier(mem_flags::mem_device);
  if (s->trap || s->w2c_memory.fault) return;
  doom_draw_opaque(s, lane, lanes);
  if (lane == 0) w2c_doom_engine_world_end(s);
  simdgroup_barrier(mem_flags::mem_device);
}
