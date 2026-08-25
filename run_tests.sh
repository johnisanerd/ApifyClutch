#!/usr/bin/env bash
# Verbose interactive runner. Same suite as test_actor.sh, no log redirect.
DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
bash "${DIR}/tests/run_all.sh"
