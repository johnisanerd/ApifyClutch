#!/usr/bin/env bash
# Run every test_*.sh in this directory and report pass/fail.

set -o pipefail
HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"

cd "${HERE}"

passed=()
failed=()
skipped=()

# Run provider-hidden test FIRST (no API key required, validates structure).
test_files=()
if [[ -f "test_provider_hidden.sh" ]]; then
    test_files+=("test_provider_hidden.sh")
fi
# Then everything else.
while IFS= read -r f; do
    test_files+=("${f}")
done < <(find . -maxdepth 1 -type f -name "test_*.sh" ! -name "test_provider_hidden.sh" | sort)

echo "================================================================"
echo "  ApifyClutch - test suite"
echo "================================================================"
echo

for t in "${test_files[@]}"; do
    name="$(basename "${t}" .sh)"
    echo "--- ${name} ---"
    output="$(bash "${t}" 2>&1)"
    rc=$?
    echo "${output}"
    if [[ "${rc}" -eq 0 ]]; then
        if echo "${output}" | grep -q "^SKIP:"; then
            skipped+=("${name}")
        else
            passed+=("${name}")
        fi
    else
        failed+=("${name}")
    fi
    echo
done

echo "================================================================"
echo "  Summary"
echo "================================================================"
echo "Passed:  ${#passed[@]}"
for n in "${passed[@]}";  do echo "  ✓ ${n}"; done
echo "Skipped: ${#skipped[@]}"
for n in "${skipped[@]}"; do echo "  • ${n}"; done
echo "Failed:  ${#failed[@]}"
for n in "${failed[@]}";  do echo "  ✗ ${n}"; done
echo

if [[ "${#failed[@]}" -ne 0 ]]; then
    exit 1
fi
exit 0
