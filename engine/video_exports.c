/* GPU presentation adapter, included after the original i_video.c.
 * SPDX-License-Identifier: GPL-2.0-or-later
 * Doom still draws its original indexed framebuffer and updates its palette.
 * The native Metal adapter expands these pixels cooperatively after rendering.
 */
_Static_assert(SCREENWIDTH == 320 && SCREENHEIGHT == 200, "Metal presentation size");
_Static_assert(sizeof(struct color) == 4, "Metal palette entry size");
void I_FinishUpdate(void) { DG_DrawFrame(); }
uintptr_t engine_index_buffer(void) { return (uintptr_t)I_VideoBuffer; }
uintptr_t engine_palette(void) { return (uintptr_t)colors; }
