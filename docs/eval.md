# WATSON eval harness (M3 / §11.2)

`watson eval` runs understanding → entity resolver → day consolidation → daily summary over every V1 day fixture and scores the result against that fixture’s `truth.yaml`.

Replay mode is the default. It is deterministic and offline. `FakeRuntime` is never selected unless you pass `--runtime fake`.

## Replay (CI default)

From a repo checkout, with the usual test key provider:

```bash
watson eval --mode replay --out /tmp/watson-eval
```

Writes `/tmp/watson-eval/report.json` and `report.md`. Exit status is `1` if any §11.2 threshold is missed, if a hard 100%/0 metric regresses, if consolidation counts change on a second run, or if a fixture day has no daily summary.

Committed recordings live at `tests/fixtures/eval/recordings.json`. Replay does not call Ollama.

## Record / run against local Ollama (8 GB Mac)

Device Ops, on the V1 machine (Apple Silicon, 8 GB), with Ollama serving `llama3.2:3b` and `nomic-embed-text`:

```bash
# record model responses and write a new recordings file
watson eval --mode record --runtime ollama \
  --recordings /tmp/watson-eval-ollama/recordings.json \
  --out /tmp/watson-eval-ollama

# score the captured run (same out dir already has the report)
cat /tmp/watson-eval-ollama/report.md
```

The report includes **wall_seconds** and **peak_rss_bytes** of the Python eval process. That is client RSS, not Ollama model weights. Record both:

| Measurement | Where |
|---|---|
| Eval process wall time | `report.json` → `wall_seconds` |
| Eval process peak RSS | `report.json` → `peak_rss_bytes` |
| Ollama model RSS | Activity Monitor / `sample` on `ollama` while the run is in progress |
| Combined worker+model RSS | sum of the two, against §11.4 worker peak RSS < 3 GB |
| Understanding latency per segment | not in this report; time `watson eval --only day_acme_q4` and divide by segment count, or log per-call times on a later pass |

`intelligence.runtime` must stay `ollama`. Do not set `intelligence.runtime = "fake"` for this path.

## Thresholds

One config: `src/watson/intelligence/eval/thresholds.json` (plan §11.2). Hard gates: unsupported achievements = 0; claims-with-evidence = 100%; citations-resolvable = 100%.
