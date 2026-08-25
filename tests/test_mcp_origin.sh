#!/usr/bin/env bash
# MCP-origin regression gate (Rule #30). MANDATORY: never SKIP - if no Python
# environment with the Apify SDK can be found, this FAILS. Exits non-zero on any
# failure so CI / the publish flow is gated on it. Self-contained (no _lib.sh).
#
# Layout note: this repo keeps tests/ at the repo root and the actor (with its
# pyproject.toml + src/) in the ApifyClutch/ subfolder.
set -uo pipefail

TESTS_DIR="$(cd "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${TESTS_DIR}/.." && pwd)"
ACTOR_DIR="${REPO_ROOT}/ApifyClutch"            # actor subfolder (pyproject.toml + src/)
PYTEST="${TESTS_DIR}/test_mcp_origin.py"

rc=1
if command -v uv >/dev/null 2>&1 && uv run --directory "${ACTOR_DIR}" python -c 'import apify' >/dev/null 2>&1; then
  uv run --directory "${ACTOR_DIR}" python "${PYTEST}"; rc=$?
else
  PY=""
  for cand in "${ACTOR_DIR}/.venv/bin/python" "${REPO_ROOT}/.venv/bin/python" "$(command -v python3 || true)"; do
    if [ -n "${cand}" ] && [ -x "${cand}" ] && "${cand}" -c 'import apify' >/dev/null 2>&1; then PY="${cand}"; break; fi
  done
  if [ -z "${PY}" ]; then
    echo "FAIL: MCP-origin regression could not run - no Python env with the Apify SDK (uv project env, .venv, or python3). NOT skipping." >&2
    exit 1
  fi
  "${PY}" "${PYTEST}"; rc=$?
fi

if [ "${rc}" -ne 0 ]; then
  echo "FAIL: MCP-origin regression test failed (exit ${rc})." >&2
  exit 1
fi
echo "PASS: MCP-origin regression test."
exit 0
