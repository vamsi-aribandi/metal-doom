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
(`R_RenderPlayerView`), and full engine rendering (`D_Display` through `engine_render`).
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
wall FPS together with render and tick GPU time, sample/scene configuration, device,
and source revision. Verify CPU/GPU equality before comparing optimized implementations.

## What was fixed

The first build serialized every stage on one GPU lane. Its memory helpers
accepted cached base/size arguments but reloaded those fields from device memory
for every access. Its general control-flow fallback also routed hot pixel loops
through a large program-counter switch. These costs were measured separately.

The current path uses the cached view, reconstructs proven reducible regions as
native loops/branches, and expands palette pixels across one SIMD group. The
world renderer remains original GPU Doom code. Palette expansion shares the
render dispatch and uses no whole-threadgroup barrier. The native window uses
precise one-shot pacing and a layer-backed image rather than repainting a scaled
bitmap and title bar every frame.

See [validation results](validation.md) for the before/after measurements and raw
reports. Headless FPS is uncapped throughput; GUI FPS is paced toward 35 Hz and
should be measured in the foreground. macOS may throttle an inactive window.
`--frame-hashes` adds validation work and is intentionally omitted from benchmark
commands; the test runner enables it for the 350-frame reference comparison.
