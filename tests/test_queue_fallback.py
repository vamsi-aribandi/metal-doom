#!/usr/bin/env python3
"""Build a tiny flush threshold in isolation and compare every rendered frame.

The queue ABI remains unchanged. A threshold of 16 forces serial overflow flushes
throughout ordinary opaque rendering, including column-to-span transitions.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from compare import compare

ROOT = Path(__file__).resolve().parent.parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--wad', required=True, type=Path)
    parser.add_argument('--output', type=Path, default=ROOT / 'build/queue-fallback')
    args = parser.parse_args()
    wad, output = args.wad.resolve(), args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    # Separate source/build paths avoid replacing the normal app or shader.
    with tempfile.TemporaryDirectory(prefix='queue-fallback-', dir=ROOT / 'build') as temp:
        checkout = Path(temp) / 'metal-doom'
        shutil.copytree(ROOT, checkout, ignore=shutil.ignore_patterns('.git', 'build', '__pycache__'))
        env = dict(os.environ, DOOM_DRAW_FLUSH_LIMIT='16')
        with (output / 'build.log').open('w') as log:
            subprocess.run(['./build.sh'], cwd=checkout, env=env, stdout=log, stderr=subprocess.STDOUT, check=True)
        with (output / 'gpu.log').open('w') as log:
            subprocess.run([str(checkout / 'build/metal-doom'), '--wad', str(wad),
                '--headless', '--ticks', '35', '--scripted', '--render-every-tick',
                '--frame-hashes', '--output', str(output / 'gpu')], cwd=checkout,
                stdout=log, stderr=subprocess.STDOUT, check=True)
    with (output / 'cpu.log').open('w') as log:
        subprocess.run(['python3', str(ROOT / 'tests/reference.py'), '--wad', str(wad),
            '--ticks', '35', '--scripted', '--render-every-tick', '--frame-hashes',
            '--output', str(output / 'cpu')], cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, check=True)
    report = {'flush_limit': 16, **compare(output / 'cpu', output / 'gpu')}
    (output / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
