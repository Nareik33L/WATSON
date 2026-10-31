# 0001 / 0002 — M0-A 8 GB runtime and model defaults

**Status:** Accepted (8 GB model defaults); package runtime deferred  
**Date:** 2026-10-31  
**Decides:** Engineering plan §14 #1 (package runtime: llama.cpp vs MLX) and §14 #2 (default VLM / LLM / embedding models and quantisations)  
**Related:** §0.1, §11.4, D2, D6, M0-A  
**Evidence:** `docs/benchmarks/2026-10-31.md`, `tools/bench/results/8gb-20261031T143012Z/`

## Decision

On the V1 machine (Apple M1, **8 GB**, macOS 14.5) the 8 GB profile defaults are:

| Role | Default | Runtime (dev) | Quantisation | Notes |
|---|---|---|---|---|
| Embeddings | `nomic-embed-text` | Ollama | fp16 | 14.99 texts/s. ≤ 0.5B as required by §0.1. |
| LLM | `llama3.2:3b` | Ollama | q4_K_M | 8.37 tok/s, **10.10 s/call**. Schema-valid JSON on all 5 frames. Meets §11.4 understanding < 20 s/segment. |
| VLM | **off** | — | — | §0.1: VLM is off by default on 8 GB (OCR-only extraction). Not a V1 default. |

**Package runtime (llama.cpp vs MLX) is not decided.** This bake-off ran **Ollama only**. llama.cpp and MLX were not installed and produced no rows. Ollama remains the development runtime on this Mac. The packaged in-process default (D6) stays open until an 8 GB llama.cpp and/or MLX run exists.

**16 GB fallback** is the existing `16gb` candidate matrix (Qwen2.5 7B / Llama 3.1 8B, Qwen2.5-VL 7B, nomic-embed-text / mxbai-embed-large). It was **not measured** on this machine and is not a V1 requirement (§0.1).

§14 #9 (is the 8 GB LLM good enough for consolidation, or do we ship OCR + segments + rule-based summaries?) stays an **M3** decision. This ADR only picks the 8 GB *candidates* to carry into M3.

## Evidence

Hardware: macOS 14.5 / Apple M1 / 8 GB / arm64 / Python 3.12.16. Profile `8gb`. Five committed sample frames. All four Ollama candidates `ok`. Hand-scoring CSV is **blank** — quality below is structured-output validity of `frames/*.json`, not human `visible_ok` / hallucination scores.

### Speed

| Candidate | Role | tokens/s | s/call or s/frame | vs §11.4 8 GB |
|---|---| ---:| ---:|---|
| `nomic-embed-text` | embeddings | — | 0.33 s/call, 14.99 texts/s | No numeric embed target. Fine. |
| `llama3.2:3b` | llm | 8.37 | **10.10 s/call** | **Meets** understanding < 20 s/segment. |
| `qwen2.5:3b` | llm | 8.43 | **20.81 s/call** | **Misses** understanding < 20 s/segment. |
| `qwen2.5vl:3b` | vlm | 8.01 | 12.57 s/frame | VLM must stay **disabled** on 8 GB. (Would meet the 16 GB VLM < 15 s/frame target if enabled.) |

Ollama-adapter peak RSS (56–62 MiB) is the Python client, not worker + model weights. It is **not** a measurement of §11.4 worker peak RSS < 3 GB.

### Structured-output validity (all 5 frames)

Schema: LLM must return `{activity_type, description, claims[]}` with each claim `{kind, text, assertion, evidence_snippet}`. VLM must return `{visible_text, ui_description, app_guess, visual_density}`.

- **Both LLMs** produced parseable, schema-shaped JSON on every frame. No invalid JSON, no missing required keys, no illegal `kind` / `assertion` enums.
- **`llama3.2:3b`** is thinner: often one claim, generic descriptions ("Email conversation", "IDE activity"). It sometimes treats `dwell_seconds` / `line_changes` as the evidence instead of OCR, and under-extracts work facts (demo date, pricing commitment, Q4 risks).
- **`qwen2.5:3b`** extracts more work-relevant claims (pricing by Friday, demo 14 Nov, revised deck, Q4 risks) and is closer to the "stronger at structured output" candidate note. Evidence snippets are sometimes paraphrases. It is the one that **misses the 20 s gate**.
- **`qwen2.5vl:3b`** returned valid VLM JSON on all five frames and read the visible text (mail, onboarding guide, chat, extract_frame snippet, Keynote risks slide). Unused for V1 8 GB because the VLM stays off.

Llama is the 8 GB default because it is the only LLM that is both schema-valid and inside the latency target. Qwen is the quality reserve if M3's §11.2 metrics fail on Llama and a ~21 s/segment call is acceptable.

## §11.4 8 GB targets — what this run does not meet or did not measure

Flagged explicitly (do not treat the bake-off as a full §11.4 pass):

| Target | Result |
|---|---|
| Understanding < 20 s/segment | **`qwen2.5:3b` misses at 20.81 s/call.** `llama3.2:3b` meets at 10.10 s/call. |
| VLM disabled | Honoured as the V1 default. The VLM *was* measured (12.57 s/frame) and is not enabled on 8 GB. |
| Helper < 2 % CPU average | **Not measured** (M0-B capture spike). |
| OCR < 1 s/frame | **Not measured** (Vision lives in the helper). |
| Consolidation < 15 min | **Not measured** (no day consolidation in M0-A). |
| Worker peak RSS < 3 GB, one model resident | **Not measured.** Adapter RSS is the client process only. |
| Machine remains usable for normal work | **Not recorded.** |
| Database growth < 20 MB/day | **Not measured.** |

16 GB profile targets (VLM < 15 s/frame, consolidation < 10 min, worker RSS < 6 GB) were not in scope.

## Consequences

- Config / model manifest for the 8 GB V1 profile: `nomic-embed-text` + `llama3.2:3b` Q4_K_M, sequential load, VLM off.
- Keep `qwen2.5:3b` in the candidate matrix for M3 if Llama's thinner claims fail §11.2.
- Do not enable `qwen2.5vl:3b` on 8 GB. Revisit only on a 16 GB profile or an explicit "rich visual understanding" setting (D2).
- Do not pick llama.cpp vs MLX from this ADR. Next bake-off row that decides §14 #1 must run those adapters on this Mac (or accept Ollama as a V1-only dev runtime and defer the package runtime to V1.2).
- §14 #9 remains M3.

## Plan B (not taken)

Defaulting the 8 GB LLM to `qwen2.5:3b` for richer claims. Rejected because it misses the published 20 s/segment target on the machine V1 has to run on.
