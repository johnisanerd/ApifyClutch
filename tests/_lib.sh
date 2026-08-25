#!/usr/bin/env bash
# Shared helpers for the test_*.sh scripts. Source this from each test script.

set -euo pipefail

# Resolve repo root and actor subfolder regardless of where the test is invoked from.
TESTS_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[1]:-${BASH_SOURCE[0]}}")" >/dev/null 2>&1 && pwd)"
REPO_ROOT="$(cd -- "${TESTS_DIR}/.." >/dev/null 2>&1 && pwd)"
ACTOR_DIR="${REPO_ROOT}/ApifyClutch"
DATASET_DIR="${ACTOR_DIR}/storage/datasets/default"
RUN_LOG="${TESTS_DIR}/.last_run.log"

# Load .env if present (no vendor key is needed; kept for local overrides).
if [[ -f "${REPO_ROOT}/.env" ]]; then
    set -a
    # shellcheck disable=SC1091
    source "${REPO_ROOT}/.env"
    set +a
fi

require_api_key() {
    # This Actor has no upstream data vendor and therefore no API key: it talks
    # to the site directly with a browser-grade TLS fingerprint. Kept as a no-op
    # so the shared harness shape matches the rest of the portfolio.
    :
}

require_jq() {
    if ! command -v jq >/dev/null 2>&1; then
        echo "SKIP: jq not installed (brew install jq)"
        exit 0
    fi
}

require_apify() {
    if ! command -v apify >/dev/null 2>&1; then
        echo "SKIP: apify CLI not installed (npm i -g apify-cli)"
        exit 0
    fi
}

# Verify the local Python environment has the `apify` package importable.
# `apify run` invokes `python3 -m src`, which fails immediately if apify isn't
# installed. We'd rather skip than report a misleading test failure.
require_python_env() {
    local python_bin="${PYTHON:-python3}"
    if ! "${python_bin}" -c "import apify" >/dev/null 2>&1; then
        echo "SKIP: 'apify' Python package not importable by ${python_bin}. Set up a venv with 'pip install -r requirements.txt' (or set PYTHON to a venv interpreter) before running this test."
        exit 0
    fi
}

# Run the actor against an input fixture. Captures stdout+stderr to RUN_LOG.
# Usage: run_actor_with_input <fixture-name>
# The exit code of `apify run` is returned via $ACTOR_EXIT_CODE.
ACTOR_EXIT_CODE=0
run_actor_with_input() {
    local fixture="$1"
    local input_path="${TESTS_DIR}/inputs/${fixture}.json"
    if [[ ! -f "${input_path}" ]]; then
        echo "FAIL: fixture not found: ${input_path}"
        exit 1
    fi

    pushd "${ACTOR_DIR}" >/dev/null

    # Pre-purge storage so we read a clean dataset.
    rm -rf storage

    set +e
    apify run --purge --input "$(cat "${input_path}")" >"${RUN_LOG}" 2>&1
    ACTOR_EXIT_CODE=$?
    set -e

    popd >/dev/null
}

# Count dataset items (excluding Apify's internal __metadata__.json).
dataset_count() {
    if [[ ! -d "${DATASET_DIR}" ]]; then
        echo 0
        return
    fi
    find "${DATASET_DIR}" -maxdepth 1 -type f -name "*.json" ! -name "__metadata__.json" | wc -l | tr -d ' '
}

# Print all dataset items as a JSON array (excluding __metadata__.json).
dataset_array() {
    if [[ ! -d "${DATASET_DIR}" ]]; then
        echo "[]"
        return
    fi
    local files=()
    while IFS= read -r -d '' f; do
        files+=("$f")
    done < <(find "${DATASET_DIR}" -maxdepth 1 -type f -name "*.json" ! -name "__metadata__.json" -print0 | sort -z)
    if [[ ${#files[@]} -eq 0 ]]; then
        echo "[]"
        return
    fi
    jq -s '.' "${files[@]}" 2>/dev/null || echo "[]"
}

pass() { echo "PASS: $1"; exit 0; }
fail() {
    echo "FAIL: $1"
    echo "  Run log tail:"
    tail -n 30 "${RUN_LOG}" 2>/dev/null | sed 's/^/    /'
    exit 1
}
