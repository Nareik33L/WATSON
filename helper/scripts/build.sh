#!/usr/bin/env bash
# Build watson-capture with SwiftPM, embed Info.plist, optionally codesign.
# Usage: helper/scripts/build.sh [--debug|--release] [--identity NAME] [--no-sign]
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG="release"
IDENTITY="${WATSON_CODESIGN_IDENTITY:-WATSON Dev}"
SIGN=1

usage() {
  cat <<'EOF'
Build watson-capture (SwiftPM, macOS 14+, Command Line Tools).

  helper/scripts/build.sh [--debug|--release] [--identity NAME] [--no-sign]

Environment:
  WATSON_CODESIGN_IDENTITY   codesign identity (default: WATSON Dev)

The Info.plist (CFBundleIdentifier com.watson.capture) is embedded in __TEXT,__info_plist.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --debug) CONFIG="debug"; shift ;;
    --release) CONFIG="release"; shift ;;
    --identity)
      IDENTITY="${2:?--identity requires a name}"
      shift 2
      ;;
    --no-sign) SIGN=0; shift ;;
    -h|--help) usage; exit 0 ;;
    *)
      echo "unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if ! command -v swift >/dev/null 2>&1; then
  echo "swift not on PATH. Install Command Line Tools: xcode-select --install" >&2
  exit 1
fi

echo "swift: $(swift --version 2>&1 | head -n 1)"
echo "building ($CONFIG) with embedded $ROOT/Info.plist"

(
  cd "$ROOT"
  # --disable-sandbox: the linker must read Info.plist via -sectcreate.
  swift build -c "$CONFIG" --disable-sandbox \
    -Xlinker -sectcreate \
    -Xlinker __TEXT \
    -Xlinker __info_plist \
    -Xlinker "$ROOT/Info.plist"
)

BIN=""
for candidate in \
  "$ROOT/.build/$CONFIG/watson-capture" \
  "$ROOT/.build/arm64-apple-macosx/$CONFIG/watson-capture" \
  "$ROOT/.build/x86_64-apple-macosx/$CONFIG/watson-capture"
do
  if [[ -x "$candidate" ]]; then
    BIN="$candidate"
    break
  fi
done

if [[ -z "$BIN" ]]; then
  echo "built binary not found under $ROOT/.build" >&2
  exit 1
fi

# Resolve SwiftPM's release/debug symlink so codesign writes the real Mach-O.
BIN="$(cd "$(dirname "$BIN")" && pwd)/$(basename "$BIN")"
echo "binary: $BIN"

if [[ "$SIGN" -eq 1 ]]; then
  "$ROOT/scripts/sign.sh" --bin "$BIN" --identity "$IDENTITY"
fi

echo "ok: $BIN"
echo "$BIN"
