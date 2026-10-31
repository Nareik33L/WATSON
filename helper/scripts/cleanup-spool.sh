#!/usr/bin/env bash
# D4 / §5 Tier 0: delete M0-B test spools (PNG + OCR sidecars) and verify they are gone.
# Does not touch $HOME/watson-m0b/*.jsonl (metadata). Never send raw frames.
#
# Safety: never rm -rf. Never take a free-form path. Only the three runbook
# spool roots, realpath-resolved, sitting under a fixed allowed base
# (/tmp or $HOME/Library/Application Support/WATSON). Refuses /, $HOME,
# any ancestor of $HOME, relative paths, and symlink escapes. Deletes only
# yyyy-MM-dd/*.{png,json} and then empty dirs; unexpected entries abort
# with nothing removed.
set -euo pipefail

usage() {
  cat <<'EOF'
  helper/scripts/cleanup-spool.sh [dir ...]

Deletes only the runbook spool roots (never rm -rf, never a free-form path):

  /tmp/watson-m0b-grant
  /tmp/watson-selftest
  $HOME/Library/Application Support/WATSON/spool

Each argument must be an absolute path that realpath-resolves to one of those
roots and stays under that root's allowed base. Relative paths, /, $HOME,
ancestors of $HOME, and symlink escapes are refused.

Only yyyy-MM-dd/*.png and yyyy-MM-dd/*.json are removed, then empty date
dirs and the empty spool root. Anything else present is a hard failure and
nothing is deleted.

With no arguments, the three known roots are cleaned (missing roots are OK).
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

resolve() {
  python3 -c 'import os, sys; print(os.path.realpath(sys.argv[1]))' "$1"
}

# True if $1 is /, $HOME, or any ancestor of $HOME (after resolve).
is_home_or_ancestor() {
  local candidate="$1"
  local home="$2"
  if [[ "$candidate" == "/" || "$candidate" == "$home" ]]; then
    return 0
  fi
  if [[ "$home" == "$candidate"/* ]]; then
    return 0
  fi
  return 1
}

HOME_R="$(resolve "$HOME")"
TMP_BASE="$(resolve /tmp)"
WATSON_BASE_LITERAL="${HOME}/Library/Application Support/WATSON"
WATSON_SPOOL_LITERAL="${WATSON_BASE_LITERAL}/spool"
KNOWN_TMP_GRANT="${TMP_BASE}/watson-m0b-grant"
KNOWN_TMP_SELFTEST="${TMP_BASE}/watson-selftest"

# Allowed bases: /tmp (realpath) and the WATSON Application Support dir.
# The WATSON base is accepted only when it still resolves under $HOME
# (so a symlink that points WATSON at / or $HOME cannot widen the allowlist).
watson_base_resolved="$(resolve "$WATSON_BASE_LITERAL")"
KNOWN_WATSON_SPOOL=""
if [[ "$watson_base_resolved" == "$HOME_R"/* && "$watson_base_resolved" != "$HOME_R" ]]; then
  KNOWN_WATSON_SPOOL="${watson_base_resolved}/spool"
fi

is_known_root() {
  local resolved="$1"
  if is_home_or_ancestor "$resolved" "$HOME_R"; then
    return 1
  fi
  if [[ "$resolved" == "$KNOWN_TMP_GRANT" || "$resolved" == "$KNOWN_TMP_SELFTEST" ]]; then
    if [[ "$resolved" == "$TMP_BASE"/* ]]; then
      return 0
    fi
  fi
  if [[ -n "$KNOWN_WATSON_SPOOL" && "$resolved" == "$KNOWN_WATSON_SPOOL" ]]; then
    if [[ "$resolved" == "$watson_base_resolved"/* ]]; then
      return 0
    fi
  fi
  return 1
}

refuse() {
  echo "REFUSE: $*" >&2
  exit 1
}

# Validate a caller-supplied path. Prints the resolved known root on success.
validate_arg() {
  local raw="$1"
  if [[ "$raw" != /* ]]; then
    refuse "relative path: $raw"
  fi
  if [[ "$raw" == "/" ]]; then
    refuse "/"
  fi
  local resolved
  resolved="$(resolve "$raw")"
  if is_home_or_ancestor "$resolved" "$HOME_R"; then
    refuse "$raw resolves to home or an ancestor of home ($resolved)"
  fi
  if ! is_known_root "$resolved"; then
    refuse "not a known spool root under an allowed base: $raw -> $resolved"
  fi
  if [[ -e "$resolved" && -L "$resolved" ]]; then
    refuse "spool root is a symlink: $raw -> $resolved"
  fi
  printf '%s\n' "$resolved"
}

# Scan $1. Prints "FILE path" / "DIR path" lines, or exits 2 if anything
# other than yyyy-MM-dd/*.{png,json} is present (including symlinks).
scan_tree() {
  python3 - "$1" <<'PY'
import os
import re
import sys

root = sys.argv[1]
date_re = re.compile(r"^\d{4}-\d{2}-\d{2}$")
unexpected = []
files = []
dirs = []

for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
    rel = os.path.relpath(dirpath, root)
    if rel == os.curdir:
        for name in list(dirnames):
            path = os.path.join(dirpath, name)
            if os.path.islink(path) or not date_re.match(name) or not os.path.isdir(path):
                unexpected.append(path)
            else:
                dirs.append(path)
        for name in filenames:
            unexpected.append(os.path.join(dirpath, name))
        continue
    # Date directories must be a single level of png/json files.
    for name in dirnames:
        unexpected.append(os.path.join(dirpath, name))
    for name in filenames:
        path = os.path.join(dirpath, name)
        if os.path.islink(path):
            unexpected.append(path)
        elif name.endswith(".png") or name.endswith(".json"):
            files.append(path)
        else:
            unexpected.append(path)

if unexpected:
    sys.stderr.write("FAIL: unexpected entries (nothing deleted):\n")
    for path in unexpected:
        sys.stderr.write(f"  {path}\n")
    sys.exit(2)

for path in files:
    print(f"FILE {path}")
for path in dirs:
    print(f"DIR {path}")
PY
}

clean_root() {
  local root="$1"
  if [[ ! -e "$root" ]]; then
    echo "removed: $root"
    return 0
  fi
  if [[ -L "$root" ]]; then
    refuse "spool root is a symlink: $root"
  fi
  if [[ ! -d "$root" ]]; then
    echo "FAIL: not a directory: $root" >&2
    return 1
  fi

  local listing
  if ! listing="$(scan_tree "$root")"; then
    return 1
  fi

  local line path
  while IFS= read -r line; do
    [[ -n "$line" ]] || continue
    path="${line#FILE }"
    if [[ "$line" == FILE\ * ]]; then
      chmod u+w "$path" 2>/dev/null || true
      rm -- "$path"
    fi
  done <<< "$listing"

  while IFS= read -r line; do
    [[ -n "$line" ]] || continue
    path="${line#DIR }"
    if [[ "$line" == DIR\ * ]]; then
      chmod u+w "$path" 2>/dev/null || true
      rmdir -- "$path"
    fi
  done <<< "$listing"

  chmod u+w "$root" 2>/dev/null || true
  rmdir -- "$root"

  if [[ -e "$root" ]]; then
    echo "FAIL: still exists: $root" >&2
    return 1
  fi
  echo "removed: $root"
}

targets=()
if [[ $# -eq 0 ]]; then
  for raw in \
    /tmp/watson-m0b-grant \
    /tmp/watson-selftest \
    "$WATSON_SPOOL_LITERAL"
  do
    if [[ ! -e "$raw" && ! -L "$raw" ]]; then
      echo "removed: $raw"
      continue
    fi
    targets+=("$(validate_arg "$raw")")
  done
else
  for raw in "$@"; do
    targets+=("$(validate_arg "$raw")")
  done
fi

# Scan every existing target before deleting any of them.
for root in "${targets[@]+"${targets[@]}"}"; do
  if [[ -e "$root" ]]; then
    scan_tree "$root" >/dev/null
  fi
done

PASS=1
for root in "${targets[@]+"${targets[@]}"}"; do
  if ! clean_root "$root"; then
    PASS=0
  fi
done

if [[ "$PASS" -ne 1 ]]; then
  echo "FAIL: spool cleanup incomplete" >&2
  exit 1
fi
echo "PASS: listed spools are empty (paths gone)."
