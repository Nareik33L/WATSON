# WATSON

WATSON is a local-first, offline-capable work-memory system for macOS (Apple Silicon first). A signed Swift helper captures the frontmost window; a Python supervisor, inference worker, and local dashboard coordinate through a single encrypted SQLite database. Frames are extracted with on-device OCR, grouped into activity segments, and recorded as claims with evidence. No component opens an outbound socket during normal operation.

This repository is at the layout-and-CI skeleton, the M0 bake-off and capture helper, and M1: config, encrypted SQLite, jobs queue, redacted logging, runtime network guard, and `watson replay` of the `day_acme_q4` fixture. The dashboard and production runtime adapters land later.

## Development

Requires Python 3.12 and [uv](https://docs.astral.sh/uv/).

```bash
uv sync --group dev
uv run watson --version
uv run ruff check .
uv run mypy src/watson
uv run pytest
```

The M0-B capture helper (`watson-capture`) lives in [`helper/`](helper/README.md). It is macOS 14+ and builds with Command Line Tools via `swiftc` (SwiftPM is `--spm` opt-in; CLT 16.2 cannot load `Package.swift`). The Mac runbook — build, sign as `WATSON Dev`, first-run Screen Recording grant, 1-hour CPU, two-rebuild TCC check — is in that README.

```bash
helper/scripts/build.sh
# SwiftPM + unit tests (CI / Xcode):
#   helper/scripts/build.sh --spm && cd helper && swift test
```

### CLI (this slice)

`watson doctor` prints resolved config paths (`~/Library/Application Support/WATSON` or `WATSON_DATA_DIR`) and database status. It never prints secrets or the SQLCipher key.

`watson db upgrade` creates the runtime directories if needed and applies Alembic migrations to the encrypted database.

`watson db status` shows whether `watson.db` exists, whether it opens with the current key provider, WAL/FTS5/sqlite-vec, and the current vs head Alembic revision.

`watson replay <fixture_dir> [--speed N|--instant]` validates `manifest.json` (sha256/dims) and each sidecar against `schemas/sidecar.schema.json`, copies the fixture into a 0700/0600 temp spool, and ingests frames through the same spool reader live capture will use. An `extract` job stub records sidecar OCR as fixture-only `frame_text`, then deletes the spool PNG and sets `deletion_verified=1`. Source fixture files are never deleted. Re-runs are idempotent on frame id.

`watson status` prints frame counts by state, job counts by state, and last replay (the upcoming status page will read the same numbers).

Linux CI runs `watson replay tests/fixtures/day_acme_q4 --instant` and `watson db status` under `unshare -rn` (no network interfaces). On Linux, set `WATSON_DB_KEY` to 64 hex characters; the key is never written to the data directory.

`run` and `models` remain stubs until later milestones.

The M0-A model bake-off lives in [`tools/bench/`](tools/bench/README.md). On the 8 GB development Mac:

```bash
uv sync --group bench
uv run watson-bench fetch --profile 8gb --runtime ollama
uv run watson-bench run --profile 8gb --runtime ollama --report
```

## Engineering plan

Architecture decisions D1–D12, repository layout, and milestones are defined in [docs/WATSON_V1_ENGINEERING_PLAN.md](docs/WATSON_V1_ENGINEERING_PLAN.md) (Revision 2). Database encryption (§14 #4) is recorded in [docs/decisions/0004-db-encryption.md](docs/decisions/0004-db-encryption.md).
