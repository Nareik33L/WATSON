#!/usr/bin/env bash
# Build watson-capture with swiftc by default (no Package.swift evaluation).
# SwiftPM is --spm opt-in: CLT 16.2 cannot link PackageDescription
# (undefined symbol PackageDescription.Package.__allocating_init).
# Usage: helper/scripts/build.sh [--debug|--release] [--spm] [--identity NAME] [--no-sign] [--no-embed-plist]
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG="release"
IDENTITY="${WATSON_CODESIGN_IDENTITY:-WATSON Dev}"
SIGN=1
USE_SPM=0
EMBED_PLIST=1
# Package.swift platforms: .macOS(.v14); swift-tools-version: 6.0
MACOS_MIN="14.0"
SWIFT_VERSION="6"

usage() {
  cat <<'EOF'
Build watson-capture (macOS 14+, Command Line Tools).

  helper/scripts/build.sh [--debug|--release] [--spm] [--identity NAME] [--no-sign] [--no-embed-plist]

Default is a plain swiftc build so CLT 16.2 (no Xcode) works. SwiftPM
cannot load Package.swift on that toolchain (ManifestAPI:
PackageDescription.Package.__allocating_init is undefined). Pass --spm
to use `swift build` (CI / full Xcode).

Environment:
  WATSON_CODESIGN_IDENTITY   codesign identity (default: WATSON Dev)

The Info.plist (CFBundleIdentifier com.watson.capture) is embedded in __TEXT,__info_plist.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --debug) CONFIG="debug"; shift ;;
    --release) CONFIG="release"; shift ;;
    --spm) USE_SPM=1; shift ;;
    --identity)
      IDENTITY="${2:?--identity requires a name}"
      shift 2
      ;;
    --no-sign) SIGN=0; shift ;;
    --no-embed-plist) EMBED_PLIST=0; shift ;;
    -h|--help) usage; exit 0 ;;
    *)
      echo "unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [[ "$CONFIG" == "release" ]]; then
  OPT_FLAGS=(-O)
else
  OPT_FLAGS=(-Onone -g)
fi

ARCH="$(uname -m)"
case "$ARCH" in
  arm64|x86_64) ;;
  *)
    echo "unsupported architecture: $ARCH (expected arm64 or x86_64)" >&2
    exit 1
    ;;
esac
TARGET="${ARCH}-apple-macosx${MACOS_MIN}"

FRAMEWORKS=(
  -framework AppKit
  -framework ApplicationServices
  -framework CoreGraphics
  -framework Foundation
  -framework ImageIO
  -framework ScreenCaptureKit
  -framework UniformTypeIdentifiers
  -framework Vision
)

find_built_binary() {
  local bin=""
  local candidate
  for candidate in \
    "$ROOT/.build/$CONFIG/watson-capture" \
    "$ROOT/.build/arm64-apple-macosx/$CONFIG/watson-capture" \
    "$ROOT/.build/x86_64-apple-macosx/$CONFIG/watson-capture"
  do
    if [[ -x "$candidate" ]]; then
      bin="$candidate"
      break
    fi
  done
  if [[ -z "$bin" ]]; then
    echo "built binary not found under $ROOT/.build" >&2
    exit 1
  fi
  # Resolve SwiftPM's release/debug symlink so codesign writes the real Mach-O.
  echo "$(cd "$(dirname "$bin")" && pwd)/$(basename "$bin")"
}

verify_binary() {
  local bin="$1"
  if ! command -v otool >/dev/null 2>&1; then
    echo "otool not on PATH (Command Line Tools required)." >&2
    exit 1
  fi
  if ! command -v codesign >/dev/null 2>&1; then
    echo "codesign not on PATH (Command Line Tools required)." >&2
    exit 1
  fi

  echo "--- codesign -dv ---"
  # Unsigned / linker-signed builds still print what they can; signed builds
  # already passed codesign --verify in sign.sh.
  codesign -dv --verbose=4 "$bin" 2>&1 || true

  echo "--- otool -s __TEXT __info_plist ---"
  otool -s __TEXT __info_plist "$bin"
  # Do not grep otool headers ("of" in "Contents of" is [0-9a-f]{2}).
  "$ROOT/scripts/verify-embedded-plist.sh" "$bin"
}

build_spm() {
  if ! command -v swift >/dev/null 2>&1; then
    echo "swift not on PATH. Install Command Line Tools: xcode-select --install" >&2
    exit 1
  fi
  echo "swift: $(swift --version 2>&1 | head -n 1)"
  if [[ "$EMBED_PLIST" -eq 1 ]]; then
    echo "building ($CONFIG) with SwiftPM + embedded $ROOT/Info.plist"
  else
    echo "building ($CONFIG) with SwiftPM (no embedded Info.plist)"
  fi

  (
    cd "$ROOT"
    # --disable-sandbox: the linker must read Info.plist via -sectcreate.
    # Do not expand an empty array under `set -u` (macOS bash 3.2).
    if [[ "$EMBED_PLIST" -eq 1 ]]; then
      swift build -c "$CONFIG" --disable-sandbox \
        -Xlinker -sectcreate \
        -Xlinker __TEXT \
        -Xlinker __info_plist \
        -Xlinker "$ROOT/Info.plist"
    else
      swift build -c "$CONFIG" --disable-sandbox
    fi
  )
}

build_swiftc() {
  if ! command -v swiftc >/dev/null 2>&1; then
    echo "swiftc not on PATH. Install Command Line Tools: xcode-select --install" >&2
    exit 1
  fi
  if ! command -v xcrun >/dev/null 2>&1; then
    echo "xcrun not on PATH. Install Command Line Tools: xcode-select --install" >&2
    exit 1
  fi

  local sdk
  sdk="$(xcrun --sdk macosx --show-sdk-path)"
  echo "swiftc: $(swiftc --version 2>&1 | head -n 1)"
  echo "sdk: $sdk"
  if [[ "$EMBED_PLIST" -eq 1 ]]; then
    echo "building ($CONFIG) with swiftc -target $TARGET -swift-version $SWIFT_VERSION + embedded $ROOT/Info.plist"
  else
    echo "building ($CONFIG) with swiftc -target $TARGET -swift-version $SWIFT_VERSION (no embedded Info.plist)"
  fi

  local lib_sources=()
  local src
  while IFS= read -r src; do
    lib_sources+=("$src")
  done < <(find "$ROOT/Sources/watson-capture" -name '*.swift' -print | sort)
  if [[ ${#lib_sources[@]} -eq 0 ]]; then
    echo "no library sources under $ROOT/Sources/watson-capture" >&2
    exit 1
  fi

  local cli_main="$ROOT/Sources/watson-capture-cli/main.swift"
  if [[ ! -f "$cli_main" ]]; then
    echo "missing $cli_main" >&2
    exit 1
  fi

  local out_dir="$ROOT/.build/$CONFIG"
  local work="$out_dir/swiftc"
  mkdir -p "$work"
  local bin="$out_dir/watson-capture"

  # Library module first, then the @main executable (import WatsonCapture).
  swiftc \
    -module-name WatsonCapture \
    -emit-module \
    -emit-module-path "$work/WatsonCapture.swiftmodule" \
    -emit-object \
    -parse-as-library \
    -whole-module-optimization \
    "${OPT_FLAGS[@]}" \
    -swift-version "$SWIFT_VERSION" \
    -target "$TARGET" \
    -sdk "$sdk" \
    -module-cache-path "$work/ModuleCache" \
    -o "$work/WatsonCapture.o" \
    "${lib_sources[@]}" \
    "${FRAMEWORKS[@]}"

  # -parse-as-library keeps @main on WatsonCaptureCLI working.
  # Do not expand an empty array under `set -u` (macOS bash 3.2).
  if [[ "$EMBED_PLIST" -eq 1 ]]; then
    swiftc \
      -parse-as-library \
      "${OPT_FLAGS[@]}" \
      -swift-version "$SWIFT_VERSION" \
      -target "$TARGET" \
      -sdk "$sdk" \
      -I "$work" \
      -module-cache-path "$work/ModuleCache" \
      -o "$bin" \
      "$cli_main" \
      "$work/WatsonCapture.o" \
      -Xlinker -sectcreate \
      -Xlinker __TEXT \
      -Xlinker __info_plist \
      -Xlinker "$ROOT/Info.plist" \
      "${FRAMEWORKS[@]}"
  else
    swiftc \
      -parse-as-library \
      "${OPT_FLAGS[@]}" \
      -swift-version "$SWIFT_VERSION" \
      -target "$TARGET" \
      -sdk "$sdk" \
      -I "$work" \
      -module-cache-path "$work/ModuleCache" \
      -o "$bin" \
      "$cli_main" \
      "$work/WatsonCapture.o" \
      "${FRAMEWORKS[@]}"
  fi

  chmod +x "$bin"
}

if [[ "$USE_SPM" -eq 1 ]]; then
  build_spm
else
  build_swiftc
fi

BIN="$(find_built_binary)"
echo "binary: $BIN"

if [[ "$SIGN" -eq 1 ]]; then
  "$ROOT/scripts/sign.sh" --bin "$BIN" --identity "$IDENTITY"
fi

verify_binary "$BIN"

echo "ok: $BIN"
echo "$BIN"
