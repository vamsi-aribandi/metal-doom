// SPDX-License-Identifier: GPL-2.0-or-later
// Standalone adapter; the reusable Doom engine has no dependency on this mailbox.
static uint32_t liveRead(const uint32_t *p) { return __atomic_load_n(p, __ATOMIC_SEQ_CST); }
static void liveWrite(uint32_t *p, uint32_t v) { __atomic_store_n(p, v, __ATOMIC_SEQ_CST); }
class PersistentEngine : public Engine {
public:
  id<MTLBuffer> hostBuffer, outputBuffer, wadBuffer, replayBuffer;
  id<MTLCommandBuffer> command;
  DoomHostControl *host = nullptr;
  DoomLiveOutput *output = nullptr;
  std::vector<uint32_t> frameCopy = std::vector<uint32_t>(Width * Height);
  int32_t stateCopy[16] = {};
  uint32_t sequence = 0, tick = 0, renders = 0, keys = 0;
  bool replaying = false;
  unsigned liveFrames = 0;
  std::chrono::steady_clock::time_point clockStart;
  std::string log;
  PersistentEngine(NSString *shader) : Engine(shader, @"doom_persistent") {
    static_assert(sizeof(DoomHostControl) == 128);
    static_assert(offsetof(DoomLiveOutput, diagnostics) == 128);
    static_assert(offsetof(DoomLiveOutput, pixels) == 4288);
    if (workers != 32) throw std::runtime_error("Persistent Doom requires a 32-lane SIMD group");
  }
  ~PersistentEngine() { stop(); }
  void start(NSData *wad, NSString *initial, const std::vector<DoomReplayOperation> *replay = nullptr,
             bool serial = false) {
    if (command) throw std::runtime_error("Persistent instance already started");
    NSData *image = readData(initial);
    if (image.length != MemoryBytes) throw std::runtime_error("Expected a 96 MiB engine memory image");
    memory = [device newBufferWithLength:MAX(NSUInteger(MemoryBytes), image.length)
                                 options:MTLResourceStorageModeShared];
    hostBuffer = [device newBufferWithLength:sizeof(DoomHostControl) options:MTLResourceStorageModeShared];
    outputBuffer = [device newBufferWithLength:sizeof(DoomLiveOutput) options:MTLResourceStorageModeShared];
    wadBuffer = [device newBufferWithBytes:wad.bytes length:wad.length options:MTLResourceStorageModeShared];
    DoomReplayOperation dummy = {};
    replayBuffer = [device newBufferWithBytes:replay && !replay->empty() ? replay->data() : &dummy
                                      length:replay && !replay->empty() ? replay->size() * sizeof(dummy) : sizeof(dummy)
                                     options:MTLResourceStorageModeShared];
    if (!memory || !hostBuffer || !outputBuffer || !wadBuffer || !replayBuffer)
      throw std::runtime_error("Cannot allocate persistent GPU buffers");
    memset(memory.contents, 0, memory.length);
    memcpy(memory.contents, image.bytes, image.length);
    memset(hostBuffer.contents, 0, hostBuffer.length);
    memset(outputBuffer.contents, 0, outputBuffer.length);
    host = (DoomHostControl *)hostBuffer.contents;
    output = (DoomLiveOutput *)outputBuffer.contents;
    DoomPersistentConfig config = {(uint32_t)wad.length, replay != nullptr,
                                    replay ? (uint32_t)replay->size() : 0, serial};
    command = [queue commandBuffer];
    id<MTLComputeCommandEncoder> encoder = [command computeCommandEncoder];
    [encoder setComputePipelineState:pipeline];
    [encoder setBuffer:memory offset:0 atIndex:0];
    [encoder setBuffer:instance offset:0 atIndex:1];
    [encoder setBuffer:wadBuffer offset:0 atIndex:2];
    [encoder setBuffer:hostBuffer offset:0 atIndex:3];
    [encoder setBuffer:outputBuffer offset:0 atIndex:4];
    [encoder setBytes:&config length:sizeof(config) atIndex:5];
    [encoder setBuffer:replayBuffer offset:0 atIndex:6];
    [encoder dispatchThreadgroups:MTLSizeMake(1,1,1) threadsPerThreadgroup:MTLSizeMake(workers,1,1)];
    [encoder endEncoding];
    replaying = replay != nullptr;
    clockStart = std::chrono::steady_clock::now();
    [command commit];
    dispatches = submissions = 1;
  }
  void input(uint32_t keys) {
    uint32_t ms = (uint32_t)std::chrono::duration_cast<std::chrono::milliseconds>(
        std::chrono::steady_clock::now() - clockStart).count();
    liveWrite(&host->keys, keys);
    liveWrite(&host->timeMS, ms);
  }
  bool poll() {
    uint32_t next = liveRead(&output->sequence);
    if (next == sequence) {
      check();
      return false;
    }
    for (unsigned i = 0; i < frameCopy.size(); ++i) frameCopy[i] = liveRead(&output->pixels[i]);
    for (unsigned i = 0; i < 16; ++i) stateCopy[i] = (int32_t)liveRead(&output->diagnostics[i]);
    tick = liveRead(&output->tick);
    keys = liveRead(&output->keys);
    renders = liveRead(&output->renders);
    uint32_t logBytes = MIN(liveRead(&output->logBytes), 4096u);
    log.resize((logBytes + 3) & ~3u);
    for (unsigned i = 0; i < (logBytes + 3) / 4; ++i) {
      uint32_t word = liveRead(&output->log[i]);
      memcpy(log.data() + i * 4, &word, 4);
    }
    log.resize(logBytes);
    if (!sequence && !replaying) clockStart = std::chrono::steady_clock::now();
    sequence = next;
    if (command.status < MTLCommandBufferStatusCompleted) ++liveFrames;
    liveWrite(&host->ack, next);
    check();
    if (stateCopy[15]) throw std::runtime_error("Doom requested exit");
    return true;
  }
  void check() {
    if (command.status == MTLCommandBufferStatusError)
      throw std::runtime_error(boundedError(command.error));
    uint32_t fault = liveRead(&output->fault), result = liveRead(&output->result);
    if (fault || result)
      throw std::runtime_error("Persistent Doom failure: trap=" + std::to_string(fault) +
                               " init=" + std::to_string(result));
  }
  void waitFrame(double timeout = 15) {
    auto begin = std::chrono::steady_clock::now();
    while (!poll()) {
      if (command.status >= MTLCommandBufferStatusCompleted)
        throw std::runtime_error("Persistent kernel exited before publishing a frame");
      if (std::chrono::duration<double>(std::chrono::steady_clock::now() - begin).count() > timeout)
        throw std::runtime_error("Timed out waiting for a persistent frame");
      std::this_thread::sleep_for(std::chrono::microseconds(200));
    }
  }
  void requestStop() { if (host) liveWrite(&host->stop, 1); }
  void stop() {
    if (!command) return;
    requestStop();
    [command waitUntilCompleted];
  }
  const uint8_t *pixelsCopy() const { return (const uint8_t *)frameCopy.data(); }
};

static int persistentHeadless(NSString *shader, NSString *initial, NSData *wad, NSString *directory,
                              unsigned ticks, bool scripted, bool renderEveryTick,
                              bool frameHashes, bool serial) {
  std::vector<DoomReplayOperation> replay;
  uint32_t previousKeys = 0;
  const uint32_t codes[] = {0xad,0xaf,0xac,0xae,0xa0,0xa1,0xa3,0xa2,13,0xb6,
                            49,50,51,52,53,54,55,9,27};
  for (unsigned i = 0; i < ticks; ++i) {
    uint32_t keys = scripted ? ((i < 140 ? 1u : 0u) | (i >= 140 && i < 210 ? 8u : 0u) |
                                 (i >= 210 && i < 280 ? 64u : 0u) | (i == 280 ? 128u : 0u)) : 0;
    for (unsigned bit = 0; bit < 19; ++bit)
      if ((keys ^ previousKeys) & (1u << bit)) replay.push_back({4,(keys >> bit) & 1,codes[bit]});
    previousKeys = keys;
    replay.push_back({1,0,0});
    if (renderEveryTick) replay.push_back({2,0,0});
    replay.push_back({9,0,0});
  }
  // Match legacy tracing: tick-only snapshots precede the final render.
  if (!renderEveryTick || !ticks) { replay.push_back({2,0,0}); replay.push_back({9,0,0}); }
  PersistentEngine engine(shader);
  engine.start(wad, initial, &replay, serial);
  engine.waitFrame();
  fwrite(engine.log.data(), 1, engine.log.size(), stderr);
  [[NSFileManager defaultManager] createDirectoryAtPath:directory
                            withIntermediateDirectories:YES attributes:nil error:nil];
  std::ostringstream hashes, trace;
  if (frameHashes) hashes << "tick,digest\n0," << sha256(engine.pixelsCopy(), Width * Height * 4) << '\n';
  trace << "tick,hash,gametic,gamestate,health,armor,x,y,z,angle,kills,items,secrets,"
           "rndindex,prndindex,leveltime,weapon,exited\n";
  auto start = std::chrono::steady_clock::now();
  uint32_t stateHash = 0;
  for (unsigned i = 0; i < ticks; ++i) {
    engine.waitFrame();
    stateHash = 2166136261u;
    for (int32_t value : engine.stateCopy) { stateHash ^= (uint32_t)value; stateHash *= 16777619; }
    trace << engine.tick << ',' << stateHash;
    for (int32_t value : engine.stateCopy) trace << ',' << value;
    trace << '\n';
    if (frameHashes && ticks)
      hashes << engine.tick << ',' << sha256(engine.pixelsCopy(), Width * Height * 4) << '\n';
  }
  if (!renderEveryTick || !ticks) engine.waitFrame();
  stateHash = 2166136261u;
  for (int32_t value : engine.stateCopy) { stateHash ^= (uint32_t)value; stateHash *= 16777619; }
  while (engine.command.status < MTLCommandBufferStatusCompleted) {
    engine.check();
    if (std::chrono::duration<double>(std::chrono::steady_clock::now()-start).count() > 300)
      throw std::runtime_error("Persistent replay failed to finish");
    std::this_thread::sleep_for(std::chrono::microseconds(200));
  }
  engine.check();
  if (liveRead(&engine.output->done) != 2 || engine.tick != ticks)
    throw std::runtime_error("Persistent replay did not exhaust its schedule");
  double seconds = std::chrono::duration<double>(std::chrono::steady_clock::now()-start).count();
  writeBytes([directory stringByAppendingPathComponent:@"frame.rgba"], engine.pixelsCopy(), Width * Height * 4);
  std::string csv = trace.str(), hashCSV = hashes.str();
  writeBytes([directory stringByAppendingPathComponent:@"ticks.csv"], csv.data(), csv.size());
  NSString *hashPath = [directory stringByAppendingPathComponent:@"frame-hashes.csv"];
  if (frameHashes) writeBytes(hashPath, hashCSV.data(), hashCSV.size());
  else [[NSFileManager defaultManager] removeItemAtPath:hashPath error:nil];
  NSDictionary *summary = @{
    @"device":engine.device.name, @"persistent":@YES, @"ticks":@(ticks), @"seconds":@(seconds),
    @"dispatches":@(engine.dispatches), @"submissions":@(engine.submissions),
    @"frames":@(engine.renders), @"live_snapshots":@(engine.liveFrames),
    @"wall_fps":@((engine.renders-1)/seconds), @"state_hash":@(stateHash),
    @"render_every_tick":@(renderEveryTick), @"serial_render":@(serial),
    @"width":@(Width), @"height":@(Height),
    @"gpu_ms":@((engine.command.GPUEndTime-engine.command.GPUStartTime)*1000),
    @"completion_reason":@(liveRead(&engine.output->done))
  };
  NSData *json = [NSJSONSerialization dataWithJSONObject:summary options:NSJSONWritingPrettyPrinted error:nil];
  writeBytes([directory stringByAppendingPathComponent:@"summary.json"], json.bytes,json.length);
  fwrite(json.bytes,1,json.length,stdout); putchar('\n');
  return 0;
}
