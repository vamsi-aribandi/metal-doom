// SPDX-License-Identifier: GPL-2.0-or-later
// Reuse the real host, including compilation, dispatch, and memory validation.
#define main doom_application_main
#include "../app/main.mm"
#undef main

struct InputEvent { unsigned tick, pressed, key; };

int main(int argc, char **argv) {
  @autoreleasepool {
    try {
      if (argc < 7)
        throw std::runtime_error("render-states WAD SHADER IMAGE EVENTS TICKS OUTPUT [--serial-render] [--ticks-per-frame 1..8]");
      bool serialRender = false;
      unsigned ticksPerFrame = 1;
      for (int index = 7; index < argc; ++index) {
        if (!strcmp(argv[index], "--serial-render")) serialRender = true;
        else if (!strcmp(argv[index], "--ticks-per-frame") && index + 1 < argc) {
          size_t consumed = 0;
          const std::string value = argv[++index];
          const unsigned long parsed = std::stoul(value, &consumed);
          if (consumed != value.size() || parsed < 1 || parsed > 8)
            throw std::runtime_error("ticks-per-frame must be an integer from 1 to 8");
          ticksPerFrame = (unsigned)parsed;
        } else throw std::runtime_error("Unknown renderer-state option");
      }
      Engine engine([NSString stringWithUTF8String:argv[2]]);
      engine.control[14] = serialRender;
      engine.load(readWad([NSString stringWithUTF8String:argv[1]]),
                  [NSString stringWithUTF8String:argv[3]]);
      const unsigned initialSubmissions = engine.submissions;
      const unsigned initialDispatches = engine.dispatches;
      std::ifstream input(argv[4]);
      if (!input) throw std::runtime_error("Cannot read input events");
      std::vector<InputEvent> events;
      InputEvent event;
      const unsigned ticks = std::stoul(argv[5]);
      while (input >> event.tick >> event.pressed >> event.key) {
        if (!event.tick || event.tick > ticks || event.pressed > 1 || event.key > 255 ||
            (!events.empty() && event.tick < events.back().tick))
          throw std::runtime_error("Invalid input event");
        events.push_back(event);
      }
      if (!input.eof()) throw std::runtime_error("Malformed input event file");
      std::ofstream output(argv[6]);
      if (!output) throw std::runtime_error("Cannot write result CSV");
      output << "tick,digest,gametic,gamestate,health,armor,x,y,z,angle,kills,items,secrets,"
                "rndindex,prndindex,leveltime,weapon,exited\n";
      auto capture = [&](unsigned tick) {
        output << tick << ',' << sha256(engine.currentPixels(), Width * Height * 4);
        const int32_t *state = engine.diagnostics();
        for (unsigned i = 0; i < 16; ++i) output << ',' << state[i];
        output << '\n';
        if (state[15]) throw std::runtime_error("Engine exited during renderer-state test");
      };
      capture(0);
      size_t next = 0;
      unsigned renderedBatches = 0;
      std::vector<Engine::Operation> operations;
      for (unsigned tick = 1; tick <= ticks; ++tick) {
        while (next < events.size() && events[next].tick == tick) {
          operations.push_back({4, events[next].pressed, events[next].key});
          ++next;
        }
        operations.push_back({1});
        if (tick % ticksPerFrame == 0 || tick == ticks) {
          operations.push_back({2});
          engine.submit(operations);
          operations.clear();
          ++renderedBatches;
          capture(tick);
        }
      }
      if (!output) throw std::runtime_error("Cannot finish result CSV");
      NSString *statsPath = [[[NSString stringWithUTF8String:argv[6]]
                               stringByDeletingPathExtension] stringByAppendingString:@".stats.json"];
      std::ofstream stats(statsPath.fileSystemRepresentation);
      stats << "{\"submissions\":" << engine.submissions
            << ",\"dispatches\":" << engine.dispatches
            << ",\"initial_submissions\":" << initialSubmissions
            << ",\"initial_dispatches\":" << initialDispatches
            << ",\"rendered_batches\":" << renderedBatches
            << ",\"ticks_per_frame\":" << ticksPerFrame << "}\n";
      if (!stats) throw std::runtime_error("Cannot write submission statistics");
      return 0;
    } catch (const std::exception &error) {
      fprintf(stderr, "%s\n", error.what());
      return 1;
    }
  }
}
