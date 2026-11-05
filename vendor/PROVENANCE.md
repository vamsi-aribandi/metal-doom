# Vendored sources

- `doomgeneric/`: https://github.com/ozkl/doomgeneric at
  `e257a09527062bfb986b6e5e0780839b6cbd5b71`. Source and original license/README
  are preserved. Doom, Chocolate Doom, and DoomGeneric authors retain their
  source credits. Source files specify GPL version 2 or later. The unused
  upstream `icon.c` raster asset is omitted; no game graphics are vendored.
- `tinylibc/`: https://github.com/Bobliuuu/open-flapping-airplanes/tree/main/doom-gpu
  at `9dd7dd0d3c1fc0245b43897eacd8d62aa096c4c1`, MIT, Jerry Zhu 2026.
  Adaptations: compiler varargs for wasm32; 64 MiB heap; explicit `uint64_t`
  memcpy/memset words and RNG, correcting assumptions about `unsigned long`.

The Doom engine is copied without gameplay edits. The build makes a temporary
copy and removes the startup loop's timed tick and draw calls. `engine/platform.c`
then explicitly calls Doom's original input responders, command builder,
`G_Ticker`, and `D_Display`. Wipe transitions are skipped by setting
`wipegamestate` before drawing. Sound output is disabled with normal engine flags.

No game WAD assets are included. A user must supply their own compatible IWAD.

The GPU build also renames the original platform `I_FinishUpdate` in its generated
copy and appends `engine/video_exports.c`. The native Metal presentation helper
expands the original indexed framebuffer and 256-entry gamma-adjusted palette
cooperatively. The independent CPU reference retains the original conversion;
all tested rendered frames match. No gameplay or indexed rasterization algorithm
is replaced by this platform presentation adapter.

The GPU build also applies `engine/parallel_draw.py` to generated copies:
opaque high-detail column/span calls enqueue their original draw parameters;
display resumes around a cooperative opaque draw, before original serial masked
drawing/UI. Low-detail rendering stays serial. A zone-cache purge flushes pending
commands before reclaiming their sources. The vendored files and CPU reference
are unchanged. This bounded queue approach is informed by Jerry Zhu's MIT
`doom-gpu/gpu_par.c`; its 256-thread CUDA barriers are not used here. Our queue,
Metal consumer, and staged wrapper are GPL-2.0-or-later.

`licenses/ccv-BSD-3-Clause.txt` preserves Liu Liu's BSD 3-Clause notice. The
standalone persistent mailbox's compiler-internal system-coherent pointer and
fence spelling follows ccv's Metal/ANE implementation at
`baa0ef3178c31a5f42befe50d5dd4a53cedbc84a`, informed by the local
metal-megakernel visibility experiments at `74b72a9d9567f4503650cd48f8a0bfff9ba62785`.
No ccv model code or assets are included. See CREDITS.md and docs/persistent.md.
