#!/bin/zsh
set -eu
cd "${0:A:h}"
[[ -x build/metal-doom && -f build/doom.metal ]] || ./build.sh
exec build/metal-doom "$@"
