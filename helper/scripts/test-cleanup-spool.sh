#!/usr/bin/env bash
# Portable checks for cleanup-spool.sh: refuse /, $HOME, a relative path,
# and a symlink escape; succeed on a fake /tmp/watson-selftest spool.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CLEAN="$ROOT/scripts/cleanup-spool.sh"
SELFTEST="/tmp/watson-selftest"
FAILED=0

assert_refuse() {
  local label="$1"
  shift
  local out rc
  set +e
  out="$("$CLEAN" "$@" 2>&1)"
  rc=$?
  set -e
  if [[ "$rc" -eq 0 ]]; then
    echo "FAIL: $label: expected refuse, got exit 0" >&2
    echo "$out" >&2
    FAILED=1
    return
  fi
  if [[ "$out" != *REFUSE* ]]; then
    echo "FAIL: $label: stderr/stdout missing REFUSE" >&2
    echo "$out" >&2
    FAILED=1
    return
  fi
  if [[ ! -d "$HOME" ]]; then
    echo "FAIL: $label: \$HOME is gone" >&2
    FAILED=1
    return
  fi
  echo "ok: refuse $label (exit=$rc)"
}

reset_selftest() {
  if [[ -L "$SELFTEST" || -e "$SELFTEST" ]]; then
    # Fixture teardown only — never call cleanup-spool.sh here (it may refuse).
    chmod -R u+w "$SELFTEST" 2>/dev/null || true
    if [[ -L "$SELFTEST" || -f "$SELFTEST" ]]; then
      rm -- "$SELFTEST"
    else
      # Known CI fixture tree: date dirs of png/json (and leftover test junk).
      find "$SELFTEST" -mindepth 1 -delete
      rmdir -- "$SELFTEST"
    fi
  fi
}

reset_selftest

# (1) refuse /
assert_refuse "/" /

# (2) refuse $HOME
assert_refuse "\$HOME" "$HOME"

# (3) refuse a relative path
assert_refuse "relative path" "watson-selftest"

# (4) refuse a symlink that escapes the allowed /tmp base into $HOME
ln -s "$HOME" "$SELFTEST"
assert_refuse "symlink escape" "$SELFTEST"
if [[ ! -d "$HOME" ]]; then
  echo "FAIL: symlink escape deleted \$HOME" >&2
  FAILED=1
fi
if [[ ! -L "$SELFTEST" ]]; then
  echo "FAIL: symlink escape removed the link (should have refused)" >&2
  FAILED=1
fi
reset_selftest

# (5) successful cleanup of a fake runbook spool
mkdir -p "$SELFTEST/2026-10-31"
printf 'x' > "$SELFTEST/2026-10-31/f.png"
printf '{}' > "$SELFTEST/2026-10-31/f.json"
set +e
success_out="$("$CLEAN" "$SELFTEST" 2>&1)"
success_rc=$?
set -e
if [[ "$success_rc" -ne 0 ]]; then
  echo "FAIL: fake spool cleanup exited $success_rc" >&2
  echo "$success_out" >&2
  FAILED=1
elif [[ -e "$SELFTEST" ]]; then
  echo "FAIL: expected $SELFTEST to be gone" >&2
  echo "$success_out" >&2
  FAILED=1
elif [[ "$success_out" != *PASS* ]]; then
  echo "FAIL: fake spool cleanup missing PASS" >&2
  echo "$success_out" >&2
  FAILED=1
else
  echo "ok: fake spool cleaned"
fi

# Bonus: unexpected file must abort and leave the tree in place.
mkdir -p "$SELFTEST/2026-10-31"
printf 'x' > "$SELFTEST/2026-10-31/f.png"
printf '{}' > "$SELFTEST/2026-10-31/f.json"
printf 'nope' > "$SELFTEST/2026-10-31/notes.txt"
set +e
unexpected_out="$("$CLEAN" "$SELFTEST" 2>&1)"
unexpected_rc=$?
set -e
if [[ "$unexpected_rc" -eq 0 ]]; then
  echo "FAIL: unexpected file should abort cleanup" >&2
  echo "$unexpected_out" >&2
  FAILED=1
elif [[ ! -e "$SELFTEST/2026-10-31/f.png" || ! -e "$SELFTEST/2026-10-31/notes.txt" ]]; then
  echo "FAIL: unexpected-file abort deleted something" >&2
  echo "$unexpected_out" >&2
  FAILED=1
else
  echo "ok: unexpected file aborted without deletion"
fi
reset_selftest

if [[ "$FAILED" -ne 0 ]]; then
  echo "FAIL: cleanup-spool.sh tests" >&2
  exit 1
fi
echo "PASS: cleanup-spool.sh refuses dangerous paths and cleans a fake spool."
