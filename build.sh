#!/bin/zsh
set -eu
cd "${0:A:h}"
./engine/build_wasm.sh
python3 tools/to_metal.py
./app/build.sh
print 'Built build/Metal Doom.app (game assets supplied separately)'
