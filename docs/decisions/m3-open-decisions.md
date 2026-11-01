# M3 open decisions — measurement stub (do not decide)

**Status:** Open. This note lists the measurements required to decide plan §14 items that landed on M3. It does not pick a default.

**Related:** §11.2, §11.4, §12 M0-A deferrals, §14 #2 #7 #9 #10, ADR 0001, `docs/eval.md`

Run `watson eval --mode record --runtime ollama` on the 8 GB Mac as described in `docs/eval.md` before anyone fills the Decision rows.

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

Measurements needed:

- Llama §11.2 table vs a rule-based baseline (no `complete()` on clustering / extract / summary; claims and titles only)
- Unsupported completions on both paths
- Consolidation wall time and peak RSS on the 8 GB Mac
- Author judgement: are the LLM paragraphs worth the RSS and the hallucination risk?

**Decision:** _not taken_

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
| True understanding < 20 s/**segment** | Time `complete()` on merged segment text + retrieval, not the per-frame M0-A proxy. |
| Ollama version and model digests | Persist in the next `run.json`. |
