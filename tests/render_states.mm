// SPDX-License-Identifier: GPL-2.0-or-later
// Reuse the real host, including compilation, dispatch, and memory validation.
#define main doom_application_main
#include "../app/main.mm"
#undef main

struct InputEvent { unsigned tick, pressed, key; };

int main(int argc, char **argv) {
  @autoreleasepool {
    try {
      if ((argc != 7 && argc != 8) || (argc == 8 && strcmp(argv[7], "--serial-render")))
        throw std::runtime_error("render-states WAD SHADER IMAGE EVENTS TICKS OUTPUT [--serial-render]");
      Engine engine([NSString stringWithUTF8String:argv[2]]);
      engine.control[14] = argc == 8;
      engine.load(readWad([NSString stringWithUTF8String:argv[1]]),
                  [NSString stringWithUTF8String:argv[3]]);
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
      for (unsigned tick = 1; tick <= ticks; ++tick) {
        while (next < events.size() && events[next].tick == tick) {
          engine.invoke(4, events[next].pressed, events[next].key);
          ++next;
        }
        engine.invoke(1);
        engine.invoke(2);
        capture(tick);
      }
      if (!output) throw std::runtime_error("Cannot finish result CSV");
      return 0;
    } catch (const std::exception &error) {
      fprintf(stderr, "%s\n", error.what());
      return 1;
    }
  }
}
