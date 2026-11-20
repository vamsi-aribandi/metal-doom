#!/usr/bin/env python3
"""Compile original Doom C for ARM64 CPU, independently of WASM/Metal lowering."""
from pathlib import Path
from renderer_compat import patch as patch_renderer_cache
import os
import re
import shutil
import subprocess

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / 'build'
SRC = BUILD / 'reference-src'
UPSTREAM = ROOT / 'vendor/doomgeneric/doomgeneric'
LIB = ROOT / 'vendor/tinylibc'

def main():
    BUILD.mkdir(exist_ok=True)
    shutil.copytree(UPSTREAM, SRC, dirs_exist_ok=True)
    patch_renderer_cache(SRC)
    p = SRC / 'd_main.c'
    s = p.read_text()
    start = s.index('void D_DoomLoop (void)\n{')
    end = s.index('\n//', start)
    body = s[start:end]
    assert body.count('    TryRunTics();') == 1
    assert body.count('    doomgeneric_Tick();') == 1
    body = body.replace('    TryRunTics();', '    /* Adapter owns deterministic simulation ticks. */')
    body = body.replace('    doomgeneric_Tick();', '    /* Adapter owns rendering. */')
    p.write_text(s[:start] + body + s[end:])
    manifest = re.search(r'^SRC_DOOM = (.*)$', (UPSTREAM / 'Makefile').read_text(), re.M).group(1)
    names = [name[:-2] for name in manifest.split() if name != 'doomgeneric_xlib.o']
    clang = os.environ.get('NATIVE_CLANG', shutil.which('clang') or 'clang')
    resource = subprocess.check_output([clang, '-print-resource-dir'], text=True).strip()
    flags = ['-std=gnu11', '-O2', '-shared', '-fPIC', '-ffreestanding', '-fno-builtin', '-fwrapv', '-w', '-nostdinc', '-isystem', str(LIB), '-isystem', resource + '/include', '-I', str(SRC), '-DNORMALUNIX', '-DLINUX', '-D_DEFAULT_SOURCE', '-DDOOMGENERIC_RESX=320', '-DDOOMGENERIC_RESY=200']
    output = BUILD / 'libdoom_reference.dylib'
    subprocess.run([clang, *flags, *[str(SRC / (name + '.c')) for name in names], str(ROOT / 'engine/platform.c'), str(LIB / 'tinylibc.c'), '-o', str(output)], check=True)
    print(output)

if __name__ == '__main__':
    main()
