# Credits and licenses

Doom: id Software, John Carmack, John Romero, Dave Taylor, and the original
Doom team. The source retains the id Software copyright and GPL notices.
Chocolate Doom: Simon Howard and contributors. DoomGeneric: ozkl and contributors.
Vendored DoomGeneric is GPL-2.0-or-later; see LICENSE and individual source headers.

Minimal freestanding libc adapted from Jerry Zhu's open-flapping-airplanes
(2026), MIT. Original license retained in vendor/tinylibc/LICENSE. GPU porting
approaches, including the opaque draw queue, also informed by Jerry Zhu's
`doom-gpu/gpu_par.c` (MIT). GPU compilation approaches informed by Joseph
Huber's doomgeneric GPU port and LLVM GPU libc.
Exact source revisions and modifications: vendor/PROVENANCE.md.

New Metal adapter, generator, standalone host and tests: GPL-2.0-or-later.
The generated Metal engine is a derivative of GPL Doom engine source.
WABT wasm2c is an external build dependency (Apache-2.0 with LLVM exception);
LLVM and LLD are external build tools. No proprietary game assets are included.

The future metal-megakernel integration target has a separate MIT license.
A distributed combined program containing this Doom engine must satisfy the
Doom GPL obligations, including providing corresponding source.
