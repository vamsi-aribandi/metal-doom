#!/usr/bin/env python3
"""Compare rendered frames and diagnostics across original C and GPU rendering.

Exercises low detail, viewport changes, automap, menus, pause, palette gamma,
and normal movement/firing. The event file is shared by both implementations.
Requires the normal build and engine/build_reference.py; no assets are bundled.
"""
import argparse
import csv
import ctypes
import hashlib
import json
from pathlib import Path
import subprocess

from reference import FIELDS

ROOT = Path(__file__).resolve().parent.parent
TICKS = 420
HEADER = ['tick', 'digest', *FIELDS]
RENDER_GLOBALS = ('detailshift', 'scaledviewwidth', 'viewheight',
                  'automapactive', 'menuactive', 'paused', 'usegamma')


def schedule():
    events = []
    def hold(key, first, end):
        events.extend(((first, 1, key), (end, 0, key)))
    def tap(key, tick):
        hold(key, tick, tick + 1)
    hold(0xad, 1, 60)       # Up: movement.
    hold(0xae, 65, 105)     # Right: turn.
    hold(0xa3, 110, 135)    # Fire.
    tap(0xbf, 140)         # F5: low detail.
    tap(0xbf, 180)         # F5: high detail again.
    for tick in (190, 194, 198, 202):
        tap(ord('-'), tick)
    for tick in (220, 224, 228, 232):
        tap(ord('='), tick)
    tap(9, 240)            # Automap on/off.
    tap(9, 270)
    tap(27, 280)           # Original menu on/off; bypass app's Esc-to-quit mapping.
    tap(27, 300)
    tap(255, 310)          # Pause/resume.
    tap(255, 335)
    hold(0xad, 350, 380)
    tap(ord('='), 355)     # Full-screen view, then restore status bar.
    tap(ord('-'), 375)
    hold(0xa3, 385, 410)
    tap(0xd7, 390)         # F11: palette gamma.
    return sorted(events, key=lambda event: event[0])


def cpu_reference(wad_path, library, events, output, ticks_per_frame=1):
    # This library is original C, independently built without the draw hooks.
    engine = ctypes.CDLL(str(library.resolve()))
    engine.engine_alloc.argtypes = [ctypes.c_uint32]
    engine.engine_alloc.restype = ctypes.c_void_p
    engine.engine_init.argtypes = [ctypes.c_size_t, ctypes.c_uint32]
    engine.engine_init.restype = ctypes.c_int
    engine.engine_key.argtypes = [ctypes.c_uint32, ctypes.c_uint32]
    engine.engine_diagnostics.restype = ctypes.POINTER(ctypes.c_int32)
    engine.engine_framebuffer.restype = ctypes.c_void_p
    engine.engine_log.restype = ctypes.c_char_p
    wad = wad_path.read_bytes()
    pointer = engine.engine_alloc(len(wad))
    if not pointer:
        raise RuntimeError('IWAD allocation failed')
    ctypes.memmove(pointer, wad, len(wad))
    if engine.engine_init(pointer, len(wad)):
        raise RuntimeError(f'CPU initialization failed: {engine.engine_log()!r}')
    globals_by_tick, states_by_tick, rows = {}, {}, []
    def capture(tick, rendered):
        state = list(engine.engine_diagnostics()[:16])
        if state[0] != tick + 1 or state[15]:
            raise AssertionError(f'Invalid CPU tick progression at {tick}: {state}')
        states_by_tick[tick] = state
        if rendered:
            pixels = ctypes.string_at(engine.engine_framebuffer(), 320 * 200 * 4)
            rows.append([str(tick), hashlib.sha256(pixels).hexdigest(), *map(str, state)])
        globals_by_tick[tick] = {
            name: ctypes.c_int.in_dll(engine, name).value for name in RENDER_GLOBALS}
    capture(0, True)
    next_event = 0
    for tick in range(1, TICKS + 1):
        while next_event < len(events) and events[next_event][0] == tick:
            _, pressed, key = events[next_event]
            engine.engine_key(pressed, key)
            next_event += 1
        engine.engine_tick()
        rendered = tick % ticks_per_frame == 0 or tick == TICKS
        if rendered:
            engine.engine_render()
        capture(tick, rendered)
    with output.open('w', newline='') as stream:
        writer = csv.writer(stream, lineterminator='\n')
        writer.writerow(HEADER)
        writer.writerows(rows)

    # Prove that events actually exercised these code paths, rather than merely
    # comparing two engines that ignored the same key input. Firing input is
    # recorded separately; its visible ammo decrement is checked in native UI.
    # Some view globals update during rendering. Inspect the first rendered
    # state at or after each checkpoint, within the same stable input interval.
    # Retain globals and diagnostics for every tick even in catch-up batches.
    def at(tick):
        rendered_tick = min(TICKS, ((tick + ticks_per_frame - 1) // ticks_per_frame)
                            * ticks_per_frame)
        return globals_by_tick[rendered_tick]
    checks = {
        'low_detail': at(150)['detailshift'] == 1 and at(185)['detailshift'] == 0,
        'reduced_viewport': at(210)['scaledviewwidth'] < at(0)['scaledviewwidth'],
        'restored_viewport': at(235)['scaledviewwidth'] == at(0)['scaledviewwidth'],
        'automap': at(250)['automapactive'] == 1 and at(275)['automapactive'] == 0,
        'menu': at(285)['menuactive'] == 1 and at(305)['menuactive'] == 0,
        'pause': at(320)['paused'] != 0 and at(340)['paused'] == 0,
        'full_screen': at(360)['viewheight'] == 200 and at(380)['viewheight'] < 200,
        'gamma': at(395)['usegamma'] != at(0)['usegamma'],
        'movement': states_by_tick[50][4:6] != states_by_tick[0][4:6],
        'turning': states_by_tick[100][7] != states_by_tick[60][7],
        'firing_input': any(pressed and key == 0xa3 for _, pressed, key in events),
    }
    if not all(checks.values()):
        raise AssertionError(f'Input schedule did not cover requested states: {checks}')
    return rows, checks


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--wad', required=True, type=Path)
    parser.add_argument('--library', type=Path, default=ROOT / 'build/libdoom_reference.dylib')
    parser.add_argument('--shader', type=Path, default=ROOT / 'build/doom.metal')
    parser.add_argument('--initial-memory', type=Path, default=ROOT / 'build/initial-memory.bin')
    parser.add_argument('--output', type=Path, default=ROOT / 'build/tests/render-states')
    parser.add_argument('--cpu-only', action='store_true')
    parser.add_argument('--serial-render', action='store_true')
    parser.add_argument('--ticks-per-frame', type=int, choices=range(1, 9), default=1,
                        help='Ticks per submitted frame; exercises catch-up batching')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    events = schedule()
    event_path = args.output / 'events.txt'
    event_path.write_text(''.join(f'{tick} {pressed} {key}\n' for tick, pressed, key in events))
    expected, coverage = cpu_reference(args.wad, args.library, events, args.output / 'cpu.csv',
                                       args.ticks_per_frame)
    submission_stats = None
    if not args.cpu_only:
        binary = ROOT / 'build/render-states'
        subprocess.run(['xcrun', 'clang++', '-O2', '-std=c++17', '-fobjc-arc',
                        '-framework', 'Cocoa', '-framework', 'Metal', '-framework', 'QuartzCore',
                        str(ROOT / 'tests/render_states.mm'), '-o', str(binary)], check=True)
        command = [str(binary), str(args.wad.resolve()), str(args.shader.resolve()),
                   str(args.initial_memory.resolve()), str(event_path.resolve()), str(TICKS),
                   str((args.output / 'gpu.csv').resolve()), '--ticks-per-frame',
                   str(args.ticks_per_frame)]
        if args.serial_render:
            command.append('--serial-render')
        subprocess.run(command, check=True)
        submission_stats = json.loads((args.output / 'gpu.stats.json').read_text())
        batches = len(expected) - 1
        expected_stats = {'initial_submissions': 3, 'initial_dispatches': 3,
                          'submissions': 3 + batches,
                          'dispatches': 3 + len(events) + TICKS + batches,
                          'rendered_batches': batches, 'ticks_per_frame': args.ticks_per_frame}
        if submission_stats != expected_stats:
            raise AssertionError(f'Unexpected GPU submission counts: '
                                 f'{submission_stats} vs {expected_stats}')
        with (args.output / 'gpu.csv').open(newline='') as stream:
            reader = csv.reader(stream)
            if next(reader) != HEADER:
                raise AssertionError('Unexpected GPU result header')
            actual = list(reader)
        if len(actual) != len(expected):
            raise AssertionError(f'Frame count mismatch: {len(expected)} vs {len(actual)}')
        for want, got in zip(expected, actual):
            if want != got:
                differences = {name: {'cpu': a, 'gpu': b}
                               for name, a, b in zip(HEADER, want, got) if a != b}
                raise AssertionError(f'First divergence at tick {want[0]}: {differences}')
    report = {'passed': True, 'cpu_only': args.cpu_only, 'ticks': TICKS,
              'ticks_per_frame': args.ticks_per_frame,
              'frames_compared': 0 if args.cpu_only else len(expected),
              'diagnostic_values_compared': 0 if args.cpu_only else len(expected) * 16,
              'submission_stats': submission_stats,
              'coverage_sampling': 'first rendered tick at or after each checkpoint',
              'coverage': coverage, 'final_frame_sha256': expected[-1][1],
              'wad_sha256': hashlib.sha256(args.wad.read_bytes()).hexdigest()}
    (args.output / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
