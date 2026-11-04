#!/usr/bin/env python3
"""Compare all real Doom wasm2c functions before/after CFG lowering on CPU.

Requires build/doom.c (engine/build_wasm.sh), clang, WABT's wasm2c runtime,
and a user-supplied IWAD. This isolates control-flow lowering from Metal runtime
semantics. No game assets are copied into source control.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'tools'))
from lower_cfg import lower_body, _selftest


def body_end(source: str, opening: int) -> int:
    """Find the closing function brace, respecting quoted strings/comments."""
    depth, i = 1, opening + 1
    while i < len(source):
        if source.startswith('//', i):
            end = source.find('\n', i)
            i = len(source) if end < 0 else end + 1
            continue
        if source.startswith('/*', i):
            i = source.index('*/', i + 2) + 2
            continue
        c = source[i]
        if c in "\"'":
            quote = c
            i += 1
            while i < len(source):
                c = source[i]
                i += 1
                if c == '\\':
                    i += 1
                elif c == quote:
                    break
            continue
        if c == '{':
            depth += 1
        elif c == '}':
            depth -= 1
            if not depth:
                return i + 1
        i += 1
    raise ValueError('Unclosed function body')


def portable_body(body: str) -> str:
    """Resolve wasm2c local cache/Segue directives as in tools/to_metal.py.

    Native builds explicitly disable Segue and mmap cache optimization, so every
    conditional generated inside engine functions is false, including nested
    Segue branches. Macros are otherwise left for the normal C preprocessor.
    """
    active, lines = [True], []
    for line in body.splitlines():
        stripped = line.lstrip()
        if stripped.startswith('#if'):
            active.append(False)
        elif stripped.startswith('#else'):
            active[-1] = not active[-1]
        elif stripped.startswith('#endif'):
            active.pop()
        elif stripped.startswith('#'):
            raise ValueError(f'Unexpected function directive: {stripped}')
        elif all(active):
            lines.append(line)
    assert len(active) == 1
    return '\n'.join(lines)


def lower_engine(source: str) -> tuple[str, int, int]:
    pattern = re.compile(r'^(void|u32|u64|f32|f64) (w2c_doom_\w+)\([^\n]*\) \{', re.M)
    chunks, previous, total, changed = [], 0, 0, 0
    for match in pattern.finditer(source):
        end = body_end(source, match.end() - 1)
        body = portable_body(source[match.end():end - 1])
        try:
            result = lower_body(body)
        except Exception as error:
            raise RuntimeError(f'Lowering {match[2]}') from error
        changed += result != body
        total += 1
        chunks += [source[previous:match.end()], '\n', result]
        # CFG exit represents original function fallthrough. All value-returning
        # engine paths return explicitly; make the synthetic impossible exit
        # explicit to the C compiler just as the Metal adapter does.
        if match[1] != 'void' and result != body:
            chunks.append('\n__builtin_unreachable();\n')
        chunks.append('}')
        previous = end
    chunks.append(source[previous:])
    return ''.join(chunks), total, changed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--wad', required=True, type=Path)
    parser.add_argument('--ticks', type=int, default=125)
    parser.add_argument('--output', type=Path, default=ROOT / 'build/lowering-test')
    parser.add_argument('--wabt-prefix', type=Path)
    parser.add_argument('--clang', default=os.environ.get('CLANG', shutil.which('clang') or 'clang'))
    args = parser.parse_args()
    if args.ticks < 0:
        parser.error('--ticks must be nonnegative')
    if not args.wad.is_file():
        parser.error('--wad must point to a supplied IWAD')
    prefix = args.wabt_prefix
    if prefix is None:
        if shutil.which('brew'):
            prefix = Path(subprocess.check_output(['brew', '--prefix', 'wabt'], text=True).strip())
        else:
            parser.error('Supply --wabt-prefix containing include/wasm-rt.h and share/wabt/wasm2c')
    runtime = prefix / 'share/wabt/wasm2c'
    args.output.mkdir(parents=True, exist_ok=True)
    _selftest()
    source = (ROOT / 'build/doom.c').read_text()
    # Snapshot the source so a concurrent rebuild cannot change one side of the
    # comparison after the lowered version has already been generated.
    original_path = args.output / 'doom-original.c'
    original_path.write_text(source)
    lowered, total, changed = lower_engine(source)
    lowered_path = args.output / 'doom-lowered.c'
    lowered_path.write_text(lowered)
    common = ['-std=gnu11', '-O2', '-Wno-return-type', '-DWASM_RT_USE_SEGUE=0',
              '-DWASM_RT_USE_MMAP=0', '-DWASM_RT_MEMCHECK_GUARD_PAGES=0',
              '-DWASM_RT_MEMCHECK_BOUNDS_CHECK=1', '-DWASM_RT_STACK_DEPTH_COUNT=1',
              '-I', str(prefix / 'include'), '-I', str(runtime), '-I', str(ROOT / 'build')]
    summaries = {}
    for name, c_path in [('original', original_path), ('lowered', lowered_path)]:
        executable = args.output / name
        compile_start = time.perf_counter()
        subprocess.run([args.clang, *common, str(c_path), str(ROOT / 'tests/lowering_harness.c'),
                        str(runtime / 'wasm-rt-impl.c'), str(runtime / 'wasm-rt-mem-impl.c'),
                        '-lm', '-o', str(executable)], check=True)
        compile_seconds = time.perf_counter() - compile_start
        folder = args.output / (name + '-results')
        folder.mkdir(exist_ok=True)
        start = time.perf_counter()
        result = subprocess.run([str(executable.resolve()), str(args.wad.resolve()), str(folder.resolve()),
                                 str(args.ticks)], capture_output=True, text=True, timeout=180, check=True)
        summaries[name] = {'compile_seconds': compile_seconds,
                           'run_seconds': time.perf_counter() - start,
                           'stdout': result.stdout.strip()}
    files = {}
    for filename in ('initial.rgba', 'ticks.bin', 'frame.rgba'):
        original = (args.output / 'original-results' / filename).read_bytes()
        converted = (args.output / 'lowered-results' / filename).read_bytes()
        if original != converted:
            first = next((i for i, (a, b) in enumerate(zip(original, converted)) if a != b),
                         min(len(original), len(converted)))
            raise AssertionError(f'{filename}: mismatch at byte {first}; lengths {len(original)} vs {len(converted)}')
        files[filename] = {'bytes': len(original), 'sha256': hashlib.sha256(original).hexdigest()}
    trace = (args.output / 'lowered-results/ticks.bin').read_bytes()
    summary = {'passed': True, 'functions': total, 'lowered_functions': changed, 'ticks': args.ticks,
               'source_sha256': hashlib.sha256(source.encode()).hexdigest(),
               'wad_sha256': hashlib.sha256(args.wad.read_bytes()).hexdigest(),
               'final_state': list(struct.unpack('<16i', trace[-64:])),
               'comparison': 'byte-for-byte original wasm2c vs CFG-lowered wasm2c using identical runtime',
               'files': files, 'timing': summaries}
    (args.output / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    main()
