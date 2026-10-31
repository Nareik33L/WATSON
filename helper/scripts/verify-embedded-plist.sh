#!/usr/bin/env bash
# Assert watson-capture embeds Info.plist with CFBundleIdentifier com.watson.capture.
# Usage: helper/scripts/verify-embedded-plist.sh BIN
#
# Must not grep otool's header: "Contents of (__TEXT,__info_plist)" matches
# [0-9a-f]{2} via the word "of". Extract the section bytes and parse them.
set -euo pipefail

EXPECTED="com.watson.capture"
BIN="${1:?usage: $0 BIN}"

if [[ ! -e "$BIN" ]]; then
  echo "FAIL: missing binary: $BIN" >&2
  exit 1
fi
if ! command -v otool >/dev/null 2>&1; then
  echo "otool not on PATH (Command Line Tools required)." >&2
  exit 1
fi
if ! command -v segedit >/dev/null 2>&1; then
  echo "segedit not on PATH (Command Line Tools required)." >&2
  exit 1
fi

# -X: no path header, no "Contents of…" line, no addresses — only hex bytes.
dump="$(otool -X -s __TEXT __info_plist "$BIN" 2>/dev/null || true)"
if [[ -z "${dump//[$' \t\n']/}" ]]; then
  echo "FAIL: no __TEXT,__info_plist bytes in $BIN" >&2
  exit 1
fi

tmp="$(mktemp -t watson-info-plist)"
cleanup() { rm -f "$tmp"; }
trap cleanup EXIT

if ! segedit "$BIN" -extract __TEXT __info_plist "$tmp" >/dev/null; then
  echo "FAIL: segedit could not extract __TEXT,__info_plist from $BIN" >&2
  exit 1
fi

# Section padding is trailing NULs; strip so the XML plist parses.
python3 -c '
import sys
path = sys.argv[1]
data = open(path, "rb").read().rstrip(b"\x00")
if not data:
    raise SystemExit("empty __info_plist after stripping padding")
open(path, "wb").write(data)
' "$tmp"

if ! plutil -lint "$tmp" >/dev/null; then
  echo "FAIL: extracted __info_plist is not a valid plist" >&2
  exit 1
fi

ident="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' "$tmp")"
if [[ "$ident" != "$EXPECTED" ]]; then
  echo "FAIL: CFBundleIdentifier is '$ident', expected $EXPECTED" >&2
  exit 1
fi

echo "ok: $BIN CFBundleIdentifier=$ident"
