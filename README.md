# Metal Doom

A native Apple Silicon Mac host for the actual Doom engine, with initialization,
gameplay ticks and software rendering executed on the GPU through Metal.
The standalone app is separate from the reusable engine. Starts in E1M1,
skill 3, at 320×200; no sound. Game assets are supplied separately.

## Build and run

Test machine: M4 Mac mini, 10 GPU cores, 16 GB, macOS 26.5.1. Apple's Command
Line Tools are sufficient for the native app and runtime Metal compilation.
A separate `metal` compiler or full Xcode installation is unnecessary.

```sh
xcode-select --install                 # if Command Line Tools are absent
brew install llvm lld wabt             # engine source translation tools
./build.sh
./run.sh --wad /absolute/path/to/doom1.wad
```

Alternatively open `build/Metal Doom.app` and select your IWAD. The app bundles
its engine program, so it can be moved away from the checkout. No WAD is bundled.
Doom shareware 1.9 is the tested IWAD (MD5
`f0cefca49926d00903cf57551d901abe`). Doom/Ultimate Doom compatible IWADs use
E1M1; Doom II/Freedoom Phase 2 is not the startup target of this milestone.
The loader does not download game assets.

Controls: W/S or Up/Down move; A/D or Left/Right turn; Q/E strafe; Space fires;
Enter uses doors/switches; Shift runs; 1–7 select weapons; Tab opens automap;
Z is menu Enter; Escape closes the app. Closing the window also exits.

## Validation

```sh
./test.sh --wad /absolute/path/to/doom1.wad
./run.sh --wad /absolute/path/to/doom1.wad --headless --scripted \
  --ticks 350 --output build/benchmark
```

Headless output contains the original 320×200 framebuffer, per-tick diagnostics,
and measured Metal command execution time. `frame.rgba` retains Doom's native
little-endian B,G,R,0 bytes despite the historical extension; it is not RGBA.
Tests compare against an independent native ARM64 build of the original engine.
See [validation](docs/validation.md) for measured results and limitations.

## Design and scope

The source translation goes C → freestanding wasm32 → typed C → native MSL.
WebAssembly is an intermediate representation, not an interpreter at runtime.
The GPU instance contains explicit linear memory and stack state. Callable
`doom_initialize`, `doom_tick`, and `doom_render` functions have no CPU callbacks
or dispatch dependencies. The small standalone kernel calls those functions;
a future persistent kernel can call them directly.

See [architecture and integration work](docs/architecture.md),
[engine ABI](engine/API.md), [credits](CREDITS.md), and [source provenance](vendor/PROVENANCE.md).
The code is GPL-2.0-or-later with retained MIT third-party components.
The first version prioritizes correctness: it uses one GPU lane and synchronous
standalone dispatches. It makes no claim of GPU acceleration over a CPU port.
Save/config persistence and audio are unavailable. The outer allocator is
monotonic; full reset requires a new engine instance.
