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

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CLEAN_PY="$HERE/cleanup_spool.py"

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

# Walk + delete in Python (NUL-safe, no shell word-splitting). The helper
# scans every root first and aborts with nothing removed if anything other
# than yyyy-MM-dd / [A-Za-z0-9_-]+.(png|json) is present.
if [[ ${#targets[@]} -gt 0 ]]; then
  python3 "$CLEAN_PY" -- "${targets[@]}"
fi

echo "PASS: listed spools are empty (paths gone)."
