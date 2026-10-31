#!/usr/bin/env bash
# D4 / §5 Tier 0: delete M0-B test spools (PNG + OCR sidecars) and verify they are gone.
# Does not touch $HOME/watson-m0b/*.jsonl (metadata). Never send raw frames.
set -euo pipefail

usage() {
  cat <<'EOF'
  helper/scripts/cleanup-spool.sh [dir ...]

Removes each directory (rm -rf) and fails if anything remains.
With no arguments, cleans the runbook locations:

  /tmp/watson-m0b-grant
  /tmp/watson-selftest
  $HOME/Library/Application Support/WATSON/spool
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

if [[ $# -eq 0 ]]; then
  set -- \
    /tmp/watson-m0b-grant \
    /tmp/watson-selftest \
    "${HOME}/Library/Application Support/WATSON/spool"
fi

PASS=1
for dir in "$@"; do
  if [[ -e "$dir" ]]; then
    chmod -R u+w "$dir" 2>/dev/null || true
    rm -rf "$dir"
  fi
  if [[ -e "$dir" ]]; then
    echo "FAIL: still exists: $dir" >&2
    PASS=0
  else
    echo "removed: $dir"
  fi
done

if [[ "$PASS" -ne 1 ]]; then
  echo "FAIL: spool cleanup incomplete" >&2
  exit 1
fi
echo "PASS: listed spools are empty (paths gone)."
