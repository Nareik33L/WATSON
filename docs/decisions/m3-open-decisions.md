# M3 open decisions — measurement stub (do not decide)

**Status:** #9 decided (Kieran, 2026-11-02, option A; 21:53/21:55 via CoS supersede the 3B-only / hard 8 GB p95+peak gate). #2 partial (code baseline `qwen2.5:3b`; bake-off picks the largest 8 GB-capable model at ≥ 0.85; embeddings still provisional). #7 and #10 remain open. Canonical write-up: `docs/decisions/0007-m3-model-and-min-spec.md`.

**Related:** §11.2, §11.4, §12 M0-A deferrals, §14 #2 #7 #9 #10, ADR 0001, ADR 0007, `docs/eval.md`, `docs/ROADMAP.md`

Run `watson eval --mode record --runtime ollama` on the 8 GB Mac as described in `docs/eval.md` before anyone fills the remaining Decision rows. The 8 GB Mac is the testing laptop; min-spec must complete without crash or kill (21:53).

## §14 #2 — quality gate for provisional 8 GB models

ADR 0001 left `llama3.2:3b` and `nomic-embed-text` **provisional**. **21:53/21:55 (Kieran via CoS):** maximise accuracy; default chosen by strict precision ≥ 0.85; 7–8B allowed in V1. The default is the largest model that runs to completion on the 8 GB Mac with strict precision ≥ 0.85, however slow. Candidates, biggest first: `gemma2:9b` Q4, `llama3.1:8b` Q4, `qwen2.5:7b` Q4; go smaller only if a bigger one crashes or misses 0.85. Code baseline until the bake-off winner is sent: `qwen2.5:3b`. Embeddings remain provisional.

Measurements still owed:

- Per-metric table from `report.json` against `src/watson/intelligence/eval/thresholds.json` for each bake-off candidate
- Unsupported achievement / observed-completion count (must be 0 under §7.2 / §11.2)
- Whether each candidate completes on 8 GB (`completed_without_kill`) at precision ≥ 0.85
- Schema-invalid / structured-output retry rate

**Decision (Kieran, 2026-11-02 21:53/21:55 via CoS, partial):** rule above. Winner not yet chosen. Embeddings remain provisional (_not taken_). Larger models are **not** parked post-POC.

## §14 #7 — tier-2 text TTL (7 days proposed)

Decide after seeing whether a 7-day `expires_at` forces a re-consolidation that still has enough OCR to meet §11.2.

Measurements needed:

- Re-run `watson eval --mode replay` after advancing the clock past 7 days vs 14 / 30
- Count of segments whose `merged_text` is gone when the TTL fires
- Whether daily summaries still cite resolvable claims after that sweep

**Decision:** _not taken_

## §14 #9 — 8 GB LLM vs rule-based summaries

Decide whether V1 ships LLM consolidation or OCR + segments + rule-based summaries (LLM only for Ask WATSON).

**Decision (Kieran, 2026-11-02 16:41, option A; 21:53/21:55 via CoS):** V1 keeps LLM consolidation.

| Profile | Machine | Understanding p95 | Worker+model peak (`phys_footprint`) | Consolidation | Precision (decisions/commitments) |
|---|---|---:|---:|---:|---:|
| **primary** | 32 GB target laptop | < 20 s/segment (gated) | < 3 GB (gated) | < 15 min | ≥ 0.85 |
| **min-spec** | 8 GB testing Mac | recorded, not gated | reported, not gated; gate = `completed_without_kill` | < 15 min | ≥ 0.85 |

One model resident on both. Encoded in `src/watson/intelligence/eval/thresholds.json` → `profiles`. `watson eval --strict --measure-model` checks the active profile. Auto-select: `min-spec` when installed RAM is < 16 GiB (covers ≤ 8 GB); `primary` otherwise. Override with `--profile primary` or `--profile min-spec`.

**21:53 via CoS:** maximise accuracy; default chosen by strict precision ≥ 0.85; 7–8B allowed in V1; min-spec p95 recorded, not gated; min-spec memory gate = completes without crash or kill; primary targets unchanged.

**21:55 via CoS:** default = largest model that completes on 8 GB at ≥ 0.85, however slow. Candidates, biggest first: `gemma2:9b` Q4, `llama3.1:8b` Q4, `qwen2.5:7b` Q4.

## §14 #10 — llama.cpp vs MLX (packaged in-process default)

M0-A measured Ollama only. The packaged runtime (D6) is still open.

Measurements needed (same five fixtures, same prompts, same thresholds.json):

| Runtime | understanding s/segment | consolidation wall | peak RSS (weights + worker) | §11.2 pass? |
|---|---:|---:|---:|---|
| Ollama `llama3.2:3b` | | | | |
| llama.cpp | | | | |
| MLX | | | | |

Record Ollama version and model digests this time (`ollama list --digest`).

**Decision:** _not taken_

## M0-A deferrals still owed

| Item | Measurement still missing |
|---|---|
| Three-runtime comparison | The table in §14 #10. Owner: Watson Lead. |
| Per-model RSS | Model-process RSS + eval-process `peak_rss_bytes`, not the 56–62 MiB Python client from M0-A. |
| 50 hand-scored frames | Score `tests/fixtures/day_acme_q4/sample50.txt` by hand; fill `scoring.csv`. Schema-valid JSON on 5 frames is not this. |
| True understanding latency /segment | Time `complete()` on merged segment text + retrieval, not the per-frame M0-A proxy. Min-spec (8 GB) p95 recorded; primary (32 GB) p95 < 20 s (§14 #9). |
| Ollama version and model digests | Persist in the next `run.json`. |
