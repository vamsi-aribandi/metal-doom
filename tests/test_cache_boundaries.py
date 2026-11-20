#!/usr/bin/env python3
"""Replay the long schedule that exposed reads into zone allocator headers.

Needs the user-supplied shareware Doom1 IWAD. Compares native original C (with
identical deterministic cache padding) to serial and cooperative Metal renderers.
No game pixels or assets are stored in this repository.
"""
import argparse
import json
from pathlib import Path
import subprocess
import sys

from test_persistent import ROOT


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--wad', required=True, type=Path)
    parser.add_argument('--output', type=Path, default=ROOT / 'build/cache-boundary-tests')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    binary = args.output.resolve() / 'fixture'
    subprocess.run(['xcrun', 'clang++', '-O2', '-std=c++17', '-fobjc-arc', '-framework',
                    'Cocoa', '-framework', 'Metal', '-framework', 'QuartzCore',
                    str(ROOT / 'tests/persistent_schedule.mm'), '-o', str(binary)], check=True)
    reports = []
    for serial in (0, 1):
        output = args.output / f'serial-{serial}'
        output.mkdir(exist_ok=True)
        csv = output.resolve() / 'gpu.csv'
        subprocess.run([str(binary), str(args.wad.resolve()), str(ROOT / 'build/doom-persistent.metal'),
                        str(ROOT / 'build/initial-memory.bin'), str(ROOT / 'tests/cache-boundary-schedule.txt'),
                        str(csv), str(serial)], check=True, timeout=180)
        # Each reference gets a fresh process because Doom owns global state.
        subprocess.run([sys.executable, str(ROOT / 'tests/test_persistent.py'), '--wad',
                        str(args.wad.resolve()), '--cpu-live', str(csv), '--output',
                        str(output.resolve())], check=True, timeout=120)
        reports.append({'serial': bool(serial), **json.loads((output / 'cpu.json').read_text())})
    (args.output / 'report.json').write_text(json.dumps(reports, indent=2) + '\n')
    print(json.dumps(reports, indent=2))


if __name__ == '__main__':
    main()
