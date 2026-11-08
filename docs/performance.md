# Performance measurement

Run the native app's headless profiler with an IWAD supplied outside the repository:

```sh
build/metal-doom --wad /path/to/doom1.wad --profile --samples 12 --output build/profile
```

`--profile` implies headless mode. `--samples` selects measured repetitions per phase
(default 12); `--ticks` independently controls simulation length for ordinary headless
runs (default 350). The profiler initializes Doom, warms each phase once, then measures
repeated rendering of that initialized scene without advancing simulation ticks.

`profile.json` records the device, sample count, and mean/minimum/maximum GPU milliseconds
for cooperative native palette expansion (`doom_present`), world rendering
(`R_RenderPlayerView`, with cooperative opaque drawing), and full engine rendering (staged `D_Display` through `doom_render`).
Each phase uses one command buffer. World rendering executes the original engine;
palette expansion uses its unchanged indexed pixels and gamma-adjusted palette. GPU command
timestamps exclude CPU encoding and waiting; mean wall time includes those costs. Shader
compilation, asset loading, initialization, and warmup are outside the measured intervals.

Phase timings describe one fixed starting scene. Full rendering also includes the HUD,
menus, and other display work, so isolated phase times need not add up exactly. Use a
simulation benchmark to measure varying scenes and tick costs:

```sh
build/metal-doom --wad /path/to/doom1.wad --headless --scripted \
  --render-every-tick --ticks 350 --output build/benchmark
```

That run records `summary.json`, a diagnostics trace, and the final framebuffer. Report
wall FPS together with GPU time, sample/scene configuration, device,
and source revision. Verify CPU/GPU equality before comparing optimized implementations.

## What was fixed

The first build serialized every stage on one GPU lane. Its memory helpers
accepted cached base/size arguments but reloaded those fields from device memory
for every access. Its general control-flow fallback also routed hot pixel loops
through a large program-counter switch. These costs were measured separately.

The current path uses the cached view, reconstructs proven reducible regions as
native loops/branches, and expands palette pixels across one SIMD group. The
world renderer preserves original scene traversal, sampling and draw order.
Opaque high-detail columns/spans now draw across the 32 game lanes; masked
textures/sprites/fuzz/UI and low detail remain serial. Palette expansion shares the
render dispatch and uses no whole-threadgroup barrier. The dispatch backend's native window uses
precise one-shot pacing and a layer-backed image rather than repainting a scaled
bitmap and title bar every frame.

See [validation results](validation.md) for the before/after measurements and raw
reports. Headless FPS is uncapped throughput; GUI FPS is paced toward 35 Hz and
should be measured in the foreground. macOS may throttle an inactive window.
`--frame-hashes` adds validation work and is intentionally omitted from benchmark
commands; the test runner enables it for the 350-frame reference comparison.

## Compare serial and cooperative drawing

Add `--serial-render` to the same headless benchmark or profiler command to use
the original serial indexed renderer in the **same binary and shader**. Palette
expansion remains cooperative in both modes. Reports record `serial_render`.
The flag is diagnostic and requires headless/profile mode. Run variants
sequentially on an otherwise idle GPU; do not compare a frame-hash run with a
throughput run. The renderer keeps one dispatch per frame and uses only the
32-lane game SIMD group; it does not occupy every GPU core.

## Submission overhead

The `--dispatch` backend submits input, ticks, and rendering in one command buffer, then waits
once. It retains separate ordered compute encoders with immutable per-operation
arguments; dispatch count and the engine API are unchanged. `--no-batch` restores
one submission/wait per operation for headless comparisons.

`summary.json` records `submissions` separately from `dispatches`.
`loop_gpu_ms` sums GPU command-buffer time in the measured gameplay loop,
excluding initialization. Batched runs also record `batched_frame_gpu_ms`,
including input, ticking and rendering. Their individual `tick_gpu_ms` and
`render_gpu_ms` are `null`, because command-buffer timestamps cannot separate
those stages. The isolated profiler continues to report individual phases;
the dispatch window displays combined GPU frame time.

The default persistent window displays FPS instead: its single command's GPU
timestamps are available only after shutdown and include idle waits. Persistent
execution and comparison commands are described in [persistent execution](persistent.md).

To repeat the controlled experiment:

```sh
python3 tests/benchmark_overhead.py --wad /path/to/doom1.wad --repeats 3
```

The harness uses one host binary and memory image, warms both pipelines, then
alternates separate submissions, batched submissions, and a test-only shader
that lets the two queue helpers inline. All final frames and tick traces must
match. The tested batching gain was about 7%; allowing the helpers to inline
made no measurable difference. The production translator remains unchanged.
[Raw experiment](results/overhead-benchmark.json).

## Persistent replay

```sh
python3 tests/benchmark_persistent.py --wad /path/to/doom1.wad --repeats 3
```

This warms each backend, then alternates the same 350-tick scripted scene with a
render after every tick. Tick traces and final pixels must match before timings
are accepted. The measured medians on the M4 were 105.95 FPS for persistent
replay and 102.75 FPS for ordered dispatch. Both include host frame handoff;
compilation and initialization are excluded. This modest difference does not
make Doom's serial gameplay and traversal parallel. The main change is one
running command instead of per-frame GPU submissions.
[Recorded results](results/persistent-benchmark.json).

## Drawing thread count

```sh
python3 tests/benchmark_parallelism.py --wad /path/to/doom1.wad --repeats 3
```

The experiment generates test-only shaders with 32, 64, 128 and 256 cooperating
threads. More than one SIMD group requires whole-threadgroup barriers and shared
broadcasts; increasing the launch size alone would create multiple game owners
and break synchronization. A transformed 32-thread control measures that extra
synchronization separately. Production shaders and the single-SIMD engine API
are unchanged.

Three warmed 350-tick runs, rendering every tick, gave these medians on the M4:

| Variant | Wall FPS | Change from production 32 |
| --- | ---: | ---: |
| 32 threads, original SIMD synchronization | 106.21 | — |
| 32 threads, whole-threadgroup synchronization | 106.23 | +0.02% |
| 64 threads | 110.38 | +3.93% |
| 128 threads | 113.25 | +6.63% |
| 256 threads | 114.88 | +8.16% |

The initial-view profiler separately measures palette conversion at 0.323 ms
with 32 threads and 0.063 ms with 256. World rendering changes from 9.740 ms to
9.309 ms, and full rendering from 10.346 ms to 9.814 ms. These fixed-scene phase
samples are not a decomposition of the gameplay-route FPS. They show strong
scaling for palette conversion and much smaller improvement for the complete
renderer; serial engine work and other rendering costs still limit throughput.

All five variants match original C across 351 scripted frames, 421 renderer-state
frames and 54 catch-up frames, including all sixteen diagnostics and every final
scripted framebuffer byte. Timed runs omit per-frame SHA256 work and still compare
every diagnostic row and final pixels. The normal 32-thread variant and the
256-thread winner also pass five-second live input and CPU replay checks, held
snapshot ownership, and shutdown while waiting for acknowledgement. Every replay
and live run uses one dispatch including initialization.

The standalone keeps its 32-thread default. The extra workers improve uncapped
throughput, but 256 threads use eight SIMD groups instead of one for an 8% gain.
A whole-threadgroup calling contract would require a different arrangement when
combined with independent training and inference groups. These results support
an optional larger game group in a future integration, rather than changing the
engine contract for a modest standalone gain. The window remains paced at 35 Hz.
The experiment validates normal gameplay paths; it does not establish that every
corrupted-memory fault path can terminate safely across multiple SIMD groups.
[Raw results](results/parallelism-benchmark.json).
