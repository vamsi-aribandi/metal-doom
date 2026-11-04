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
350 scripted ticks, plus 35 ticks drawing after every tick. Each CPU case repeats from a fresh engine; the GPU scripted
case also repeats. Each GPU tick is compared across all 16 diagnostic fields,
and each final 320 by 200 framebuffer is compared across every byte. The test
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
