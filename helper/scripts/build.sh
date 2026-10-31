#!/usr/bin/env bash
# Build watson-capture with swiftc by default (no Package.swift evaluation).
# SwiftPM is --spm opt-in: CLT 16.2 cannot link PackageDescription
# (undefined symbol PackageDescription.Package.__allocating_init).
# Usage: helper/scripts/build.sh [--debug|--release] [--spm] [--identity NAME] [--no-sign] [--no-embed-plist] [--no-clt-overlay]
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG="release"
IDENTITY="${WATSON_CODESIGN_IDENTITY:-WATSON Dev}"
SIGN=1
USE_SPM=0
EMBED_PLIST=1
CLT_OVERLAY=1
# Package.swift platforms: .macOS(.v14); swift-tools-version: 6.0
MACOS_MIN="14.0"
SWIFT_VERSION="6"
# Set by prepare_clt_overlay_if_needed when the stale CLT modulemap is detected.
CLT_OVERLAY_FILE=""

# --- CLT SwiftBridging overlay (sourced by test-clt-overlay.sh) ---
# Stale Aug 2023 CLT usr/include/swift/module.modulemap redefines SwiftBridging,
# which bridging.modulemap (Dec 2024) also defines. That breaks
# `could not build module 'Foundation'` via CoreAudio CATapDescription.h.
# Workaround: clang VFS overlay mapping the stale file to an empty modulemap.
# The empty file hides the *whole* stale module.modulemap (every module in
# that file), not only SwiftBridging. bridging.modulemap is left as-is.
# WATSON_CLT_ROOT is test-only: honoured only when CI is set (GitHub Actions
# or CI=1). It must point at a fake tree so the overlay never remaps the
# real SDK.

_abs_norm() {
  python3 -c 'import sys; from pathlib import Path; print(Path(sys.argv[1]).expanduser().resolve())' "$1"
}

_abs_existing() {
  local p="$1"
  if [[ -d "$p" ]]; then
    (cd "$p" && pwd)
  elif [[ -e "$p" ]]; then
    echo "$(cd "$(dirname "$p")" && pwd)/$(basename "$p")"
  else
    _abs_norm "$p"
  fi
}

resolve_clt_root() {
  local raw=""
  if [[ -n "${WATSON_CLT_ROOT:-}" ]]; then
    if [[ -z "${CI:-}" ]]; then
      echo "warning: WATSON_CLT_ROOT is test-only and ignored unless CI is set" >&2
    else
      echo "warning: WATSON_CLT_ROOT is a test-only override; overlay maps only that fake tree" >&2
      raw="$WATSON_CLT_ROOT"
    fi
  fi
  if [[ -z "$raw" ]] && command -v xcode-select >/dev/null 2>&1; then
    raw="$(xcode-select -p 2>/dev/null || true)"
  fi
  if [[ -z "$raw" ]]; then
    return 1
  fi
  _abs_existing "$raw"
}

clt_has_stale_swiftbridging() {
  local root="$1"
  local mm="$root/usr/include/swift/module.modulemap"
  local bm="$root/usr/include/swift/bridging.modulemap"
  if [[ ! -f "$mm" || ! -f "$bm" ]]; then
    return 1
  fi
  # Require a module declaration, not a comment that mentions SwiftBridging.
  grep -Eq '^[[:space:]]*module[[:space:]]+SwiftBridging' "$mm"
}

_assert_under_build_dir() {
  local dest="$1"
  local build="$ROOT/.build"
  mkdir -p "$build"
  local abs_build abs_dest
  abs_build="$(_abs_norm "$build")"
  abs_dest="$(_abs_norm "$dest")"
  case "$abs_dest" in
    "$abs_build"|"$abs_build"/*) ;;
    *)
      echo "refusing to write CLT overlay outside $abs_build" >&2
      exit 1
      ;;
  esac
  mkdir -p "$dest"
}

write_clt_overlay() {
  local root="$1"
  local dest="$2"
  _assert_under_build_dir "$dest"
  dest="$(cd "$dest" && pwd)"
  local swift_inc empty ov
  swift_inc="$(_abs_existing "$root/usr/include/swift")"
  empty="$dest/empty.modulemap"
  ov="$dest/overlay.yaml"
  # Empty on purpose: hide the entire stale module.modulemap.
  : > "$empty"
  empty="$(_abs_existing "$empty")"
  ov="$(_abs_existing "$ov")"
  python3 - "$ov" "$swift_inc" "$empty" <<'PY'
import json
import sys
from pathlib import Path

ov, swift_inc, empty = sys.argv[1], sys.argv[2], sys.argv[3]
Path(ov).write_text(
    "version: 0\n"
    "roots:\n"
    "  - type: directory\n"
    f"    name: {json.dumps(swift_inc)}\n"
    "    contents:\n"
    "      - type: file\n"
    "        name: module.modulemap\n"
    f"        external-contents: {json.dumps(empty)}\n",
    encoding="utf-8",
)
PY
  CLT_OVERLAY_FILE="$ov"
}

prepare_clt_overlay_if_needed() {
  CLT_OVERLAY_FILE=""
  if [[ "${CLT_OVERLAY:-1}" -eq 0 ]]; then
    return 0
  fi
  local root=""
  root="$(resolve_clt_root)" || return 0
  if [[ -z "$root" ]]; then
    return 0
  fi
  if ! clt_has_stale_swiftbridging "$root"; then
    return 0
  fi
  local dest="${1:-$ROOT/.build/${CONFIG:-release}/clt-overlay}"
  write_clt_overlay "$root" "$dest"
  echo "CLT SwiftBridging overlay applied: $CLT_OVERLAY_FILE"
}

# Allow helper/scripts/test-clt-overlay.sh to source the helpers above
# without compiling or signing.
if [[ "${BASH_SOURCE[0]}" != "${0}" ]]; then
  return 0
fi

usage() {
  cat <<'EOF'
Build watson-capture (macOS 14+, Command Line Tools).

  helper/scripts/build.sh [--debug|--release] [--spm] [--identity NAME] [--no-sign] [--no-embed-plist] [--no-clt-overlay]

Default is a plain swiftc build so CLT 16.2 (no Xcode) works. SwiftPM
cannot load Package.swift on that toolchain (ManifestAPI:
PackageDescription.Package.__allocating_init is undefined). Pass --spm
to use `swift build` (CI / full Xcode).

On CLT 16.2 a stale usr/include/swift/module.modulemap can redefine
SwiftBridging (also defined in bridging.modulemap) and fail the default
swiftc build with `could not build module 'Foundation'`. The script then
writes a clang VFS overlay under helper/.build and passes
-vfsoverlay / -ivfsoverlay. --no-clt-overlay disables that.

Environment:
  WATSON_CODESIGN_IDENTITY   codesign identity (default: WATSON Dev)
  WATSON_CLT_ROOT            test-only fake CLT/SDK root. Honoured only when
                             CI is set. Overlay maps that fake tree, not the
                             real SDK. Do not set this on the development Mac.

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
    --no-clt-overlay) CLT_OVERLAY=0; shift ;;
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

# Overlay flags last so empty-array `set -u` on bash 3.2 is never an issue.
# Echo the argv so CI can assert -vfsoverlay / -ivfsoverlay were (or were not) passed.
_echo_argv() {
  local label="$1"
  shift
  local buf="$label"
  local a
  for a in "$@"; do
    buf+=" $(printf '%q' "$a")"
  done
  echo "$buf"
}

run_swiftc() {
  if [[ -n "${CLT_OVERLAY_FILE:-}" ]]; then
    _echo_argv "swiftc-argv:" "$@" \
      -vfsoverlay "$CLT_OVERLAY_FILE" \
      -Xcc -ivfsoverlay \
      -Xcc "$CLT_OVERLAY_FILE"
    swiftc "$@" \
      -vfsoverlay "$CLT_OVERLAY_FILE" \
      -Xcc -ivfsoverlay \
      -Xcc "$CLT_OVERLAY_FILE"
  else
    _echo_argv "swiftc-argv:" "$@"
    swiftc "$@"
  fi
}

run_swift_build() {
  if [[ -n "${CLT_OVERLAY_FILE:-}" ]]; then
    _echo_argv "swift-build-argv:" "$@" \
      -Xswiftc -vfsoverlay \
      -Xswiftc "$CLT_OVERLAY_FILE" \
      -Xcc -ivfsoverlay \
      -Xcc "$CLT_OVERLAY_FILE"
    swift build "$@" \
      -Xswiftc -vfsoverlay \
      -Xswiftc "$CLT_OVERLAY_FILE" \
      -Xcc -ivfsoverlay \
      -Xcc "$CLT_OVERLAY_FILE"
  else
    _echo_argv "swift-build-argv:" "$@"
    swift build "$@"
  fi
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
      run_swift_build -c "$CONFIG" --disable-sandbox \
        -Xlinker -sectcreate \
        -Xlinker __TEXT \
        -Xlinker __info_plist \
        -Xlinker "$ROOT/Info.plist"
    else
      run_swift_build -c "$CONFIG" --disable-sandbox
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
  run_swiftc \
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
    run_swiftc \
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
    run_swiftc \
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

prepare_clt_overlay_if_needed

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
