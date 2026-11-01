# WATSON eval harness (M3 / §11.2)

`watson eval` runs understanding → entity resolver → day consolidation → daily summary over every V1 day fixture and scores the result against that fixture’s `truth.yaml`.

Replay mode is the default. It is deterministic and offline. `FakeRuntime` is never selected unless you pass `--runtime fake`.

**Replay is a pipeline-only gate.** Committed recordings are hand-authored plausible model output (`tests/fixtures/eval/recordings.json`, `source: hand-authored-imperfect`). They are not copied from `truth.yaml` and they are not a quality claim for `llama3.2:3b`. Replay CI checks: the pipeline finishes, consolidation is idempotent, each day has a non-empty daily summary with at least one resolvable `[c:id]` when the day has claims, and the hard 100%/0 structural metrics do not regress. Soft §11.2 F1/recall/precision numbers are **measured and reported**; they do not fail replay CI. Do not lower `thresholds.json` if those numbers miss a target.

## Replay (CI default)

From a repo checkout, with the usual test key provider:

```bash
watson eval --mode replay --out /tmp/watson-eval
```

Writes `/tmp/watson-eval/report.json` and `report.md`. Exit status is `1` if the pipeline gate fails (zero fixtures, missing/empty aggregate, hard 100%/0 regression, missing summary, empty/uncited narrative on a day with claims, or consolidation counts that change on a second run).

`--strict` (and `--mode record`) also exit `1` when any §11.2 quality threshold is missed.

Replay does not call Ollama and does not rewrite recordings after load.

## Record / run against local Ollama (8 GB Mac)

Device Ops, on the V1 machine (Apple Silicon, 8 GB), with Ollama serving `llama3.2:3b` and `nomic-embed-text`:

```bash
# record model responses to a NEW file (never the committed fixture)
watson eval --mode record --runtime ollama \
  --recordings /tmp/watson-eval-ollama/recordings.json \
  --out /tmp/watson-eval-ollama

cat /tmp/watson-eval-ollama/report.md
```

`--mode record` writes recordings under `--out` (`recordings.json`) unless `--recordings` is set. It will not overwrite `tests/fixtures/eval/recordings.json` by default.

The report includes **wall_seconds** and **peak_rss_bytes** of the Python eval process. That is client RSS, not Ollama model weights. Record both:

| Measurement | Where |
|---|---|
| Eval process wall time | `report.json` → `wall_seconds` |
| Eval process peak RSS | `report.json` → `peak_rss_bytes` |
| Ollama model RSS | Activity Monitor / `sample` on `ollama` while the run is in progress |
| Combined worker+model RSS | sum of the two, against §11.4 worker peak RSS < 3 GB |
| Understanding latency per segment | time `watson eval --only day_acme_q4` and divide by segment count |

`intelligence.runtime` must stay `ollama`. Do not set `intelligence.runtime = "fake"` for this path.

## Thresholds

One config: `src/watson/intelligence/eval/thresholds.json` (plan §11.2). Hard gates: unsupported achievements = 0; claims-with-evidence = 100%; citations-resolvable = 100% over a **non-empty** citation set when the day has claims. `duplicate_observation_rate` is extras on the same activity segment (D3 / one observation per segment), not same-description revisits later in the day. These numbers are not lowered to match replay.
