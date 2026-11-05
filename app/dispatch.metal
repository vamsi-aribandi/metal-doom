kernel void doom_dispatch(device u8 *memory [[buffer(0)]], device u32 *c [[buffer(1)]],
                          device DoomState *instance [[buffer(2)]],
                          constant uint *operation [[buffer(3)]],
                          uint lane [[thread_index_in_threadgroup]],
                          uint3 groupSize [[threads_per_threadgroup]]) {
  const uint op = operation[0], lanes = groupSize.x;
  if (op == 5) {
    if (lane == 0)
      doom_bind(instance, memory);
    return;
  }
  if (instance->trap || instance->w2c_memory.fault) {
    if (lane == 0) c[13] = instance->trap ? instance->trap : instance->w2c_memory.fault;
    return;
  }
  if (op == 0) {
    uint result = doom_initialize(instance, operation[1], operation[2], lane, lanes);
    if (lane == 0)
      c[8] = result;
  }
  if (op == 1 && lane == 0)
    doom_tick(instance);
  if (op == 2 || op == 8) {
    if (c[14]) doom_render_serial(instance, lane, lanes);
    else doom_render(instance, lane, lanes);
  }
  if (op == 3 && lane == 0)
    c[8] = doom_allocate(instance, operation[1]);
  if (op == 4 && lane == 0)
    doom_key(instance, operation[1], operation[2]);
  if (op == 6)
    doom_present(instance, lane, lanes);
  if (op == 7) {
    if (c[14]) {
      if (lane == 0) w2c_doom_engine_profile_world_0(instance);
    } else doom_profile_world(instance, lane, lanes);
  }
  if (lane == 0) {
    c[9] = doom_framebuffer_offset(instance);
    c[10] = doom_diagnostics_offset(instance);
    c[11] = doom_log_offset(instance);
    c[12] = doom_log_size(instance);
    c[13] = instance->trap ? instance->trap : instance->w2c_memory.fault;
  }
}
