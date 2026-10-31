#!/usr/bin/env bash
# Rebuild twice with the same identity/identifier and check that the Screen
# Recording grant still holds (CGPreflightScreenCaptureAccess via --preflight).
# Does not call tccutil reset. Does not capture the screen.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
IDENTITY="${WATSON_CODESIGN_IDENTITY:-WATSON Dev}"
PASS=1

usage() {
  cat <<'EOF'
  helper/scripts/check-tcc.sh [--identity NAME]

1. rm -rf .build (do not invoke SwiftPM; CLT 16.2 cannot load Package.swift)
2. build + sign (build 1) → watson-capture --preflight
3. rm -rf .build
4. build + sign (build 2) → watson-capture --preflight
5. Compare codesign identifier + designated requirement; require granted=true both times.

Grant Screen Recording to the signed helper once before this will pass.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --identity)
      IDENTITY="${2:?--identity requires a name}"
      shift 2
      ;;
    -h|--help) usage; exit 0 ;;
    *)
      echo "unknown argument: $1" >&2
      exit 2
      ;;
  esac
done

preflight() {
  local label="$1"
  local bin="$2"
  echo "=== $label: $bin ==="
  codesign -dv --verbose=4 "$bin" 2>&1 | tee /tmp/watson-capture-"$label"-codesign.txt
  codesign -d -r- "$bin" > /tmp/watson-capture-"$label"-dr.txt 2>/tmp/watson-capture-"$label"-dr-err.txt
  cat /tmp/watson-capture-"$label"-dr.txt
  local ident
  ident="$(awk -F= '/^Identifier=/{print $2; exit}' /tmp/watson-capture-"$label"-codesign.txt)"
  echo "identifier: ${ident:-unknown}"

  local out
  set +e
  out="$("$bin" --preflight 2>/tmp/watson-capture-"$label"-stderr.txt)"
  local rc=$?
  set -e
  printf '%s\n' "$out" | tee /tmp/watson-capture-"$label"-preflight.jsonl

  if ! printf '%s\n' "$out" | grep -q '"granted":true'; then
    echo "FAIL $label: Screen Recording not granted (exit $rc)." >&2
    echo "Run the signed binary once, allow Screen Recording, then re-run this script." >&2
    echo "System Settings → Privacy & Security → Screen Recording → enable watson-capture." >&2
    PASS=0
  else
    echo "ok $label: granted=true"
  fi

  if [[ "$ident" != "com.watson.capture" ]]; then
    echo "FAIL $label: identifier is '$ident', expected com.watson.capture" >&2
    PASS=0
  fi
}

echo "=== TCC persistence: two rebuilds (identity='$IDENTITY') ==="

echo "--- clean + build 1 ---"
rm -rf "$ROOT/.build"
BIN1="$("$ROOT/scripts/build.sh" --release --identity "$IDENTITY" | tail -n 1)"
preflight build1 "$BIN1"

echo "--- clean + build 2 ---"
rm -rf "$ROOT/.build"
BIN2="$("$ROOT/scripts/build.sh" --release --identity "$IDENTITY" | tail -n 1)"
preflight build2 "$BIN2"

echo "=== compare identifiers + designated requirement ==="
ID1="$(awk -F= '/^Identifier=/{print $2; exit}' /tmp/watson-capture-build1-codesign.txt)"
ID2="$(awk -F= '/^Identifier=/{print $2; exit}' /tmp/watson-capture-build2-codesign.txt)"
DR1="$(tr -d '\n' < /tmp/watson-capture-build1-dr.txt)"
DR2="$(tr -d '\n' < /tmp/watson-capture-build2-dr.txt)"
echo "build1 identifier=$ID1"
echo "build2 identifier=$ID2"
echo "build1 designated requirement=$DR1"
echo "build2 designated requirement=$DR2"
if [[ "$ID1" != "$ID2" || "$ID1" != "com.watson.capture" ]]; then
  echo "FAIL: identifier changed or is not com.watson.capture" >&2
  PASS=0
fi
if [[ -z "$DR1" || "$DR1" != "$DR2" ]]; then
  echo "FAIL: designated requirement missing or changed across rebuilds" >&2
  PASS=0
fi

if [[ "$PASS" -eq 1 ]]; then
  echo "PASS: Screen Recording grant persisted across two rebuilds (identifier=$ID1)."
  exit 0
fi
echo "FAIL: see /tmp/watson-capture-build{1,2}-*" >&2
exit 1
