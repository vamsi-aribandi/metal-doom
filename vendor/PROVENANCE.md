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
