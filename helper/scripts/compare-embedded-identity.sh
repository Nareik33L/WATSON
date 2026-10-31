#!/usr/bin/env bash
# Compare two watson-capture binaries: same __TEXT,__info_plist bytes,
# CFBundleIdentifier com.watson.capture, and codesign -dv Identifier.
# Usage: helper/scripts/compare-embedded-identity.sh BIN_A BIN_B
set -euo pipefail

if [[ $# -ne 2 ]]; then
  echo "usage: $0 BIN_A BIN_B" >&2
  exit 2
fi

A="$1"
B="$2"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

for bin in "$A" "$B"; do
  if [[ ! -e "$bin" ]]; then
    echo "missing binary: $bin" >&2
    exit 1
  fi
  "$ROOT/scripts/verify-embedded-plist.sh" "$bin"
done

plist_hex() {
  # Strip the address column; drop trailing section padding.
  otool -s __TEXT __info_plist "$1" | awk 'NR>2 {
    for (i = 2; i <= NF; i++) printf "%s", $i
  }' | sed 's/00*$//'
}

codesign_ident() {
  codesign -dv "$1" 2>&1 | awk -F= '/^Identifier=/{print $2; exit}'
}

echo "=== $A ==="
codesign -dv --verbose=4 "$A" 2>&1 || true
otool -s __TEXT __info_plist "$A"
echo "=== $B ==="
codesign -dv --verbose=4 "$B" 2>&1 || true
otool -s __TEXT __info_plist "$B"

H1="$(plist_hex "$A")"
H2="$(plist_hex "$B")"
if [[ -z "$H1" || "$H1" != "$H2" ]]; then
  echo "FAIL: Info.plist section differs or is empty" >&2
  echo "a hex length=${#H1} b hex length=${#H2}" >&2
  exit 1
fi

I1="$(codesign_ident "$A")"
I2="$(codesign_ident "$B")"
echo "a codesign Identifier=$I1"
echo "b codesign Identifier=$I2"
if [[ "$I1" != "com.watson.capture" || "$I1" != "$I2" ]]; then
  echo "FAIL: codesign Identifier mismatch or not com.watson.capture" >&2
  exit 1
fi

echo "PASS: identical Info.plist section and Identifier=$I1"
