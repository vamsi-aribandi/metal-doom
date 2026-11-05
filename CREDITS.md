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

The live CPU/GPU mailbox uses system-coherent pointer and fence spelling
informed by Liu Liu's [ccv Metal/ANE implementation](https://github.com/liuliu/ccv/blob/baa0ef3178c31a5f42befe50d5dd4a53cedbc84a/lib/nnc/mfa/ccv_nnc_mfa_ane_rowwise_coreml.mm#L368).
Its BSD 3-Clause notice is retained in
[vendor/licenses/ccv-BSD-3-Clause.txt](vendor/licenses/ccv-BSD-3-Clause.txt).
The protocol builds on the visibility experiments in
[metal-megakernel](https://github.com/vamsi-aribandi/metal-megakernel), revision
`74b72a9d9567f4503650cd48f8a0bfff9ba62785`;
its [MIT notice](vendor/licenses/metal-megakernel-MIT.txt) is retained. It relies on undocumented Metal
compiler behavior; see [persistent execution](docs/persistent.md).
