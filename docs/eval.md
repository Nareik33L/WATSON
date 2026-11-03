# W.A.T.S.O.N eval harness (M3 / §11.2)

`watson eval` runs understanding → entity resolver → day consolidation → daily summary over every V1 day fixture and scores the result against that fixture’s `truth.yaml`.

Replay mode is the default. It is deterministic and offline. `FakeRuntime` is never selected unless you pass `--runtime fake`.

**Replay is a pipeline-only gate.** Committed recordings are hand-authored plausible model output (`tests/fixtures/eval/recordings.json`, `source: hand-authored-imperfect`). They are not copied from `truth.yaml` and they are not a quality claim for `qwen2.5:3b` (or any bake-off candidate). Replay CI checks: the pipeline finishes, consolidation is idempotent, each day has a non-empty daily summary with at least one resolvable `[c:id]` when the day has claims, and the hard 100%/0 structural metrics do not regress. Soft §11.2 F1/recall/precision numbers are **measured and reported**; they do not fail replay CI. Do not lower `thresholds.json` if those numbers miss a target.

## Replay (CI default)

From a repo checkout, with the usual test key provider:

```bash
watson eval --mode replay --out /tmp/watson-eval
```

Writes `/tmp/watson-eval/report.json` and `report.md`. Exit status is `1` if the pipeline gate fails (zero fixtures, missing/empty aggregate, hard 100%/0 regression, missing summary, empty/uncited narrative on a day with claims, or consolidation counts that change on a second run).

`--strict` (and `--mode record`) also exit `1` when any §11.2 quality threshold is missed. With `--strict --measure-model`, the active §11.4 profile is also checked (`--profile auto|primary|min-spec`; auto selects `min-spec` when installed RAM is < 16 GiB). **Primary** gates understand p95 < 20 s and worker+model peak < 3 GB. **Min-spec** records p95 and peak but gates `completed_without_kill` (every fixture day processed, worker exit 0, no connection loss or kill) plus consolidation < 15 min. Strict precision ≥ 0.85 stays gating on both. The 8 GB Mac is the testing laptop. Code baseline is `qwen2.5:3b` until the bake-off winner is sent (Kieran 21:53/21:55).

Replay does not call Ollama and does not rewrite recordings after load.

## Record / run against local Ollama (8 GB Mac)

Device Ops, on the V1 machine (Apple Silicon, 8 GB), with Ollama serving the candidate (`qwen2.5:3b` baseline, or `--llm-model gemma2:9b` / `llama3.1:8b` / `qwen2.5:7b`) and `nomic-embed-text`:

```bash
# record model responses to a NEW file (never the committed fixture)
watson eval --mode record --runtime ollama \
  --recordings /tmp/watson-eval-ollama/recordings.json \
  --out /tmp/watson-eval-ollama

cat /tmp/watson-eval-ollama/report.md
```

`--mode record` writes recordings under `--out` (`recordings.json`) unless `--recordings` is set. It will not overwrite `tests/fixtures/eval/recordings.json` by default.

The report is always written (`report.json` and `report.md`), including on abort, hold (`ConsolidationWait`), or a missing runtime. Partial per-day rows stay in `days` with `error` set; `failure_reason` names the exception.

### Latency

`report.json` → `latency`:

| Field | Meaning |
|---|---|
| `complete` / `embed` | per-call n, p50/p95/max seconds |
| `understand_segment.per_segment_s` | one entry per segment that ran understanding |
| `timeouts` | `/api/chat` or `/api/embed` calls that hit `intelligence.ollama.timeout_s` |

§11.4 profiles (Kieran 2026-11-02 21:53 via CoS): **min-spec** (8 GB testing Mac) understand p95 is recorded, not gated; memory gate is `completed_without_kill`. **Primary** (32 GB) understanding p95 < 20 s/segment and worker+model < 3 GB. Consolidation < 15 min is gated on both. Strict precision ≥ 0.85 on both. 7–9B Q4 models are allowed; per-model timeout/num_ctx apply (900 s for 7B on 8 GB). Engineering headroom on 8 GB (p95 < 30 s, peak < 3.3 GiB) is `report.json` → `headroom` and does **not** fail the gate. Bake-off without a config edit: `watson eval --llm-model gemma2:9b` (or `llama3.1:8b`, `qwen2.5:7b`, `llama3.2:3b`) / `WATSON_LLM_MODEL=...`.

### §11.4 worker peak RSS

**Worker peak RSS** is the eval/worker process **plus** the resident model server, measured by Darwin **`phys_footprint`** (includes Metal/GPU allocations). Client `ru_maxrss` alone is not that number.

```bash
watson eval --mode record --runtime ollama \
  --measure-model \
  --out /tmp/watson-eval-ollama
```

`--measure-model` discovers `ollama` / `llama-server` PIDs and samples `phys_footprint` (via `footprint` on macOS, `VmRSS` on Linux) alongside the worker. `--rss-pid` adds an explicit PID (repeatable). The combined peak is `report.json` → `worker_plus_model_peak_bytes` (primary gate < 3 GB; min-spec reported, not gated). `completed_without_kill` is the min-spec memory gate. `worker_peak_bytes` is the eval/worker process alone. `peak_rss_bytes` remains the Python eval process `ru_maxrss`. `report.json` → `headroom` records the non-gating 8 GB targets (p95 < 30 s, peak < 3.3 GiB).

Manual check on the V1 Mac while a run is in progress:

```bash
# llama-server / Ollama runner PID from Activity Monitor
footprint -p <pid>    # look for phys_footprint
```

`intelligence.runtime` must stay `ollama`. Do not set `intelligence.runtime = "fake"` for this path.

8 GB Ollama defaults: `context_window = 4096`, `intelligence.ollama.timeout_s = 300` for 3B (900 s preset for 7–9B), `num_predict = 512`, `unload_between_phases = true` (unload at understanding-chat → understanding-embed → consolidation; cluster/extract/summary share one chat load; one model resident). Every chat and unload call sends `num_ctx` / `num_predict` / `keep_alive` so the KV cache is not resized. The understanding **v3** OCR cap is 3000 characters and new rows record `prompt_version = understanding/v3` with `pipeline_version = 3` (see `docs/config.md`). `report.json` holds metrics, ids, counts, timings, and hashes — never raw OCR, prompt bodies, or window titles. A terminal-failed understand segment is excluded from that day's consolidation and flagged as `skipped_understand` (and `understand_failed=N` on `working_days.consolidation_gap`); it does not fail the day.

## Thresholds

One config: `src/watson/intelligence/eval/thresholds.json` (plan §11.2). Hard gates: unsupported achievements = 0; claims-with-evidence = 100%; citations-resolvable = 100% over a **non-empty** citation set when the day has claims.

`duplicate_observation_rate` measures **over-segmentation**, not per-segment extras (those are always 0: `observations` is `UNIQUE(segment_id, pipeline_version)`). An observation is a duplicate when it immediately follows an adjacent segment with the **same app** (`bundle_id`) **and** the **same window** (`window_title_norm`) **and** a token-set Jaccard similarity **≥ 0.5** against the previous description. Tokens are the case-folded, punctuation-stripped words from `normalize()`. Rotating the first verb still counts (paraphrase). A later revisit after a different app or window does not. The cutoff lives on `duplicate_observation_rate.jaccard_min` in `thresholds.json` (default 0.5). Load-time validation requires `0 < jaccard_min <= 1`. The rate threshold `< 0.05` is not lowered to match replay.
