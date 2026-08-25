#!/usr/bin/env bash
# Wrapper so run_all.sh (which globs test_*.sh) picks up the Python test.
# MANDATORY: never SKIP. If no Python env with the deps can be found, this FAILS.
set -uo pipefail

TESTS_DIR="$(cd "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${TESTS_DIR}/.." && pwd)"
ACTOR_DIR="${REPO_ROOT}/ApifyClutch"
PYTEST="${TESTS_DIR}/test_offline_units.py"

rc=1
if command -v uv >/dev/null 2>&1 && uv run --directory "${ACTOR_DIR}" python -c 'import curl_cffi' >/dev/null 2>&1; then
  uv run --directory "${ACTOR_DIR}" python "${PYTEST}"; rc=$?
else
  PY=""
  for cand in "${ACTOR_DIR}/.venv/bin/python" "${REPO_ROOT}/.venv/bin/python" "$(command -v python3 || true)"; do
    if [ -n "${cand}" ] && [ -x "${cand}" ] && "${cand}" -c 'import curl_cffi' >/dev/null 2>&1; then PY="${cand}"; break; fi
  done
  if [ -z "${PY}" ]; then
    echo "FAIL: test_offline_units could not run - no Python env with curl_cffi. NOT skipping." >&2
    exit 1
  fi
  "${PY}" "${PYTEST}"; rc=$?
fi

if [ "${rc}" -ne 0 ]; then echo "FAIL: test_offline_units (exit ${rc})." >&2; exit 1; fi
exit 0
