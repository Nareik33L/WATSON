# W.A.T.S.O.N eval harness (M3 / §11.2)

`watson eval` runs understanding → entity resolver → day consolidation → daily summary over every V1 day fixture and scores the result against that fixture’s `truth.yaml`.

V1 quality (plan Revision 3, Kieran via CoS 2026-11-03 10:38 UTC; #45 QA restatement) is **deal grouping accuracy ≥ 0.85** and **deal summary accuracy ≥ 0.85**, defined below so they can be computed and so trivial outputs cannot pass. Generic project / decision / commitment / achievement extract is parked after V1. **Until M3f**, the committed `thresholds.json` rows still select the bake-off model. **At M3f** those parked rows become report-only and the deal gate takes over. §3.5 understanding retrieval stays in V1. §11.3 privacy tests are unchanged.

Replay mode is the default. It is deterministic and offline. `FakeRuntime` is never selected unless you pass `--runtime fake`.

**Replay is a pipeline-only gate.** Committed recordings are hand-authored plausible model output (`tests/fixtures/eval/recordings.json`, `source: hand-authored-imperfect`). They are not copied from `truth.yaml` and they are not a quality claim for `qwen2.5:3b` (or any bake-off candidate). Replay CI checks: the pipeline finishes, consolidation is idempotent, each day has a non-empty daily summary with at least one resolvable `[c:id]` when the day has claims, and the hard 100%/0 structural metrics do not regress. Soft quality numbers are **measured and reported**; they do not fail replay CI. Do not lower `thresholds.json` if those numbers miss a target.

## Replay (CI default)

From a repo checkout, with the usual test key provider:

```bash
watson eval --mode replay --out /tmp/watson-eval
```

Writes `/tmp/watson-eval/report.json` and `report.md`. Exit status is `1` if the pipeline gate fails (zero fixtures, missing/empty aggregate, hard 100%/0 regression, missing summary, empty/uncited narrative on a day with claims, or consolidation counts that change on a second run).

`--strict` (and `--mode record`) also exit `1` when any §11.2 quality threshold is missed. With `--strict --measure-model`, the active §11.4 profile is also checked (`--profile auto|primary|min-spec`; auto selects `min-spec` when installed RAM is < 16 GiB). **Primary** gates understand p95 < 20 s, worker+model peak < 3 GB, and consolidation < 15 min. **Min-spec** records p95, peak, and `consolidate_s` but gates `completed_without_kill` (every fixture day processed, worker exit 0, no connection loss or kill). Deal grouping accuracy ≥ 0.85 and deal summary accuracy ≥ 0.85 stay gating on both once M3f lands; until then `--strict` still uses the committed `thresholds.json`. The 8 GB Mac is the testing laptop. Code baseline is `qwen2.5:3b` until the bake-off winner is sent (Kieran 21:53/21:55). The default model is the largest that completes on 8 GB and meets the active gate.

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

§11.4 profiles (Kieran 2026-11-02 21:53/21:55 and 2026-11-03 02:59 via CoS; quality gate restated 2026-11-03 10:38): **min-spec** (8 GB testing Mac) understand p95 and `consolidate_s` are recorded, not gated; memory gate is `completed_without_kill`. **Primary** (32 GB) understanding p95 < 20 s/segment, worker+model < 3 GB, consolidation < 15 min. Deal grouping accuracy ≥ 0.85 and deal summary accuracy ≥ 0.85 on both from M3f. 7–9B Q4 models are allowed; per-model timeout/num_ctx apply (3600 s for 7–9B). Engineering headroom on 8 GB (p95 < 30 s, peak < 3.3 GiB) is `report.json` → `headroom` and does **not** fail the gate. Bake-off without a config edit: `watson eval --llm-model gemma2:9b` (or `llama3.1:8b`, `qwen2.5:7b`, `llama3.2:3b`) / `WATSON_LLM_MODEL=...`. Default model = largest that completes on 8 GB and meets the active gate (`thresholds.json` until M3f; deal grouping / deal summary from M3f).

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

8 GB Ollama defaults: `context_window = 4096`, `intelligence.ollama.timeout_s = 300` for 3B (3600 s preset for 7–9B), `num_predict = 512`, `unload_between_phases = true` (unload at understanding-chat → understanding-embed → consolidation; cluster/extract/summary share one chat load; one model resident). Every chat and unload call sends `num_ctx` / `num_predict` / `keep_alive` so the KV cache is not resized. The understanding **v3** OCR cap is 3000 characters and new rows record `prompt_version = understanding/v3` with `pipeline_version = 3` (see `docs/config.md`). `report.json` holds metrics, ids, counts, timings, and hashes — never raw OCR, prompt bodies, or window titles. A terminal-failed understand segment is excluded from that day's consolidation and flagged as `skipped_understand` (and `understand_failed=N` on `working_days.consolidation_gap`); it does not fail the day.

## V1 §11.2 quality gate (Revision 3)

Canonical definitions live in `docs/WATSON_V1_ENGINEERING_PLAN.md` §11.2. This section is the harness-facing copy.

**Per-day match, then pool.** Key predicted clusters as `(fixture_day, cluster_id)`. Match that day's clusters to that day's gold deals, form pairs **within the day**, then **sum counts across days** and compute each ratio once. Per-day ratios are reported only.

**Corpus guidance** (not a guarantee): 5 days; ≥ 8 deals; ≥ 3 multi-touch; ≥ 4 same-company pairs; ≥ 2 look-alike name pairs; ≥ 20 % noise. The **fixture-validation test** is authoritative.

### Segment alignment

Gold segments are keyed by fixture `id` and defined by `frame_ids` on that day. Covering predicted segment = most frames of G (tie: earlier `started_at`, then smaller predicted `id`). Uncovered G is `missing`.

**Privacy bait** (`privacy_bait: true`): excluded from grouping accuracy. `missing` or `none` is correct. Bait in a deal or summary is a **hard privacy FAIL**.

**`privacy_bait` fixture check:** the flag is legal only if every frame of that segment exists and is `skipped` (`excluded_app` or `excluded_title`). Every such bait segment is present in the fixtures. After replay those frames/OCR are absent from all stored output (`frame_text`, observations, claims, evidence). A hard segment cannot be flagged bait to drop it from scoring.

### Deal-cluster matching (one algorithm, per day)

`none` / `missing` never match. On each day, pair that day's predicted clusters 1:1 with that day's gold deals by **greedy maximum overlap**: sort (P, G) by overlap descending, then gold `id` ascending, then predicted `id` ascending; take a pair if neither side is taken **on this day**. No Hungarian step. No cross-day matching.

### Deal grouping accuracy ≥ 0.85

`none` is its own class. Non-bait gold segments only.

- **Assignment accuracy** = correct / non-bait gold segments (`none`↔`none` is correct; deal↔`none`/`missing`/wrong/unmatched cluster is not).
- **Deal-segment recall** = gold deal≠`none` segments whose predicted cluster matches that gold deal / those segments. All-`none` is 0.
- **Pairwise F1** on same-day pairs. Gold pair = two segments on the same gold deal ≠ `none`. Predicted pair = two segments on the same predicted cluster ≠ `none`/`missing`. If `TP + FP = 0`, **pairwise precision = 0**. If `TP + FN = 0`, recall = 0. Vacuous P = R = 1 only when there are no predicted pairs **and** no gold deal in the corpus has two or more non-bait segments.

**Deal grouping accuracy** = `min(assignment_accuracy, deal_segment_recall, pairwise_F1)`.

### Citations → frame ids

`[c:…]` / claim id → evidence rows. `frame_id` if set, else every fixture frame id on that `segment_id`. Join to gold segments via `frame_ids` on the same day. Required-term checks read redacted `frame_text` of those frames.

### Deal summary accuracy ≥ 0.85

Slots are `what_happened` | `who_involved` | `next_step`. Gold rows are **atomic facts** with `required_terms` (and bounded aliases).

**Caps.** After normalize + stopword drop, a claim has at most **24 content tokens**. One claim covers **at most one** gold fact. A claim that also contains an exclusive required term of another gold fact that day does not cover.

**Cover.** Contiguous-phrase match of that one fact's required terms; slot modal rules; cited gold segments **overlap** `evidence_segments`.

- **Modals.** `what_happened` rejects `will` / `should` / `need to` / `going to`. `next_step` accepts `will` / `should` / `need to` and rejects done forms (`sent` vs `send`), hedges (`might`, `may`, `could`), and negation. Will vs may, or done vs planned, does not match.
- **Aliases.** Closed list: roster person forms; number forms of the same value; date forms of the same calendar date. **Weekday ≠ date** (`friday` ≠ `14 nov`). Per-fact aliases: ≤ 3 contiguous phrases, no stopwords, no term from another deal, no term exclusive to another fact. Fixture-checked.
- **Who:** `person_entities_match` to `person_id`. Shared full names need a company qualifier. Cited segments must overlap the who-fact's `evidence_segments`.
- **Unsupported** is citation failure only:
  1. Cite does not resolve to a fixture frame.
  2. **Matched deal:** no cited frame on that gold deal → unsupported. **Unmatched deal:** test (2) is **predicted-cluster membership only**. Cite on that cluster → precision miss, not unsupported. Cite off the cluster → unsupported. Never both.
  3. If paired: cited OCR lacks required terms, or cited segments miss `evidence_segments` → unsupported. Extra (unpaired) claims skip (3).
- Mutants: polarity, number/date, weekday↔date, `what_happened`+will/should, `next_step`+may/sent, keyword dump.

**Deal summary accuracy** = `min(precision, what_happened_recall, next_step_recall, who_involved_recall)` if unsupported is 0, else 0.

### Fixture-validation test (M3f, authoritative)

Compute these baselines **on the actual gold corpus** (no model). The build **fails** if any scores ≥ 0.85: all-`none`; one deal per day and per corpus; one deal per segment; group by app; **group by company**; empty summary; **keyword dump** (per gold fact, concatenate all OCR tokens of that deal that day, cite every segment).

The 12/1/1/1 Acme corpus (group-by-company F1 **0.905**) is a required **failing** input of this checker. Guidance counts do not replace it.

## Thresholds

One config: `src/watson/intelligence/eval/thresholds.json` (plan §11.2). **Until M3f** that file is the bake-off / `--strict` quality gate (parked Revision 2 rows, including strict decisions/commitments precision ≥ 0.85). **At M3f** those rows become **report-only**; the gate is deal grouping accuracy ≥ 0.85 and deal summary accuracy ≥ 0.85 (implies unsupported summary claims = 0). Structural hard gates that stay: claims-with-evidence = 100%; citations-resolvable = 100% over a **non-empty** citation set when the day has claims.

Do not lower `thresholds.json` in this docs change. Implementing the deal metrics is M3f.

`duplicate_observation_rate` measures **over-segmentation**, not per-segment extras (those are always 0: `observations` is `UNIQUE(segment_id, pipeline_version)`). An observation is a duplicate when it immediately follows an adjacent segment with the **same app** (`bundle_id`) **and** the **same window** (`window_title_norm`) **and** a token-set Jaccard similarity **≥ 0.5** against the previous description. Tokens are the case-folded, punctuation-stripped words from `normalize()`. Rotating the first verb still counts (paraphrase). A later revisit after a different app or window does not. The cutoff lives on `duplicate_observation_rate.jaccard_min` in `thresholds.json` (default 0.5). Load-time validation requires `0 < jaccard_min <= 1`. The rate threshold `< 0.05` is not lowered to match replay. The rate is parked after V1 (still measured).

## Item matching (parked: decisions, commitments, completed)

People and projects still use `names_match`: normalized equality or substring (so `Priya Natarajan` matches `Priya Natarajan (Acme)`).

Decisions, commitments, and completed / achievement items use wording-tolerant matching in `metrics.py`:

1. Expand `won't` / `n't` / `I'll`, then `normalize()` (case, parentheticals, punctuation).
2. Strip a leading first-person future (`i will `). That is a commitment-voice **prefix**, not a stopword — `will` and `i` stay content words everywhere else.
3. Drop `ITEM_STOPWORDS` (`a`, `an`, `and`, `is`, `the`, …). **Not** in the list: `will`, `i`, `from`, `to`, `by`, `or`.
4. Token-set Jaccard via the existing helper. Empty token sets score **0**, never 1.
5. A pair matches when the normalized strings are identical **or** (guards pass **and** Jaccard ≥ `ITEM_MATCH_JACCARD_MIN` = 0.7, inclusive).
6. Pairing is **1:1 on both sides**, greedy by **best** score (descending), then prediction order. A gold item is consumed once; duplicate predictions cannot each take a different gold.

Guards — any mismatch is a hard reject, even when Jaccard is high:

- **Polarity / modal / status.** If one side has a negation (`not`, `no`, `never`, `won't` / `didn't` / `isn't` / `hasn't` → `… not`, `unpublished`, …), a modal (`should`, `could`, `might`, `may`), or a different status lemma (`publish`/`published`, `send`/`sent`, `adopt`/`reject`, `agree`, `draft`, …) and the other side does not, there is no match. `section 3 not published` does not match `section 3 published`.
- **Relation markers.** `from`/`to`/`by`/`or` must be the same set. `to Priya` is not `from Priya`; `by Friday` is not `or Friday`.
- **Numbers, dates, weekdays.** Digits, `Q4`, number-words, month names, and `today`/`tomorrow`/`Friday`/… must be equal as sets when present on **either** side. `page 2` ≠ `page 1`; `14 Nov` ≠ `15 Nov`; `Friday` ≠ `tomorrow`.
- **Names.** A name is a **known V1 person or project**, or any **title-case** token that is not a skipped first-word opener. Mid-string title-case is always a name (`for Globex`, `with Jordan`). Sentence-initial capitals are names unless they are in `NON_NAME_OPENERS` (`Please`, `Need`, `Remember`, `Successfully`, `Finalised`, `Highlight`, `Agreed`, `Decided`) or a small leading-verb list (`Send`, `Publish`, …). `Jordan published…` therefore counts `Jordan`. Title nouns (`Internal`, `Guide`, `Section`) are never names.
  - **Completed / achievements:** if either side has a name, both must have the same set. `Jordan published Onboarding Guide section 3` does not support `Onboarding Guide section 3 published`. `Sent the revised Q4 pricing deck` does not support `Mina sent the revised Q4 pricing deck`.
  - **Decisions / commitments:** a name-less prediction may match a named gold item only when that pairing is **unambiguous** — exactly one gold candidate passes every other guard (polarity, dates, Jaccard, …). Two name-less `Send the slide outline tomorrow` rows cannot take both Priya's item and Mina's item. The bake-off `I will send the slide outline tomorrow` vs a single Priya gold stays a match.

`0.7` still rejects a one-content-token swap on a typical 5-token item (4/6 ≈ 0.667). True matches that must stay: `I will send the slide outline tomorrow` vs `Send Priya the slide outline tomorrow` (4/5 = 0.8 after the prefix); `Cedar Onboarding page 1 is published` vs `Cedar Onboarding page 1 published` (1.0).

This constant is **not** in `thresholds.json`. Changing it does not loosen the §11.2 quality gates.
