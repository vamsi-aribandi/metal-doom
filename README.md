# metal-doom

The actual Doom engine running on an Apple Silicon GPU through Metal, with a
native Mac app. Gameplay, the original software renderer, and palette conversion
execute on the GPU. The CPU loads assets, collects keyboard input, and presents
completed frames. Starts in E1M1, skill 3, at 320×200; sound can wait.

On the tested M4 Mac mini, the 350-frame gameplay benchmark runs at **about
55 FPS**, versus 7.7 FPS in the first correctness build. The window is paced to
Doom's 35 Hz game clock; foreground testing measured roughly 32–35 FPS. All 351
captured frames match the independent CPU reference. See
[measurements and tests](docs/validation.md).

## Build and run

Requires Apple Silicon, macOS, Apple's Command Line Tools, Python 3, and the
engine build tools below. Tested on M4, 16 GB, macOS 26.5.1. Full Xcode and the
offline `metal` executable are unnecessary: the app compiles Metal at launch.

```sh
git clone https://github.com/vamsi-aribandi/metal-doom.git
cd metal-doom
brew install llvm lld wabt
./build.sh
./run.sh --wad /absolute/path/to/doom1.wad
```

Install Command Line Tools with `xcode-select --install` if absent. Alternatively
open `build/Metal Doom.app` and choose an IWAD. The app bundles its engine program
and can be moved away from the checkout. First launch may spend tens of seconds
compiling; the loading window remains responsive.

**No game assets are bundled or downloaded.** Doom shareware 1.9 is the tested
IWAD (MD5 `f0cefca49926d00903cf57551d901abe`). Doom/Ultimate Doom compatible IWADs
use E1M1; Doom II and Freedoom Phase 2 are not this milestone's startup target.

Controls: W/S or Up/Down move; A/D or Left/Right turn; Q/E strafe; Space fires;
Enter uses doors/switches; Shift runs; 1–7 choose weapons; Tab opens automap;
M opens the original menu; Z is menu Enter; Escape quits.

## Test and profile

```sh
./test.sh --wad /absolute/path/to/doom1.wad
./run.sh --wad /absolute/path/to/doom1.wad --headless --scripted \
  --ticks 350 --render-every-tick --output build/benchmark
./run.sh --wad /absolute/path/to/doom1.wad --profile --samples 12 \
  --output build/profile
```

Tests compare original ARM64 C against GPU state, every captured frame's SHA256,
and every final framebuffer byte. Compiler restructuring also has independent
randomized differential tests. Profiling isolates world rendering, palette
conversion, and full-frame GPU time. [Methodology](docs/performance.md).

## Reusable GPU engine

The source translation is C → freestanding wasm32 → typed C → native Metal.
WebAssembly is an intermediate representation, not a runtime interpreter. Hot
control flow becomes native loops and branches, with a conservative fallback.
Doom's original indexed renderer runs on the owner lane; one SIMD group expands
its exact palette in parallel inside the same render function and dispatch.

`build/doom-engine.metal` contains the reusable device functions and explicit
`DoomState`, without a kernel entrypoint. `app/dispatch.metal` supplies the
standalone launch adapter. Initialization/rendering use only SIMD-group barriers;
there are no CPU callbacks or mandatory separate engine launches.

See [architecture and remaining megakernel work](docs/architecture.md),
[engine ABI](engine/API.md), [credits](CREDITS.md), and
[source provenance](vendor/PROVENANCE.md). Combined training/inference scheduling
has not been tested. World rasterization is still serial, so there is more GPU
performance work available. Audio and save/config persistence are unavailable;
long sessions, full levels, and other maps need broader testing.

GPL-2.0-or-later, with retained MIT third-party components. Proprietary WADs and
game graphics are excluded from source control and app archives.
