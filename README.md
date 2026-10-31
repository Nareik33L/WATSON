# WATSON

WATSON is a local-first, offline-capable work-memory system for macOS (Apple Silicon first). A signed Swift helper captures the frontmost window; a Python supervisor, inference worker, and local dashboard coordinate through a single encrypted SQLite database. Frames are extracted with on-device OCR, grouped into activity segments, and recorded as claims with evidence. No component opens an outbound socket during normal operation.

This repository is at the layout-and-CI skeleton, the M0-A bake-off harness, the M0-B Swift capture helper, and the first M1 foundation slice (config, encrypted SQLite, jobs queue, redacted logging, runtime network guard). Replay, the dashboard, and production runtime adapters land later.

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

`run`, `replay`, and `models` remain stubs until later milestones.

The M0-A model bake-off lives in [`tools/bench/`](tools/bench/README.md). On the 8 GB development Mac:

```bash
uv sync --group bench
uv run watson-bench fetch --profile 8gb --runtime ollama
uv run watson-bench run --profile 8gb --runtime ollama --report
```

## Engineering plan

Architecture decisions D1–D12, repository layout, and milestones are defined in [docs/WATSON_V1_ENGINEERING_PLAN.md](docs/WATSON_V1_ENGINEERING_PLAN.md) (Revision 2). Database encryption (§14 #4) is recorded in [docs/decisions/0004-db-encryption.md](docs/decisions/0004-db-encryption.md).

The release ladder (V1.0 personal build → V1.1 meeting intelligence → V1.2 commercialisation → V2.0 → V3.0+ Organisation) is in [docs/ROADMAP.md](docs/ROADMAP.md). Follow-on plans: [docs/WATSON_V1_1_AUDIO_MEETINGS_PLAN.md](docs/WATSON_V1_1_AUDIO_MEETINGS_PLAN.md) and [docs/WATSON_V1_2_COMMERCIALISATION_PLAN.md](docs/WATSON_V1_2_COMMERCIALISATION_PLAN.md).
