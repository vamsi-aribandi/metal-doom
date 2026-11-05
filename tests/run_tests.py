#!/usr/bin/env python3
"""Test initialization, deterministic simulation, and exact GPU/CPU equivalence."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from compare import compare

ROOT = Path(__file__).resolve().parent.parent

def run(command):
    result = subprocess.run([str(item) for item in command], cwd=ROOT, capture_output=True, text=True)
    if result.returncode:
        sys.stderr.write(result.stdout + result.stderr)
        raise subprocess.CalledProcessError(result.returncode, command)

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--wad', required=True, type=Path)
    parser.add_argument('--binary', type=Path, default=ROOT / 'build/metal-doom')
    parser.add_argument('--output', type=Path, default=ROOT / 'build/tests')
    parser.add_argument('--cpu-only', action='store_true', help='Validate independent native reference and deterministic replay without invoking Metal')
    parser.add_argument('--skip-build-reference', action='store_true')
    args = parser.parse_args()
    args.wad = args.wad.resolve()
    args.binary = args.binary.resolve()
    args.output = args.output.resolve()
    if not args.skip_build_reference:
        run([sys.executable, ROOT / 'engine/build_reference.py'])
    wad_sha = hashlib.sha256(args.wad.read_bytes()).hexdigest()
    golden_path = ROOT / 'tests/golden-shareware.json'
    golden = json.loads(golden_path.read_text()) if golden_path.exists() else {}
    results = []
    cases = [('init', 0, False), ('idle', 35, False), ('scripted', 350, True), ('rendered', 350, True)]
    for name, ticks, scripted in cases:
        reference = args.output / (name + '-cpu')
        repeat = args.output / (name + '-cpu-repeat')
        for output in (reference, repeat):
            command = [sys.executable, ROOT / 'tests/reference.py', '--wad', args.wad, '--ticks', ticks, '--output', output]
            if scripted:
                command.append('--scripted')
            if name == 'rendered':
                command.extend(['--render-every-tick', '--frame-hashes'])
            run(command)
        replay = compare(reference, repeat)
        summary = json.loads((reference / 'summary.json').read_text())
        if golden.get('wad_sha256') == wad_sha and name in golden.get('cases', {}):
            for field, value in golden['cases'][name].items():
                if summary[field] != value:
                    raise AssertionError(f'{name}: original reference differs from known shareware golden {field}: {summary[field]!r} != {value!r}')
        result = {'case': name, 'cpu_deterministic_replay': replay}
        if not args.cpu_only:
            gpu = args.output / (name + '-gpu')
            command = [args.binary, '--wad', args.wad, '--headless', '--ticks', ticks, '--output', gpu]
            if scripted:
                command.append('--scripted')
            if name == 'rendered':
                command.extend(['--render-every-tick', '--frame-hashes'])
            run(command)
            result['gpu_vs_original_c'] = compare(reference, gpu)
            if name == 'scripted':
                gpu_repeat = args.output / (name + '-gpu-repeat')
                repeat_command = [args.binary, '--wad', args.wad, '--headless', '--ticks', ticks, '--output', gpu_repeat, '--scripted']
                run(repeat_command)
                result['gpu_deterministic_replay'] = compare(gpu, gpu_repeat)
        results.append(result)
    report = {'passed': True, 'cpu_only': args.cpu_only, 'wad_sha256': wad_sha, 'cases': results}
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))

if __name__ == '__main__':
    main()
