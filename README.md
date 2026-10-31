# WATSON

WATSON is a local-first, offline-capable work-memory system for macOS (Apple Silicon first). A signed Swift helper captures the frontmost window; a Python supervisor, inference worker, and local dashboard coordinate through a single encrypted SQLite database. Frames are extracted with on-device OCR, grouped into activity segments, and recorded as claims with evidence. No component opens an outbound socket during normal operation.

This repository is at the layout-and-CI skeleton, the M0-A bake-off harness, and the M0-B Swift capture helper. Production runtime adapters and the dashboard land in later milestones.

## Development

Requires Python 3.12 and [uv](https://docs.astral.sh/uv/).

```bash
uv sync --group dev
uv run watson --version
uv run ruff check .
uv run mypy src/watson
uv run pytest
```

The M0-B capture helper (`watson-capture`) lives in [`helper/`](helper/README.md). It is SwiftPM-only (no Xcode project), macOS 14+, and builds with Command Line Tools. The Mac runbook — build, sign as `WATSON Dev`, first-run Screen Recording grant, 1-hour CPU, two-rebuild TCC check — is in that README.

```bash
cd helper && swift build && swift test
# signed Mac build (requires the WATSON Dev identity; see helper/README.md):
helper/scripts/build.sh
```

The M0-A model bake-off lives in [`tools/bench/`](tools/bench/README.md). On the 8 GB development Mac:

```bash
uv sync --group bench
uv run watson-bench fetch --profile 8gb --runtime ollama
uv run watson-bench run --profile 8gb --runtime ollama --report
```

## Engineering plan

Architecture decisions D1–D12, repository layout, and milestones are defined in [docs/WATSON_V1_ENGINEERING_PLAN.md](docs/WATSON_V1_ENGINEERING_PLAN.md) (Revision 2).
