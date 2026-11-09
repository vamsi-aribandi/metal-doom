# Persistent execution

The native app launches `doom_persistent` once and keeps that dispatch running
until quit. Binding engine memory, allocating and copying the IWAD, initialization,
input, simulation, rendering, and frame publication all happen in that dispatch.
There are no setup dispatches, GPU-to-CPU engine calls, or per-frame submissions.

```sh
./run.sh --wad /path/to/doom1.wad
./run.sh --dispatch --wad /path/to/doom1.wad
```

The second command selects the older ordered-dispatch implementation. It uses
public Metal synchronization at command completion and is useful for comparison
or if persistent execution fails on a different OS/compiler/device.

## Scheduling and state

`app/persistent.metal` wraps the ordinary functions in
`engine/entrypoints.metal`. One 32-lane SIMD group owns the Doom instance; lane
zero runs gameplay, and the group cooperates on rendering. The engine's 96 MiB
linear memory, state header, asset upload and output buffers belong to that
instance. The CPU uploads the initial image and IWAD before launch.

The Mac app polls at 240 Hz to collect keyboard state, supply elapsed milliseconds,
and copy available frames. The GPU accumulates time at 35 ticks per second,
runs up to eight catch-up ticks, and renders once. The startup clock begins when
the initialized frame is received. The CPU does not issue tick/render requests.
Thirty-five FPS in the window is deliberate game pacing, not maximum throughput.

A GPU-owned output buffer holds pixels, diagnostics, observed keys, tick count
and a publication sequence. The CPU reads a published snapshot into private
memory, then acknowledges it through a separate CPU-owned control buffer. The
GPU cannot overwrite the snapshot before that acknowledgement. This simple
backpressure also pauses the game if the host stops consuming frames. A delayed
host can trigger up to eight catch-up ticks when it resumes; longer gaps are
capped. Display uses the private copy after acknowledgement.

Every poll checks shutdown, including waits for time or an acknowledgement.
On quit the app requests stop, waits for the original command to complete, and
then releases its buffers. It does not launch a shutdown kernel.

## Live memory visibility

**The live mailbox uses undocumented Metal compiler features.** Tested behavior
on an M4 with macOS 26.5.1 does not establish support on every Apple Silicon Mac
or future compiler. The fallback above remains available, and the mailbox is
confined to the standalone adapter rather than the reusable engine.

Metal's public [shared-storage contract](https://developer.apple.com/documentation/metal/mtlstoragemode/shared)
requires synchronization between CPU and GPU access. Unified physical memory
alone does not establish visibility while a dispatch is still running.
[Shared events](https://developer.apple.com/documentation/metal/synchronizing-events-between-a-gpu-and-the-cpu)
are signaled by encoded commands around workloads. Metal 4's public
[visibility options](https://developer.apple.com/documentation/metal/mtl4visibilityoptions)
do not provide a system-coherent shader mailbox.

The experimental wrapper enables `#pragma METAL internals`, accesses live buffers
through `volatile coherent(system)` pointers, and uses system-scope fences.
The spelling follows [ccv's Metal/ANE implementation](https://github.com/liuliu/ccv/blob/baa0ef3178c31a5f42befe50d5dd4a53cedbc84a/lib/nnc/mfa/ccv_nnc_mfa_ane_rowwise_coreml.mm#L368);
its BSD license is included. Each polling path fences, even while idle. Every
lane fences its payload writes, a SIMD barrier joins the writers, then lane zero
publishes the sequence. The host uses atomic word reads/writes. CPU and GPU
never write the same buffer during execution. The protocol is experimentally
validated, rather than guaranteed by the public C++/Metal memory models.

## Replay and tests

Headless mode uses the dispatch backend by default for existing tests and isolated
profiling. Select persistent replay explicitly:

```sh
./run.sh --persistent --headless --wad /path/to/doom1.wad \
  --scripted --render-every-tick --frame-hashes --ticks 350
python3 tests/test_persistent.py --wad /path/to/doom1.wad
python3 tests/test_persistent.py --wad /path/to/doom1.wad --live-seconds 60
```

Replay uploads its complete input/tick/render schedule before the single launch.
The host only consumes snapshots; it does not send per-tick operations. This
allows exact CPU comparisons, including Doom's render-dependent random-number
sequence. Realtime tests also record the GPU's actual tick/key/render schedule,
then replay that schedule through the independent C reference.

The tests check frame hashes, all sixteen diagnostics, publication before command
completion, one submission/dispatch including initialization, live changing input,
35 Hz pacing, snapshot ownership and shutdown with an unacknowledged frame.
Reports are written beneath `build/persistent-tests/`.

Persistent command timestamps cover initialization, game work and idle waits.
They cannot measure individual tick/render latency. The headless replay's wall
FPS includes snapshot handoff; it is not directly equivalent to the isolated
renderer profiler. No extra timing dispatches run during the persistent loop.

## Embedding with training and inference

`build/doom-engine.metal` has no kernel or host-mailbox dependency. To reuse the
complete persistent game loop, concatenate these sources in order:

1. `build/doom-engine.metal`
2. `app/persistent_protocol.h`
3. `#define DOOM_EMBEDDED 1`, then `app/persistent.metal`
4. The outer kernel, which calls `doom_persistent_job` from its game SIMD group.

The callable function takes ordinary arguments, without fixed buffer bindings:

```metal
doom_persistent_job(memory, instance, wad, host, out, config, operations,
                    lane, lanes);
```

The argument types and allocation requirements are the same as the standalone
`doom_persistent` kernel: a fresh 96 MiB initial memory image, a zeroed
`DoomState`, immutable IWAD bytes, separate zeroed `DoomHostControl` and
`DoomLiveOutput` buffers, constant `DoomPersistentConfig`, and replay operations
(a valid dummy buffer in realtime mode). The outer kernel chooses its own buffer
indices. All 32 lanes of exactly one complete SIMD group must call the function
with lane indices 0–31 and `lanes == 32`. No other group may access that Doom
instance concurrently. Other SIMD groups in the same threadgroup can run
independently; the job and engine use only SIMD-group barriers.

Defining `DOOM_EMBEDDED` omits the standalone kernel entry point. Without it, the
existing standalone kernel forwards to the same job, preserving its buffer
layout and behavior. The job returns after host stop, replay exhaustion, or an
engine failure, publishing `out->done` as 1, 2, or 3 respectively. It neither
stops nor joins the other workloads. The outer host must request their shutdown
too before waiting for the combined command to complete.

Keep consuming and acknowledging Doom snapshots through `host->ack`, update
`host->timeMS` and `host->keys`, and write `host->stop` to stop the job. A held
snapshot pauses only Doom; shutdown remains checked on that wait path. Preserve
the system-coherent qualifiers and fences when binding the live mailboxes.
The copied pixels are `0x00RRGGBB` (BGRA bytes with alpha ignored).

This interface does not include or validate the combined ML pipeline.

That integration must still check combined register/stack/resource limits,
progress of all workers and latency under real ML workloads. The standalone
uses call-stack depth 32; a combined pipeline must establish its own viable
resource settings. Long dispatches can also fail with an OS/driver timeout;
a successful sustained test does not guarantee indefinite operation.

## Deterministic cache boundaries

`engine/renderer_compat.py` patches generated C copies for both the native C
reference and the GPU build. Cached lumps reserve a zero-filled 128-byte tail;
composite wall textures clear their allocated data and the same tail. The
original vendored C stays unchanged.

Vanilla Doom's column routine wraps texture indices to 128 even for shorter
textures. At a short column's end, fixed-point rounding can sample beyond its
data. Our longer E1M1 recording found a read into zone allocator metadata:
identical game state and texture coordinates yielded different pixels on 32-bit
WASM and 64-bit native C. Heap metadata is not game image data. Padding gives
those trailing reads a defined zero value; clearing composite holes removes
another source of allocator-dependent pixels. This is a compatibility change at
undefined pixels, not a promise to match arbitrary historical heap contents.
The general issue is also described by
[Headless Doom's portability notes](https://github.com/jwhitham/HeadlessDoom/blob/master/BUGS.md).

Regression (no assets bundled):

```sh
./build.sh
./engine/build_reference.sh
python3 tests/test_cache_boundaries.py --wad /path/to/doom1.wad
```

The fixture preloads the captured 2,056-frame tick/key/render schedule into one
persistent dispatch. It checks serial and cooperative Metal rendering against
native C, comparing all pixels by SHA256 and all sixteen diagnostics. It covers
the later E1M1 views that the shorter 420-tick test did not reach.
