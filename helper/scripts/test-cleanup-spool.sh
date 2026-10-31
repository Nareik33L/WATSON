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

# Newline in a day-dir filename must abort; nothing outside the root is
# touched. The name is shaped like the old bash-loop exploit
# (extra "FILE <relative>" line after the newline).
ABS_CANARY="/tmp/watson-newline-outside-canary"
REL_CANARY="outside-canary"
printf 'keep-abs' > "$ABS_CANARY"
printf 'keep-rel' > "$REL_CANARY"
python3 - "$SELFTEST" <<'PY'
import os, sys
day = os.path.join(sys.argv[1], "2026-10-31")
os.makedirs(day, exist_ok=True)
open(os.path.join(day, "f.png"), "w", encoding="utf-8").write("x")
open(os.path.join(day, "f.json"), "w", encoding="utf-8").write("{}")
open(os.path.join(day, "x.png\nFILE outside-canary"), "w", encoding="utf-8").write("evil")
PY
set +e
nl_out="$("$CLEAN" "$SELFTEST" 2>&1)"
nl_rc=$?
set -e
nl_evil="$(python3 -c 'import os; print(os.path.exists("/tmp/watson-selftest/2026-10-31/" + "x.png\nFILE outside-canary"))')"
if [[ "$nl_rc" -eq 0 ]]; then
  echo "FAIL: newline filename should abort cleanup" >&2
  echo "$nl_out" >&2
  FAILED=1
elif [[ ! -e "$ABS_CANARY" || "$(cat "$ABS_CANARY")" != "keep-abs" ]]; then
  echo "FAIL: newline filename touched the absolute canary" >&2
  echo "$nl_out" >&2
  FAILED=1
elif [[ ! -e "$REL_CANARY" || "$(cat "$REL_CANARY")" != "keep-rel" ]]; then
  echo "FAIL: newline filename touched the relative canary" >&2
  echo "$nl_out" >&2
  FAILED=1
elif [[ ! -e "$SELFTEST/2026-10-31/f.png" || ! -e "$SELFTEST/2026-10-31/f.json" ]]; then
  echo "FAIL: newline-filename abort deleted a spool file" >&2
  echo "$nl_out" >&2
  FAILED=1
elif [[ "$nl_evil" != "True" ]]; then
  echo "FAIL: newline-named file was removed" >&2
  echo "$nl_out" >&2
  FAILED=1
else
  echo "ok: newline filename aborted without touching anything outside the root"
fi
rm -f -- "$ABS_CANARY" "$REL_CANARY"
reset_selftest

if [[ "$FAILED" -ne 0 ]]; then
  echo "FAIL: cleanup-spool.sh tests" >&2
  exit 1
fi
echo "PASS: cleanup-spool.sh refuses dangerous paths and cleans a fake spool."
