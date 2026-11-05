"""Apply small opaque-renderer hooks to generated GPU build copies only."""
from pathlib import Path
import re


def replace_once(text, before, after):
    assert text.count(before) == 1, before
    return text.replace(before, after, 1)


def patch(src: Path):
    path = src / 'r_draw.c'
    text = path.read_text()
    declarations = '''\nextern int doom_draw_active;
void doom_queue_column(byte*, const byte*, const byte*, int, int, int, int);
void doom_queue_span(byte*, const byte*, const byte*, unsigned, unsigned, int);
'''
    text = replace_once(text, '#include "r_local.h"', '#include "r_local.h"' + declarations)
    # First occurrences belong to high-detail R_DrawColumn/R_DrawSpan. Low-detail
    # functions remain serial; an entire frame uses one detail setting.
    marker = '    frac = dc_texturemid + (dc_yl-centery)*fracstep; '
    index = text.index(marker) + len(marker)
    text = text[:index] + '\n    if (doom_draw_active) { doom_queue_column(dest, dc_source, dc_colormap, frac, fracstep, count, dc_x); return; }\n' + text[index:]
    marker = '    count = ds_x2 - ds_x1;'
    index = text.index(marker) + len(marker)
    text = text[:index] + '\n    if (doom_draw_active) { doom_queue_span(dest, ds_source, ds_colormap, position, step, count); return; }\n' + text[index:]
    path.write_text(text)

    path = src / 'r_main.c'
    text = path.read_text()
    text = replace_once(text, '    R_DrawMasked ();', '''    extern int doom_draw_active, doom_drew_world;
    if (doom_draw_active) { doom_drew_world = 1; return; }
    R_DrawMasked ();''')
    path.write_text(text)

    path = src / 'd_main.c'
    text = path.read_text()
    text = replace_once(text, 'void D_Display (void)', 'void D_DisplayStage (int resume)')
    start = text.index('void D_DisplayStage (int resume)')
    end = text.index('\n//\n// Add configuration', start)
    body = text[start:end]
    # Only wipe/y are needed after the split; preserve them across collective work.
    body = replace_once(body, '    int\t\t\t\ty;', '    static int y;')
    body, count = re.subn(r'    boolean\s+wipe;', '    static boolean wipe;', body)
    assert count == 1
    marker = '    if (nodrawers)'
    body = replace_once(body, marker, '    if (resume) goto opaque_done;\n\n' + marker)
    marker = '    if (gamestate == GS_LEVEL && gametic)\n    \tHU_Drawer ();'
    body = replace_once(body, marker, '    return;\nopaque_done:\n    if (nodrawers) return;\n' + marker)
    text = text[:start] + body + '\nvoid D_Display(void) { D_DisplayStage(0); D_DisplayStage(1); }\n' + text[end:]
    path.write_text(text)

    # Pending commands retain cache pointers until the collective draw. Preserve
    # normal cache policy, flushing only if an allocation actually purges data.
    path = src / 'z_zone.c'
    text = path.read_text()
    marker = '                Z_Free ((byte *)rover+sizeof(memblock_t));'
    text = replace_once(text, marker,
        '                extern void doom_draw_flush_serial(void);\n'
        '                doom_draw_flush_serial();\n' + marker)
    path.write_text(text)
