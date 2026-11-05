# Persistent execution: 2026-11-05

The M4 hardware and toolchain were rechecked. The native app now initializes and
runs Doom inside one continuously running Metal dispatch, with live keyboard
input and acknowledged framebuffer snapshots. The engine API remains unchanged.

`./test.sh --wad /external/path/doom1.wad` passes both backends, including
initialization, idle, scripted 350-tick and rendered 350-tick cases. Persistent
headless replay uses exactly one submission and one dispatch, including asset
copy and initialization. [Regression report](results/persistent-regression.json).

The dedicated persistent tests compare 421 rendered frames at one tick per frame
and 54 frames with eight-tick batches against original C. Every hash and all 16
diagnostics match; each frame is received before the single command completes.
The same suite's 60-second realtime test changes turning/fire input and captures
2,100 frames at 34.97 FPS. Replaying its actual observed tick/key/render schedule
in independent C matches every frame and 33,600 diagnostic values. Holding the
last snapshot without acknowledgement preserves its pixels; requesting stop
from that waiting state completes in 0.152 ms. Across these three tests, 2,575
frame hashes and 41,200 diagnostics match. [Sustained report](results/persistent-report.json).

A native-window run delivered 4,026 frames at an average 35.01 FPS, accepted turning
and firing (ammo 50→49), then quit with command status 4 and one total dispatch.
The window deliberately paces gameplay to 35 Hz. A successful run establishes
this tested duration, not indefinite operation on all Macs.

Three warmed 350-frame scripted benchmarks gave median wall throughput of
105.95 FPS for persistent replay and 102.75 FPS for ordered dispatch. Every tick
trace and final framebuffer matched. Persistent replay uses one submission and
dispatch; ordered dispatch uses 353 submissions and 711 dispatches. The small
speed difference does not change the serial gameplay/scene-traversal bottleneck.
The timings include frame handoff and exclude shader compilation/initialization.
[Benchmark report](results/persistent-benchmark.json).

A fresh source copy built successfully and its persistent replay matched the
CPU for 350 ticks, 351 frame hashes and all 256,000 final framebuffer bytes.
The generated shader and memory image remain a matched pair from that build.
Quitting during initialization and normal play both completed cleanup. Invalid
negative, overflowing and nonnumeric tick counts are rejected before launch.

Live mid-dispatch memory exchange depends on undocumented system-coherent Metal
compiler features. The public-API `--dispatch` backend is retained in a separate
shader. See [persistent execution](persistent.md) for the protocol, limitations,
reproduction commands, and work remaining before combined ML integration.

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
| Render after every tick | 350 | All 16 diagnostics; 351 frame SHA256s; all final frame bytes |
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

The independent CPU reference retains original serial indexed drawing and palette
conversion; it does not use the new queue or staged display hooks. A second
420-tick schedule compares **421 complete frame hashes and 6,736 diagnostic
values**, exercising low/high detail, reduced/restored/full viewport, automap,
menus, pause/resume, gamma, movement, turning and fire input.
[Renderer-state report](results/renderer-states.json).


Batched submission checks also pass with **eight ticks per render**, including
its final partial batch: 54 captured frames and 864 diagnostic values match the
CPU under the same schedule. There are 53 rendered batches plus three setup
submissions. The 350-frame gameplay test uses 353 submissions and 711 dispatches;
the default renderer-state schedule also asserts one submission per frame.
[Catch-up report](results/catchup-report.json).

An isolated build with a 16-command flush threshold forces overflow fallback;
36 frames, every tick diagnostic, and all final frame bytes match original C.
[Fallback report](results/queue-fallback.json). This test builds in a temporary
checkout without replacing the normal shader or app.

`python3 tests/test_lowering.py --wad /external/path/doom1.wad` separately
compared the original wasm2c output with the structured-control-flow version:
740 function bodies, 516 requiring lowering, 350 ticks, identical initial frame,
full diagnostic trace, and final frame. [Report](results/cfg-report.json).
The restructuring suite also passes 13 fixed regressions and 78,660 independent
randomized/void differential executions under clang O0, O2, and UBSan. Unsupported
regions conservatively retain the general control-flow fallback.

A clean checkout exported from Git built successfully and produced the same
initial GPU frame/state as the native CPU reference. The source checkout and
app bundle contain no WADs or game graphics. The unused upstream raster icon
was omitted.

Native UI tested through keyboard input: Up moved the player, Right turned,
Space fired and reduced ammo from 50 to 49, M opened/closed the original Doom menu, and Escape stopped the app.
The final packaged-app WAD picker and loading window were also verified. The
windowed version uses a serial background GPU queue, one-shot tick deadlines,
and a layer-backed image; Cocoa handles input and
window events on the main thread. Startup compilation and WAD selection occur
after a window is created, so the loading UI can respond immediately.
Malformed IWAD header, PWAD, invalid directory and out-of-bounds lump inputs
were rejected by the asset loader before GPU initialization.

## Performance

Reproduce the uncapped benchmark with a render after every game tick:

```sh
./run.sh --wad /external/path/doom1.wad --headless --scripted \
  --ticks 350 --render-every-tick --output build/benchmark
./run.sh --wad /external/path/doom1.wad --profile --samples 12 \
  --output build/profile
```

Three controlled 350-frame runs per variant give these medians at 320×200:

| Host/shader variant | Wall FPS | GPU work per frame | Submissions | Dispatches |
| --- | ---: | ---: | ---: | ---: |
| Separate submissions | 96.93 | 8.989 ms | 711 | 711 |
| One submission per frame | 103.68 | 8.968 ms | 353 | 711 |
| Batched, queue helpers allowed to inline | 103.74 | 8.970 ms | 353 | 711 |

Batching improves throughput about **7%**, primarily by reducing CPU submission
and waiting overhead. The inline change is within timing variation and is not
shipped. All variants produce identical final frames and complete tick traces.
The harness uses the same host/image, warms both shaders, and alternates trial
order. [Full A/B report](results/overhead-benchmark.json).

A representative batched run is saved as [raw benchmark](results/render-benchmark.json).
Its combined input/tick/render GPU time is reported as one total; individual
phase fields are `null`. The earlier 98.17 FPS cooperative-renderer run is
retained [before batching](results/render-benchmark-before-batching.json), and
the earlier 53.78 FPS serial-world run is retained as a
[historical comparison](results/render-benchmark-serial.json). Initialization
and compilation are excluded from all timed gameplay loops.

Fixed-scene profiling below uses the **same initialized scene**, 12 measured
samples per phase. The first build measured 7.69 FPS over 35 initial scripted frames; it is
not the same gameplay sample count as the current benchmark.

| GPU phase | First build | Before parallel drawing | Current build |
| --- | ---: | ---: | ---: |
| Palette expansion | 82.87 ms | 0.321 ms | 0.321 ms |
| World rendering | 41.16 ms | 17.53 ms | 9.53 ms |
| Full frame | 125.27 ms | 18.27 ms | 10.23 ms |

Raw [baseline profile](results/profile-baseline.json),
[before parallel drawing](results/profile-before-parallel.json), and
[current profile](results/profile-current.json). Per-phase sums differ slightly
from full-frame time because of other display work and GPU clock variation.

The renderer queues original high-detail opaque column/span parameters and
consumes them across 32 lanes inside the same dispatch. Scene traversal,
masked textures/sprites/fuzz/UI and low detail retain original serial behavior.
Fixed-point sampling, column overwrite order, and resulting pixels are
preserved. Queue overflow and impending texture-cache reclamation use a serial
flush. No extra render dispatch or whole-threadgroup barrier is required.

The native window remains paced toward Doom's **35 Hz game clock**.
The batched host was verified at about 35 FPS in the foreground, including
turning, firing (ammo 50 → 49), and the original menu overlay. The 104 FPS
uncapped benchmark describes engine throughput and timing headroom.
macOS can throttle inactive windows. First compilation of a changed engine
still takes tens of seconds.

This does not claim to outperform native CPU Doom or to use every GPU core.
It uses the intended single 32-lane game group, leaving the integration's other
groups independent. See [profiling methodology](performance.md) and
[architecture](architecture.md).

Native recursion works, but depth 128 failed pipeline creation with
`Compute pipeline exceeds available stack space`; depth 32 passed this suite.
Unusual maps or gameplay can require deeper native calls and are unverified.
Sound, saving/config persistence, full-level completion, all maps, unusual WADs,
long sessions, allocator exhaustion and simultaneous training/inference remain
untested or unavailable. [Integration work](architecture.md) lists the next steps.
