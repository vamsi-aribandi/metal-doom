// SPDX-License-Identifier: GPL-2.0-or-later
// Test-only host: production launch sizes and engine contracts stay unchanged.
#define main doom_application_main
#include "../app/main.mm"
#undef main

int main(int argc, char **argv) { @autoreleasepool {
  try {
    if (argc != 9) throw std::runtime_error("parallelism WAD SHADER IMAGE THREADS OPERATIONS HASH OUTPUT MODE");
    NSString *shader = [NSString stringWithUTF8String:argv[2]];
    NSString *image = [NSString stringWithUTF8String:argv[3]];
    NSString *output = [NSString stringWithUTF8String:argv[7]];
    unsigned threads = std::stoul(argv[4]);
    if (threads != 32 && threads != 64 && threads != 128 && threads != 256)
      throw std::runtime_error("Threads must be 32, 64, 128, or 256");
    [[NSFileManager defaultManager] createDirectoryAtPath:output withIntermediateDirectories:YES
                                             attributes:nil error:nil];
    NSData *wad = readWad([NSString stringWithUTF8String:argv[1]]);
    if (!strcmp(argv[8], "profile")) {
      Engine engine(shader);
      if (threads > engine.pipeline.maxTotalThreadsPerThreadgroup)
        throw std::runtime_error("Thread count exceeds pipeline resources");
      engine.workers = threads;
      engine.load(wad,image);
      NSMutableDictionary *phases = [NSMutableDictionary dictionary];
      const uint32_t op[] = {6,7,8};
      const char *names[] = {"palette","world","full_render"};
      for (unsigned phase=0;phase<3;++phase) {
        engine.invoke(op[phase]);
        double sum=0;
        for (unsigned i=0;i<12;++i) {
          double before=engine.gpuMS; engine.invoke(op[phase]); sum+=engine.gpuMS-before;
        }
        phases[[NSString stringWithUTF8String:names[phase]]] = @(sum/12);
      }
      NSDictionary *report = @{@"device":engine.device.name, @"threads":@(threads),
        @"pipeline_max_threads":@(engine.pipeline.maxTotalThreadsPerThreadgroup),
        @"static_threadgroup_bytes":@(engine.pipeline.staticThreadgroupMemoryLength),
        @"mean_gpu_ms":phases};
      NSData *json=[NSJSONSerialization dataWithJSONObject:report options:NSJSONWritingPrettyPrinted error:nil];
      writeBytes([output stringByAppendingPathComponent:@"profile.json"],json.bytes,json.length);
      return 0;
    }
    if (strcmp(argv[8], "replay")) throw std::runtime_error("Unknown experiment mode");
    std::ifstream input(argv[5]);
    if (!input) throw std::runtime_error("Cannot open preloaded operations");
    std::vector<DoomReplayOperation> operations;
    DoomReplayOperation op;
    unsigned snapshots = 1, ticks = 0;
    while (input >> op.code >> op.a >> op.b) {
      if (op.code!=1 && op.code!=2 && op.code!=4 && op.code!=9)
        throw std::runtime_error("Invalid operation");
      operations.push_back(op);
      snapshots += op.code==9; ticks += op.code==1;
    }
    if (!input.eof()) throw std::runtime_error("Malformed operations");
    PersistentEngine engine(shader);
    if (threads > engine.pipeline.maxTotalThreadsPerThreadgroup)
      throw std::runtime_error("Thread count exceeds pipeline resources");
    engine.workers = threads;
    engine.start(wad,image,&operations);
    std::ofstream csv([[output stringByAppendingPathComponent:@"frames.csv"] fileSystemRepresentation]);
    csv << "tick,digest,gametic,gamestate,health,armor,x,y,z,angle,kills,items,secrets,"
           "rndindex,prndindex,leveltime,weapon,exited\n";
    bool hashFrames = !strcmp(argv[6],"1");
    auto capture = [&] {
      csv << engine.tick << ',';
      if (hashFrames) csv << sha256(engine.pixelsCopy(),Width*Height*4);
      for (int32_t value : engine.stateCopy) csv << ',' << value;
      csv << '\n';
    };
    engine.waitFrame(); capture();
    auto start=std::chrono::steady_clock::now();
    for (unsigned i=1;i<snapshots;++i) {engine.waitFrame(); capture();}
    while (engine.command.status<MTLCommandBufferStatusCompleted) {
      engine.check(); std::this_thread::sleep_for(std::chrono::microseconds(200));
      if (std::chrono::duration<double>(std::chrono::steady_clock::now()-start).count()>60)
        throw std::runtime_error("Persistent replay failed to terminate");
    }
    double seconds=std::chrono::duration<double>(std::chrono::steady_clock::now()-start).count();
    engine.check();
    if (engine.dispatches!=1 || engine.submissions!=1 || engine.liveFrames!=snapshots ||
        engine.sequence!=snapshots || engine.tick!=ticks || liveRead(&engine.output->done)!=2 || !csv)
      throw std::runtime_error("Persistent replay invariants failed");
    writeBytes([output stringByAppendingPathComponent:@"frame.rgba"],engine.pixelsCopy(),Width*Height*4);
    NSDictionary *report = @{@"device":engine.device.name, @"threads":@(threads),
      @"seconds":@(seconds), @"wall_fps":@((engine.renders-1)/seconds),
      @"ticks":@(ticks), @"frames":@(engine.sequence), @"live_frames":@(engine.liveFrames),
      @"dispatches":@(engine.dispatches), @"submissions":@(engine.submissions),
      @"completion_reason":@(liveRead(&engine.output->done)),
      @"pipeline_max_threads":@(engine.pipeline.maxTotalThreadsPerThreadgroup),
      @"static_threadgroup_bytes":@(engine.pipeline.staticThreadgroupMemoryLength)};
    NSData *json=[NSJSONSerialization dataWithJSONObject:report options:NSJSONWritingPrettyPrinted error:nil];
    writeBytes([output stringByAppendingPathComponent:@"summary.json"],json.bytes,json.length);
    return 0;
  } catch (const std::exception &e) {fprintf(stderr,"parallelism: %s\n",e.what());return 1;}
}}
