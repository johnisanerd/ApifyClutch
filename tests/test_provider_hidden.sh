#!/usr/bin/env bash
# Verify no credential or internal detail is committed anywhere in this repo.
# Runs unconditionally: no API key, no apify CLI, no network.
#
# This Actor has NO upstream data vendor. It fetches the site directly with a
# browser-grade TLS fingerprint, so unlike the vendor-backed Actors in this
# portfolio there is no provider brand to hide. What must never be committed
# instead is a credential: a proxy URL with an embedded user and password, an
# Apify token, or a private key. Runtime error-text sanitization is covered by
# test_upstream_error_sanitized.py.

set -euo pipefail
HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
# shellcheck source=_lib.sh
source "${HERE}/_lib.sh"

TEST_NAME="test_provider_hidden"
THIS_SCRIPT="${HERE}/$(basename "${BASH_SOURCE[0]}")"

# Files allowed to contain a matching string:
#  - this script, which literally greps for the patterns
#  - the sanitization test, which asserts these strings never leak and so must
#    construct fake ones
ALLOWED_PATHS=(
    "${THIS_SCRIPT}"
    "${REPO_ROOT}/tests/test_upstream_error_sanitized.py"
)

# Credential shapes and residential-proxy vendor hosts. The proxy ladder is a
# fallback in this Actor, but if it is ever wired to a real endpoint the
# credentials must come from the platform at run time, never from a file.
FORBIDDEN_PATTERNS=(
    "apify_api_"
    "BEGIN RSA PRIVATE KEY"
    "BEGIN OPENSSH PRIVATE KEY"
    "brd-customer"
    "brd.superproxy"
    "zproxy."
    "luminati.io"
)

# Any URL carrying inline credentials, e.g. http://user:pass@host.
# Extended-regex syntax: this is used with `grep -E`, where `\?` and `\+` mean
# LITERAL '?' and '+' and would make the check silently match nothing.
CREDENTIAL_URL_RE='https?://[A-Za-z0-9._%-]+:[A-Za-z0-9._%-]+@'

is_allowed() {
    local target="$1" allowed
    for allowed in "${ALLOWED_PATHS[@]}"; do
        [[ "${target}" == "${allowed}" ]] && return 0
    done
    return 1
}

# Files tracked in the repo, excluding build and fixture artifacts. Fixtures are
# captured public pages, so they are scanned for credentials but not for
# vendor-host strings that legitimately appear in page markup.
# `mapfile` is bash 4+; macOS ships bash 3.2, so read the list portably.
FILES=()
while IFS= read -r _f; do
    FILES+=("${_f}")
done < <(
    find "${REPO_ROOT}" \
        -type d \( -name .git -o -name .venv -o -name storage -o -name __pycache__ \) -prune -o \
        -type f -print
)

violations=0

for pattern in "${FORBIDDEN_PATTERNS[@]}"; do
    for f in "${FILES[@]}"; do
        is_allowed "${f}" && continue
        case "${f}" in
            "${REPO_ROOT}"/tests/fixtures/*) continue ;;
            *.png|*.jpg|*.ico|*.lock) continue ;;
        esac
        if grep -qi -- "${pattern}" "${f}" 2>/dev/null; then
            echo "LEAK: '${pattern}' found in ${f#"${REPO_ROOT}"/}"
            grep -in -- "${pattern}" "${f}" 2>/dev/null | head -3 | sed 's/^/      /'
            violations=$((violations + 1))
        fi
    done
done

for f in "${FILES[@]}"; do
    is_allowed "${f}" && continue
    case "${f}" in
        *.png|*.jpg|*.ico|*.lock) continue ;;
    esac
    if grep -qE -- "${CREDENTIAL_URL_RE}" "${f}" 2>/dev/null; then
        echo "LEAK: URL with inline credentials in ${f#"${REPO_ROOT}"/}"
        violations=$((violations + 1))
    fi
done

# A .env must never be committed. (.env.example, which holds no values, is fine.)
if git -C "${REPO_ROOT}" ls-files --error-unmatch .env >/dev/null 2>&1; then
    echo "LEAK: .env is tracked by git"
    violations=$((violations + 1))
fi

if [[ "${violations}" -ne 0 ]]; then
    echo "FAIL: ${TEST_NAME} — ${violations} violation(s)"
    exit 1
fi

echo "PASS: ${TEST_NAME} — no credentials or private keys committed"
exit 0
