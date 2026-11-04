#!/bin/zsh
set -eu
cd "${0:A:h}/.."
[[ -x build/metal-doom ]] || ./app/build.sh
exec ./build/metal-doom "$@"
