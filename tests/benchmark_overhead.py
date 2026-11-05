#!/usr/bin/env python3
"""Sequential A/B measurements of batching and queue-helper inlining.

Uses the same compiled host, initial memory, gameplay input and tick count.
Warmups compile both shader variants outside all timed loops. No assets copied.
"""
import argparse
import hashlib
import json
from pathlib import Path
import statistics
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'tools'))
from to_metal import read_functions, read_function_table, engine_source


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--wad', required=True, type=Path)
    parser.add_argument('--output', type=Path, default=ROOT / 'build/overhead-benchmark')
    parser.add_argument('--repeats', type=int, default=3)
    parser.add_argument('--ticks', type=int, default=350)
    args = parser.parse_args()
    if args.repeats < 1 or args.ticks < 1:
        parser.error('repeats and ticks must be positive')
    args.output = args.output.resolve()
    args.output.mkdir(parents=True, exist_ok=True)
    source = (ROOT / 'build/doom.c').read_text()
    functions, table = read_functions(source), read_function_table(source)
    # Keep the unhelpful inlining experiment here, outside the production
    # translator. Only these two directly called custom helpers are changed.
    helpers = ('w2c_doom_doom_queue_column', 'w2c_doom_doom_queue_span')
    assert not set(helpers).intersection(name for _, name in table)
    engine = engine_source(functions, table)
    shaders = {}
    for inline in (False, True):
        variant = engine
        if inline:
            for name in helpers:
                declaration = '[[visible]] void ' + name + '('
                assert variant.count(declaration) == 2
                variant = variant.replace(declaration, 'inline void ' + name + '(')
        path = args.output / ('inline.metal' if inline else 'visible.metal')
        path.write_text(variant + (ROOT / 'app/dispatch.metal').read_text())
        shaders[inline] = path
    variants = {'separate': (False, False), 'batched': (True, False), 'batched_inline': (True, True)}
    runs = {name: [] for name in variants}
    expected = None
    def run(name, tick_count, folder):
        nonlocal expected
        batch, inline = variants[name]
        command = [str(ROOT / 'build/metal-doom'), '--wad', str(args.wad.resolve()),
                   '--shader', str(shaders[inline]), '--initial-memory', str(ROOT / 'build/initial-memory.bin'),
                   '--headless', '--scripted', '--render-every-tick', '--ticks', str(tick_count),
                   '--output', str(folder)]
        if not batch: command.append('--no-batch')
        with folder.with_suffix('.log').open('w') as log:
            subprocess.run(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, check=True)
        summary = json.loads((folder / 'summary.json').read_text())
        if tick_count:
            actual = (summary['state_hash'], digest(folder / 'frame.rgba'), digest(folder / 'ticks.csv'))
            if expected is None: expected = actual
            if actual != expected: raise AssertionError(f'{name}: gameplay/frame divergence')
        return summary
    # Both pipelines, including their initial memory/WAD setup, are warmed first.
    for name in ('separate', 'batched_inline'):
        run(name, 0, args.output / ('warmup-' + name))
    for repeat in range(args.repeats):
        order = list(variants)
        if repeat % 2: order.reverse()
        for name in order:
            result = run(name, args.ticks, args.output / f'{name}-{repeat}')
            runs[name].append(result)
            print(f'{name} [{repeat + 1}]: {result["wall_fps"]:.2f} FPS', flush=True)
    report = {'device': runs['separate'][0]['device'], 'ticks': args.ticks, 'repeats': args.repeats,
              'host_sha256': digest(ROOT / 'build/metal-doom'), 'wad_sha256': digest(args.wad),
              'shader_sha256': {str(k): digest(v) for k, v in shaders.items()},
              'identical_outputs': True, 'variants': {}}
    for name, samples in runs.items():
        report['variants'][name] = {
            'median_wall_fps': statistics.median(s['wall_fps'] for s in samples),
            'median_loop_gpu_ms_per_frame': statistics.median(s['loop_gpu_ms'] / s['frames'] for s in samples),
            'submissions': samples[0]['submissions'], 'dispatches': samples[0]['dispatches'], 'runs': samples}
    (args.output / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
