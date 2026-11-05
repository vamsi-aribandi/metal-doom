# Correctness tests

Run from the repository root with an external IWAD:

```sh
python3 tests/run_tests.py --wad /absolute/path/to/doom1.wad
```

Build the native app first using the repository setup instructions. The test
runner builds an independent native ARM64 C reference library directly from the
vendored original engine, without passing through WASM, wasm2c, CFG lowering, or
Metal. Only the same startup loop scheduling change and platform adapter are
shared. The reference runs in fresh subprocesses because original Doom uses
global state.

The cases are initialization with zero additional ticks, 35 idle ticks, and
350 scripted ticks, plus 350 ticks drawing after every tick. Each CPU case repeats from a fresh engine; the GPU scripted
case also repeats. Each GPU tick is compared across all 16 diagnostic fields,
and each final 320 by 200 framebuffer is compared across every byte. The rendered
case also compares SHA256 for the initial frame and every tick: 351 complete
frame hashes, captured without additional renders. The test
also checks exactly one game tick per step, no tick advancement during drawing,
CSV hashes, and engine exit flags. Failure identifies the first differing field
or framebuffer pixel. `build/tests/report.json` records results and SHA-256
frame hashes. The `.rgba` suffix follows the host naming; actual byte order is
B, G, R, zero, matching the original DoomGeneric output.

The scripted input matches `app/main.mm`: forward for ticks indexed 0–139,
turn right 140–209, fire 210–279, use once at 280, then idle. Only key transitions
are enqueued. As in headless GPU mode, the CPU draws at initialization and once
after the final tick. The extra rendered case draws after every tick in both implementations.
Interactive mode draws according to its presentation schedule.

For Doom shareware 1.9 with SHA-256
`1d7d43be501e67d927e415e0b8f3e29c3bf33075e859721816f652a526cac771`,
`golden-shareware.json` additionally pins exact initial/final diagnostic arrays,
state byte hashes, framebuffer hashes, and full tick trace hashes. This file
contains no game assets. Other IWADs still receive full CPU/GPU comparisons.

To run only CPU reference and repeatability checks:

```sh
python3 tests/run_tests.py --wad /absolute/path/to/doom1.wad --cpu-only
```

To compare an existing GPU run without dispatching more GPU work:

```sh
python3 tests/reference.py --wad /absolute/path/to/doom1.wad \
  --ticks 350 --scripted --output build/reference-scripted
python3 tests/compare.py --reference build/reference-scripted \
  --actual build/results
```

The CPU reference is independent of the GPU compiler path, but both versions
share the same original game code and platform adapter; these tests are not a
proof of full compatibility with every WAD, savegame, demo, or exceptional path.
Sound and save/config writes are disabled. The first test of a Metal source may
spend time compiling the library before executing.

The native Metal palette conversion uses the original engine's indexed pixels
and gamma-adjusted palette across one SIMD group. The CPU reference deliberately
retains the original C conversion, so frame comparisons cover that optimization.
`tests/test_structured_cfg.py` separately compares generated structured loops and
branches with independently compiled ordinary C, including randomized inputs and
side effects. `tests/test_lowering.py` checks the complete translated engine on
CPU; its presentation adapter reconstructs RGB for its CFG-only comparisons.

## Cooperative rendering

```sh
python3 tests/test_render_states.py --wad /absolute/path/to/doom1.wad
python3 tests/test_queue_fallback.py --wad /absolute/path/to/doom1.wad
```

The first command (also included in `run_tests.py` / `test.sh`) uses a shared raw-key event schedule to compare 421 frame
hashes and every diagnostic against original C. CPU-side assertions verify
that low detail, smaller/full viewports, automap, menus, pause and gamma actually
activate. Movement, turning and fire input are included. Its Metal fixture reuses
the real native host. `--serial-render` checks the retained serial GPU path.

The second command builds a temporary source copy with
`DOOM_DRAW_FLUSH_LIMIT=16`, forcing serial overflow flushes while retaining the
fixed queue ABI. It compares 36 frame hashes, every tick diagnostic and every
final framebuffer byte, then removes the temporary source/build. Run the normal
build/reference tests first. These tests do not bundle assets.
