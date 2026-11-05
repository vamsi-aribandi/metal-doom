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

`build/doom-engine.metal` still has no kernel or host-mailbox dependency. A combined
kernel can call its functions from an independent game SIMD group while other
groups run training and inference. Only SIMD-group barriers occur inside the
Doom API. The standalone persistent wrapper demonstrates the game loop and live
ownership protocol, but does not include or validate the combined ML pipeline.

That integration must still check combined register/stack/resource limits,
progress of all workers and latency under real ML workloads. The standalone
uses call-stack depth 32; a combined pipeline must establish its own viable
resource settings. Long dispatches can also fail with an OS/driver timeout;
a successful sustained test does not guarantee indefinite operation.
