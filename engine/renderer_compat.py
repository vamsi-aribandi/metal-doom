"""Make legacy column overreads independent of cache/allocator layout.

Apply identically to generated native-reference and GPU C copies. Original
vendored source stays unchanged. Doom's column sampler wraps to 128 texels even
for short textures; rounding can also sample just beyond a post. Reserve a zero
128-byte tail so a read past the final column cannot sample zone headers.
"""
from pathlib import Path


def patch(src: Path):
    path = src / "w_wad.c"
    text = path.read_text()
    before = "lump->cache = Z_Malloc(W_LumpLength(lumpnum), tag, &lump->cache);"
    assert text.count(before) == 1
    text = text.replace(before,
        "lump->cache = Z_Malloc(W_LumpLength(lumpnum) + 128, tag, &lump->cache);\n"
        "        memset((byte *)lump->cache + W_LumpLength(lumpnum), 0, 128);")
    path.write_text(text)
    path = src / "r_data.c"
    text = path.read_text()
    before = "block = Z_Malloc (texturecompositesize[texnum],"
    assert text.count(before) == 1
    text = text.replace(before, "block = Z_Malloc (texturecompositesize[texnum] + 128,")
    before = "    collump = texturecolumnlump[texnum];"
    assert text.count(before) == 2
    text = text.replace(before,
        "    memset(block, 0, texturecompositesize[texnum] + 128);\n" + before, 1)
    path.write_text(text)
