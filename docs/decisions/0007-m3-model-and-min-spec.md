# 0007 — M3 default model rule and min-spec gates

**Status:** Decided (Kieran, 2026-11-02 21:53 and 21:55 via CoS)
**Decides:** plan §14 #2 (default LLM) and the min-spec half of §14 #9 / §11.4
**Related:** `docs/ROADMAP.md`, `docs/eval.md`, `src/watson/intelligence/eval/thresholds.json`

## Decision

Kieran 2026-11-02 21:53 via CoS: maximise accuracy; default chosen by strict precision ≥ 0.85; 7–8B allowed in V1; min-spec p95 recorded, not gated; min-spec memory gate = completes without crash or kill; primary targets unchanged.

Kieran 2026-11-02 21:55 via CoS: the default model is the **largest** model that runs to completion on the 8 GB Mac with strict precision ≥ 0.85, however slow. Candidates, biggest first: `gemma2:9b` Q4, `llama3.1:8b` Q4, `qwen2.5:7b` Q4; go smaller only if a bigger one crashes or misses 0.85.

## What this changes

| Surface | Rule |
|---|---|
| Min-spec understand p95 | Recorded in `report.json` → `latency.understand_segment.p95_s`. Not a gate. |
| Min-spec memory | Gate is `completed_without_kill` = 1.0: every fixture day processed, worker exit 0, no runtime connection loss or kill. Peak worker+model is reported, not gated. |
| Min-spec consolidation | Still < 15 min (`consolidate_s` < 900). |
| Primary | Unchanged: p95 < 20 s, worker+model < 3 GB, consolidation < 15 min. |
| §11.2 quality | Strict decisions/commitments precision ≥ 0.85 and the other §11.2 metrics stay gating on **both** profiles. |
| Models | 7–9B Q4 allowed in V1. Per-model Ollama `timeout_s` / `num_ctx` (`watson eval --llm-model`, `WATSON_LLM_MODEL`). 7B-on-8 GB default timeout is 900 s. |
| Code default until bake-off | `qwen2.5:3b`. `llama3.2:3b` remains selectable. Device Ops sends the winner. |

This supersedes the 16:42 “hard 8 GB/3B gate” wording and the 16:43 “larger model parked post-POC” parking. The 8 GB Mac remains the testing laptop; W.A.T.S.O.N must complete eval on it without crash or kill.

## Not decided here

The bake-off winner (which of `gemma2:9b` / `llama3.1:8b` / `qwen2.5:7b` / a smaller 3B becomes `DEFAULT_LLM_MODEL`). Embeddings stay ADR 0001 provisional (`nomic-embed-text`).
