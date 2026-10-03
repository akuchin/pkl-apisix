#!/usr/bin/env bash
# Run every check. Usage: tools/test.sh [--no-docker]
#   1. generated code is up to date with the schema snapshot
#   2. every schema regex compiles in Pkl
#   3. tests/cases: Pkl evaluates them and APISIX accepts the output
#   4. tests/pkl-only: Pkl evaluates them
#   5. tests/negative: Pkl rejects them with the expected message
#   6. docs conformance: every Admin API example of the APISIX docs (Pkl vs. a real APISIX)
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "${ROOT}"
VERSION="$(python3 -c 'import glob,os; print(sorted(glob.glob("schema/apisix-*.json"))[-1][len("schema/apisix-"):-5])')"
DOCKER=1
[ "${1:-}" = "--no-docker" ] && DOCKER=0
FAILED=0
fail() { echo "FAIL: $*"; FAILED=1; }
step() { echo; echo "== $*"; }

mkdir -p build/cases

# Throwaway self-signed certificate for tests/cases/ssl-and-stream.pkl (not stored in git).
if [ ! -f tests/fixtures/test.key ]; then
  openssl req -x509 -newkey rsa:2048 -nodes -days 3650 -subj "/CN=test.example.com" \
    -addext "subjectAltName=DNS:test.example.com" \
    -keyout tests/fixtures/test.key -out tests/fixtures/test.crt 2>/dev/null || fail "openssl"
fi

step "generate (APISIX ${VERSION})"
python3 tools/generate.py "schema/apisix-${VERSION}.json" || fail "generate"
if git rev-parse --git-dir >/dev/null 2>&1 && [ -n "$(git status --porcelain -- src COVERAGE.md)" ] && [ -n "${CI:-}" ]; then
  git status --porcelain -- src COVERAGE.md
  fail "generated files are not up to date: run tools/generate.py and commit"
fi

step "regex patterns compile in Pkl"
python3 - <<'PY' || fail "patterns"
import json, sys
sys.path.insert(0, "tools")
from generate import pkl_regex
pats = json.load(open("build/patterns.json"))
open("build/patterns.pkl", "w").write(
    "compiled: List<Boolean> = List(\n" + "".join(f'  "".contains({pkl_regex(p)}) || true,\n' for p in pats) + ")\n")
print(f"{len(pats)} patterns")
PY
pkl eval build/patterns.pkl >/dev/null || fail "a regex does not compile"

step "tests/cases"
for f in tests/cases/*.pkl; do
  out="build/cases/$(basename "${f%.pkl}").yaml"
  if pkl eval "${f}" -o "${out}"; then
    if [ "${DOCKER}" = 1 ]; then tools/apisix-check.sh "${VERSION}" "${out}" || fail "${f}: rejected by APISIX"
    else echo "ok (Pkl only): ${f}"; fi
  else
    fail "${f}: Pkl error"
  fi
done

step "tests/pkl-only"
for f in tests/pkl-only/*.pkl; do
  pkl eval "${f}" >/dev/null && echo "ok: ${f}" || fail "${f}"
done

step "tests/negative"
for f in tests/negative/*.pkl; do
  expected="$(head -1 "${f}" | sed 's|^// expect: ||')"
  if out="$(pkl eval "${f}" 2>&1)"; then
    fail "${f}: accepted, expected an error"
  elif grep -qF -- "${expected}" <<<"${out}"; then
    echo "ok: ${f}"
  else
    fail "${f}: wrong error, expected: ${expected}"; sed -n 2,4p <<<"${out}"
  fi
done

if [ "${DOCKER}" = 1 ]; then
  step "docs conformance"
  python3 tools/docs_examples.py "${VERSION}" && python3 tools/conformance.py "${VERSION}" || fail "conformance (see CONFORMANCE.md)"
fi

echo
[ "${FAILED}" = 0 ] && echo "ALL CHECKS PASSED" || echo "SOME CHECKS FAILED"
exit "${FAILED}"
