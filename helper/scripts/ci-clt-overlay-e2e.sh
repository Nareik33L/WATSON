#!/usr/bin/env bash
# macos-15 CI: run build.sh end-to-end against a fake WATSON_CLT_ROOT so
# overlay detection, flag injection, and --no-clt-overlay are actually
# compiled. The fake tree is not the SDK — the overlay remaps only that path.
#
#   helper/scripts/ci-clt-overlay-e2e.sh prepare|swiftc|spm|no-overlay|no-overlay-spm
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
BUILD="$REPO/helper/scripts/build.sh"
FAKE="/tmp/watson-ci-fake-clt"
LOG="/tmp/watson-clt-overlay-e2e.log"
BIN_DIR="/tmp/watson-capture-ci-overlay"

usage() {
  echo "usage: $0 {prepare|swiftc|spm|no-overlay|no-overlay-spm}" >&2
  exit 2
}

prepare_fake() {
  rm -rf "$FAKE"
  mkdir -p "$FAKE/usr/include/swift"
  cat > "$FAKE/usr/include/swift/module.modulemap" <<'EOF'
module SwiftBridging [system] {
  header "bridging"
  export *
}
EOF
  cat > "$FAKE/usr/include/swift/bridging.modulemap" <<'EOF'
module SwiftBridging [system] {
  header "bridging"
  export *
}
EOF
  mkdir -p "$BIN_DIR"
  echo "ok: fake CLT at $FAKE"
}

last_binary() {
  # build.sh prints the Mach-O path as its last line.
  tail -n 1 "$LOG"
}

assert_signed() {
  local bin="$1"
  if [[ ! -x "$bin" ]]; then
    echo "FAIL: expected executable $bin" >&2
    exit 1
  fi
  codesign --verify --verbose=2 "$bin"
  local dv
  dv="$(codesign -dv --verbose=4 "$bin" 2>&1 || true)"
  echo "$dv"
  if ! echo "$dv" | grep -q 'Identifier=com.watson.capture'; then
    echo "FAIL: signed binary missing Identifier=com.watson.capture" >&2
    exit 1
  fi
  if ! echo "$dv" | grep -Eq 'Signature=adhoc|Authority='; then
    echo "FAIL: codesign -dv did not show a signature" >&2
    exit 1
  fi
  echo "ok: signed $bin"
}

assert_overlay_maps_only_fake() {
  local ov=""
  ov="$(sed -n 's/^CLT SwiftBridging overlay applied: //p' "$LOG" | tail -n 1)"
  if [[ -z "$ov" || ! -f "$ov" ]]; then
    echo "FAIL: could not find overlay YAML from applied line" >&2
    cat "$LOG" >&2
    exit 1
  fi
  case "$ov" in
    */.build/clt-overlay/*) ;;
    *)
      echo "FAIL: overlay path is not under .build/clt-overlay/: $ov" >&2
      exit 1
      ;;
  esac
  if ! grep -F "$FAKE" "$ov"; then
    echo "FAIL: overlay YAML does not name the fake CLT path $FAKE" >&2
    cat "$ov" >&2
    exit 1
  fi
  if grep -E '/Applications/Xcode|CommandLineTools|MacOSX\.sdk' "$ov"; then
    echo "FAIL: overlay YAML remaps a real SDK/CLT path" >&2
    cat "$ov" >&2
    exit 1
  fi
  echo "ok: overlay maps only the fake tree"
}

assert_no_overlay_run() {
  local argv_label="$1"
  if grep -F 'CLT SwiftBridging overlay applied:' "$LOG"; then
    echo "FAIL: --no-clt-overlay still printed overlay applied" >&2
    exit 1
  fi
  if ! grep -E "^${argv_label}:" "$LOG"; then
    echo "FAIL: --no-clt-overlay produced no ${argv_label}: line (assertion would be vacuous)" >&2
    exit 1
  fi
  if grep -E "^${argv_label}:.*-vfsoverlay" "$LOG"; then
    echo "FAIL: --no-clt-overlay still passed -vfsoverlay" >&2
    exit 1
  fi
  if grep -E "^${argv_label}:.*-ivfsoverlay" "$LOG"; then
    echo "FAIL: --no-clt-overlay still passed -ivfsoverlay" >&2
    exit 1
  fi
  if [[ -e "$REPO/helper/.build/clt-overlay/release/overlay.yaml" ]]; then
    echo "FAIL: --no-clt-overlay wrote overlay.yaml" >&2
    exit 1
  fi
}

run_build() {
  rm -rf "$REPO/helper/.build"
  # CI must be set or WATSON_CLT_ROOT is ignored (test-only).
  # Do not expand an empty array under `set -u` (macOS bash 3.2).
  if [[ $# -gt 0 ]]; then
    CI=true WATSON_CLT_ROOT="$FAKE" \
      "$BUILD" --release --identity - "$@" | tee "$LOG"
  else
    CI=true WATSON_CLT_ROOT="$FAKE" \
      "$BUILD" --release --identity - | tee "$LOG"
  fi
}

cmd="${1:-}"
case "$cmd" in
  prepare)
    prepare_fake
    ;;
  swiftc)
    prepare_fake
    run_build
    if ! grep -F 'CLT SwiftBridging overlay applied:' "$LOG"; then
      echo "FAIL: swiftc overlay build missing applied line" >&2
      exit 1
    fi
    if ! grep -E '^swiftc-argv:.*-vfsoverlay' "$LOG"; then
      echo "FAIL: swiftc-argv missing -vfsoverlay" >&2
      exit 1
    fi
    if ! grep -E '^swiftc-argv:.*-ivfsoverlay' "$LOG"; then
      echo "FAIL: swiftc-argv missing -ivfsoverlay" >&2
      exit 1
    fi
    assert_overlay_maps_only_fake
    bin="$(last_binary)"
    assert_signed "$bin"
    cp "$bin" "$BIN_DIR/watson-capture-swiftc-overlay"
    echo "ok: swiftc overlay e2e"
    ;;
  spm)
    prepare_fake
    run_build --spm
    if ! grep -F 'CLT SwiftBridging overlay applied:' "$LOG"; then
      echo "FAIL: --spm overlay build missing applied line" >&2
      exit 1
    fi
    if ! grep -E '^swift-build-argv:.*-vfsoverlay' "$LOG"; then
      echo "FAIL: swift-build-argv missing -vfsoverlay" >&2
      exit 1
    fi
    if ! grep -E '^swift-build-argv:.*-ivfsoverlay' "$LOG"; then
      echo "FAIL: swift-build-argv missing -ivfsoverlay" >&2
      exit 1
    fi
    assert_overlay_maps_only_fake
    bin="$(last_binary)"
    assert_signed "$bin"
    cp "$bin" "$BIN_DIR/watson-capture-spm-overlay"
    echo "ok: --spm overlay e2e"
    ;;
  no-overlay)
    prepare_fake
    run_build --no-clt-overlay
    assert_no_overlay_run swiftc-argv
    bin="$(last_binary)"
    assert_signed "$bin"
    echo "ok: --no-clt-overlay swiftc e2e"
    ;;
  no-overlay-spm)
    prepare_fake
    run_build --spm --no-clt-overlay
    assert_no_overlay_run swift-build-argv
    bin="$(last_binary)"
    assert_signed "$bin"
    echo "ok: --spm --no-clt-overlay e2e"
    ;;
  *)
    usage
    ;;
esac
