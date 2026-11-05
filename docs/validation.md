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

`python3 tests/test_lowering.py --wad /external/path/doom1.wad` separately
compared the original wasm2c output with the structured-control-flow version:
725 function bodies, 507 requiring lowering, 350 ticks, identical initial frame,
full diagnostic trace, and final frame. [Report](results/cfg-report.json).
The restructuring suite also passes 13 fixed regressions and 78,660 independent
randomized/void differential executions under clang O0, O2, and UBSan. Unsupported
regions conservatively retain the general control-flow fallback.

A clean checkout exported from Git built successfully and produced the same
initial GPU frame/state as the native CPU reference. The source checkout and
app bundle contain no WADs or game graphics. The unused upstream raster icon
was omitted.

Native UI tested through keyboard input: Up moved the player, Right turned,
Space fired and reduced ammo from 50 to 48, M opened/closed the original Doom menu, and Escape stopped the app.
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

The latest 350-frame gameplay run took **6.326 seconds: 55.33 FPS** at the same
320×200 resolution. Mean measured GPU time was **16.69 ms/render** and
**0.579 ms/tick** across its changing views. Compilation/initialization are
excluded from the timed loop; `gpu_ms` includes initialization, while the
separate render/tick counters do not. [Raw benchmark](results/render-benchmark.json).

The original correctness build measured 7.69 FPS over 35 initial scripted
frames. The table below isolates the **same initialized scene**, 12 measured
samples per phase, to distinguish implementation speedups from changing views:

| GPU phase | First build | Current build |
| --- | ---: | ---: |
| Palette expansion | 82.87 ms | 0.321 ms |
| Original world rendering | 41.16 ms | 17.53 ms |
| Full frame | 125.27 ms | 18.27 ms |

Raw [baseline profile](results/profile-baseline.json) and
[current profile](results/profile-current.json). Full-frame execution improved
about 6.9× in this fixed-scene comparison. The per-phase sum differs slightly
from full-frame time because of other display work and GPU clock variation.

The fixes use cached GPU memory views, native loop/branch reconstruction, and
cooperative palette expansion across one SIMD group. The original indexed
rasterizer is unchanged. CPU reference equality is checked on the complete
presented frame, including palettes and HUD, for all 351 captured frames.

Foreground window testing measured roughly **32–35 FPS**, paced toward Doom's
35 Hz game clock. Turning and firing were verified with the layer-backed view.
The uncapped headless benchmark measures engine throughput; it is not a claim
that the window presents 55 frames per second. macOS can throttle inactive
windows, so background FPS does not describe foreground gameplay performance.
First compilation of a changed engine still takes tens of seconds.

World rasterization remains sequential on the GPU, so more parallelization is
possible. The project does not claim to outperform native CPU Doom. See the
[profiling methodology](performance.md) and [architecture](architecture.md).

Native recursion works, but depth 128 failed pipeline creation with
`Compute pipeline exceeds available stack space`; depth 32 passed this suite.
Unusual maps or gameplay can require deeper native calls and are unverified.
Sound, saving/config persistence, full-level completion, all maps, unusual WADs,
long sessions, allocator exhaustion and simultaneous training/inference remain
untested or unavailable. [Integration work](architecture.md) lists the next steps.
