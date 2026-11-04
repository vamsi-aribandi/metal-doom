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
for the original palette conversion (`I_FinishUpdate`), world rendering
(`R_RenderPlayerView`), and full engine rendering (`D_Display` through `engine_render`).
Each phase uses one command buffer and runs the original GPU engine function. GPU command
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
