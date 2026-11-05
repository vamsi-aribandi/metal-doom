#!/usr/bin/env python3
"""Measure ordered dispatch vs persistent replay after checking exact outputs."""
import argparse
import hashlib
import json
from pathlib import Path
import statistics
import subprocess

ROOT = Path(__file__).resolve().parent.parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--wad', required=True, type=Path)
    parser.add_argument('--repeats', type=int, default=3)
    parser.add_argument('--output', type=Path, default=ROOT / 'build/persistent-benchmark')
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error('--repeats must be positive')
    args.output.mkdir(parents=True, exist_ok=True)
    results, reference = [], None
    for trial in range(args.repeats + 1):
        for backend in ('dispatch', 'persistent'):
            output = args.output / f'{trial}-{backend}'
            subprocess.run([str(ROOT / 'build/metal-doom'), '--' + backend, '--headless',
                            '--wad', str(args.wad.resolve()), '--ticks', '350', '--scripted',
                            '--render-every-tick', '--output', str(output.resolve())],
                           cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                           check=True, timeout=120)
            hashes = {name: hashlib.sha256((output / name).read_bytes()).hexdigest()
                      for name in ('ticks.csv', 'frame.rgba')}
            if reference is None:
                reference = hashes
            if hashes != reference:
                raise AssertionError(f'Output changed: trial={trial}, backend={backend}')
            summary = json.loads((output / 'summary.json').read_text())
            if trial:
                results.append({'trial': trial, 'backend': backend, 'fps': summary['wall_fps'],
                                'seconds': summary['seconds'], 'dispatches': summary['dispatches'],
                                'submissions': summary['submissions'], 'hashes': hashes})
    report = {'device': summary['device'], 'ticks': 350, 'render_every_tick': True,
              'resolution': [320, 200], 'warmups_per_backend': 1, 'trials': results,
              'median_fps': {name: statistics.median(row['fps'] for row in results
                                                    if row['backend'] == name)
                             for name in ('dispatch', 'persistent')},
              'note': 'Wall throughput includes frame handoff; persistent GPU timestamps '
                      'include initialization and idle. No frame-hash instrumentation during timing.'}
    (args.output / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
