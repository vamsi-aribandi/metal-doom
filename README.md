# metal-doom

A Doom port for Apple Silicon Macs. The Doom engine runs on the GPU using
Metal, with a native Mac app for keyboard input and display. Based on
[doomgeneric](https://github.com/ozkl/doomgeneric).

## Requirements

- An Apple Silicon Mac.
- Apple's Command Line Tools (`xcode-select --install`).
- Python 3 and Homebrew.
- LLVM, LLD, and WABT, installed with the command below.
- A Doom IWAD file. The port is tested with Doom shareware 1.9 (`doom1.wad`).

Tested on an M4 Mac mini running macOS 26.5.1. Full Xcode is not required;
Metal shaders are compiled by the app at launch.

Game assets are not included or downloaded by the build scripts.

## Build

```sh
git clone https://github.com/vamsi-aribandi/metal-doom.git
cd metal-doom
brew install llvm lld wabt
./build.sh
```

This builds the executable and `build/Metal Doom.app`.

## Run

```sh
./run.sh --wad /path/to/doom1.wad
```

You can also open `build/Metal Doom.app` and select an IWAD in the file picker.
The app bundles the engine and shaders, so it can be moved out of the checkout.
The first launch can take tens of seconds while Metal compiles the shaders.

The game starts in E1M1 at skill 3 and renders at 320×200. Gameplay runs at
Doom's original 35 ticks per second, with the window drawing at the same rate.
Doom II and Freedoom Phase 2 are not supported.
Audio, saved games, and configuration persistence are not implemented.

### Controls

| Key | Action |
| --- | --- |
| W / S or Up / Down | Move forward / backward |
| A / D or Left / Right | Turn |
| Q / E | Strafe |
| Space | Fire |
| Enter | Use doors and switches |
| Shift | Run |
| 1–7 | Select weapon |
| Tab | Toggle automap |
| M | Open or close the game menu |
| Z | Select a menu item |
| Escape | Quit the app |

## How it works

The build compiles DoomGeneric's C engine to WebAssembly, converts it to typed
C with `wasm2c`, then generates Metal source. The app compiles that source into
native GPU functions at launch.

Game logic and rendering execute on the GPU. One GPU thread handles gameplay
and scene traversal. Opaque surfaces and palette conversion run across 32
threads. Sprites, special effects, and low-detail rendering use the original
serial drawing code.
The CPU loads the IWAD, collects input, and displays completed frames. The app
submits input, game ticks, and rendering together in one Metal command buffer
per frame.

The engine and app are separate:

- `engine/` contains the platform adapter, GPU state, and initialization, tick,
  and rendering functions.
- `tools/` translates the compiled engine into Metal.
- `app/` contains the native window, input handling, and standalone dispatch
  kernel.
- `vendor/` contains the upstream engine and minimal C library.

The generated `build/doom-engine.metal` can be embedded in another Metal
kernel. Integration with training and inference workloads has not been tested.
See [architecture](docs/architecture.md) and the [engine API](engine/API.md)
for details.

## Tests

After building, run the CPU/GPU comparisons with the same IWAD:

```sh
./test.sh --wad /path/to/doom1.wad
```

The tests compare game state and rendered frames against a native CPU build
of the engine. See [test instructions](tests/README.md),
[profiling instructions](docs/performance.md), and
[validation results](docs/validation.md).

## License

GPL-2.0-or-later, with MIT-licensed components. See [LICENSE](LICENSE),
[credits](CREDITS.md), and [source provenance](vendor/PROVENANCE.md).
