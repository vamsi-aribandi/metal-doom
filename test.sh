#!/bin/zsh
set -eu
cd "${0:A:h}"
[[ -x build/metal-doom && -f build/doom.metal && -f build/doom-persistent.metal ]] || ./build.sh
python3 tools/lower_cfg.py
python3 tests/test_structured_cfg.py
python3 tests/run_tests.py "$@"
