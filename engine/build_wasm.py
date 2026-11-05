#!/usr/bin/env python3
"""Build pinned Doom C as one freestanding, import-free wasm module.

Wasm is an intermediate portable C representation, translated to native Metal.
Only generated copies in build/ are patched. No gameplay source is rewritten.
"""
from pathlib import Path
import concurrent.futures
import os
import shutil
import subprocess
from parallel_draw import patch as patch_parallel_draw

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / 'build'
SRC = BUILD / 'doom-src'
LIB = ROOT / 'vendor/tinylibc'
UPSTREAM = ROOT / 'vendor/doomgeneric/doomgeneric'
FILES = '''dummy am_map doomdef doomstat dstrings d_event d_items d_iwad d_loop d_main d_mode d_net f_finale f_wipe g_game hu_lib hu_stuff info i_cdmus i_endoom i_joystick i_scale i_sound i_system i_timer memio m_argv m_bbox m_cheat m_config m_controls m_fixed m_menu m_misc m_random p_ceilng p_doors p_enemy p_floor p_inter p_lights p_map p_maputl p_mobj p_plats p_pspr p_saveg p_setup p_sight p_spec p_switch p_telept p_tick p_user r_bsp r_data r_draw r_main r_plane r_segs r_sky r_things sha1 sounds statdump st_lib st_stuff s_sound tables v_video wi_stuff w_checksum w_file w_main w_wad z_zone w_file_stdc i_input i_video doomgeneric'''.split()
EXPORTS = 'engine_alloc engine_init engine_key engine_tick engine_render engine_profile_world engine_index_buffer engine_palette engine_framebuffer engine_diagnostics engine_log engine_log_size engine_draw_queue engine_render_begin engine_render_end engine_world_begin engine_world_end'.split()
BUILD.mkdir(exist_ok=True)
shutil.copytree(UPSTREAM, SRC, dirs_exist_ok=True)
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
video = SRC / 'i_video.c'
video_source = video.read_text()
original_update = 'void I_FinishUpdate (void)'
assert video_source.count(original_update) == 1
video.write_text(video_source.replace(original_update, 'void I_FinishUpdate_Reference (void)') + '\n' + (ROOT / 'engine/video_exports.c').read_text())
patch_parallel_draw(SRC)
clang = os.environ.get('CLANG', ('/opt/homebrew/opt/llvm/bin/clang' if Path('/opt/homebrew/opt/llvm/bin/clang').exists() else shutil.which('clang') or 'clang'))
ld = os.environ.get('WASM_LD', shutil.which('wasm-ld') or '/opt/homebrew/opt/lld/bin/wasm-ld')
resource = subprocess.check_output([clang, '-print-resource-dir'], text=True).strip()
flags = ['--target=wasm32', '-std=gnu11', '-O2', '-ffreestanding', '-fno-builtin', '-fno-stack-protector', '-fwrapv', '-Wno-incompatible-pointer-types', '-Wno-pointer-sign', '-Wno-unused-value', '-Wno-format', '-Wno-implicit-function-declaration', '-nostdinc', '-isystem', str(LIB), '-isystem', resource + '/include', '-I', str(SRC), '-DNORMALUNIX', '-DLINUX', '-D_DEFAULT_SOURCE', '-DDOOMGENERIC_RESX=320', '-DDOOMGENERIC_RESY=200']
flush_limit = int(os.environ.get('DOOM_DRAW_FLUSH_LIMIT', '4096'))
if not 1 <= flush_limit <= 4096:
    raise ValueError('DOOM_DRAW_FLUSH_LIMIT must be between 1 and 4096')
flags.append('-DDOOM_DRAW_FLUSH_LIMIT=' + str(flush_limit))
sources = [SRC / (f + '.c') for f in FILES] + [LIB / 'tinylibc.c', ROOT / 'engine/platform.c', ROOT / 'engine/parallel_draw.c']
def compile_one(source):
    obj = BUILD / (source.stem + '.o')
    result = subprocess.run([clang, *flags, '-c', str(source), '-o', str(obj)], capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(str(source) + '\n' + result.stderr)
    return obj
with concurrent.futures.ThreadPoolExecutor(max_workers=min(8, os.cpu_count() or 1)) as pool:
    objects = list(pool.map(compile_one, sources))
cmd = [ld, '--no-entry', '--stack-first', '-z', 'stack-size=1048576', '--initial-memory=100663296', '--max-memory=100663296', '--export-memory', '--gc-sections', *['--export=' + e for e in EXPORTS], *map(str, objects), '-o', str(BUILD / 'doom.wasm')]
subprocess.run(cmd, check=True)
subprocess.run(['wasm2c', '-n', 'doom', str(BUILD / 'doom.wasm'), '-o', str(BUILD / 'doom.c')], check=True)
print(BUILD / 'doom.wasm')
