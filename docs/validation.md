# Validation on 2026-11-04

Tested on the actual Apple M4 Mac mini: 10 CPU cores, 10 GPU cores, 16 GB unified
memory, macOS 26.5.1 (25F80). CLT Apple clang 21 built the native host; Homebrew
LLVM/LLD 23.1.5 and WABT 1.0.42 produced the engine. Runtime MSL 3.2 compilation
works without the offline `metal` executable. [Runtime capability probes](../probes/README.md)
also executed recursive and indirect native functions under MSL 3.2 and 4.0.
MSL 4.1/generic pointers were rejected on the installed compiler.

## Exact comparisons

`./test.sh --wad /external/path/doom1.wad` passed:

| Case | Additional ticks | CPU/GPU comparison |
| --- | ---: | --- |
| Initialize E1M1 | 0 | Final diagnostic hash and all 256,000 frame bytes |
| Idle | 35 | All 16 diagnostics every tick; all frame bytes |
| Move, turn, fire, use | 350 | All 16 diagnostics every tick; all frame bytes |
| Render after every tick | 35 | All 16 diagnostics every tick; all frame bytes |
| Fresh GPU replay | 350 | Identical trace and framebuffer |

Each CPU case was repeated in a fresh process. The ARM64 reference builds the
original C directly, independently of WASM and the Metal generator. It shares
the platform/tick scheduling adapter, so a common adapter bug can escape these
tests. The tests establish this bounded schedule, not all Doom compatibility.

The Doom shareware 1.9 IWAD was external to the repo (SHA-256
`1d7d43be501e67d927e415e0b8f3e29c3bf33075e859721816f652a526cac771`).
The first GPU frame SHA-256 was
`7fc65d25045d2218905c3c05e62cac6698330db0d2e8780c3c7e5f53cb84fda6`.
After 350 scripted ticks it was
`aadefb30246a9c756eb1d59456099c8ffe1e700c4552f72287395e402c755c85`.
Raw results: [CPU/GPU report](results/cpu-gpu-report.json).

`python3 tests/test_lowering.py --wad /external/path/doom1.wad` separately
compared the original wasm2c output with the structured-control-flow version:
720 function bodies, 509 requiring lowering, 125 ticks, identical initial frame,
full diagnostic trace, and final frame. [Report](results/cfg-report.json).
Seven small control-flow reference cases also pass.

A clean checkout exported from Git built successfully and produced the same
initial GPU frame/state as the native CPU reference. The source checkout and
app bundle contain no WADs or game graphics. The unused upstream raster icon
was omitted.

Native UI tested through keyboard input: Up moved the player, Right turned,
Space fired and reduced ammo from 50 to 48, M opened/closed the original Doom menu, and Escape stopped the app.
The final packaged-app WAD picker and loading window were also verified. The
windowed version uses a serial background GPU queue; Cocoa handles input and
window events on the main thread. Startup compilation and WAD selection occur
after a window is created, so the loading UI can respond immediately.
Malformed IWAD header, PWAD, invalid directory and out-of-bounds lump inputs
were rejected by the asset loader before GPU initialization.

## Performance

Reproduce an actual render-per-tick benchmark, not a tick-only throughput test:

```sh
./run.sh --wad /external/path/doom1.wad --headless --scripted \
  --ticks 35 --render-every-tick --output build/benchmark
```

The measured 35-frame run took 4.554 seconds, **7.69 FPS**. Metal command timing
reported **128.37 ms/render** and **0.683 ms/tick** on average. These timings
exclude initial compilation/initialization; `gpu_ms` includes initialization,
while `tick_gpu_ms` and `render_gpu_ms` count their own dispatches. See the
[raw benchmark](results/render-benchmark.json). GUI sampling showed about 6–7 FPS.
Compilation of a changed engine took tens of seconds; cached launches are faster.

The 350-tick run with just one final render completed its timed loop in about
0.597 seconds. That demonstrates tick throughput, **not interactive FPS**.
The implementation deliberately executes the C engine on one GPU lane. It is
slower than native CPU Doom, especially while rendering. This is a playable,
choppy correctness demo, not a claim of useful GPU acceleration.

Native recursion works, but depth 128 failed pipeline creation with
`Compute pipeline exceeds available stack space`; depth 32 passed this suite.
Unusual maps or gameplay can require deeper native calls and are unverified.
Sound, saving/config persistence, full-level completion, all maps, unusual WADs,
long sessions, allocator exhaustion and simultaneous training/inference remain
untested or unavailable. [Integration work](architecture.md) lists the next steps.
