// SPDX-License-Identifier: GPL-2.0-or-later
#include <array>
#define main doom_application_main
#include "../app/main.mm"
#undef main

int main(int argc, char **argv) { @autoreleasepool {
  try {
    if (argc != 8) throw std::runtime_error("persistent WAD SHADER IMAGE EVENTS TICKS OUTPUT TICKS_PER_FRAME");
    std::vector<DoomReplayOperation> operations;
    std::ifstream events(argv[4]);
    if (!events) throw std::runtime_error("Cannot open input event file");
    unsigned eventTick, pressed, key;
    std::vector<std::array<unsigned,3>> schedule;
    while (events >> eventTick >> pressed >> key) schedule.push_back({eventTick,pressed,key});
    unsigned ticks = std::stoul(argv[5]), perFrame = std::stoul(argv[7]);
    size_t next = 0;
    for (unsigned tick = 1; tick <= ticks; ++tick) {
      while (next < schedule.size() && schedule[next][0] == tick) {
        operations.push_back({4,schedule[next][1],schedule[next][2]}); ++next;
      }
      operations.push_back({1,0,0});
      if (tick % perFrame == 0 || tick == ticks) {
        operations.push_back({2,0,0}); operations.push_back({9,0,0});
      }
    }
    PersistentEngine engine([NSString stringWithUTF8String:argv[2]]);
    engine.start(readWad([NSString stringWithUTF8String:argv[1]]),
                 [NSString stringWithUTF8String:argv[3]], &operations);
    std::ofstream output(argv[6]);
    output << "tick,digest,gametic,gamestate,health,armor,x,y,z,angle,kills,items,secrets,"
              "rndindex,prndindex,leveltime,weapon,exited\n";
    auto start = std::chrono::steady_clock::now();
    for (unsigned frame = 0; frame <= (ticks + perFrame - 1) / perFrame; ++frame) {
      engine.waitFrame();
      output << engine.tick << ',' << sha256(engine.pixelsCopy(), Width * Height * 4);
      for (int32_t value : engine.stateCopy) output << ',' << value;
      output << '\n';
    }
    // Let replay finish naturally, rather than making stop masquerade as exhaustion.
    while (engine.command.status < MTLCommandBufferStatusCompleted) {
      engine.check(); std::this_thread::sleep_for(std::chrono::milliseconds(1));
      if (std::chrono::duration<double>(std::chrono::steady_clock::now()-start).count() > 60)
        throw std::runtime_error("Replay did not terminate");
    }
    engine.check();
    if (liveRead(&engine.output->done) != 2 || engine.dispatches != 1 || engine.submissions != 1 ||
        engine.liveFrames != engine.sequence || engine.tick != ticks)
      throw std::runtime_error("Replay persistence invariant failed");
    printf("PASS dispatches=%u submissions=%u frames=%u live_frames=%u ticks=%u seconds=%.6f status=%ld reason=%u\n",
            engine.dispatches,engine.submissions,engine.sequence,engine.liveFrames,engine.tick,
            std::chrono::duration<double>(std::chrono::steady_clock::now()-start).count(),
            (long)engine.command.status,liveRead(&engine.output->done));
    return 0;
  } catch (const std::exception &e) { fprintf(stderr,"%s\n",e.what()); return 1; }
}}
