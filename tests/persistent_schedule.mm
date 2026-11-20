// SPDX-License-Identifier: GPL-2.0-or-later
// Replay a captured tick/render schedule; no per-frame host commands.
#define main doom_application_main
#include "../app/main.mm"
#undef main
int main(int argc, char **argv) { @autoreleasepool { try {
  if (argc != 7) throw std::runtime_error("WAD SHADER IMAGE SCHEDULE OUTPUT SERIAL");
  std::ifstream input(argv[4]);
  std::vector<std::pair<unsigned,unsigned>> frames;
  unsigned tick, keys;
  while (input >> tick >> keys) {
    if (keys >= (1u << 19) || (frames.empty() ? tick != 0 || keys != 0 :
        tick <= frames.back().first || tick - frames.back().first > 8))
      throw std::runtime_error("Invalid tick/key schedule");
    frames.emplace_back(tick, keys);
  }
  if (!input.eof() || frames.size() < 2) throw std::runtime_error("Invalid or missing schedule");
  std::vector<DoomReplayOperation> operations;
  const unsigned codes[] = {0xad,0xaf,0xac,0xae,0xa0,0xa1,0xa3,0xa2,13,0xb6,
                            49,50,51,52,53,54,55,9,27};
  for (size_t i = 1; i < frames.size(); ++i) {
    for (unsigned bit = 0; bit < 19; ++bit)
      if ((frames[i-1].second ^ frames[i].second) & (1u << bit))
        operations.push_back({4,(frames[i].second >> bit) & 1,codes[bit]});
    for (unsigned t = frames[i-1].first; t < frames[i].first; ++t) operations.push_back({1,0,0});
    operations.push_back({2,0,0}); operations.push_back({9,0,0});
  }
  std::ofstream output(argv[5]);
  if (!output) throw std::runtime_error("Cannot create replay output");
  output << "tick,keys,digest,gametic,gamestate,health,armor,x,y,z,angle,kills,items,secrets,"
            "rndindex,prndindex,leveltime,weapon,exited\n";
  PersistentEngine e([NSString stringWithUTF8String:argv[2]]);
  e.start(readWad([NSString stringWithUTF8String:argv[1]]),
          [NSString stringWithUTF8String:argv[3]], &operations, std::stoi(argv[6]));
  for (auto frame : frames) {
    e.waitFrame();
    if (e.tick != frame.first) throw std::runtime_error("Replay tick differs from schedule");
    // Replay key operations are preloaded above; output->keys is live-mode telemetry.
    output << e.tick << ',' << frame.second << ',' << sha256(e.pixelsCopy(), Width * Height * 4);
    for (int32_t value : e.stateCopy) output << ',' << value;
    output << '\n';
  }
  auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(5);
  while (e.command.status < MTLCommandBufferStatusCompleted) {
    e.check();
    if (std::chrono::steady_clock::now() > deadline) throw std::runtime_error("Replay did not finish");
    std::this_thread::sleep_for(std::chrono::milliseconds(1));
  }
  e.check();
  if (liveRead(&e.output->done) != 2 || e.dispatches != 1 || e.submissions != 1)
    throw std::runtime_error("Replay did not exhaust in one dispatch");
  printf("PASS frames=%zu ticks=%u serial=%s dispatches=1 submissions=1\n", frames.size(), e.tick, argv[6]);
  return 0;
} catch (const std::exception &e) { fprintf(stderr,"%s\n",e.what()); return 1; } } }
