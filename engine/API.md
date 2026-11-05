# Translated engine ABI

For the public native Metal interface and its SIMD calling contract, see
[architecture](../docs/architecture.md). These frontend exports are implementation
details used by the native wrappers and CPU compiler tests.

The exported engine functions operate on an explicit WASM instance: its linear
memory contains all original Doom globals, heap, WAD data, input queue, and
framebuffer. The generated Metal adapter passes that instance explicitly.
All pointers below are 32-bit offsets into the instance's linear memory.

- `engine_alloc(bytes) -> offset`: reserve space for an IWAD. Copy asset bytes to
  that offset before initialization.
- `engine_init(wad_offset, wad_bytes) -> int`: initialize E1M1 at skill 3, load
  the deferred level using one original game tick, and draw the first indexed frame. The native Metal wrapper then presents it.
  Returns 0 on success. A fresh instance is required to restart.
- `engine_key(pressed, doom_key)`: enqueue a key transition; the original Doom
  input responders consume it at the next tick. Queue capacity is 128 events.
- `engine_tick()`: process input, menu, original command building, and exactly
  one original `G_Ticker`; increments `gametic` once.
- `engine_render()`: original indexed software renderer;
  no game tick. Time-based wipe transitions are skipped.
- `engine_render_begin()` / `engine_render_end()`: GPU frontend staging around
  collective opaque drawing. Begin runs display setup/scene traversal and queues
  high-detail opaque commands; end runs original masked drawing and display UI.
  These are internal; callers should use the collective native `doom_render`.
- `engine_draw_queue() -> offset`: internal fixed-layout opaque draw queue.
- `engine_index_buffer() -> offset`, `engine_palette() -> offset`: GPU frontend
  accessors used for cooperative native Metal presentation. The CPU reference
  retains the original palette conversion.
- `engine_framebuffer() -> offset`: 320 by 200 32-bit `0x00RRGGBB` pixels
  (little-endian bytes B, G, R, zero). Display as BGRA8 with alpha ignored.
- `engine_diagnostics() -> offset`: 16 signed 32-bit integers, ordered:
  `gametic, gamestate, health, armor, x, y, z, angle, kills, items, secrets,
  rndindex, prndindex, leveltime, readyweapon, exit_flag`. Position uses Doom's
  16.16 fixed point; angle preserves its unsigned 32-bit bit pattern. This
  accessor refreshes the array and must run on the instance owner lane.
- `engine_log() -> offset`, `engine_log_size() -> bytes`: startup/error log.

Keyboard defaults are DoomGeneric's: left `0xac`, up `0xad`, right `0xae`, down
`0xaf`, strafe left `0xa0`, strafe right `0xa1`, use `0xa2`, fire `0xa3`, run
`0xb6`; menu Escape is 27, Enter is 13, and weapon keys use ASCII 1 through 7.

The engine does not call the CPU, perform host IO, read wall time, or request a
kernel dispatch. The clock is deterministic at 35 ticks per second. Scheduling
belongs to the caller: the standalone persistent kernel schedules ticks using
host-supplied time, while the dispatch backend schedules them on the CPU. The linear memory is fixed at 96 MiB; its 64 MiB outer
allocator is monotonic, while Doom's zone allocator internally reuses gameplay
allocations. Save/config writes and sound output are unavailable.

`./engine/build_wasm.sh` uses freestanding LLVM, LLD, and wasm2c from WABT.
Apple's bundled clang currently cannot emit the wasm32 target; Homebrew LLVM
is selected when present. Override `CLANG` and `WASM_LD` as needed.

A native reference may compile the same generated patched C sources and
`platform.c`/`tinylibc.c`; the pointer-bearing API types are `uintptr_t` for native
64-bit compilation and remain 32-bit in the WASM ABI.
