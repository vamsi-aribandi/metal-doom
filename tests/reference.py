#!/usr/bin/env python3
"""Run independent native original-C reference. One process per engine instance."""
import argparse
import csv
import ctypes
import hashlib
import json
from pathlib import Path
import struct
import time

ROOT = Path(__file__).resolve().parent.parent
FIELDS = 'gametic gamestate health armor x y z angle kills items secrets rndindex prndindex leveltime weapon exited'.split()
HEADER = ['tick', 'hash', *FIELDS]
KEY_CODES = [0xad, 0xaf, 0xac, 0xae, 0xa0, 0xa1, 0xa3, 0xa2, 13, 0xb6, 49, 50, 51, 52, 53, 54, 55, 9]

def state_hash(values):
    value = 2166136261
    for item in values:
        value = ((value ^ (item & 0xffffffff)) * 16777619) & 0xffffffff
    return value

def input_mask(index, scripted):
    if not scripted:
        return 0
    return ((1 if index < 140 else 0) | (8 if 140 <= index < 210 else 0) |
            (64 if 210 <= index < 280 else 0) | (128 if index == 280 else 0))

def sha256(data):
    return hashlib.sha256(data).hexdigest()

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--wad', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--ticks', type=int, default=350)
    parser.add_argument('--scripted', action='store_true')
    parser.add_argument('--render-every-tick', action='store_true')
    parser.add_argument('--frame-hashes', action='store_true')
    parser.add_argument('--library', type=Path, default=ROOT / 'build/libdoom_reference.dylib')
    args = parser.parse_args()
    if args.frame_hashes and not args.render_every_tick:
        parser.error('--frame-hashes requires --render-every-tick')
    if args.ticks < 0:
        parser.error('--ticks must be nonnegative')
    d = ctypes.CDLL(str(args.library.resolve()))
    d.engine_alloc.argtypes = [ctypes.c_uint32]
    d.engine_alloc.restype = ctypes.c_void_p
    d.engine_init.argtypes = [ctypes.c_size_t, ctypes.c_uint32]
    d.engine_init.restype = ctypes.c_int
    d.engine_key.argtypes = [ctypes.c_uint32, ctypes.c_uint32]
    d.engine_tick.argtypes = []
    d.engine_render.argtypes = []
    d.engine_diagnostics.argtypes = []
    d.engine_diagnostics.restype = ctypes.POINTER(ctypes.c_int32)
    d.engine_framebuffer.argtypes = []
    d.engine_framebuffer.restype = ctypes.c_void_p
    d.engine_log.restype = ctypes.c_char_p
    wad = args.wad.read_bytes()
    offset = d.engine_alloc(len(wad))
    if not offset:
        raise RuntimeError('IWAD allocation failed')
    ctypes.memmove(offset, wad, len(wad))
    start = time.perf_counter()
    result = d.engine_init(offset, len(wad))
    if result:
        raise RuntimeError(f'Engine initialization failed: {result}: {d.engine_log().decode(errors="replace")}')
    initial = list(d.engine_diagnostics()[:16])
    rows = []
    frame_hashes = [(0, sha256(ctypes.string_at(d.engine_framebuffer(), 320 * 200 * 4)))] if args.frame_hashes else []
    previous = 0
    for index in range(args.ticks):
        keys = input_mask(index, args.scripted)
        changed = keys ^ previous
        for bit, code in enumerate(KEY_CODES):
            if changed & (1 << bit):
                d.engine_key((keys >> bit) & 1, code)
        previous = keys
        d.engine_tick()
        if args.render_every_tick:
            d.engine_render()
            if args.frame_hashes:
                frame_hashes.append((index + 1, sha256(ctypes.string_at(d.engine_framebuffer(), 320 * 200 * 4))))
        state = list(d.engine_diagnostics()[:16])
        if state[0] != index + 2 or state[15]:
            raise AssertionError(f'Invalid tick progression: tick={index + 1}, state={state}')
        rows.append([index + 1, state_hash(state), *state])
    # Match headless host: initialize with one draw, advance all ticks, then draw once.
    before_render = list(d.engine_diagnostics()[:16])
    if not args.render_every_tick or not args.ticks:
        d.engine_render()
    after_render = list(d.engine_diagnostics()[:16])
    if before_render[0] != after_render[0] or before_render[13] != after_render[13]:
        raise AssertionError('Rendering advanced game ticks')
    frame = ctypes.string_at(d.engine_framebuffer(), 320 * 200 * 4)
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / 'frame.rgba').write_bytes(frame)
    hash_path = args.output / 'frame-hashes.csv'
    if args.frame_hashes:
        with hash_path.open('w', newline='') as f:
            writer = csv.writer(f, lineterminator='\n')
            writer.writerow(['tick', 'digest'])
            writer.writerows(frame_hashes)
    elif hash_path.exists():
        hash_path.unlink()
    with (args.output / 'ticks.csv').open('w', newline='') as f:
        writer = csv.writer(f, lineterminator='\n')
        writer.writerow(HEADER)
        writer.writerows(rows)
    summary = {'reference': 'original native C; independent of WASM and Metal lowering',
               'ticks': args.ticks, 'scripted': args.scripted,
               'seconds': time.perf_counter() - start,
               'initial_state': initial, 'initial_state_hash': state_hash(initial),
               'final_state': after_render, 'state_hash': state_hash(after_render),
               'state_bytes_sha256': sha256(struct.pack('<16i', *after_render)),
               'frame_sha256': sha256(frame),
               'trace_sha256': sha256((args.output / 'ticks.csv').read_bytes()),
               'wad_sha256': sha256(wad), 'width': 320, 'height': 200,
               'frame_bytes': len(frame)}
    (args.output / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    print(json.dumps(summary))

if __name__ == '__main__':
    main()
