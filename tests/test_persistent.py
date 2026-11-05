#!/usr/bin/env python3
"""Compare a single persistent GPU dispatch with original Doom C.

Replays the render-state schedule with one and eight ticks per rendered frame.
Each frame's pixels and all sixteen diagnostics must match the CPU reference.
The native fixture also verifies live publication before dispatch completion.
"""
import argparse
import csv
import ctypes
import hashlib
import json
import math
from pathlib import Path
import subprocess
import sys

from test_render_states import HEADER, ROOT, TICKS, cpu_reference, schedule

LIVE_HEADER = ['tick', 'keys', *HEADER[1:]]
KEY_CODES = (0xad, 0xaf, 0xac, 0xae, 0xa0, 0xa1, 0xa3, 0xa2, 13, 0xb6,
             49, 50, 51, 52, 53, 54, 55, 9, 27)


def live_seconds(value):
    seconds = float(value)
    if not math.isfinite(seconds) or not 1 <= seconds <= 600:
        raise argparse.ArgumentTypeError('live duration must be between 1 and 600 seconds')
    return seconds


def run(command, output, timeout):
    """Save subprocess evidence, including when a command fails or times out."""
    with output.with_suffix('.stdout.txt').open('w') as stdout, \
            output.with_suffix('.stderr.txt').open('w') as stderr:
        subprocess.run(command, check=True, timeout=timeout, stdout=stdout, stderr=stderr)


def arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--wad', required=True, type=Path)
    parser.add_argument('--library', type=Path, default=ROOT / 'build/libdoom_reference.dylib')
    parser.add_argument('--shader', type=Path, default=ROOT / 'build/doom-persistent.metal')
    parser.add_argument('--initial-memory', type=Path, default=ROOT / 'build/initial-memory.bin')
    parser.add_argument('--output', type=Path, default=ROOT / 'build/persistent-tests')
    parser.add_argument('--live-seconds', type=live_seconds, default=5,
                        help='Realtime keyboard, pacing and stop test duration (1..600; default: 5)')
    cpu = parser.add_mutually_exclusive_group()
    cpu.add_argument('--cpu-case', type=int, choices=(1, 8), help=argparse.SUPPRESS)
    cpu.add_argument('--cpu-live', type=Path, help=argparse.SUPPRESS)
    return parser.parse_args()


def cpu_case(args):
    events = schedule()
    (args.output / 'events.txt').write_text(
        ''.join(f'{tick} {pressed} {key}\n' for tick, pressed, key in events))
    rows, coverage = cpu_reference(args.wad, args.library, events, args.output / 'cpu.csv',
                                   args.cpu_case)
    (args.output / 'cpu.json').write_text(json.dumps({
        'coverage': coverage, 'frames': len(rows), 'final_frame_sha256': rows[-1][1],
    }, indent=2) + '\n')


def read_rows(path, header=HEADER):
    with path.open(newline='') as stream:
        reader = csv.reader(stream)
        if next(reader, None) != header:
            raise AssertionError(f'Unexpected CSV header: {path}')
        rows = list(reader)
    if any(len(row) != len(header) for row in rows):
        raise AssertionError(f'Incomplete CSV row: {path}')
    return rows


def cpu_live(args):
    """Replay the GPU's captured realtime tick/render/input schedule in original C."""
    rows = read_rows(args.cpu_live, LIVE_HEADER)
    if len(rows) < 2:
        raise AssertionError('Live schedule must include initialization and subsequent frames')
    engine = ctypes.CDLL(str(args.library.resolve()))
    engine.engine_alloc.argtypes = [ctypes.c_uint32]
    engine.engine_alloc.restype = ctypes.c_void_p
    engine.engine_init.argtypes = [ctypes.c_size_t, ctypes.c_uint32]
    engine.engine_init.restype = ctypes.c_int
    engine.engine_key.argtypes = [ctypes.c_uint32, ctypes.c_uint32]
    engine.engine_tick.argtypes = []
    engine.engine_render.argtypes = []
    engine.engine_diagnostics.argtypes = []
    engine.engine_diagnostics.restype = ctypes.POINTER(ctypes.c_int32)
    engine.engine_framebuffer.argtypes = []
    engine.engine_framebuffer.restype = ctypes.c_void_p
    engine.engine_log.restype = ctypes.c_char_p
    wad = args.wad.read_bytes()
    pointer = engine.engine_alloc(len(wad))
    if not pointer:
        raise RuntimeError('CPU live-reference IWAD allocation failed')
    ctypes.memmove(pointer, wad, len(wad))
    if engine.engine_init(pointer, len(wad)):
        raise RuntimeError(f'CPU initialization failed: {engine.engine_log()!r}')
    previous_tick = previous_keys = maximum_gap = observed_keys = 0
    expected = []
    for index, row in enumerate(rows):
        tick, keys = map(int, row[:2])
        if keys < 0 or keys >= 1 << len(KEY_CODES):
            raise AssertionError(f'Unknown key mask at tick {tick}: {keys}')
        if index == 0:
            if tick or keys:
                raise AssertionError(f'Live initial frame must have tick=0 and keys=0: {row}')
        else:
            gap = tick - previous_tick
            if not 1 <= gap <= 8:
                raise AssertionError(f'Invalid live tick progression: {previous_tick} -> {tick}')
            maximum_gap = max(maximum_gap, gap)
            for bit, code in enumerate(KEY_CODES):
                if (keys ^ previous_keys) & (1 << bit):
                    engine.engine_key((keys >> bit) & 1, code)
            for _ in range(gap):
                engine.engine_tick()
            # Exactly one render per published frame, including catch-up batches.
            engine.engine_render()
        state = list(engine.engine_diagnostics()[:16])
        if state[0] != tick + 1 or state[15]:
            raise AssertionError(f'Invalid CPU live-reference state at tick {tick}: {state}')
        pixels = ctypes.string_at(engine.engine_framebuffer(), 320 * 200 * 4)
        want = [str(tick), str(keys), hashlib.sha256(pixels).hexdigest(), *map(str, state)]
        if want != row:
            differences = {name: {'cpu': a, 'gpu': b}
                           for name, a, b in zip(LIVE_HEADER, want, row) if a != b}
            raise AssertionError(f'First realtime divergence at tick {tick}: {differences}')
        expected.append(want)
        previous_tick, previous_keys = tick, keys
        observed_keys |= keys
    with (args.output / 'cpu.csv').open('w', newline='') as stream:
        writer = csv.writer(stream, lineterminator='\n')
        writer.writerow(LIVE_HEADER)
        writer.writerows(expected)
    report = {'passed': True, 'frames_compared': len(rows),
              'diagnostic_values_compared': len(rows) * 16, 'ticks': previous_tick,
              'max_ticks_per_frame': maximum_gap, 'observed_key_mask': observed_keys,
              'final_frame_sha256': expected[-1][2]}
    (args.output / 'cpu.json').write_text(json.dumps(report, indent=2) + '\n')


def persistence_stats(path, frames):
    lines = [line for line in path.read_text().splitlines() if line.startswith('PASS ')]
    if len(lines) != 1:
        raise AssertionError(f'Missing unique native fixture PASS result: {path}')
    stats = dict(word.split('=', 1) for word in lines[0].split()[1:])
    required = {'dispatches': 1, 'submissions': 1, 'frames': frames,
                'live_frames': frames, 'ticks': TICKS, 'status': 4, 'reason': 2}
    for name, expected in required.items():
        if name not in stats or int(stats[name]) != expected:
            raise AssertionError(f'Persistent invariant {name}: {stats.get(name)} != {expected}')
    parsed = {}
    for name, value in stats.items():
        try:
            parsed[name] = int(value)
        except ValueError:
            parsed[name] = float(value)
    return parsed


def main():
    args = arguments()
    args.output.mkdir(parents=True, exist_ok=True)
    if args.cpu_case:
        cpu_case(args)
        return
    if args.cpu_live:
        cpu_live(args)
        return
    (args.output / 'report.json').unlink(missing_ok=True)
    for path in (args.wad, args.library, args.shader, args.initial_memory):
        if not path.is_file():
            raise FileNotFoundError(f'Missing required input: {path}')
    # Compile a test-only host that includes the same PersistentEngine as the app.
    binary = args.output / 'persistent-fixture'
    run(['xcrun', 'clang++', '-O2', '-std=c++17', '-fobjc-arc',
         '-framework', 'Cocoa', '-framework', 'Metal', '-framework', 'QuartzCore',
         str(ROOT / 'tests/persistent.mm'), '-o', str(binary.resolve())],
        args.output / 'compile', timeout=180)
    live_binary = args.output / 'persistent-live-fixture'
    run(['xcrun', 'clang++', '-O2', '-std=c++17', '-fobjc-arc',
         '-framework', 'Cocoa', '-framework', 'Metal', '-framework', 'QuartzCore',
         str(ROOT / 'tests/persistent_live.mm'), '-o', str(live_binary.resolve())],
        args.output / 'compile-live', timeout=180)
    cases = []
    for per_frame in (1, 8):
        output = args.output / f'frame-{per_frame}'
        output.mkdir(exist_ok=True)
        # Original Doom holds global state, so each reference needs a fresh process.
        run([sys.executable, str(Path(__file__).resolve()), '--cpu-case', str(per_frame),
             '--wad', str(args.wad.resolve()), '--library', str(args.library.resolve()),
             '--output', str(output.resolve())], output / 'reference', timeout=120)
        expected = read_rows(output / 'cpu.csv')
        run([str(binary.resolve()), str(args.wad.resolve()), str(args.shader.resolve()),
             str(args.initial_memory.resolve()), str((output / 'events.txt').resolve()),
             str(TICKS), str((output / 'gpu.csv').resolve()), str(per_frame)],
            output / 'fixture', timeout=180)
        actual = read_rows(output / 'gpu.csv')
        if len(actual) != len(expected):
            raise AssertionError(f'{per_frame} ticks/frame: frame count '
                                 f'{len(actual)} != {len(expected)}')
        for want, got in zip(expected, actual):
            if want != got:
                differences = {name: {'cpu': a, 'gpu': b}
                               for name, a, b in zip(HEADER, want, got) if a != b}
                raise AssertionError(f'{per_frame} ticks/frame: first divergence '
                                     f'at tick {want[0]}: {differences}')
        stats = persistence_stats(output / 'fixture.stdout.txt', len(expected))
        reference = json.loads((output / 'cpu.json').read_text())
        cases.append({'ticks_per_frame': per_frame, 'ticks': TICKS,
                      'frames_compared': len(expected),
                      'diagnostic_values_compared': len(expected) * 16,
                      'coverage': reference['coverage'], 'persistence': stats,
                      'final_frame_sha256': expected[-1][1]})
    output = args.output / 'live'
    output.mkdir(exist_ok=True)
    (output / 'gpu.json').unlink(missing_ok=True)
    run([str(live_binary.resolve()), str(args.wad.resolve()), str(args.shader.resolve()),
         str(args.initial_memory.resolve()), str(args.live_seconds),
         str((output / 'gpu.csv').resolve()), str((output / 'gpu.json').resolve())],
        output / 'fixture', timeout=args.live_seconds + 180)
    run([sys.executable, str(Path(__file__).resolve()),
         '--cpu-live', str((output / 'gpu.csv').resolve()), '--wad', str(args.wad.resolve()),
         '--library', str(args.library.resolve()), '--output', str(output.resolve())],
        output / 'reference', timeout=120)
    live = json.loads((output / 'gpu.json').read_text())
    reference = json.loads((output / 'cpu.json').read_text())
    for name, value in {'passed': True, 'dispatches': 1, 'submissions': 1,
                        'status': 4, 'stop_while_unacknowledged': True,
                        'frames': reference['frames_compared'], 'ticks': reference['ticks'],
                        'max_ticks_per_frame': reference['max_ticks_per_frame'],
                        'observed_key_mask': reference['observed_key_mask']}.items():
        if live.get(name) != value:
            raise AssertionError(f'Live fixture invariant {name}: {live.get(name)} != {value}')
    report = {'passed': True, 'cases': cases, 'live': {'gpu': live, 'cpu_reference': reference},
              'frames_compared': sum(case['frames_compared'] for case in cases)
                                 + reference['frames_compared'],
              'diagnostic_values_compared': sum(case['diagnostic_values_compared'] for case in cases)
                                           + reference['diagnostic_values_compared'],
              'wad_sha256': hashlib.sha256(args.wad.read_bytes()).hexdigest()}
    (args.output / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
