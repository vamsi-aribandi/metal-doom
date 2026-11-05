// SPDX-License-Identifier: GPL-2.0-or-later
#define main doom_application_main
#include "../app/main.mm"
#undef main

int main(int argc, char **argv) { @autoreleasepool {
  try {
    if (argc != 7) throw std::runtime_error("persistent-live WAD SHADER IMAGE SECONDS CSV REPORT");
    double duration = std::stod(argv[4]);
    if (duration < 1 || duration > 600) throw std::runtime_error("Duration must be 1..600 seconds");
    PersistentEngine engine([NSString stringWithUTF8String:argv[2]]);
    engine.start(readWad([NSString stringWithUTF8String:argv[1]]),[NSString stringWithUTF8String:argv[3]]);
    engine.waitFrame();
    std::ofstream csv(argv[5]);
    csv << "tick,keys,digest,gametic,gamestate,health,armor,x,y,z,angle,kills,items,secrets,"
           "rndindex,prndindex,leveltime,weapon,exited\n";
    auto capture = [&] {
      csv << engine.tick << ',' << engine.keys << ',' << sha256(engine.pixelsCopy(),Width*Height*4);
      for (int32_t value : engine.stateCopy) csv << ',' << value;
      csv << '\n';
      if (engine.stateCopy[15]) throw std::runtime_error("Doom exited during live test");
    };
    capture();
    auto start = std::chrono::steady_clock::now();
    uint32_t observedKeys = 0, maximumGap = 0, previousTick = 0;
    while (std::chrono::duration<double>(std::chrono::steady_clock::now()-start).count() < duration) {
      double seconds = std::chrono::duration<double>(std::chrono::steady_clock::now()-start).count();
      // Alternate turning and firing. Keep the player in the starting room.
      unsigned phase = (unsigned)(seconds * 2) % 8;
      uint32_t keys = phase == 1 ? 8 : phase == 3 ? 4 : phase == 5 ? 64 : 0;
      engine.input(keys);
      if (engine.poll()) {
        observedKeys |= engine.keys;
        maximumGap = MAX(maximumGap,engine.tick-previousTick);
        previousTick = engine.tick;
        capture();
      }
      if (engine.command.status >= MTLCommandBufferStatusCompleted)
        throw std::runtime_error("Persistent command completed during live operation");
      std::this_thread::sleep_for(std::chrono::milliseconds(2));
    }
    // Don't acknowledge the next frame. Prove stable ownership and stop while
    // blocked on the missing acknowledgement; no further input or GPU commands.
    auto waitStart = std::chrono::steady_clock::now();
    while (liveRead(&engine.output->sequence) == engine.sequence) {
      engine.input(0); engine.check();
      if (std::chrono::duration<double>(std::chrono::steady_clock::now()-waitStart).count() > 2)
        throw std::runtime_error("No final frame to test backpressure");
      std::this_thread::sleep_for(std::chrono::milliseconds(1));
    }
    uint32_t held = liveRead(&engine.output->sequence);
    std::vector<uint32_t> snapshot(Width*Height);
    for (size_t i=0;i<snapshot.size();++i) snapshot[i]=liveRead(&engine.output->pixels[i]);
    std::this_thread::sleep_for(std::chrono::milliseconds(100));
    if (engine.command.status != MTLCommandBufferStatusScheduled || liveRead(&engine.output->sequence) != held)
      throw std::runtime_error("Frame was overwritten before acknowledgement");
    for (size_t i=0;i<snapshot.size();++i)
      if (snapshot[i]!=liveRead(&engine.output->pixels[i])) throw std::runtime_error("Torn held frame");
    auto stopped = std::chrono::steady_clock::now();
    engine.stop(); engine.check();
    double stopMS = std::chrono::duration<double,std::milli>(std::chrono::steady_clock::now()-stopped).count();
    double seconds = std::chrono::duration<double>(stopped-start).count()-0.1;
    if (liveRead(&engine.output->done)!=1 || engine.command.status!=MTLCommandBufferStatusCompleted ||
        engine.liveFrames<20 || observedKeys!= (duration >= 3 ? 76u : observedKeys) ||
        engine.tick < duration*30 || engine.tick > duration*36 || maximumGap>8)
      throw std::runtime_error("Live input, pacing, or completion invariant failed");
    std::ofstream report(argv[6]);
    report << "{\"passed\":true,\"dispatches\":" << engine.dispatches
           << ",\"submissions\":" << engine.submissions << ",\"frames\":" << engine.liveFrames
           << ",\"ticks\":" << engine.tick << ",\"seconds\":" << seconds
           << ",\"fps\":" << (engine.liveFrames-1)/seconds << ",\"max_ticks_per_frame\":" << maximumGap
           << ",\"observed_key_mask\":" << observedKeys << ",\"stop_ms\":" << stopMS
           << ",\"stop_while_unacknowledged\":true,\"status\":" << (long)engine.command.status << "}\n";
    if (!csv || !report) throw std::runtime_error("Cannot write live test evidence");
    printf("PASS live_frames=%u ticks=%u seconds=%.3f fps=%.3f keys=%u stop_ms=%.3f status=%ld dispatches=%u\n",
           engine.liveFrames,engine.tick,seconds,(engine.liveFrames-1)/seconds,observedKeys,stopMS,
           (long)engine.command.status,engine.dispatches);
    return 0;
  } catch (const std::exception &e) { fprintf(stderr,"%s\n",e.what()); return 1; }
}}
