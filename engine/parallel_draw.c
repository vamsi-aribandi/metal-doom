/* SPDX-License-Identifier: GPL-2.0-or-later
 * Opaque draw queue. One owner produces commands; one SIMD group consumes them.
 * Queue approach informed by Jerry Zhu's MIT doom-gpu/gpu_par.c.
 * Sprites/fuzz/UI remain on the original serial path. Overflow flushes serially.
 */
#include <stdint.h>
#include "doomdef.h"
#include "doomstat.h"
#include "r_local.h"

#define DRAW_CAPACITY 4096
#ifndef DOOM_DRAW_FLUSH_LIMIT
#define DOOM_DRAW_FLUSH_LIMIT DRAW_CAPACITY
#endif
_Static_assert(DOOM_DRAW_FLUSH_LIMIT > 0 && DOOM_DRAW_FLUSH_LIMIT <= DRAW_CAPACITY,
               "Draw flush limit must fit the fixed queue");
struct DrawCommand { uint32_t dest, source, colors, position, step, count; int32_t next; };
struct DrawQueue {
    uint32_t columns, spans;
    int32_t head[320], tail[320];
    struct DrawCommand column[DRAW_CAPACITY], span[DRAW_CAPACITY];
};
_Static_assert(sizeof(struct DrawCommand) == 28, "Metal command ABI");
_Static_assert(sizeof(struct DrawQueue) == 231944, "Metal queue ABI");
static struct DrawQueue draw_queue;
int doom_draw_active, doom_drew_world;
void D_DisplayStage(int resume);
void NetUpdate(void);
int engine_can_render(void);
static int draw_pending;

static void draw_reset(void) {
    draw_queue.columns = draw_queue.spans = 0;
    for (int x = 0; x < 320; ++x) draw_queue.head[x] = draw_queue.tail[x] = -1;
}
static void draw_serial(struct DrawCommand *c, int span) {
    byte *dest = (byte *)(uintptr_t)c->dest;
    const byte *source = (const byte *)(uintptr_t)c->source;
    const byte *colors = (const byte *)(uintptr_t)c->colors;
    uint32_t position = c->position;
    for (uint32_t i = 0; i <= c->count; ++i) {
        uint32_t spot = span ? ((position >> 4) & 0xfc0) | (position >> 26)
                             : ((int32_t)position >> 16) & 127;
        *dest = colors[source[spot]];
        dest += span ? 1 : SCREENWIDTH;
        position += c->step;
    }
}
void doom_draw_flush_serial(void) {
    if (!doom_draw_active) return;
    for (uint32_t i = 0; i < draw_queue.columns; ++i) draw_serial(&draw_queue.column[i], 0);
    for (uint32_t i = 0; i < draw_queue.spans; ++i) draw_serial(&draw_queue.span[i], 1);
    draw_reset();
}
void doom_queue_column(byte *dest, const byte *source, const byte *colors,
                       int position, int step, int count, int x) {
    if (draw_queue.columns == DOOM_DRAW_FLUSH_LIMIT) doom_draw_flush_serial();
    int i = draw_queue.columns++;
    draw_queue.column[i] = (struct DrawCommand){(uint32_t)(uintptr_t)dest,
        (uint32_t)(uintptr_t)source, (uint32_t)(uintptr_t)colors,
        (uint32_t)position, (uint32_t)step, (uint32_t)count, -1};
    if (draw_queue.head[x] < 0) draw_queue.head[x] = i;
    else draw_queue.column[draw_queue.tail[x]].next = i;
    draw_queue.tail[x] = i;
}
void doom_queue_span(byte *dest, const byte *source, const byte *colors,
                     unsigned position, unsigned step, int count) {
    if (draw_queue.spans == DOOM_DRAW_FLUSH_LIMIT) doom_draw_flush_serial();
    draw_queue.span[draw_queue.spans++] = (struct DrawCommand){(uint32_t)(uintptr_t)dest,
        (uint32_t)(uintptr_t)source, (uint32_t)(uintptr_t)colors, position, step, (uint32_t)count, -1};
}
uintptr_t engine_draw_queue(void) { return (uintptr_t)&draw_queue; }
void engine_render_begin(void) {
    draw_reset();
    draw_pending = engine_can_render();
    if (!draw_pending) return;
    doom_draw_active = 1;
    doom_drew_world = 0;
    wipegamestate = gamestate;
    D_DisplayStage(0);
}
void engine_render_end(void) {
    if (!draw_pending) return;
    draw_pending = 0;
    doom_draw_active = 0;
    if (doom_drew_world) { R_DrawMasked(); NetUpdate(); }
    D_DisplayStage(1);
}
void engine_world_begin(void) {
    draw_reset();
    draw_pending = engine_can_render();
    if (!draw_pending) return;
    doom_draw_active = 1;
    R_RenderPlayerView(&players[displayplayer]);
}
void engine_world_end(void) {
    if (!draw_pending) return;
    draw_pending = 0;
    doom_draw_active = 0;
    R_DrawMasked();
    NetUpdate();
}
