# 0001 — M0-A 8 GB runtime and model defaults

**Status:** Accepted (Ollama as V1.0 **dev** runtime); provisional (8 GB model defaults); remainder deferred  
**Date:** 2026-10-31  
**Decides:** §14 #2 (provisional 8 GB VLM / LLM / embedding defaults). Partially decides §14 #1: V1.0 **dev** runtime is Ollama; the packaged llama.cpp-versus-MLX comparison is deferred to M3 (plan §14 #10).  
**Related:** §0.1, §7.2, §11.2, §11.4, §14 #9, D2, D6, M0-A  
**Evidence:** `docs/benchmarks/2026-10-31.md`, `tools/bench/results/8gb-20261031T143012Z/` (`run.json` is the Mac dump; post-run notes are in `run.notes.json`)  
**Owner of deferred items:** Watson Lead

## Decision

On the V1 machine (Apple M1, **8 GB**, macOS 14.5):

| Role | Default | Status | Runtime | Quantisation | Notes |
|---|---|---|---|---|---|
| Embeddings | `nomic-embed-text` | Provisional | Ollama | fp16 | 14.99 texts/s. ≤ 0.5B as required by §0.1. |
| LLM | `llama3.2:3b` | **Provisional** | Ollama | q4_K_M | Chosen for latency and schema validity only. Pending the M3 quality gate (§14 #9). |
| VLM | **off** | Accepted for V1.0 8 GB | — | — | §0.1: VLM is off by default on 8 GB (OCR-only extraction). |

**§14 #1 (M0-A):** the V1.0 **development** runtime is **Ollama** — that is what this bake-off measured. The llama.cpp-versus-MLX comparison (the packaged in-process default, D6) is **not** decided here; see the deferred table.

`llama3.2:3b` is the **provisional** 8 GB LLM default. It is not a quality default. On **4/5 frames** it emitted requests or plans as `kind: outcome` with `assertion: observed`. That violates §7.2 (outcomes need visible evidence of completion) and the §11.2 target of **0** unsupported achievement / completion claims. The M3 quality gate (§14 #9) decides whether this model is good enough after prompt/schema work, or whether V1 ships OCR + segments + rule-based summaries.

### Plan B

**Plan B = `qwen2.5:3b`** if Llama fails the M3 outcome-versus-request gate after prompt/schema work.

`qwen2.5:3b` latency on this run (n=5, same five sample frames): **3/5 calls under 20 s**, mean **20.81 s/call**. Per-frame: 22.25, 18.43, 18.14, 19.79, 25.44 s. It more often marks unfinished requests `unconfirmed` (see f001_mail) and extracts more work facts. It is not the default because the **mean** misses the 20 s per-frame proxy.

### Deferred, with owner and milestone

| Item | Decision now | Owner | Milestone | Why |
|---|---|---|---|---|
| llama.cpp vs MLX (packaged in-process runtime, D6) | Explicitly **not** compared. Only Ollama ran. | Watson Lead | **M3** | §14 #1's either/or needs those adapters on this Mac. M0-A decides only the V1.0 **dev** runtime (Ollama). |
| 16 GB fallback profile (per-role 16 GB defaults) | **Deferred.** No 16 GB run. Keep the `16gb` candidate matrix; do not fetch it on this M1. | Watson Lead | **V1.2** | §0.1: 8 GB first. 16 GB+ is a configuration profile, not a V1 requirement. |
| Per-model RSS (weights + worker, §11.4 worker peak RSS < 3 GB) | **Not measured.** The 56–62 MiB column is the Ollama Python client. | Watson Lead | **M3** | Client RSS is not worker RSS. |
| 50 hand-scored fixture frames | **Not done.** This run used 5 sample frames. `scoring.csv` is blank. `tests/fixtures/day_acme_q4/sample50.txt` exists for the later pass. | Watson Lead | **M3** | §12 M0-A asked for 50 hand-scored frames; quality here is schema validity of 5 JSONs only. |
| True §11.4 understanding < 20 s/**segment** | **Not measured.** Times below are a **per-frame proxy**. | Watson Lead | **M3** | Segment input is merged ≤20 min text plus retrieved context (§3.5). Prompt tokens were not recorded. |
| Ollama version and model digests | **Not captured** on the original run. | Watson Lead | **M3** | Recorded as nulls in `run.notes.json`, not in `run.json`. Next bake-off must persist them. |

## Evidence

Hardware: macOS 14.5 / Apple M1 / 8 GB / arm64 / Python 3.12.16. Profile `8gb`. Five committed sample frames (`tools/bench/samples/frames`). All four Ollama candidates `ok`.

Harness settings reconstructed from the committed adapter live in `run.notes.json` (not `run.json`): temperature **0**, `num_ctx` **8192**, `keep_alive` **5m**, LLM prompt `bench-understanding-v1`, VLM prompt `bench-vlm-v1`. Ollama version, model digests, and prompt-token counts were **not recorded**.

### Speed (per-frame proxy, not segment latency)

| Candidate | Role | tokens/s | s/call or s/frame | vs §11.4 8 GB |
|---|---| ---:| ---:|---|
| `nomic-embed-text` | embeddings | — | 0.33 s/call, 14.99 texts/s | No numeric embed target. Fine. |
| `llama3.2:3b` | llm | 8.37 | **10.10 s/call** (mean of 5 frames) | **Meets the per-frame proxy** of < 20 s. Not a segment measurement. |
| `qwen2.5:3b` | llm | 8.43 | **20.81 s/call** mean; **3/5** frames < 20 s | **Misses** the per-frame proxy on the mean. |
| `qwen2.5vl:3b` | vlm | 8.01 | 12.57 s/frame | VLM stays **disabled** on 8 GB. (Would meet the 16 GB VLM < 15 s/frame target if enabled.) |

### Structured-output validity — and the Llama quality failure

Schema: LLM must return `{activity_type, description, claims[]}` with each claim `{kind, text, assertion, evidence_snippet}`. VLM must return `{visible_text, ui_description, app_guess, visual_density}`.

Both LLMs produced parseable, schema-shaped JSON on every frame. No invalid JSON, no missing required keys, no illegal `kind` / `assertion` enums.

**`llama3.2:3b` emitted requests as observed outcomes on 4/5 frames.** State that plainly: it treats unfinished asks and plans as completed work. That is a calibration failure under §7.2 and a §11.2 miss (unsupported completion claims must be 0). Frames:

| Frame | OCR / input | Llama claim | Why it fails |
|---|---|---|---|
| f001_mail | "Please send revised pricing by Friday." | `kind: outcome`, `assertion: observed`, "Revised pricing will be sent by Friday" | A request, not a completed send. |
| f002_doc | `dwell_seconds: 45` (viewing the onboarding guide) | `kind: outcome`, `assertion: observed`, "The document has been viewed for 45 seconds." | A view is not an outcome. |
| f003_chat | "You: I will send the revised deck" | `kind: outcome`, `assertion: observed`, "Revised deck to be sent" | A plan / commitment, not evidence it was sent. |
| f004_ide | Viewing `extract_frame` source | `kind: outcome`, `assertion: observed`, "Extracted frame from ocr text" | Reading code is not a completed extraction. |
| f005_slides | Keynote "Risks" slide | `kind: activity` about 45 s dwell only | The one frame that does **not** mint a false observed outcome. |

`qwen2.5:3b` on f001_mail tags the pricing send and the 14 Nov demo move `unconfirmed` rather than `observed`. It still paraphrases some evidence snippets. It is the named Plan B if Llama fails the M3 outcome-versus-request gate.

**`qwen2.5vl:3b`** returned valid VLM JSON on all five frames. On **f002_doc** it misread OCR "Still editing: Acme proposal appendix" as **"Stilled editing"**. Unused for V1 8 GB because the VLM stays off.

## §11.4 8 GB targets — what this run does not meet or did not measure

Do not treat the bake-off as a full §11.4 pass.

| Target | Result |
|---|---|
| Understanding < 20 s/segment | **Per-frame proxy only.** `llama3.2:3b` mean 10.10 s/frame (meets the proxy). `qwen2.5:3b` mean 20.81 s/frame, 3/5 under 20 s (misses the proxy mean). True segment latency is M3. |
| VLM disabled | Honoured as the V1 default. Measured anyway at 12.57 s/frame; not enabled on 8 GB. |
| Helper < 2 % CPU average | **Not measured** (M0-B). |
| OCR < 1 s/frame | **Not measured** (Vision lives in the helper). |
| Consolidation < 15 min | **Not measured.** |
| Worker peak RSS < 3 GB, one model resident | **Not measured.** Assigned to M3 (Watson Lead). |
| Machine remains usable for normal work | **Not recorded.** |
| Database growth < 20 MB/day | **Not measured.** |

16 GB profile targets were not in scope (deferred to V1.2 with the 16 GB fallback).

## Consequences

- 8 GB V1.0 **dev** stack: Ollama + provisional `nomic-embed-text` + provisional `llama3.2:3b` Q4_K_M, sequential load, VLM off.
- Do not treat Llama as quality-approved. Prompt and schema work before M3 must stop request-as-observed-outcome. If that gate fails, switch to Plan B `qwen2.5:3b`.
- Do not enable `qwen2.5vl:3b` on 8 GB.
- Do not pick llama.cpp or MLX from this ADR. Watson Lead runs that comparison at M3.
- §14 #9 remains M3.

Plan §7.1 still lists a ~7–9B packaged LLM against §0.1's ~3–4B 8 GB default. That inconsistency is a separate plan edit, not this bake-off.
