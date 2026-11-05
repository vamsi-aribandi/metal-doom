/* Metal Doom platform adapter. GPL-2.0-or-later; upstream engine remains in vendor/. */
#include <stdint.h>
#include <string.h>
#include <stdlib.h>
#include "doomgeneric.h"
#include "doomstat.h"
#include "d_player.h"
#include "d_event.h"
#include "d_loop.h"
#include "g_game.h"
#include "m_menu.h"
#include "i_video.h"
#include "r_main.h"
#include "doomkeys.h"

extern const unsigned char *tl_wad;
extern long tl_wad_len;
extern volatile char *tl_dbgbuf;
extern long tl_dbglen;
extern volatile long tl_dbgn;
extern volatile int tl_exited;
extern int rndindex, prndindex;
extern boolean advancedemo;
void D_ProcessEvents(void);
void D_DoAdvanceDemo(void);
void D_Display(void);
void I_StartTic(void);

static char log_buffer[16384];
static uint32_t virtual_ticks, slept_ms;
static uint16_t events[128];
static uint32_t event_read, event_write;
static int initialized;
static ticcmd_t tick_commands[MAXPLAYERS];
static int32_t diagnostics[16];

void DG_Init(void) {}
void DG_DrawFrame(void) {}
void DG_SleepMs(uint32_t ms) { slept_ms += ms; }
uint32_t DG_GetTicksMs(void) { return (virtual_ticks * 1000u + 34u) / 35u + slept_ms; }
void DG_SetWindowTitle(const char *title) { (void)title; }
int DG_GetKey(int *pressed, unsigned char *key) {
  if (event_read == event_write)
    return 0;
  uint16_t e = events[event_read++ & 127u];
  *pressed = e >> 8;
  *key = e & 255;
  return 1;
}

void *engine_alloc(uint32_t bytes) { return malloc(bytes); }
void engine_key(uint32_t pressed, uint32_t key) {
  if (event_write - event_read < 128)
    events[event_write++ & 127u] = (uint16_t)(((pressed != 0) << 8) | (key & 255));
}
void engine_tick(void) {
  if (!initialized || tl_exited)
    return;
  ++virtual_ticks;
  I_StartTic();
  D_ProcessEvents();
  M_Ticker();
  memset(tick_commands, 0, sizeof tick_commands);
  G_BuildTiccmd(&tick_commands[consoleplayer], gametic);
  netcmds = tick_commands;
  if (advancedemo)
    D_DoAdvanceDemo();
  G_Ticker();
  ++gametic;
}
int engine_can_render(void) { return initialized && !tl_exited; }
void engine_render(void) {
  if (!initialized || tl_exited)
    return;
  /* Wipes wait for wall time and combine rendering with simulation. Skip only that effect. */
  wipegamestate = gamestate;
  D_Display();
}
/* Isolated renderer entry for GPU profiling; normal rendering uses D_Display. */
void engine_profile_world(void) { R_RenderPlayerView(&players[displayplayer]); }
int engine_init(uintptr_t wad_offset, uint32_t wad_bytes) {
  if (initialized)
    return -2;
  tl_wad = (const unsigned char *)(uintptr_t)wad_offset;
  tl_wad_len = wad_bytes;
  tl_dbgbuf = log_buffer;
  tl_dbglen = sizeof log_buffer;
  tl_dbgn = 0;
  static char *args[] = {"doom",   "-iwad", "doom1.wad", "-warp",  "1",        "1",
                         "-skill", "3",     "-nomusic",  "-nosfx", "-nosound", 0};
  singletics = true;
  doomgeneric_Create(11, args);
  initialized = 1;
  engine_tick();
  engine_render();
  return tl_exited ? -1 : 0;
}
uintptr_t engine_framebuffer(void) { return (uintptr_t)DG_ScreenBuffer; }
uintptr_t engine_diagnostics(void) {
  player_t *p = &players[consoleplayer];
  diagnostics[0] = gametic;
  diagnostics[1] = gamestate;
  diagnostics[2] = p->health;
  diagnostics[3] = p->armorpoints;
  diagnostics[4] = p->mo ? p->mo->x : 0;
  diagnostics[5] = p->mo ? p->mo->y : 0;
  diagnostics[6] = p->mo ? p->mo->z : 0;
  diagnostics[7] = p->mo ? (int32_t)p->mo->angle : 0;
  diagnostics[8] = p->killcount;
  diagnostics[9] = p->itemcount;
  diagnostics[10] = p->secretcount;
  diagnostics[11] = rndindex;
  diagnostics[12] = prndindex;
  diagnostics[13] = leveltime;
  diagnostics[14] = p->readyweapon;
  diagnostics[15] = tl_exited;
  return (uintptr_t)diagnostics;
}
uintptr_t engine_log(void) { return (uintptr_t)log_buffer; }
uint32_t engine_log_size(void) { return (uint32_t)tl_dbgn; }
