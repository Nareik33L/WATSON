#!/usr/bin/env bash
# Compare two watson-capture binaries: same __TEXT,__info_plist bytes and
# CFBundleIdentifier com.watson.capture. Used by CI across swiftc vs SwiftPM.
# Usage: helper/scripts/compare-embedded-identity.sh BIN_A BIN_B
set -euo pipefail

if [[ $# -ne 2 ]]; then
  echo "usage: $0 BIN_A BIN_B" >&2
  exit 2
fi

A="$1"
B="$2"
for bin in "$A" "$B"; do
  if [[ ! -e "$bin" ]]; then
    echo "missing binary: $bin" >&2
    exit 1
  fi
done

plist_hex() {
  # Strip the address column; drop trailing section padding.
  otool -s __TEXT __info_plist "$1" | awk 'NR>2 {
    for (i = 2; i <= NF; i++) printf "%s", $i
  }' | sed 's/00*$//'
}

ident_from_plist() {
  local tmp
  tmp="$(mktemp -t watson-info-plist)"
  # codesign -dv on an unsigned/linker-signed binary reports Identifier=
  # from the filename (watson-capture), not CFBundleIdentifier. Read the
  # embedded plist instead — that is the TCC identifier we ship.
  if ! segedit "$1" -extract __TEXT __info_plist "$tmp" >/dev/null; then
    echo "segedit failed on $1" >&2
    rm -f "$tmp"
    return 1
  fi
  # Section padding is trailing NULs; strip so the XML plist parses.
  python3 -c '
import sys
path = sys.argv[1]
data = open(path, "rb").read().rstrip(b"\x00")
open(path, "wb").write(data)
' "$tmp"
  /usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' "$tmp"
  rm -f "$tmp"
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

I1="$(ident_from_plist "$A")"
I2="$(ident_from_plist "$B")"
echo "a CFBundleIdentifier=$I1"
echo "b CFBundleIdentifier=$I2"
if [[ "$I1" != "com.watson.capture" || "$I1" != "$I2" ]]; then
  echo "FAIL: identifier mismatch or not com.watson.capture" >&2
  exit 1
fi

echo "PASS: identical Info.plist section and identifier=$I1"
