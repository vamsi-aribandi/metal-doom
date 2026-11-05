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
void doom_render(device DoomState *s, uint lane, uint lanes) {
  if (lane == 0)
    w2c_doom_engine_render_0(s);
  simdgroup_barrier(mem_flags::mem_device);
  if (!s->trap && !s->w2c_memory.fault)
    doom_present(s, lane, lanes);
}
