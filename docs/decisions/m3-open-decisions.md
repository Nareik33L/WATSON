# M3 open decisions — measurement stub (do not decide)

**Status:** #9 decided (Kieran, 2026-11-02, option A). #2, #7 and #10 remain open. This note lists the measurements required to decide the remaining plan §14 items that landed on M3.

**Related:** §11.2, §11.4, §12 M0-A deferrals, §14 #2 #7 #9 #10, ADR 0001, `docs/eval.md`

Run `watson eval --mode record --runtime ollama` on the 8 GB Mac as described in `docs/eval.md` before anyone fills the remaining Decision rows.

## §14 #2 — quality gate for provisional 8 GB models

ADR 0001 left `llama3.2:3b` and `nomic-embed-text` **provisional**. Decide only after a recorded Ollama eval on the five V1 day fixtures.

Measurements needed:

- Per-metric table from `report.json` against `src/watson/intelligence/eval/thresholds.json`
- Unsupported achievement / observed-completion count (must be 0 under §7.2 / §11.2)
- Whether `qwen2.5:3b` (Plan B in ADR 0001) clears the same gate if Llama misses it
- Schema-invalid / structured-output retry rate

**Decision:** _not taken_

## §14 #7 — tier-2 text TTL (7 days proposed)

Decide after seeing whether a 7-day `expires_at` forces a re-consolidation that still has enough OCR to meet §11.2.

Measurements needed:

- Re-run `watson eval --mode replay` after advancing the clock past 7 days vs 14 / 30
- Count of segments whose `merged_text` is gone when the TTL fires
- Whether daily summaries still cite resolvable claims after that sweep

**Decision:** _not taken_

## §14 #9 — 8 GB LLM vs rule-based summaries

Decide whether V1 ships LLM consolidation or OCR + segments + rule-based summaries (LLM only for Ask WATSON).

**Decision (Kieran, 2026-11-02 16:41, option A; supersedes the 16:35 8 GB-only wording):** V1 keeps LLM consolidation. The model is the same on both hardware profiles.

| Profile | Machine | Understanding p95 | Worker+model peak (`phys_footprint`) | Consolidation | Precision (decisions/commitments) |
|---|---|---:|---:|---:|---:|
| **primary** | 32 GB target laptop | < 20 s/segment | < 3 GB | < 15 min | ≥ 0.85 |
| **min-spec** | 8 GB development Mac | < 35 s/segment | < 3.5 GB | < 15 min | ≥ 0.85 |

One model resident on both. The M3 gate is measured on the 8 GB Mac against the **min-spec** targets.

Encoded in `src/watson/intelligence/eval/thresholds.json` → `profiles`. `watson eval --strict --measure-model` checks p95 latency and peak memory against the active profile. Auto-select: `min-spec` when installed RAM is < 16 GiB (covers ≤ 8 GB); `primary` otherwise. Override with `--profile primary` or `--profile min-spec`.

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
| True understanding latency /segment | Time `complete()` on merged segment text + retrieval, not the per-frame M0-A proxy. Min-spec (8 GB) p95 < 35 s; primary (32 GB) p95 < 20 s (§14 #9). |
| Ollama version and model digests | Persist in the next `run.json`. |
