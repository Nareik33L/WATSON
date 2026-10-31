#!/usr/bin/env bash
# Codesign watson-capture with a stable identifier and a named local identity.
# Never generates or writes certificates. Pass the identity via --identity or
# WATSON_CODESIGN_IDENTITY (default: WATSON Dev).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
IDENTITY="${WATSON_CODESIGN_IDENTITY:-WATSON Dev}"
BIN=""
IDENTIFIER="com.watson.capture"

usage() {
  cat <<'EOF'
  helper/scripts/sign.sh --bin PATH [--identity NAME]

Default identity: WATSON Dev (override with --identity or WATSON_CODESIGN_IDENTITY).
Uses --timestamp=none so signing works offline. No hardened runtime (local cert).
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --bin)
      BIN="${2:?--bin requires a path}"
      shift 2
      ;;
    --identity)
      IDENTITY="${2:?--identity requires a name}"
      shift 2
      ;;
    --identifier)
      IDENTIFIER="${2:?--identifier requires a string}"
      shift 2
      ;;
    -h|--help) usage; exit 0 ;;
    *)
      echo "unknown argument: $1" >&2
      exit 2
      ;;
  esac
done

if [[ -z "$BIN" ]]; then
  for candidate in \
    "$ROOT/.build/release/watson-capture" \
    "$ROOT/.build/arm64-apple-macosx/release/watson-capture"
  do
    if [[ -x "$candidate" ]]; then
      BIN="$candidate"
      break
    fi
  done
fi

if [[ -z "$BIN" || ! -e "$BIN" ]]; then
  echo "binary not found. pass --bin or run scripts/build.sh first." >&2
  exit 1
fi

if ! command -v codesign >/dev/null 2>&1; then
  echo "codesign not on PATH (Command Line Tools required)." >&2
  exit 1
fi

if ! security find-identity -v -p codesigning | grep -F "$IDENTITY" >/dev/null; then
  echo "no codesigning identity named '$IDENTITY'." >&2
  echo "Create it with Keychain Access (see helper/README.md) then retry." >&2
  echo "Available identities:" >&2
  security find-identity -v -p codesigning >&2 || true
  exit 1
fi

# --timestamp=none: local identity, no Apple timestamp server (no network).
codesign --force --sign "$IDENTITY" \
  --identifier "$IDENTIFIER" \
  --entitlements "$ROOT/entitlements.plist" \
  --timestamp=none \
  "$BIN"

codesign --verify --verbose=2 "$BIN"
echo "--- codesign -dv ---"
codesign -dv --verbose=4 "$BIN" 2>&1
echo "signed: $BIN  identity='$IDENTITY'  identifier=$IDENTIFIER"
