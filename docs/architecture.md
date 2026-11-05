# GPU engine and native host

DoomGeneric's original C gameplay and indexed software renderer execute on the
GPU. The build compiles freestanding C to wasm32, uses WABT's typed C output as
an intermediate, then generates native Metal functions. There is no WebAssembly
bytecode interpreter at runtime. Original pointers become 32-bit offsets into
explicit GPU memory; indirect calls use signature-checked switches. Recursive
calls remain native Metal calls.

`tools/structured_cfg.py` reconstructs native loops and branches when dominator,
loop-entry/exit, and region checks establish that the transformation is valid.
Unsupported graphs retain the general program-counter fallback in
`tools/lower_cfg.py`. Independent differential tests cover both paths. This
keeps hot pixel loops out of the old per-basic-block switch dispatcher.

`engine/parallel_draw.py` patches generated GPU build copies at the existing
opaque column/span calls and at the display/opaque/masked boundaries. The owner
runs scene traversal and queues exact fixed-point drawing parameters; 32 lanes
consume the queue inside the same render call. Every screen column has one owner,
preserving overwrite order. Original visplane spans do not overlap. Two
SIMD-group barriers separate columns, spans, and the original serial masked
textures/sprites/fuzz/UI. Low-detail drawing remains serial.

`engine/parallel_draw.c` stores a fixed 231,944-byte queue in instance memory.
Overflow flushes pending commands serially and resumes queuing. The same flush
runs before the zone allocator purges a cached source texture/flat, so deferred
commands never retain reclaimed data. No cache pinning or gameplay changes are
needed. `doom_render_serial` retains the original indexed path for comparison.
Initialization currently draws its first frame serially.

Doom still produces its original indexed framebuffer and gamma-adjusted palette.
`engine/video_exports.c` replaces only the final platform palette-expansion loop
in generated build copies. `engine/entrypoints.metal` expands those exact colors
to `0x00RRGGBB` across one SIMD group. The software renderer and presentation run
in the same callable function and dispatch; the CPU never draws world pixels.
The independent native CPU reference retains upstream's original conversion.

All mutable engine globals, WAD bytes, heap, input queue, palette, framebuffer,
and stack pointer belong to a `DoomState` instance. There are no CPU callbacks,
RPC, host IO, device enqueue, or mandatory separate engine kernel launches.
The CPU uploads program constants and assets, collects keys, and displays output.

# Callable interface

The generator writes:

- `build/doom-engine.metal`: runtime, translated engine, and ordinary device
  entrypoints; **no kernel entrypoint**.
- `build/doom.metal`: that engine plus `app/dispatch.metal`, the standalone's
  small operation dispatcher.

Public GPU functions are in `engine/entrypoints.metal`:

| Function | Calling contract |
| --- | --- |
| `doom_bind(state, memory)` | Instance owner lane; bind a fresh uploaded 96 MiB program image. |
| `doom_allocate(state, bytes)` | Owner lane; allocate asset storage, returning a linear-memory offset. |
| `doom_key(state, pressed, key)` | Owner lane; enqueue an original Doom key transition. |
| `doom_initialize(state, wadOffset, wadBytes, lane, lanes)` | Collective call by one SIMD group; initializes and presents the first frame. |
| `doom_tick(state)` | Owner lane; exactly one original game tick. |
| `doom_render(state, lane, lanes)` | Collective call by that SIMD group; render and present without advancing a tick. |

Offsets for framebuffer, diagnostics, and logs also have public accessors. The
state header and linear-memory allocation must be separate and correctly
aligned. `lane` is zero-based within the participating SIMD group and `lanes`
is its active width, 32 in this app. All participating lanes call collective
functions with the same state and arguments. Tick, input, and allocation have
one owner, lane zero. Only SIMD-group device-memory barriers are used; no full
threadgroup barrier constrains independent workloads.

The standalone uses repeated dispatches and public command-completion
synchronization. That scheduling belongs to the host, not the engine. A
persistent caller can preload assets and invoke binding, initialization, ticks,
and rendering from its own kernel without a CPU handshake between calls.
See [engine details](../engine/API.md) for the translated frontend's offsets and
input/diagnostic layout.

Simulation advances at 35 ticks per second. Rendering remains separate and can
consume Doom's original rendering PRNG, especially for fuzzy sprites; CPU/GPU
comparisons therefore use identical tick/render schedules. The GUI paces frames
to game ticks with a one-shot timer and runs GPU work on a serial background
queue. Sound and wipe transitions are disabled.

# Before persistent megakernel integration

Inspected target: `vamsi-aribandi/metal-megakernel` commit `74b72a9`, one
96-thread threadgroup with independent training, inference, and game SIMD groups.

1. Concatenate the generated engine-only source with the target's kernel source.
   Reserve independent state, linear memory, assets, and output for Doom.
2. Have all 32 lanes of the game SIMD group call initialization/rendering;
   lane zero calls ticks and input functions. Its SIMD-group barriers do not
   require training or inference groups to join.
3. Link the generated visible functions and validate call-stack/register limits
   in the **combined** pipeline. Depth 32 passes the standalone suite; depth
   128 exceeded the M4's available stack. Combined ML resource use is untested.
4. Add input/frame ownership and double buffering around this API. Standalone
   command completion does not establish a live CPU/persistent-GPU mailbox.
   The target's experimental `coherent(system)` mechanism remains separate.
5. Measure tick/render latency alongside real ML work. Opaque columns/spans
   already share the game SIMD group; scene traversal,
   masked drawing and low detail remain sequential. Optimize further only if
   coexistence measurements require it.
6. Extend gameplay coverage to full levels, death/restart, other maps, unusual
   BSP depth, and long sessions. The outer allocator is monotonic; a fresh
   instance resets it, while Doom's zone allocator reuses internal allocations.
7. Preserve GPL source availability and upstream notices in a combined release.

The standalone does not modify the ML repository or claim simultaneous
training/inference/Doom has been validated.
