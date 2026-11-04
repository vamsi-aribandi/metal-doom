# Engine and standalone boundary

The actual DoomGeneric C engine is compiled freestanding for wasm32, then WABT
emits typed scalar C. `tools/to_metal.py` turns that intermediate into native
Metal functions. This is ahead-of-time source translation, not a GPU bytecode
interpreter. The intermediate gives every C pointer a 32-bit linear-memory
offset; function tables become checked signature-specific switches. Recursive
calls are native Metal calls. All original gameplay and software rendering run
on a single GPU lane in this correctness milestone.

`tools/lower_cfg.py` translates labels and branches to a structured basic-block
loop. Every generated function receives the GPU instance explicitly. There are
no mutable module globals, CPU callbacks, RPC, host libc, device enqueue, or
mandatory barriers. The original globals, zone heap, allocator, WAD bytes,
input queue, palette and framebuffer all live in that instance's GPU buffer.
The host uploads the immutable program data image and IWAD, then calls GPU
initialization. It never initializes map/gameplay state or draws world pixels.

Public callable device entrypoints in generated `build/doom.metal` are
`doom_initialize(instance, wadOffset, wadBytes)`, `doom_tick(instance)`, and
`doom_render(instance)`. See engine/API.md for input and diagnostics. The tiny
`doom_dispatch` kernel is only the standalone launch adapter. The app owns
asset loading, key transitions, scheduling and presentation. Tick and rendering
can be invoked consecutively inside another kernel without a dispatch boundary.

The engine clock advances once per game tick at 35 Hz. Rendering is a separate
operation, so callers choose when to present without changing simulation time.
Rendering can consume the original rendering PRNG, as in Doom; deterministic
comparisons must use the same tick/render schedule, especially with fuzzy
sprites. Animation/gameplay is driven by the original `G_Ticker`, input
responders and `G_BuildTiccmd`. Sound and wipe transitions are disabled.

# Before persistent megakernel integration

Target inspected: vamsi-aribandi/metal-megakernel commit
74b72a9 (one threadgroup, separate SIMD groups for training/inference/game).

1. Include the generated engine device functions and reserve independent
   instance memory, stack and framebuffer. The standalone wrapper kernel can
   be excluded. Initialization and tick/render have no CPU handshake.
2. Call the serial Doom engine on one lane of the game SIMD group, with all
   its work completed before publishing a frame. The engine uses no whole
   threadgroup barrier, so training and inference can remain independent.
3. Configure linked visible functions and sufficient native call stack depth
   on the combined pipeline. The standalone uses depth 32; measure actual
   stack, register pressure, occupancy and watchdog behavior in the combined
   pipeline before choosing launch configuration.
4. Add a frame ownership protocol/double buffer and input mailbox around the
   engine API. The standalone synchronizes through command completion; it
   makes no claim that concurrent CPU/GPU non-atomic access is safe. The target
   currently experiments with `coherent(system)`; public Metal command-boundary
   synchronization is what this app uses.
5. Keep bounded scheduling: a tick/render call is serial and may run for a
   long time. Profile it under simultaneous ML work. Parallelizing original
   columns/spans and reducing native call overhead are follow-up work.
6. Test level changes, death/restart, menus, all supported maps, pathological
   BSP depth and heap exhaustion. The outer allocator currently does not free;
   Doom's internal zone does. Recreate an instance for a full memory reset.
7. Ship corresponding GPL source and upstream notices with any combined binary.

The standalone is deliberately independent of the ML project. It does not
modify that repository or claim simultaneous ML/Doom scheduling is tested.
