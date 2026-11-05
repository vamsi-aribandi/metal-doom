/* Test adapter only: run the same wasm2c engine before and after CFG lowering. */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "doom.h"

static void save(const char *folder, const char *name, const void *data, size_t size) {
  char path[4096];
  if (snprintf(path, sizeof(path), "%s/%s", folder, name) >= sizeof(path))
    exit(20);
  FILE *f = fopen(path, "wb");
  if (!f || fwrite(data, 1, size, f) != size || fclose(f))
    exit(21);
}

static void save_frame(const char *folder, const char *name, w2c_doom *d) {
  /* Native Metal presents after the translated C renderer. Reconstruct its
     unchanged 32-bit palette expansion here so CFG tests still check pixels. */
  uint32_t frame[320 * 200];
  const uint8_t *indices = d->w2c_memory.data + w2c_doom_engine_index_buffer(d);
  const uint8_t *colors = d->w2c_memory.data + w2c_doom_engine_palette(d);
  for (unsigned i = 0; i < 320 * 200; i++) {
    uint32_t color;
    memcpy(&color, colors + 4 * indices[i], 4);
    frame[i] = color & 0x00ffffffu;
  }
  save(folder, name, frame, sizeof(frame));
}

int main(int argc, char **argv) {
  if (argc != 4)
    return 2;
  unsigned ticks = (unsigned)strtoul(argv[3], NULL, 10);
  wasm_rt_init();
  w2c_doom d;
  wasm2c_doom_instantiate(&d);
  FILE *f = fopen(argv[1], "rb");
  if (!f || fseek(f, 0, SEEK_END))
    return 3;
  long length = ftell(f);
  if (length <= 0 || length > UINT32_MAX || fseek(f, 0, SEEK_SET))
    return 4;
  u32 offset = w2c_doom_engine_alloc(&d, (u32)length);
  if (fread(d.w2c_memory.data + offset, 1, (size_t)length, f) != (size_t)length)
    return 5;
  fclose(f);
  int result = w2c_doom_engine_init(&d, offset, (u32)length);
  if (result) {
    fprintf(stderr, "init=%d: %s\n", result, d.w2c_memory.data + w2c_doom_engine_log(&d));
    return 6;
  }
  const size_t frame_bytes = 320 * 200 * 4;
  save_frame(argv[2], "initial.rgba", &d);
  int32_t *trace = calloc((size_t)ticks + 1, 16 * sizeof(int32_t));
  if (!trace)
    return 7;
  memcpy(trace, d.w2c_memory.data + w2c_doom_engine_diagnostics(&d), 16 * sizeof(int32_t));
  for (unsigned i = 0; i < ticks; ++i) {
    /* Same initial 100 idle ticks then held forward as research-smoke.c. */
    if (i == 100)
      w2c_doom_engine_key(&d, 1, 0xad);
    w2c_doom_engine_tick(&d);
    w2c_doom_engine_render(&d);
    memcpy(trace + (i + 1) * 16, d.w2c_memory.data + w2c_doom_engine_diagnostics(&d),
           16 * sizeof(int32_t));
  }
  save(argv[2], "ticks.bin", trace, ((size_t)ticks + 1) * 16 * sizeof(int32_t));
  save_frame(argv[2], "frame.rgba", &d);
  printf("final_state=");
  for (unsigned i = 0; i < 16; ++i)
    printf("%s%d", i ? "," : "", trace[ticks * 16 + i]);
  puts("");
  free(trace);
  wasm2c_doom_free(&d);
  wasm_rt_free();
  return 0;
}
