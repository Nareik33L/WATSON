#!/usr/bin/env bash
# Fake the stale CLT SwiftBridging modulemap collision and assert that
# build.sh writes a valid clang VFS overlay under helper/.build.
# Detection root is WATSON_CLT_ROOT (test-only override).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=build.sh
source "$ROOT/scripts/build.sh"

FAILED=0
BUILD="$ROOT/.build"
TEST_DEST="$BUILD/clt-overlay-test"
CANARY="/tmp/watson-clt-overlay-outside-canary"

cleanup() {
  rm -f -- "$CANARY"
  if [[ -d "$TEST_DEST" ]]; then
    find "$TEST_DEST" -mindepth 1 -delete
    rmdir -- "$TEST_DEST" 2>/dev/null || true
  fi
}

fail() {
  echo "FAIL: $*" >&2
  FAILED=1
}

assert_vfs_overlay() {
  local ov="$1"
  local clt_root="$2"
  local empty
  empty="$(dirname "$ov")/empty.modulemap"
  if [[ ! -f "$ov" ]]; then
    fail "overlay YAML missing: $ov"
    return
  fi
  if [[ ! -f "$empty" ]]; then
    fail "empty modulemap missing: $empty"
    return
  fi
  if grep -q 'SwiftBridging' "$empty"; then
    fail "empty modulemap must not define SwiftBridging"
    return
  fi
  case "$ov" in
    "$BUILD"|"$BUILD"/*) ;;
    *)
      fail "overlay YAML is not under $BUILD: $ov"
      return
      ;;
  esac
  if [[ "$ov" != /* || "$empty" != /* ]]; then
    fail "overlay paths must be absolute"
    return
  fi
  python3 - "$ov" "$clt_root" "$empty" <<'PY' || fail "overlay YAML failed validation"
import sys
from pathlib import Path

ov, clt_root, empty = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
text = ov.read_text(encoding="utf-8")
if "version: 0" not in text:
    raise SystemExit("missing 'version: 0'")
if "roots:" not in text:
    raise SystemExit("missing 'roots:'")
if "type: directory" not in text or "type: file" not in text:
    raise SystemExit("missing VFS type entries")
if "name: module.modulemap" not in text and 'name: "module.modulemap"' not in text:
    raise SystemExit("missing module.modulemap mapping")
swift_inc = (clt_root / "usr" / "include" / "swift").resolve()
if str(swift_inc) not in text:
    raise SystemExit(f"overlay does not name {swift_inc}")
if str(empty.resolve()) not in text:
    raise SystemExit(f"overlay does not name empty modulemap {empty}")
if not empty.is_file():
    raise SystemExit("empty modulemap is not a file")
if empty.stat().st_size != 0 and "SwiftBridging" in empty.read_text(encoding="utf-8"):
    raise SystemExit("empty modulemap still defines SwiftBridging")
# No path field may be relative.
for line in text.splitlines():
    stripped = line.strip()
    if stripped.startswith("name:") or stripped.startswith("external-contents:"):
        value = stripped.split(":", 1)[1].strip().strip('"').strip("'")
        if value in {"directory", "file", "module.modulemap"}:
            continue
        if value.startswith("/"):
            continue
        if value == "module.modulemap":
            continue
        raise SystemExit(f"non-absolute VFS path: {stripped}")
print("ok: overlay YAML is a version-0 VFS with absolute paths")
PY
}

reset_state() {
  CLT_OVERLAY=1
  CONFIG="release"
  CLT_OVERLAY_FILE=""
  unset WATSON_CLT_ROOT
  rm -rf "$TEST_DEST"
}

make_fake_clt() {
  local root="$1"
  local define_bridging="${2:-1}"
  mkdir -p "$root/usr/include/swift"
  if [[ "$define_bridging" -eq 1 ]]; then
    cat > "$root/usr/include/swift/module.modulemap" <<'EOF'
module SwiftBridging [system] {
  header "bridging"
  export *
}
EOF
  else
    cat > "$root/usr/include/swift/module.modulemap" <<'EOF'
module Darwin [system] {
  export *
}
EOF
  fi
  cat > "$root/usr/include/swift/bridging.modulemap" <<'EOF'
module SwiftBridging [system] {
  header "bridging"
  export *
}
EOF
}

printf 'keep' > "$CANARY"
reset_state

# (1) stale pair + SwiftBridging → overlay YAML generated and valid
FAKE="$TEST_DEST/fake-clt"
make_fake_clt "$FAKE" 1
export WATSON_CLT_ROOT="$FAKE"
mkdir -p "$TEST_DEST"
prepare_clt_overlay_if_needed "$TEST_DEST/overlay" > "$TEST_DEST/prepare.log"
out="$(cat "$TEST_DEST/prepare.log")"
if [[ "$out" != *"CLT SwiftBridging overlay applied:"* ]]; then
  fail "expected overlay applied line, got: $out"
elif [[ -z "$CLT_OVERLAY_FILE" ]]; then
  fail "CLT_OVERLAY_FILE unset after detection"
else
  echo "ok: overlay applied line printed"
  assert_vfs_overlay "$CLT_OVERLAY_FILE" "$FAKE"
fi
if [[ ! -f "$CANARY" || "$(cat "$CANARY")" != "keep" ]]; then
  fail "wrote outside the build dir (canary touched)"
else
  echo "ok: canary outside the build dir untouched"
fi

# (2) --no-clt-overlay disables generation
reset_state
make_fake_clt "$FAKE" 1
export WATSON_CLT_ROOT="$FAKE"
CLT_OVERLAY=0
mkdir -p "$TEST_DEST"
prepare_clt_overlay_if_needed "$TEST_DEST/overlay" > "$TEST_DEST/prepare.log"
out="$(cat "$TEST_DEST/prepare.log")"
if [[ "$out" == *"overlay applied"* ]]; then
  fail "--no-clt-overlay still applied overlay: $out"
elif [[ -n "$CLT_OVERLAY_FILE" ]]; then
  fail "--no-clt-overlay set CLT_OVERLAY_FILE"
elif [[ -e "$TEST_DEST/overlay/overlay.yaml" ]]; then
  fail "--no-clt-overlay wrote overlay.yaml"
else
  echo "ok: --no-clt-overlay / CLT_OVERLAY=0 skips overlay"
fi

# (3) both files but module.modulemap does not define SwiftBridging
reset_state
make_fake_clt "$FAKE" 0
export WATSON_CLT_ROOT="$FAKE"
mkdir -p "$TEST_DEST"
prepare_clt_overlay_if_needed "$TEST_DEST/overlay" > "$TEST_DEST/prepare.log"
out="$(cat "$TEST_DEST/prepare.log")"
if [[ "$out" == *"overlay applied"* || -n "$CLT_OVERLAY_FILE" ]]; then
  fail "overlay applied without SwiftBridging in module.modulemap: $out"
else
  echo "ok: no overlay when module.modulemap lacks SwiftBridging"
fi

# (4) missing bridging.modulemap
reset_state
make_fake_clt "$FAKE" 1
rm -f "$FAKE/usr/include/swift/bridging.modulemap"
export WATSON_CLT_ROOT="$FAKE"
mkdir -p "$TEST_DEST"
prepare_clt_overlay_if_needed "$TEST_DEST/overlay" > "$TEST_DEST/prepare.log"
out="$(cat "$TEST_DEST/prepare.log")"
if [[ "$out" == *"overlay applied"* || -n "$CLT_OVERLAY_FILE" ]]; then
  fail "overlay applied without bridging.modulemap: $out"
else
  echo "ok: no overlay when bridging.modulemap is absent"
fi

# (5) refuse dest outside helper/.build
reset_state
make_fake_clt "$FAKE" 1
export WATSON_CLT_ROOT="$FAKE"
set +e
outside_out="$(write_clt_overlay "$FAKE" /tmp/watson-clt-overlay-escape 2>&1)"
outside_rc=$?
set -e
if [[ "$outside_rc" -eq 0 ]]; then
  fail "write_clt_overlay allowed a dest outside helper/.build"
elif [[ ! -f "$CANARY" ]]; then
  fail "outside-dest attempt damaged the canary"
else
  echo "ok: refuse dest outside helper/.build (exit=$outside_rc)"
fi
rm -rf /tmp/watson-clt-overlay-escape

cleanup
unset WATSON_CLT_ROOT

if [[ "$FAILED" -ne 0 ]]; then
  echo "FAIL: CLT overlay tests" >&2
  exit 1
fi
echo "PASS: CLT SwiftBridging overlay YAML is generated and valid."
