// SPDX-License-Identifier: GPL-2.0-or-later
// Word-only layout shared by Objective-C++ and runtime-compiled Metal.
#ifndef DOOM_PERSISTENT_PROTOCOL_H
#define DOOM_PERSISTENT_PROTOCOL_H
#ifdef __METAL_VERSION__
using DoomWord = uint;
#else
#include <cstdint>
using DoomWord = uint32_t;
#endif
struct DoomHostControl {
  DoomWord stop, timeMS, keys, ack;
  DoomWord padding[28];
};
struct DoomPersistentConfig {
  DoomWord wadBytes, replay, operationCount, serialRender;
};
struct DoomReplayOperation { DoomWord code, a, b; };
struct DoomLiveOutput {
  // sequence publishes the payload; done is independent of frame ownership.
  DoomWord sequence, tick, result, fault, done, logBytes, renders, keys;
  DoomWord padding[24];
  DoomWord diagnostics[16];
  DoomWord log[1024];
  DoomWord pixels[320 * 200];
};
#endif
