# W.A.T.S.O.N V1 — Engineering Plan (Revision 3)

**Supersedes:** W.A.T.S.O.N V1 Engineering Plan Revision 2; W.A.T.S.O.N V1 Product Requirements & Engineering Specification v1.1
**Status:** Proposed engineering baseline
**Platform:** macOS (Apple Silicon first), local-first, offline-capable
**Core language:** Python, with a small Swift capture helper

**Changelog (2026-11-03, QA on #47 @d6325e4):** Copies that cover the same facts with overlapping citations count once before matching, regardless of text (no Jaccard / 3-fact gate). Sliding 6-term/stride-2 windows over unique or raw terms in every slot must score < 0.85. §14 / D8 / D10 / D11 unchanged.

**Changelog (2026-11-03, QA on #47 @f0a84e9):** Pair claims to gold facts by maximum 1:1 matching, not the first covered fact in file order. Honest paraphrases that mention an earlier fact still take their own fact. Packed near-duplicates (same covered-fact set, overlapping citations) count once; extra copies stay in the precision denominator. Sliding 6-term/stride-2 keyword dumps must score < 0.85. §14 / D8 / D10 / D11 unchanged.

**Changelog (2026-11-03, QA on #47 @855dd6a):** Duplicate claims collapse by what they cover, not by text: same deal, same slot, overlapping cited segments, and the same best gold fact count once. Extra copies are not true positives and do not raise precision. Keyword-dump (duplicate-what) variants (extra token, dropped last token, mixed fillers, need-to, three copies, ≥2-channel, reversed order, shifted window) must score < 0.85. §14 / D8 / D10 / D11 unchanged.

**Changelog (2026-11-03, QA on #47 @089ee2a):** Identical or near-identical claims in the same deal and slot count once before 1:1 pairing. Keyword dump (duplicate-what) is a required negative baseline. §14 / D8 / D10 / D11 unchanged.

**Changelog (2026-11-03, QA on #47 @03e3124):** Per-fact `aliases` may use `date:` / `amount:` keys for a slot value that is not a required term. §14 / D8 / D10 / D11 unchanged.

**Changelog (2026-11-03, QA on #49 @a217ae6):** Each stored summary paragraph carries `deal_id`. Today cards bind blocks from that id, per deal; a count mismatch no longer blanks every card. Legacy rows without paragraph `deal_id` still render. Claimless deals stay empty; fallback-only deals keep their cited text.

**Changelog (2026-11-03, QA on #49 @abdbf89):** Deal groups show on Today only (History and `/days` stay unchanged). Cards bind to summary blocks by stored `deal_id` / deal order, never by title. Deal insert stamps are offset so same-second UUID order cannot swap cards.

**Changelog (2026-11-03, QA on #45 @7812803):** Exclusive-term rule scoped to same-slot contradictions only; honest-summary positive control ≥ 0.85. Keyword dump split into over-cap and within-cap. Group-by-company uses roster `company` in the window title only. Fixture-validation and baselines are pytest under `tests/` in existing Linux CI. §14 / D8 / D10 / D11 unchanged.

**Changelog (2026-11-03, QA on #45 @de6f062):** Claim length and one-claim-one-fact caps; keyword-dump baseline. Per-slot modals (done vs planned, will vs may). Aliases bounded; weekday ≠ date. Correct claims must overlap gold `evidence_segments`. Unmatched-deal citation test (2) is cluster membership only. Same-company "guarantee" replaced by a computed fixture-validation test (the 12/1/1/1 corpus must fail it). `privacy_bait` only on frames already `skipped` as excluded. §14 / D8 / D10 / D11 unchanged.

**Changelog (2026-11-03, QA on #45 @9b43417):** Excluded-app bait is correct when `missing`/`none` and a hard privacy FAIL if it appears in a deal or summary. Gold summaries are atomic facts with required terms (not exact strings); unsupported is citation failure only. Deal matching is per fixture day, then counts pool. Look-alike floor raised so group-by-company scores < 0.85. §14 / D8 / D10 / D11 unchanged.

**Changelog (2026-11-03, QA on #45):** §8 restores "Nothing else leaves the app"; deal-summary export is copy-as-Markdown or `.md` only. §11.2 metrics restated so they are computable and not gameable (segment alignment by fixture id / `frame_ids`; one greedy max-overlap deal match; pooled pairwise F1 + assignment + deal-segment recall; summary precision and recall; fixture minimums; trivial-output check). Parked `thresholds.json` rows become report-only at M3f; until then the committed file still selects the bake-off model. §3.5 understanding retrieval stays in V1. §14 / D8 / D10 / D11 unchanged.

**Changelog (2026-11-03 10:38 UTC, Kieran via CoS):** Revision 3. V1 is screenshot capture → per-segment understanding → grouping the day's activity by sales opportunity/deal → a daily summary per deal (what happened, who was involved, next step; each claim cited to evidence). Emails, calls, docs and CRM screens about the same deal across the day link to **one** deal. Generic project, decision, commitment and achievement extraction and their reports move to §16 Parked after V1 (not deleted). §11.2 quality gate is **deal grouping accuracy ≥ 0.85** and **deal summary accuracy ≥ 0.85** (definitions in §11.2). Privacy, local-first, and §11.3 tests are unchanged. 8 GB min-spec rule is unchanged: must complete without an OOM kill; `consolidate_s` and understand p95 are recorded only; 7–9B timeout is 3600 s. 32 GB primary targets are unchanged. Default model is the largest model that completes on 8 GB and meets the §11.2 gate.

**Changelog (2026-10-31):** M0-A bake-off (`docs/decisions/0001-m0a-8gb-runtime-and-models.md`) — §14 #1 is Ollama for V1.0 **dev**; llama.cpp-versus-MLX, per-model RSS and the 50 hand-scored frames move to M3 (Watson Lead); 16 GB fallback moves to V1.2 (§0.1). §7.1 package LLM class is ~3–4B on 8 GB (V1.0); ~7–9B is the V1.2 16 GB profile.

**Changelog (2026-11-02):** §14 #9 option A (Kieran) — V1 keeps LLM consolidation. Two hardware profiles, same model: **primary** (32 GB target laptop) keeps original §11.4 understanding p95 < 20 s/segment and worker+model peak < 3 GB; **min-spec** (8 GB testing Mac) is p95 < 35 s and peak < 3.5 GB. Consolidation stays < 15 min on both and is gated in `thresholds.json`. Strict decisions/commitments precision ≥ 0.85 on both. Engineering headroom on 8 GB (p95 < 30 s, peak < 3.3 GiB) is reported in `report.json` → `headroom` and is not a gate. **16:42:** the 8 GB Mac is the testing laptop; W.A.T.S.O.N must fit and run on it on the 3B model within min-spec targets. That is a **hard compatibility gate for every release**, whatever model is later chosen for 32 GB, not optional. **16:43:** V1/POC stays on `llama3.2:3b` on both profiles. A larger model for the 32 GB primary profile is parked post-POC (`docs/ROADMAP.md`). **Kieran 2026-11-02:** if `llama3.2:3b` is still < 0.85 strict precision after the quality-fix PR, switch the default to `qwen2.5:3b` and rerun; if qwen also misses 0.85 or breaks the 8 GB targets, escalate to Kieran.

**Changelog (2026-11-02 21:53/21:55 via CoS):** Kieran 2026-11-02 21:53 via CoS: maximise accuracy; default chosen by strict precision ≥ 0.85; 7–8B allowed in V1; min-spec p95 recorded, not gated; min-spec memory gate = completes without crash or kill; primary targets unchanged. **21:55:** the default model is the largest model that runs to completion on the 8 GB Mac with strict precision ≥ 0.85, however slow. Candidates, biggest first: `gemma2:9b` Q4, `llama3.1:8b` Q4, `qwen2.5:7b` Q4; go smaller only if a bigger one crashes or misses 0.85. Code default stays `qwen2.5:3b` until the bake-off winner is sent. Larger models are no longer parked post-POC.

**Changelog (2026-11-03 02:59 via CoS):** min-spec `consolidate_s` is recorded, not gated (timing is not a min-spec issue). Primary still gates consolidation < 15 min. 7–9B / unknown-model timeout preset is 3600 s.

This document keeps the privacy principles, local-first non-goals, and capture/process architecture of Revision 2. Revision 3 narrows *what* V1 ships: a personal sales-activity journal, not the full generic memory-and-reports product in v1.1. The build changes from Revision 2 still fall into four groups; a fifth records the V1 scope trim:

1. **Privacy exposure is reduced by an order of magnitude** — frames are deleted minutes after capture rather than at end of day, only the frontmost window is captured by default, and extracted text is itself tiered and expiring.
2. **The AI pipeline is restructured** around OCR-first extraction and *activity segments* rather than per-screenshot vision-model calls. This is cheaper, more accurate, and gives verbatim evidence for provenance.
3. **The process model and platform integration are made realistic** — a signed Swift helper owns capture, inference runs in an isolated worker, the menu bar shows capture state, and packaging/TCC-permission pitfalls are addressed up front.
4. **Development is driven by a replay harness and a synthetic golden dataset**, so the memory and reporting engine can be built and measured before any real screen is ever captured — which is what the v1.1 "immediate next step" asked for, but the v1.1 milestones did not deliver.
5. **V1 product scope is screenshot, understand, group by deal, summarise the day** — emails, calls, docs and CRM screens about the same sales opportunity across the day must resolve to one deal; each deal gets a cited daily summary. Generic project, decision, commitment and achievement extraction and their reports are parked after V1 (§16), not deleted.

---

## 0. Summary of changes from v1.1

| Area | v1.1 | Revision 2 / 3 | Why |
|---|---|---|---|
| Capture scope | Full screen(s), app exclusions | **Frontmost window only** by default; full-display as opt-in | Less sensitive data, smaller images, exact app attribution, no multi-monitor stitching |
| Raw screenshot lifetime | Until end-of-day processing | **Deleted on successful frame extraction** (target median < 5 min); end-of-day only sweeps stragglers | Minimises exposure window; removes the "day's screenshots on disk" liability |
| Primary extractor | Vision-language model per screenshot | **Apple Vision OCR** + window metadata per frame; VLM used selectively on frames OCR cannot explain | 10–50× cheaper, far less hallucination, verbatim text as evidence |
| Unit of understanding | One observation per screenshot | **Activity segment** (contiguous frames in same app/window/content) → one observation | Dedup falls out naturally; LLM sees coherent context, not fragments |
| Extracted text retention | Vague ("deleted or consolidated") | Explicit **four-tier retention model** with per-tier TTLs | Deleting the image is meaningless if its full text lives forever |
| Process model | Single Python app | **Supervisor + Swift capture helper + inference worker + dashboard**, coordinated through SQLite | Inference crash/OOM must not kill capture or UI; TCC permission needs a stable signed binary |
| Capture visibility | Dashboard status | **Menu bar item** with live state, plus dashboard | Dashboard is not visible when browser is closed; "no covert surveillance" requires always-visible state |
| Inference runtime | Ollama | Adapter interface; **in-process runtime (llama.cpp or MLX) for the packaged app**, Ollama permitted in dev | Ollama is a separate server binary that is awkward to bundle, sign and run offline |
| Search | Separate embedding store | **SQLite FTS5 + sqlite-vec** in the same encrypted database | One file, one backup, one deletion path |
| Memory model | Observations → summaries | **Claims with evidence and assertion type**, user confirmation/correction loop | Provenance and "observed vs inferred" become first-class rows, not prose |
| Day boundary | Implicit midnight | Configurable **work-day end**, catch-up consolidation on next launch, explicit day states | Laptops get closed; summaries must still appear |
| Milestones | Dashboard with sample data before any pipeline | **Hardware/capture spikes first, then pipeline on fixtures, then dashboard on real pipeline output, then live capture** | De-risks the two biggest unknowns early and avoids building UI against invented data shapes |
| AI quality | "Evaluate whether…" | **Golden synthetic dataset, replay harness, numeric thresholds in CI** | A hallucination rate you cannot measure is one you cannot reduce |
| V1 product output (Rev 3) | Generic projects, decisions, commitments, achievements, weekly/project reports | **Screenshot → understand → group the day by sales deal → cited daily summary per deal** | Author's V1 is a sales-activity journal; generic extract/reports parked in §16 |

Everything in v1.1 §3.2 (exclusions) and §21 (principles) stands unchanged. v1.1 §22 product definition is narrowed for V1 as §0.2; the parked remainder is §16.

### 0.1 Release framing: V1 is the build that works for its author

V1.0 is a **personal build**: W.A.T.S.O.N running every working day on the developer's own Mac (Apple Silicon, **8 GB**, macOS 14+), capturing the screen and producing a **daily deal summary** the developer actually uses. It is complete when the author relies on it, not when it can be handed to someone else. Meeting/audio intelligence follows as **V1.1**, and everything needed to sell and distribute it to other people (packaging, notarisation, licensing, onboarding, customer documentation) is **V1.2 — Commercialisation** (`WATSON_V1_2_COMMERCIALISATION_PLAN.md`). The full ladder is in `docs/ROADMAP.md`.

Consequences for this plan:

- **Hardware target is 8 GB first.** The VLM is off by default on 8 GB (OCR-only extraction, D2 already allows this); the LLM is a ~3–4B-class model at Q4 with a 4–8k context; the embedding model is ≤ 0.5B; models load sequentially, never together. 16 GB+ settings are a configuration profile exercised by the bench harness, not a V1 requirement. The 16 GB figures elsewhere in this document are retained as the profile targets for V1.2.
- **Packaging in V1 is "runs from the repo on this Mac"**: `uv run watson run`, a launchd LaunchAgent for start-at-login, and the signed helper. No PyInstaller bundle, no clean-machine install, no Intel.
- **Security controls that protect the author's own data stay in V1** (encrypted DB with Keychain key, loopback-only dashboard with token auth, egress guard, redacted logs, spool permissions). Controls whose purpose is third-party assurance (SBOM, threat-model document, installation/removal instructions, security documentation for customers and employers) move to V1.2.
- **Dogfooding is the acceptance test.** From M5 onward the developer runs W.A.T.S.O.N daily with real work on screen; the synthetic fixtures remain the regression gate, but the definition of done for V1 is the author's own **daily per-deal summaries**, generated by W.A.T.S.O.N and edited rather than rewritten.
- **Milestones:** M0–M2 are done and tagged (`v0.0.0-m0`, `v0.1.0-m1`, `v0.2.0-m2`). M3a–M3e and M4a–M4b have landed on main (untagged). Remaining V1 work is M3f (deal grouping + per-deal daily summary), M5 (live capture), and M7 (hardening). M6 (weekly/monthly/project/achievement reports and Ask W.A.T.S.O.N) is dropped from V1 and parked in §16. Former M7 packaging and M8 documentation remain V1.2.

### 0.2 V1 product scope (Revision 3)

Kieran, 2026-11-03 10:38 UTC via CoS: V1 "basically just needs to screenshot and group my sales activity so if someone emails me throughout the day it needs to be recognised as the same sales opportunity or deal"; the previous spec is too complicated; V1 should take screenshots and summarise the day.

V1 therefore does exactly this, in order:

1. **Screenshot capture** of the frontmost window (D1), with the existing privacy, exclusion, and local-first rules.
2. **Per-segment understanding** of each activity segment (D3): what was on screen, who appeared, which deal it is about if any.
3. **Grouping the day's activity by sales opportunity / deal.** Emails, calls, documents and CRM screens about the same deal across the day link to **one** deal. Activity that is not about any deal stays ungrouped (`none`); it must not be attached to a real deal and must not invent one.
4. **A daily summary per deal:** what happened, who was involved, the next step. Every claim is cited to evidence (segment / OCR snippet).

Privacy, local-first processing, encrypted storage, no outbound sockets, and the §11.3 privacy tests stay exactly as Revision 2 specified.

---

## 1. Key architectural decisions

Each decision is recorded with rationale so it can be challenged deliberately rather than drift.

### D1 — Capture the frontmost window, not the desktop

Default capture is the frontmost window of the frontmost application, via ScreenCaptureKit with an `SCContentFilter` scoped to that window. Full-display capture is an explicit setting.

- The frontmost window is where the work is; background windows are the main source of incidental sensitive data (chat, mail previews, dashboards).
- Attribution (app, window title, bundle ID) becomes exact rather than inferred by the model.
- Multi-display handling collapses to "which display has the frontmost window".
- Images are smaller, so OCR and any VLM pass are faster.

Trade-off: side-by-side work (document + reference) is observed as alternating segments rather than one composite. This is acceptable for V1 and is actually easier to reason about.

### D2 — OCR-first; the vision model is a specialist, not the front door

Per frame: Apple Vision `VNRecognizeTextRequest` (on-device, ANE-accelerated, sub-second) produces text with bounding boxes. Combined with bundle ID, window title and, where available, browser URL, this is sufficient to characterise the large majority of knowledge-work frames.

The VLM is invoked only when a policy says so:

- OCR yields little text but the frame is visually dense (diagrams, slides, design tools, video calls).
- A segment is long/important and has not yet received a visual description.
- The user has explicitly enabled "rich visual understanding" and the backlog is small.

Rationale: local VLMs on 16–24 GB machines take 5–40 s per image and invent detail. OCR text is verbatim and can be quoted as evidence; a VLM description cannot. This decision is the single largest contributor to both accuracy and battery life.

### D3 — Activity segments are the unit of understanding

Frames are grouped into **segments**: maximal runs of consecutive frames with the same (bundle ID, normalised window title) where OCR-text similarity stays above a threshold. A segment has a start, an end, a representative text (merged OCR, de-duplicated by line), a change count and a list of frame IDs.

The LLM produces **one observation per segment** (or per segment chunk for very long segments), not per frame. This:

- Eliminates duplicate observations from repeated screenshots structurally.
- Gives the model "you spent 23 minutes in this document and these lines changed" rather than a single snapshot.
- Reduces LLM calls from ~800/day to ~40–120/day.

### D4 — Delete raw frames at extraction time, not at end of day

A frame's lifecycle is: `captured → extracted → deleted` (or `failed`). Once OCR, perceptual hash and (if selected) VLM output are persisted in a transaction, the PNG is removed and its removal verified. Target: median time-on-disk under 5 minutes; p99 under 30 minutes when the worker is healthy.

End-of-day processing operates on *extracted text*, not images. The daily sweep deletes only frames stuck in `captured`/`failed` after the recovery policy is exhausted, and records why.

The v1.1 rule still holds and becomes stricter: *never delete the only recoverable copy of an observation before its required information has been persisted* — and "required information" is now the frame extraction, so the window is minutes, not hours.

### D5 — Multi-process: Swift capture helper, Python core

```
watson (launcher / supervisor, Python, owns menu bar)
├── watson-capture      (Swift CLI, signed; ScreenCaptureKit + Vision OCR; writes frames + JSON sidecar)
├── watson-worker       (Python; extraction, segmentation, LLM, consolidation)
└── watson-dashboard    (Python; FastAPI on 127.0.0.1)
```

- macOS Screen Recording permission (TCC) is granted to a *code signature + bundle identifier*. A PyInstaller binary that changes every build will re-prompt or silently lose permission. A small, stable, signed Swift helper that rarely changes fixes this and isolates all platform-specific code (v1.1 §15.5).
- Vision OCR lives in the helper too: it is a Swift API and the image never needs to cross into Python.
- The inference worker is a separate process so a model OOM or crash cannot stop capture or the dashboard. It also lets us set `nice`/QoS and memory limits on inference alone.
- SQLite in WAL mode is the coordination layer (job table); no message broker.

Fallback if Swift is unavailable in the team: PyObjC + ScreenCaptureKit works but carries the TCC/signing issue into the main binary; this is documented as plan B, not the default.

### D6 — Inference runtime behind an adapter; in-process runtime for the package

`intelligence/runtime/` defines `VisionRuntime`, `LanguageRuntime`, `EmbeddingRuntime` protocols. Implementations:

- `ollama` — development convenience.
- `llamacpp` — `llama-cpp-python` with Metal; GGUF models; bundles cleanly; supports grammar-constrained JSON.
- `mlx` — `mlx-lm` / `mlx-vlm`; typically fastest on Apple Silicon; evaluated at M0.

The packaged app ships with one in-process runtime and pre-downloaded model files; Ollama is not a runtime dependency. All LLM calls request structured output (JSON schema / grammar) and are validated with Pydantic; invalid output is retried once then recorded as a failed job, never silently accepted.

Model families are chosen at M0 by benchmark, not now. The configuration carries `model_id`, `runtime`, `quantisation`, `context_window` and `min_ram_gb` per role.

### D7 — One encrypted SQLite file: relational + FTS5 + vectors

- SQLCipher via `sqlcipher3`, key held in macOS Keychain (`keyring`), rotated by re-keying.
- FTS5 virtual tables over observations, claims, summaries and reports for keyword search.
- `sqlite-vec` for embeddings, stored alongside the row they describe.
- Alembic migrations from the first commit; `pipeline_version` on every derived row so reprocessing is possible.

Plan B if SQLCipher wheels prove fragile for the target Python: application-level encryption of content columns with `cryptography` (AES-GCM, Keychain key), FTS over a tokenised-but-hashed index. Decided at M1.

### D8 — Claims with evidence, not just summaries

Every statement W.A.T.S.O.N may later repeat is a **claim** row with:

- `kind`: `activity | outcome | decision | commitment | achievement | person_interaction`
- `assertion`: `observed | inferred | unconfirmed | user_confirmed | user_corrected | user_rejected`
- `confidence`
- links to one or more **evidence** rows (segment ID, frame time range, short verbatim OCR snippet, optional VLM description)

Summaries are rendered *from* claims and cite them. The dashboard lets the employee confirm, correct or reject a claim; corrections propagate to later summaries. This implements v1.1 §6.4, §7.4 and §11.4 as data rather than as prompt instructions.

**V1 uses this model for deal summaries only.** A V1 claim is a cited statement in a per-deal daily summary (what happened, who was involved, next step), typically `activity`, `outcome`, or `person_interaction`, plus an optional next-step commitment when it is the deal's next step. Generic extraction of projects, decisions, commitments and achievements — and the weekly/monthly/project/achievement reports built from them — is parked after V1 (§16). The claim / evidence / assertion schema is not removed.

### D9 — Menu bar presence is mandatory

A menu bar item (via `rumps` in the supervisor) shows: capturing / paused / blocked (no permission) / backlog, with Pause (15 min, 1 h, rest of day), Open dashboard, Quit. This is the "visible capture status" requirement made real, and the fastest route to "pause immediately".

### D10 — Replay harness and synthetic golden dataset are the primary development loop

`watson replay --fixtures tests/fixtures/day_acme_q4/` runs the full pipeline on a directory of frames + sidecars with no capture involved. Fixtures are generated by rendering fictional "apps" (mail, docs, chat, spreadsheet, browser, IDE, CRM) as HTML with Playwright into PNGs. V1 ground-truth YAML describes **deals**, the segments that belong to each deal (or `none`), people on those deals, and the gold per-deal daily summary claims. Required fixture shapes are in §11.1. The older project / decision / commitment / achievement truth fields are retained on fixtures for parked work (§16) and are not a V1 gate.

This means: the whole AI/memory/reporting stack is built and measured on a Linux CI box or a Mac without any permissions; real capture is integrated last, as the spec's §23 intends.

### D11 — Dashboard: server-rendered, no Node toolchain, real local auth

FastAPI + Jinja2 + HTMX (+ a few hundred lines of vanilla JS). No bundler, so the portable package is simpler.

Local access control: a random session token is generated at startup, written to a `0600` file, and the launcher opens `http://127.0.0.1:<port>/auth?token=…` once, which sets a `HttpOnly; SameSite=Strict` cookie. Every state-changing route requires the cookie *and* an `Origin`/`Host` check against `127.0.0.1:<port>` (defeats DNS rebinding and cross-site requests). Port defaults to 8000 with automatic fallback.

### D12 — Explicit work-day lifecycle

`working_days.state`: `open → closing → consolidated | failed`. The day closes at a configurable time (default 18:30 local) *or* on first launch after that time if the machine was asleep ("catch-up consolidation"). Capture outside configured work hours is off by default. Days are keyed by local date and the configured day-end, so work at 00:30 belongs to the day the user thinks it does.

---

## 2. System architecture

```
                    ┌──────────────────────────────────────────────────────────┐
                    │ macOS                                                    │
                    │                                                          │
   Screen Recording │  ┌──────────────────┐   frames/*.png + *.json            │
   permission ────► │  │ watson-capture   │ ─────────────────────┐             │
                    │  │ (Swift, signed)  │                      ▼             │
                    │  │ SCK + Vision OCR │              ~/Library/Application  │
                    │  └──────────────────┘              Support/WATSON/spool/ │
                    │          ▲ control (stdin JSON)              │            │
                    │          │                                   ▼            │
                    │  ┌──────────────────┐  jobs/state  ┌──────────────────┐  │
                    │  │ watson           │◄────────────►│ watson.db        │  │
                    │  │ supervisor +     │  (SQLite WAL)│ (SQLCipher,      │  │
                    │  │ menu bar         │              │  FTS5, sqlite-vec│  │
                    │  └──────────────────┘              └──────────────────┘  │
                    │          │ spawns/monitors                 ▲    ▲         │
                    │          ▼                                 │    │         │
                    │  ┌──────────────────┐                      │    │         │
                    │  │ watson-worker    │──────────────────────┘    │         │
                    │  │ extraction →     │  reads spool, writes      │         │
                    │  │ segments → LLM → │  extraction, deletes PNG  │         │
                    │  │ consolidation    │                           │         │
                    │  └──────────────────┘                           │         │
                    │          │ in-process runtime (llama.cpp / MLX) │         │
                    │          ▼                                      │         │
                    │     models/*.gguf                               │         │
                    │                                                 │         │
                    │  ┌──────────────────┐                           │         │
                    │  │ watson-dashboard │───────────────────────────┘         │
                    │  │ FastAPI, 127.0.0.1│                                    │
                    │  └──────────────────┘                                    │
                    │          ▲ cookie + Origin check                          │
                    └──────────│──────────────────────────────────────────────┘
                               │
                          local browser
```

No component opens an outbound socket during normal operation. The only network activity is the documented model download step during setup, which is a separate command (`watson models fetch`) and never runs implicitly.

### 2.1 Filesystem layout at runtime

```
~/Library/Application Support/WATSON/
├── config.toml                 user settings (no secrets)
├── watson.db                   encrypted database (+ -wal, -shm)
├── spool/                      raw frames awaiting extraction; 0700; excluded from Time Machine
│   └── 2026-10-31/<frame-id>.png + .json
├── models/                     GGUF / MLX model files
├── logs/                       structured JSON logs, redacted, rotated
└── session/                    dashboard token (0600), PIDs
```

The spool directory has the `com.apple.metadata:com_apple_backup_excludeItem` attribute set and is created with `0700`. If the Application Support directory is on a cloud-synced volume, startup warns.

---

## 3. Pipeline stages and state machines

### 3.1 Frame

Produced by `watson-capture` on each tick (default 45 s; adaptive — see §3.6).

Sidecar JSON: `frame_id, captured_at, display_id, bundle_id, app_name, window_title, window_id, is_private_browsing (best effort), url (best effort, if Accessibility granted), ocr: {lines: [{text, bbox, confidence}]}, phash, width, height, capture_mode`.

States: `captured → extracted → deleted` | `captured → failed(n) → deleted_unprocessed` (after N retries and operator policy) | `skipped` (excluded app, locked screen, paused, idle).

Frame rows in the database hold **metadata only**. OCR text moves to the `frame_text` tier (see §5).

### 3.2 Pre-filters in the helper (before anything is written)

In order, cheapest first:

1. Paused / outside work hours / screen locked (`CGSessionCopyCurrentDictionary` → `CGSSessionScreenIsLocked`) / display asleep → skip.
2. User idle > 3 min (`CGEventSourceSecondsSinceLastEventType`) → skip.
3. Frontmost bundle ID in exclusion list → skip, log `skipped(excluded_app)`.
4. Window title matches an exclusion pattern (e.g. `Private Browsing`, `Incognito`, `1Password`, `Sign in`) → skip.
5. Window `kCGWindowSharingState == none` → skip (apps that opt out of sharing are honoured).
6. Perceptual hash within distance 4 of the previous frame from the same window → skip, increment `duplicate_of` counter on the previous frame (so dwell time is still known).

Only frames passing all six are written to the spool.

### 3.3 Extraction (worker)

Per spooled frame, in one transaction:

1. Validate sidecar; load OCR.
2. Redaction pass on OCR text (§5.3).
3. Decide VLM eligibility (D2 policy); if eligible, run VLM with a constrained-JSON prompt asking only for *what is visible* (no interpretation).
4. Persist `frame`, `frame_text`, optional `frame_visual`.
5. Delete PNG; `fstat` to confirm absence; write `audit_events(frame_deleted)`.
6. Mark `extracted`.

If step 4 fails, the PNG stays and the frame is `failed(n)`; the worker retries with backoff. If the worker is down, frames accumulate in the spool and the menu bar shows a backlog warning; nothing is lost and nothing is deleted.

### 3.4 Segmentation (worker, incremental)

Runs every few minutes over newly extracted frames. Produces/extends `segments` using the D3 rule. For each segment it maintains `merged_text` (line-level union, order-preserving, capped), `line_changes` (count of new lines between frames — a cheap signal for *writing* versus *reading*), `frame_count`, `dwell_seconds`.

### 3.5 Understanding (worker, incremental)

A segment becomes eligible once it is closed (window changed) or exceeds 20 minutes. The LLM receives: app/window/URL, dwell, change statistics, merged text (trimmed), optional visual description, and **retrieved context**: top-k matching **deal** and people entries from memory (hybrid search over the segment text). **§3.5 retrieval stays in V1** — it is how a later email is recognised as the same deal. It is not Ask W.A.T.S.O.N (§7.3 / §16). The LLM returns an **observation** with candidate claims, each tagged `observed`/`inferred`/`unconfirmed` and each pointing at the evidence snippet lines it relied on, plus a deal candidate or `none`.

Entity linking: the model proposes `deal_candidates` and `people_candidates` as free text; a resolver maps them to existing entities by alias table + embedding similarity, creating new entities only above a threshold and marking them `provisional` for user review. The model never emits database IDs. Generic `project_candidates` remain in the schema for parked work (§16) and are not a V1 output.

### 3.6 Adaptive capture and backpressure

- Interval: 45 s default; stretches to 120 s when the last three frames were duplicates; tightens to 30 s on window change. Configurable bounds.
- Backpressure: if spool depth exceeds a threshold, the worker disables VLM for new frames (OCR-only degraded mode) and the menu bar reports it. If depth keeps growing, capture interval increases. Observations are never dropped.
- Power/thermal: on battery or at `ProcessInfo.thermalState >= .serious`, VLM is disabled and consolidation defers to AC power unless the user forces it.

### 3.7 Day consolidation

Trigger: configured day-end, or catch-up on launch. Idempotent by `(working_day_id, pipeline_version)`; re-running replaces the previous output in a transaction rather than appending.

1. Confirm all frames for the day are `extracted` or terminally `failed`; otherwise wait/retry up to the recovery deadline, then proceed with a recorded gap.
2. Close open segments; run understanding on any pending.
3. Cluster observations into **deals** (same sales opportunity across email, call, document and CRM screens). A segment that is not about any deal is `none`.
4. Per-deal daily summary generated from that deal's claims: what happened, who was involved, next step; each paragraph / claim carries claim IDs.
5. Verify persistence (`PRAGMA wal_checkpoint`, re-read summary row).
6. Apply `frame_text` retention (§5), sweep spool, audit.
7. Set day `consolidated`.

Parked after V1 (do not delete; see §16): project/people memory deltas; cross-segment decision/commitment extraction; achievement candidates; weekly/monthly/project/achievement reports.

If any step fails the day is `failed` with a reason, visible on the dashboard with a "Retry" action; the spool is not swept.

---

## 4. Data model (revised)

SQLite with Alembic migrations. Columns abbreviated; all tables have `id` (UUIDv7), `created_at`, `updated_at`.

| Table | Purpose | Key columns |
|---|---|---|
| `settings` | Single-row user config mirror (source of truth is `config.toml`) | `json` |
| `working_days` | One per local work day | `date`, `started_at`, `ended_at`, `state`, `consolidation_error`, `pipeline_version` |
| `frames` | Metadata per captured frame; **no image, no text** | `working_day_id`, `captured_at`, `bundle_id`, `app_name`, `window_title_hash`, `window_title` (nullable, retention-controlled), `url_domain`, `phash`, `state`, `failure_count`, `deleted_at`, `deletion_verified` |
| `frame_text` | Tier-2 extracted text, expiring | `frame_id`, `text_redacted`, `ocr_confidence`, `expires_at` |
| `frame_visual` | Optional VLM description, expiring | `frame_id`, `description`, `model_id`, `expires_at` |
| `segments` | Contiguous activity in one window | `working_day_id`, `bundle_id`, `window_title_norm`, `url_domain`, `started_at`, `ended_at`, `frame_count`, `dwell_seconds`, `line_changes`, `merged_text` (expiring), `state` |
| `observations` | LLM understanding of a segment | `segment_id`, `activity_type`, `description`, `confidence`, `model_id`, `prompt_version`, `raw_json` |
| `claims` | Atomic statements W.A.T.S.O.N may repeat | `observation_id`, `working_day_id`, `kind`, `text`, `assertion`, `confidence`, `project_id`, `status` (for commitments: `open/done/dropped`), `due_hint` |
| `evidence` | Why a claim is believed | `claim_id`, `segment_id`, `frame_id`, `snippet` (≤ 300 chars, retained), `snippet_kind` (`ocr/visual`) |
| `entities` | Projects and people (single table, `type`) | `type`, `canonical_name`, `description`, `first_seen`, `last_seen`, `provisional`, `merged_into_id` |
| `entity_aliases` | Names the model has used for an entity | `entity_id`, `alias`, `source` |
| `claim_entities` | Many-to-many | `claim_id`, `entity_id`, `role` |
| `entity_history` | Per-day deltas for projects/people | `entity_id`, `working_day_id`, `delta_text`, `claim_ids` |
| `daily_summaries` | Rendered narrative + structured sections | `working_day_id`, `narrative_md`, `sections_json`, `claim_ids`, `pipeline_version` |
| `reports` | Weekly/monthly/project/achievement reports (**parked after V1**, §16). V1 persists per-deal daily summaries on `daily_summaries` | `type`, `period_start`, `period_end`, `entity_id`, `generated_md`, `claim_ids` |
| `report_versions` | User edits, newest wins | `report_id`, `body_md`, `edited_by_user` |
| `corrections` | User feedback on claims/entities | `target_type`, `target_id`, `action` (`confirm/correct/reject/merge/not_work`), `new_text` |
| `jobs` | Durable work queue | `kind`, `payload`, `idempotency_key` (unique), `state`, `attempts`, `run_after`, `last_error` |
| `audit_events` | Deletions, permission changes, consolidation, data-deletion requests | `kind`, `detail_json`, `actor` (`system/user`) |
| `embeddings` (`sqlite-vec`) | Vectors for claims, summaries, entities | `ref_type`, `ref_id`, `vector`, `model_id` |
| FTS5: `fts_claims`, `fts_summaries`, `fts_entities` | Keyword search | content-synced triggers |

Notable differences from v1.1: `frames` replaces the implicit "screenshot" record and is metadata-only; `segments`, `claims`, `evidence`, `entity_aliases`, `corrections`, `report_versions`, `jobs` are new; `projects`/`people` are unified as `entities` with aliases to make merging and renaming possible; `project_observations`/`people_observations` are replaced by `claim_entities` because links belong on the statement, not the screenshot.

---

## 5. Privacy and retention tiers

v1.1 §8.3 correctly notes that deleting a screenshot does not delete its contents. This plan makes the derived data explicit and gives each tier its own TTL.

| Tier | What | Sensitivity | Default retention | User control |
|---|---|---|---|---|
| 0 | Raw frame PNG in spool | Highest | Deleted on extraction (minutes); hard limit 24 h then `deleted_unprocessed` with audit | Delete now |
| 1 | Frame metadata (`frames`): app, window title, domain, timestamps | Medium | Window titles 30 days, then nulled; app/domain/timestamps 1 year | Configurable; title capture can be disabled |
| 2 | Extracted text (`frame_text`, `frame_visual`, `segments.merged_text`) | High — this *is* the screen content | **Until day consolidation + 7 days** (grace for re-running consolidation), then deleted | 0–30 days |
| 3 | Evidence snippets (≤ 300 chars per claim) | Medium–high | Life of the claim | Delete per claim/day |
| 4 | Claims, observations, entities, summaries, reports, embeddings | Medium | Indefinite by default; optional rolling window (e.g. 24 months) | Delete per day / project / person / all |

Rules:

- Tier 2 exists so a failed or improved consolidation can be re-run without the images; it is not a searchable archive. It is excluded from FTS and embeddings.
- Deleting a day deletes tiers 1–4 for that day and re-renders any weekly/monthly report that cited it with a "source removed" marker.
- Deleting an entity (project/person) removes its claims' links and history; claims that have no remaining entity become `unlinked`, not deleted, unless the user chooses "and everything only about this".
- "Delete all local work history" drops the database and spool, re-creates an empty encrypted database and records a single audit event in the new one.

### 5.1 Capture exclusions (prevention)

- Bundle-ID exclusion list, pre-populated with password managers, banking apps, Messages, FaceTime, Photos, Health-type apps; user lists **merge** with those defaults. `allowed_bundle_ids` is the visible override (load warning + status/dashboard); see [`docs/config.md`](config.md).
- Window-title patterns (private browsing, sign-in pages, "password", "verification code").
- Browser domain allow/deny lists, applied when the URL is available; denylist pre-populated with personal mail/banking/health/social domains.
- `kCGWindowSharingState` honoured.
- Work-hours schedule; capture off outside it.
- "Pause for…" from the menu bar.

### 5.2 Minimisation (what the model is allowed to keep)

The understanding prompt is instructed — and the output schema constrains — that observations describe *work activity*, not personal content. An `activity_type = non_work` observation retains only app, dwell and timestamps; its segment text is deleted immediately rather than at consolidation.

### 5.3 Redaction (before storage)

OCR text passes through a redaction filter before it is written anywhere: payment card numbers (Luhn), IBAN-like strings, OTP codes near "code"/"verification", likely passwords (fields adjacent to "Password"), long high-entropy tokens (API keys), national-ID-shaped numbers. Replaced with typed placeholders (`[CARD]`, `[SECRET]`) so the model still understands context.

### 5.4 Logging

Structured JSON logs. A `ContentField` wrapper marks any value derived from screen content; the log formatter replaces it with a length and hash at every level including DEBUG. A unit test fails if any logger call passes a raw `frame_text`/`merged_text`/`snippet`.

---

## 6. Security

- **Network:** dashboard binds `127.0.0.1` only. A CI test runs the full replay pipeline under a network namespace with no routes (`unshare -n` on Linux; on macOS, a `pf` deny rule in the test harness) and asserts success. A runtime guard patches `socket.create_connection` in worker and dashboard to refuse non-loopback destinations unless `WATSON_ALLOW_NETWORK=1` (used only by `watson models fetch`).
- **Dashboard auth:** session cookie from launcher token (D11); `Origin`/`Host` validation; CSRF token on forms; HTMX requests carry the same header. No `*` CORS.
- **Encryption at rest:** SQLCipher, key in Keychain under the app's access group; key never written to disk or logs. Spool `0700`, files `0600`, backup-excluded.
- **Helper integrity:** supervisor verifies the capture helper's code signature before launching it.
- **Secrets:** none required. No cloud credentials exist in V1.
- **Updates:** none in V1; the portable package is replaced manually. Documented so IT is not surprised by the absence of an updater.
- **Threat model (documented for IT):** local attacker with user privileges can read what the user can read — the database is encrypted but the key is in the user's Keychain. The protection is against lost/stolen disks (with FileVault off), backups, sync and accidental exposure, not against malware running as the user.

---

## 7. AI layer

### 7.1 Roles and adapters

| Role | Interface | Dev default | Package default (llama.cpp vs MLX at M3) |
|---|---|---|---|
| OCR | Swift Vision in helper | — | — |
| Visual description | `VisionRuntime.describe(image, schema)` | Ollama (off on 8 GB, §0.1 / ADR 0001) | llama.cpp or MLX VLM, ~2–8B class (16 GB V1.2 profile) |
| Understanding / consolidation / reports / Q&A | `LanguageRuntime.complete(messages, schema)` | Ollama, `qwen2.5:3b` baseline until bake-off (Kieran 21:53/21:55). 7–9B Q4 allowed (`gemma2:9b`, `llama3.1:8b`, `qwen2.5:7b`) | llama.cpp or MLX LLM. V1 default is the largest model that completes on 8 GB **and meets the §11.2 gate** (deal grouping accuracy ≥ 0.85 and deal summary accuracy ≥ 0.85). ~7–9B Q4–Q5 remains available on the 16 GB V1.2 profile. |
| Embeddings | `EmbeddingRuntime.embed(texts)` | Ollama, `nomic-embed-text` (ADR 0001) | in-process small embedding model (≤ 0.5B on 8 GB) |

The table names the current defaults from the M0-A bake-off (ADR 0001). Identifiers remain configuration because the local-model landscape still changes faster than this document.

### 7.2 Prompting and output

- Prompts are versioned files under `intelligence/prompts/<task>/<version>.md` with a `prompt_version` recorded on every output row.
- Every call uses schema-constrained generation; outputs are validated with Pydantic. Failure → one retry with the validation error appended → job failure.
- Prompts include explicit calibration instructions mirroring v1.1 §6.4: outcomes require visible evidence of completion; "sent", "approved", "agreed" require the corresponding text; otherwise `unconfirmed`.
- Reports and answers are generated from a provided list of claims with IDs and must cite IDs inline (`[c:…]`); a post-processor verifies every cited ID exists and strips sentences whose citations are missing. Uncited material claims are rendered in an "unverified" section rather than in the body.

### 7.3 Retrieval (Ask W.A.T.S.O.N, reports)

**Understanding-time retrieval (§3.5) stays in V1.** The same hybrid index (aliases, FTS5, sqlite-vec) feeds the understanding prompt so later segments can attach to a deal already seen that day.

**Parked after V1 (§16):** Ask W.A.T.S.O.N and weekly/monthly/project/achievement reports. That Q&A path is kept here so it is not redesigned later.

Hybrid: query → (date range parser; entity resolver over aliases; FTS5 BM25; sqlite-vec kNN) → reciprocal-rank fusion → top-k claims and summaries with their evidence snippets → LLM answer with citations. The context budget is fixed (e.g. 6k tokens); nothing outside the retrieved set is given to the model. Answers show "Based on N records from dates X–Y" and link each citation to its day.

### 7.4 Graceful degradation

| Condition | Behaviour |
|---|---|
| Runtime or model missing | Capture continues; frames extracted OCR-only; understanding queued; dashboard shows the exact missing model and the fetch command |
| Worker crash | Supervisor restarts with backoff; spool preserved |
| Backlog growing | VLM off → interval up → still never drop frames |
| Low disk (< 2 GB) | Capture pauses; menu bar and dashboard warn; audit event |
| Consolidation fails | Day `failed`, retry button, spool retained, tier-2 text retained |

---

## 8. Dashboard

Sections as v1.1 §10.2, scoped to V1 deals:

- **Today** shows the live segment timeline (app, window, dwell) even before any LLM output exists, so the user sees what W.A.T.S.O.N saw within minutes, then the day's deal groups and each deal's summary once consolidation has run.
- **Memory** exposes claims with their assertion badges and evidence snippets; one-click confirm / correct / reject / "not work"; deal and person merge/rename.
- Deal-summary export is "copy as Markdown" and "export .md". Nothing else leaves the app.
- **Privacy** shows: permission state, spool depth and oldest frame age, tier-2 text volume and next purge, exclusion lists, work hours, and the deletion controls with confirmation and audit trail.
- **Status bar** on every page: capturing / paused / blocked, worker health, model health.

Parked after V1 (§16): generic **Projects** as a first-class non-deal entity page; **Reports** (weekly / monthly / project / achievement) with the versioned editor. The M4a Projects/People pages that already landed stay in the repo; V1 treats People as people-on-deals and does not gate generic project or achievement reports.

No productivity metrics anywhere; dwell is shown as context on a timeline, never aggregated into a score.

---

## 9. API (revised)

All under `/api`, cookie-authenticated, `Origin`-checked. Additions and renames relative to v1.1 §13:

| Method | Path | Notes |
|---|---|---|
| GET | `/status` | app, helper, worker, model, spool, day state |
| GET/POST | `/capture/status`, `/capture/pause`, `/capture/resume` | pause accepts `duration_minutes` |
| GET | `/today` | segments + observations + claims so far |
| GET | `/days?from=&to=` ; `/days/{date}` | summaries, states |
| POST | `/days/{date}/consolidate` | retry/re-run (idempotent) |
| GET | `/entities?type=project\|person` ; `/entities/{id}` | with history |
| POST | `/entities/{id}/merge` , `/entities/{id}/rename` | |
| GET | `/claims?…` ; POST `/claims/{id}/feedback` | confirm/correct/reject |
| GET/POST | `/reports` , `/reports/generate` , `/reports/{id}/versions` | |
| POST | `/ask` | returns answer + citations |
| GET | `/search?q=&from=&to=&entity=` | hybrid search |
| GET/PUT | `/settings` | |
| POST | `/privacy/delete-spool` , `/privacy/delete-day/{date}` , `/privacy/delete-entity/{id}` , `/privacy/delete-all` | all require a confirmation nonce issued by GET |
| GET | `/audit?…` | |

---

## 10. Repository layout

```
watson/
├── pyproject.toml                 (uv/pip; pinned lock)
├── README.md
├── docs/                          this plan, ADRs, IT pack sources
├── helper/                        Swift package: watson-capture
│   ├── Package.swift
│   └── Sources/watson-capture/    Capture.swift, OCR.swift, Filters.swift, Protocol.swift
├── src/watson/
│   ├── cli.py                     `watson run|replay|models|db|doctor`
│   ├── supervisor/                process management, menu bar, health
│   ├── capture/                   helper protocol, spool, exclusions, platform abstraction
│   ├── pipeline/                  extraction, redaction, segmentation, jobs, consolidation
│   ├── intelligence/              runtime adapters, prompts/, schemas, entity resolution
│   ├── memory/                    db, migrations/, repositories, retrieval (fts + vec)
│   ├── reporting/                 daily, weekly, monthly, project, achievement, export
│   ├── dashboard/                 FastAPI app, auth, templates/, static/
│   ├── privacy/                   retention policies, deletion, audit
│   └── config/                    settings schema, defaults
├── tests/
│   ├── unit/
│   ├── integration/               replay pipeline end-to-end
│   ├── privacy/                   no-network, no-content-in-logs, deletion verification
│   ├── eval/                      golden dataset scoring
│   └── fixtures/                  synthetic days (generated; committed)
├── tools/
│   ├── fixture_gen/               Playwright renderer for fictional apps + ground truth
│   └── bench/                     model/hardware benchmark scripts
└── packaging/                     PyInstaller spec, entitlements, signing scripts, model manifest
```

---

## 11. Testing and evaluation

### 11.1 Synthetic golden dataset

`tools/fixture_gen` renders fictional applications from templates (mail client, document editor, chat, spreadsheet, browser with fake intranet / CRM, IDE, video call placeholder) into frame sequences with sidecars. Each V1 day fixture ships a `truth.yaml` that names **deals**, defines every gold segment by a fixture `id` and its `frame_ids`, assigns that segment to a deal or to `none`, and lists gold **atomic facts** per deal. Every gold fact has `required_terms` (or a `person_id`) and non-empty `evidence_segments` whose ids exist on that day.

```yaml
deals:
  - id: acme-q4
    name: Acme Q4 Proposal
    lookalike_of: null
  - id: acme-renewal
    name: Acme Renewal          # look-alike name; must not merge with acme-q4
    lookalike_of: acme-q4
people:
  - id: priya-natarajan
    name: Priya Natarajan
    company: Acme
  - id: priya-chen
    name: Priya Chen            # confusable contact (shared first name)
    company: Globex
  - id: tom-ashworth
    name: Tom Ashworth
    company: Delivery
segments:
  - id: s-mail-priya-am
    frame_ids: [f0410, f0411, f0412]
    deal: acme-q4               # email about the Q4 proposal
  - id: s-call-priya
    frame_ids: [f0500, f0501]
    deal: acme-q4               # same deal, different channel
  - id: s-doc-pricing
    frame_ids: [f0600]
    deal: acme-q4               # doc / CRM screen about the same deal
  - id: s-mail-renewal
    frame_ids: [f0700, f0701]
    deal: acme-renewal          # look-alike deal name
  - id: s-news
    frame_ids: [f0800]
    deal: none                  # non-deal noise
  - id: s-1password
    frame_ids: [f0900]
    deal: none
    privacy_bait: true          # deal-like OCR in an excluded app; replay skips it
deal_summaries:
  acme-q4:
    what_happened:
      - id: fact-q4-call
        required_terms: [pricing, call]
        aliases: {pricing: [price, pricing]}
        evidence_segments: [s-call-priya]
      - id: fact-q4-deck
        required_terms: [q4, deck]
        evidence_segments: [s-doc-pricing]
    who_involved:
      - id: fact-q4-priya
        person_id: priya-natarajan
        evidence_segments: [s-mail-priya-am, s-call-priya]
    next_step:
      - id: fact-q4-send
        required_terms: [send, pricing, friday]
        aliases: {pricing: [pricing, price]}
        evidence_segments: [s-mail-priya-am]
noise: [news site 10:42–10:49, personal calendar glance]
```

Gold summaries are **atomic facts** (one fact per gold row), not a paragraph. Each fact has `required_terms` and optional `aliases`. Every fact has non-empty `evidence_segments` whose ids exist on that day.

**Corpus guidance** (authoring targets, **not** a guarantee that trivial baselines fail):

| Guidance | Value |
|---|---|
| Days | **5** (the existing five, all relabelled with deals: focused, fragmented, meeting-heavy, sensitive-trap, near-empty) |
| Distinct gold deals | **≥ 8** across the corpus |
| Multi-touch deals | **≥ 3**, each with email **and** call **and** doc/CRM segments on the same day |
| Same-company / different-deal pairs | **≥ 4** unordered pairs; as a writing hint, segments of all-but-the-largest deal in each same-company group ≥ 20 % of deal-segments (that would cap company-merge recall at 0.80). **The computed fixture-validation test in §11.2 is authoritative.** |
| Look-alike name pairs | **≥ 2** (shared name token, not only the company token; must stay distinct gold deals) |
| Non-deal noise | **≥ 20 %** of gold segments, pooled |
| Interleaved deals | **≥ 2** gold deals on every non-empty day (the near-empty day may have 0–1) |
| Confusable people | **≥ 1** pair of roster people who share a first name or a full name (disambiguated by `person_id` + company) |
| Excluded-app bait | **≥ 1** `privacy_bait: true` segment; every frame of it must already be `skipped` (`excluded_app` or `excluded_title`) |
| Evidence | every gold fact has `evidence_segments`; every listed segment id exists |

Existing fixtures that still carry `projects` / `decisions` / `commitments` / `completed` fields keep those keys for parked work (§16). They are not the V1 quality gate.

### 11.2 Metrics and thresholds (gate in CI on fixtures)

V1 quality gate (Kieran, 2026-11-03 10:38 UTC via CoS; computable restatement after #45 QA). The numbers below are computed; both must hold on the fixture corpus with `--strict` / `--mode record`. Replay CI remains a pipeline-only gate (`docs/eval.md`).

| Metric | Target |
|---|---|
| **Deal grouping accuracy** | **≥ 0.85** |
| **Deal summary accuracy** | **≥ 0.85** |
| Strict claim precision | ≥ 0.85 (constituent of deal summary accuracy) |
| Recall of gold **what happened** | ≥ 0.85 (constituent) |
| Recall of gold **next step** | ≥ 0.85 (constituent) |
| Recall of gold **who involved** | ≥ 0.85 (constituent; reported per slot) |
| Unsupported summary claims | **0** |
| Claims with ≥ 1 evidence row | 100 % (structural) |
| Summary citations resolvable | 100 % (structural) |

Parked after V1 (measured if present, **not** a V1 gate): project identification F1 ≥ 0.80; people identification F1 ≥ 0.75; completed vs. not-completed accuracy ≥ 0.90; decisions recall / precision ≥ 0.70 / ≥ 0.85; commitments recall / precision ≥ 0.70 / ≥ 0.85; unsupported achievement claims = 0; duplicate observations < 5 %; noise classified `non_work` ≥ 0.90. Those definitions stay in §16.

The committed `thresholds.json` still encodes the parked Revision 2 rows. **Until M3f lands**, the bake-off / default-model rule uses that file (strict decisions/commitments precision ≥ 0.85 and the other current rows, plus the 8 GB min-spec rule). **At M3f** those parked rows become **report-only**; the default model is then the largest that completes on 8 GB and meets the deal grouping / deal summary gate. Do not lower the committed file in this docs change.

Thresholds are targets for the chosen model; they are adjusted with evidence, not quietly. The 8 GB min-spec hardware rule in §11.4 is unchanged.

**Per-day match, then pool.** Predicted cluster ids are scoped to a fixture day. Key is `(fixture_day, cluster_id)` — a persistent id reused on another day is a different cluster. On each day: align that day's gold segments, match that day's predicted clusters to that day's gold deals, and form pairwise / assignment / summary counts **within the day**. Then **sum the counts across days** and compute each ratio once from the totals. Per-day ratios are written to `report.json` → `days` and are not the gate.

#### Segment alignment (predicted → gold, keyed by fixture segment ID)

Gold segments are the scoring units. Each gold row has a fixture `id` and a `frame_ids` set. Every fixture frame id appears in exactly one gold segment on that day.

After the pipeline runs, each predicted segment still carries those fixture frame ids.

1. For each gold segment G, collect every predicted segment **from the same fixture day** that contains at least one frame in `G.frame_ids`.
2. G's **covering prediction** is the predicted segment that contains the most frames of G. Tie-break: earlier `started_at`, then lexicographically smaller predicted segment `id`.
3. If no predicted segment covers any frame of G, G is **uncovered**: predicted deal = `missing` (not `none`).
4. G's predicted deal is the covering prediction's deal cluster id, or `none`.

No other joining key is used. Pipeline-internal UUIDs are ignored.

**Privacy-bait segments** (`privacy_bait: true`). Replay correctly skips them, so they are usually `missing`. They are **excluded from grouping accuracy** (they are not in the assignment / deal-segment-recall / pairwise denominators). `missing` or `none` is the expected label and is not a miss. If any bait frame, its OCR, or a snippet from it appears in a predicted deal cluster or in any summary claim / evidence row, that is a **hard privacy FAIL** of the eval (in addition to §11.3). Bait in a deal or summary can never be a grouping or summary credit.

**`privacy_bait` fixture check** (required at M3f; fails the build):

1. `privacy_bait: true` is legal **only** when every `frame_id` of that gold segment is present in the fixture **and** is `skipped` with reason `excluded_app` or `excluded_title`. A captured / extracted / hard segment cannot be flagged bait to pull it out of scoring.
2. Every excluded-app or excluded-title bait segment in the fixtures is listed with `privacy_bait: true` (none are omitted).
3. After replay, those frames and their OCR are **asserted absent** from all stored output: no `frame_text`, no observation, no claim, no evidence snippet.

#### Deal-cluster matching (one algorithm, per day)

`none` and `missing` are **not** clusters. They are their own class and never enter this matching. Privacy-bait segments never enter overlap counts.

On **each fixture day independently**, predicted deal clusters of that day are paired **1:1** with that day's gold deals by **greedy maximum overlap**:

1. Overlap(P, G) = number of that day's (non-bait) gold segments whose predicted deal is P and whose gold deal is G.
2. List every (P, G) with overlap > 0. Sort by overlap descending, then gold deal `id` ascending, then predicted cluster `id` ascending.
3. Walk the list. Take a pair if neither P nor G is already matched **on this day**.
4. Unmatched predicted clusters and unmatched gold deals stay unmatched for that day.

There is no second algorithm (no Hungarian step, no alternate greedy). There is no cross-day matching.

#### Deal grouping accuracy (computable)

Treat `none` as its own class. Form counts **per day**, then **pool**. Privacy-bait segments are omitted.

**Assignment accuracy.** One score per non-bait gold segment:

| Gold | Predicted (after that day's cluster match) | Score |
|---|---|---|
| deal D | cluster matched to D | correct |
| `none` | `none` | correct |
| deal D | `none` or `missing` | incorrect |
| `none` | any deal cluster or `missing` | incorrect |
| deal D | cluster matched to a different gold deal | incorrect |
| deal D | unmatched predicted cluster | incorrect |

`assignment_accuracy = correct / non_bait_gold_segments` (pooled).

**Deal-segment recall.** Of non-bait gold segments whose gold deal ≠ `none`: fraction whose predicted cluster is matched to that same gold deal. All-`none` scores 0 here.

**Pairwise F1** on unordered pairs of distinct non-bait gold segments **from the same day**:

- A **gold pair** is two same-day segments with the same gold deal and that deal ≠ `none`.
- A **predicted pair** is two same-day segments with the same predicted deal cluster and that cluster ≠ `none` and ≠ `missing`.
- TP: pair is both a gold pair and a predicted pair.
- FP: predicted pair that is not a gold pair (different gold deals, or either gold `none`).
- FN: gold pair that is not a predicted pair.

`pairwise_precision = TP / (TP + FP)` after pooling. If `TP + FP = 0` (no predicted pairs), **pairwise_precision = 0**. If `TP + FN = 0`, pairwise_recall = 0. The single vacuous exception: no gold deal in the corpus has two or more non-bait segments **and** there are no predicted pairs — then P = 1 and R = 1. `pairwise_F1 = 2PR / (P + R)` when P+R > 0, else 0.

All-`none` on a multi-touch corpus has TP = 0, FN > 0, P = 0, F1 = 0. One giant predicted deal has high recall and low precision on a balanced day. **Do not treat the guidance counts as a guarantee.** A 60-segment corpus with 12 noise, Acme deals sized 12/1/1/1 (6 same-company pairs, 31 % of deal-segments) and other deals 8/8/8/9 scores group-by-company recall 0.938, assignment 0.95, pairwise F1 **0.905** — it meets every old count floor and still passes 0.85. The computed fixture-validation test below **must reject that shape**.

**Deal grouping accuracy** = `min(assignment_accuracy, deal_segment_recall, pairwise_F1)`. Gate: ≥ 0.85.

#### Citations → frame ids

A predicted claim cites evidence rows (`[c:…]` / claim id → `evidence`). Mapping:

1. If the evidence row has `frame_id`, that fixture frame id is cited.
2. Else the evidence row's `segment_id` contributes every fixture frame id on that predicted segment.
3. Those fixture frame ids join to gold segments via `frame_ids` on the same day (§11.2 alignment).

OCR checked for required terms is the redacted `frame_text` of those cited frames. Window titles are not evidence.

#### Deal summary accuracy (computable)

**Slots.** Every predicted summary claim must carry `slot` ∈ {`what_happened`, `who_involved`, `next_step`}. A claim with a missing or unknown slot is a precision miss (false). Gold facts use the same three slots.

**Length cap (keyword stuffing).** After `normalize()` and dropping `ITEM_STOPWORDS`, a predicted claim may have at most **24 content tokens**. Over-length claims cannot cover any gold fact; they are precision misses (not unsupported) if their citations resolve.

**One claim, one fact.** A predicted claim satisfies **at most one** gold fact. Pairing is 1:1 inside `(day, matched deal, slot)` by **maximum matching**: copies that cover the same facts with overlapping cited segments count once before matching (claim text is ignored), then matching assigns whichever fact maximises the number of pairs. File order does not choose the credited fact. Extra copies of a credited claim are not true positives and stay in the precision denominator. A gold fact is consumed once.

**Slot-value contradiction (same day + deal + slot only).** A claim does **not** cover gold fact F if, in **that same slot**, it asserts a **different** person, calendar date, or amount than F's slot value. Extra people, dates, or amounts that are **correct for this deal** and appear in the claim's **cited evidence** do not contradict — they do not reject an honest sentence such as "Priya Natarajan emailed about pricing" or "send revised pricing by Friday". Person names, deal/company names, and dates mentioned in another **slot** are not contradictions. Cross-day and cross-deal terms are out of scope for this rule.

**Atomic gold facts.** Each gold row is one fact with `required_terms` (or a `person_id`) and optional `aliases`. A predicted claim **covers** a gold fact iff all of these hold:

1. Same fixture day, same slot, predicted cluster matched to that gold deal.
2. Every required term of **that one fact** appears as a **contiguous-word phrase** in the normalized predicted text (aliases count as the term). For a `who_involved` fact, this screen-text check means a **roster form** of that person's name (`name`, `name (company)`, or `name company`).
3. The claim is within the 24-token cap and does not **contradict** F's slot value (person / date / amount) as defined above.
4. Slot modal rules pass (below).
5. Cited gold segments **overlap** that fact's `evidence_segments` (see evidence link).

This is **not** exact-string equality. Paraphrase is allowed when the required terms of that one fact are present and the caps hold.

**Per-slot modal rules.** Token sets after normalize:

| Slot | Accepts | Rejects (no cover) |
|---|---|---|
| `what_happened` | Observed / past (`sent`, `agreed`, `happened`) | Intent / future / commitment: `will`, `should`, `need to`, `going to`, `plan to` |
| `next_step` | Commitment: `will`, `should`, `need to` (or a bare imperative that names the action) | **Done** forms of the same verb (`sent` vs `send`); hedges `might`, `may`, `could`; negation `not`, `never`, `won't` |
| `who_involved` | Resolved `person_id` | n/a (modals ignored) |

So a next step stated as done vs planned does not match, and `will` vs `may` does not match. `#41` polarity/date/name guards still apply, **except** `should` / `need to` / `will` are **not** reject-modals on `next_step` (they would have rejected "Kieran should send revised pricing by Friday").

**Modal / stuffing mutants** (required at M3f): `what_happened` + `will`/`should`; `next_step` + `may`/`might`/`could`/`won't`/`sent`; a weekday swapped for a calendar date; a different person/date/amount in the same slot; both keyword-dump baselines. None of these cover.

**Aliases (closed list, fixture-checked).** The only allowed aliases are:

1. **Roster person forms** for that `person_id` only: `name`, `name (company)`, `name company`.
2. **Number forms of the same value:** `2` / `two`. Not a different number.
3. **Date forms of the same calendar date:** `14 nov` / `14 november` / `2026-11-14`. **A weekday never matches a date** (`friday` ≠ `14 nov`; 14 Nov 2026 is a Saturday).
4. **Per-fact `aliases` map:** each required term → at most **3** contiguous phrases; each phrase has ≥ 1 content token; no phrase that is only `ITEM_STOPWORDS`; no phrase that is a required term or alias of a **different deal**; no phrase that is **exclusive** to a different fact on the same deal (shared terms such as `pricing` on two facts of one deal are allowed).
5. **Slot-value keys** `date:` and `amount:` (not a required term) hold the fact's typed date or amount when that value must be in `fact_slot_values` for `contradicts()` but must **not** be required to cover. Swapping `14 nov` → `15 nov` or `$90k` → `$9k` still has every required term; only the contradiction check fails.

A fixture-validation test rejects weekday↔date aliases, stopword aliases, **cross-deal aliases** (a phrase that is a required term or alias of a different deal), exclusive-to-another-fact aliases, and more than 3 aliases per term.

**Who (`person_entities_match`).** Do **not** use `names_match` (substring). Resolve the predicted string to a roster `person_id`:

1. `normalize()` as above.
2. If exactly one roster person on **that day** has that normalized `name`, resolve to their `person_id`.
3. If two or more roster people share that full name, the prediction must also include a company (or other roster qualifier) that uniquely selects one `person_id`. Otherwise it is unresolved.
4. First name only, company only, or any proper substring is **not** a resolution.

Two who-facts match iff they resolve to the same `person_id`. The gold who-fact's `person_id` is the required term. **Evidence link for who:** the claim's cited gold segments must **intersect** that who-fact's `evidence_segments` (the segments where that person is on screen). A correct who-claim that cites only some other segment of the same deal does not cover.

**Gold evidence link (all slots).** A claim is correct only if the gold segments of its cited frames **intersect** the paired fact's `evidence_segments`. Screen-text required-term checks on those cited frames still apply (for `who_involved`, a roster form of the person's name in that cited OCR). A citation that is merely "somewhere on the same deal" is not enough.

**Correct predicted claim.** On a predicted cluster that **matches** a gold deal that day: the claim covers a still-unpaired gold fact in the same slot on that gold deal (1:1), with evidence-segment overlap. That is a true positive for precision and for that slot's recall.

**Unsupported** is **citation failure only**. Tests:

1. **Resolve.** The claim has no resolvable citation (no evidence row, or the cite does not map to a fixture frame id) → unsupported.
2. **Cluster / deal membership** — two cases, do not mix them:
   - **Matched predicted deal:** at least one cited frame must belong to the matched gold deal that day. If none do → unsupported.
   - **Unmatched predicted deal:** test (2) checks **predicted-cluster membership only**. At least one cited frame must belong to that predicted cluster. If yes, and (1) passed, the claim is a **precision miss, not unsupported**. If no cited frame is on that cluster → unsupported. This is the only reading: an unmatched-deal claim is never both a precision miss and unsupported, and it never zeroes the summary score by itself.
3. **Paired evidence text.** If the claim is paired with a gold fact, the cited frames' OCR must contain that fact's required terms **and** the cited gold segments must intersect `evidence_segments`. Failure → unsupported. Unpaired (extra) claims **skip** this clause.

Well-cited extra claims are **precision misses, not unsupported**. Gold facts on unmatched gold deals are recall misses. Claims are never skipped from the precision denominator.

**Strict claim precision** = (correct predicted claims) / (all predicted summary claims, including unmatched-deal claims). If there are no predicted claims and the corpus has at least one gold fact, precision is **0.0**. If both sides are empty, precision is **1.0**.

**Slot recall** for `what_happened`, `next_step`, and `who_involved` = (gold facts in that slot paired with a correct prediction) / (gold facts in that slot). If a slot has zero gold facts in the corpus, omit it from the min below.

**Unsupported count** = predicted claims that fail the citation tests above. Gate: **0**.

**Deal summary accuracy** = `min(claim_precision, what_happened_recall, next_step_recall, who_involved_recall)` when unsupported count is 0, else **0.0**. Gate: ≥ 0.85.

#### Fixture-validation test (authoritative; required at M3f)

These checks are **ordinary pytest** under `tests/` (for example `tests/eval/test_fixture_validation.py`). They run in the existing Linux CI job (`ruff, mypy, pytest`). **No `.github` change.** They do not need a model.

The test **computes every trivial baseline on the actual gold corpus**. The build **fails** if any negative baseline's deal grouping accuracy or deal summary accuracy is **≥ 0.85**. It also **fails** if the honest-summary positive control scores **< 0.85**. Guidance counts in §11.1 do not substitute for this test.

| Baseline / control | How it is produced | Must score |
|---|---|---|
| All-`none` | every non-bait segment → `none` | grouping < 0.85 |
| All-one-deal | one predicted cluster for the whole day; and separately one cluster for the whole corpus | grouping < 0.85 |
| One deal per gold segment | each segment its own cluster | grouping < 0.85 |
| Group by app | cluster = `bundle_id` | grouping < 0.85 |
| Group by company | For each gold segment, take `window_title` only (never the gold deal name). If a roster / fixture `people[].company` or `deals[].company` value appears as a whole token in that title, the cluster id is that **company** string (casefolded). If two companies match, use the longest match, then lexicographically smaller. **No match → `none`.** The 12/1/1/1 synthetic gold carries such titles. | grouping < 0.85 |
| Empty summary | no claims | summary < 0.85 |
| **Keyword dump (over-cap)** | for each gold fact, one claim whose text is the unique OCR tokens of **all** segments of that deal that day, cited to all those segments (always > 24 content tokens). Tests the length cap. | summary < 0.85 |
| **Keyword dump (within-cap)** | for each deal-day, join that deal's required terms (no verbs or sentence wrapper added) and emit each ≤24-content-token window **in every slot**, cited to that deal's segments. Lists terms without being true claims. | summary < 0.85 |
| **Keyword dump (duplicate-what)** | for each deal-day, emit each ≤24-content-token window of that deal's required terms **twice** in `what_happened`, as `will <window>` (or `need to <window>`) in `next_step`, and once in `who_involved`. Gold-free: dump windows only, no gold fact pairing. Required variants of the second `what_happened` copy: extra token (`also` / `again` / `ok` / `noted`); 4–12-token pads (`update status review progress` and longer); last token dropped; `please` on one copy and `also` on the other; three copies; duplicate only on deals with ≥ 2 channels; reversed word order; windows split at a different term offset. | summary < 0.85 |
| **Keyword dump (sliding-window)** | for each deal-day, sliding 6-term windows at stride 2 of that deal's required terms (unique and raw). Each window in `what_happened`, as `will <window>` in `next_step`, and once in `who_involved`. Nearby widths 5–7 and strides 1–3 on both term lists are the same baseline. | summary < 0.85 |
| **Honest summary (positive)** | a hand-written, well-cited correct summary for **each fixture deal** (committed next to the fixture, e.g. `honest_summary.yaml`): full names, deadlines, one claim per gold fact, cites overlapping `evidence_segments`. | summary **≥ 0.85** |

The 12/1/1/1 Acme shape (60 segments, 12 noise, Acme 12/1/1/1, others 8/8/8/9, group-by-company F1 **0.905**, titles containing the roster company) is a **required failing fixture** of the group-by-company check: pytest must load that synthetic gold under `tests/fixtures` and assert group-by-company ≥ 0.85 so the validator is proven to catch it. The committed V1 corpus must then be a different shape that the same checker accepts (every negative baseline < 0.85, honest summary ≥ 0.85).

Privacy-bait leaking into a baseline deal is a privacy FAIL, not a grouping credit.

### 11.3 Privacy tests

- Sensitive-trap fixture: no `frame_text` row for excluded apps/titles; redaction placeholders present for card/secret patterns.
- Spool empty after replay; `frames.deletion_verified = 1` for every extracted frame; audit rows present.
- No-network test (§6).
- Log scan test (§5.4).
- Dashboard bind test: connection to the host's LAN IP refused; request with foreign `Origin` rejected; request without cookie rejected.
- Pause test: helper emits no frames while paused (helper has a `--selftest` mode).

### 11.4 Performance tests (M0 then regression)

Per frame: OCR time, VLM time; per day: consolidation time, peak RSS of worker, database growth; capture helper CPU over an hour.

Two hardware profiles, **same default model** on both (§14 #9). Helper < 2 % CPU average; OCR < 1 s/frame; VLM disabled in V1.0; one model resident at a time; the machine remains usable for normal work while the worker runs; database growth < 20 MB/day; consolidation < 15 min on primary (recorded, not gated on the 8 GB min-spec).

- **Primary** (32 GB target laptop): understanding p95 < 20 s/segment; peak memory (worker plus model server, Darwin `phys_footprint`) < 3 GB; consolidation < 15 min. Unchanged (Kieran 21:53).
- **Minimum-spec** (8 GB development / testing Mac): understand p95 is **recorded, not gated**. Memory gate is **completed without crash or OOM kill** (every fixture day processed, worker exit 0, no runtime connection loss or kill). Peak worker+model and `consolidate_s` are reported but not gated. Deal grouping accuracy ≥ 0.85 and deal summary accuracy ≥ 0.85 stay gating on both profiles. 7–9B Q4 models are allowed; timeout/num_ctx are per-model (3600 s for 7–9B / unknown models; 300 s for 3B). Engineering headroom (p95 < 30 s, peak < 3.3 GiB) is recorded in `report.json` → `headroom` and does not fail the gate.

**Default-model rule:** the V1 default is the largest model that completes on 8 GB (no OOM kill) **and** meets the active quality gate. Candidates remain biggest first: `gemma2:9b` Q4, `llama3.1:8b` Q4, `qwen2.5:7b` Q4; go smaller only if a bigger one crashes or misses the gate. Code default stays `qwen2.5:3b` until the bake-off winner is sent. **Until M3f**, that quality gate is the committed `thresholds.json` (parked Revision 2 rows, including strict decisions/commitments precision ≥ 0.85). **From M3f**, those rows are report-only and the gate is deal grouping accuracy ≥ 0.85 and deal summary accuracy ≥ 0.85.

Targets for the **16 GB V1.2 profile** (commercialisation, not the M3 eval profiles above): as the original 8 GB line plus VLM < 15 s/frame when used; consolidation < 10 min; worker peak RSS < 6 GB.

### 11.5 Manual dogfooding

Before M5 completes, the developer runs W.A.T.S.O.N on their own machine with only synthetic/authorised content on screen, for several full days, reviewing the Memory page and corrections each evening. Findings feed prompt and resolver changes with corresponding fixture additions.

---

## 12. Milestones (Revision 3, trimmed) with acceptance criteria

Each milestone ends with a tagged build and its acceptance tests green. Status is as of `origin/main` at `ed3ce84` (2026-11-03).

| ID | Status | Tag / landing | Revision 3 note |
|---|---|---|---|
| M0 | **Done** | `v0.0.0-m0` | Unchanged |
| M1 | **Done** | `v0.1.0-m1` | Unchanged |
| M2 | **Done** | `v0.2.0-m2` | Unchanged |
| M3a–M3e | **Done** (as originally scoped) | landed `#27`–`#31` (+ quality `#36` `#39` `#40` `#41`); no `v0.3.0-m3` tag yet | Understanding, claims, consolidation, day summary, eval harness. Generic decision/commitment/achievement extract stays in code; it is **not** the remaining V1 gate |
| M4a–M4b | **Done** (as originally scoped) | landed `#32` `#35` `#38`; no `v0.4.0-m4` tag yet | Today, History, Projects, People, Memory. Generic project/achievement reports are not a V1 gate |
| M3f | **Remaining** | next tag `v0.3.1-m3f` | Deal grouping + per-deal daily summary + §11.2 fixtures/gate |
| M5 | **Remaining** | tag `v0.5.0-m5` | Live capture. Unchanged except the summary is per-deal |
| M6 | **Dropped from V1** | — | Merged into §16 (Ask W.A.T.S.O.N, weekly/monthly/project/achievement reports). Markdown export of a day's deal summaries moves into M3f/M7 |
| M7 | **Remaining** | tag `v0.7.0-m7` | Hardening. Definition of done is daily deal summaries, not weekly reports |

### M0 — De-risk the two unknowns (in parallel) — **done**

**A. Hardware and model bake-off.** Benchmark candidate VLM/LLM/embedding models on the development Mac. Output: `docs/benchmarks/<date>.md` and a chosen default per role (ADR 0001).

**B. Capture spike.** Swift helper that captures the frontmost window via ScreenCaptureKit, runs Vision OCR, writes PNG + sidecar, honours pause over stdin, detects lock/sleep/idle. Signed with a stable identifier; verify TCC permission survives a rebuild. Measure CPU.

*Acceptance (M0-A, 2026-10-31, ADR 0001):* benchmark report with recommendation from the 8 GB Ollama run. The original bake-off wording is narrowed as follows:

- Three-runtime comparison → **M3**, Watson Lead — only Ollama was measured.
- Per-model RSS → **M3**, Watson Lead — the harness RSS is the Python client, not model weights.
- 50 hand-scored fixture frames → **M3**, Watson Lead — this run used 5 sample frames; `scoring.csv` is blank.
- 16 GB fallback per role → **V1.2** — §0.1 is 8 GB first; 16 GB is not a V1 requirement.

*Acceptance (M0-B):* helper runs for one hour on a test desktop producing correct sidecars with < 2 % CPU; permission persists across two rebuilds.

*Tagged build:* `v0.0.0-m0`.

### M1 — Foundation and development loop — **done**

Repo, `pyproject`, CLI, config schema, encrypted SQLite with Alembic, `jobs` queue, structured redacted logging, FastAPI skeleton with auth and status page, `watson replay` reading a fixture directory, `fixture_gen` producing the first two day fixtures, CI running unit + privacy-log tests.

*Acceptance:* `watson replay` ingests a fixture into `frames` with deletion verification and audit; dashboard status page shows counts; no-network test passes.

*Tagged build:* `v0.1.0-m1`.

### M2 — Extraction and segmentation (no LLM) — **done**

Redaction, perceptual-hash dedup, segmentation, tier-2 retention, Today timeline (segments only), spool sweeper, backlog handling.

*Acceptance:* all five fixtures segment with ≤ 5 % over-segmentation vs. truth; sensitive-trap fixture produces no text rows for excluded content; Today page shows the live timeline from replay.

*Tagged build:* `v0.2.0-m2`.

### M3 — Understanding, memory and consolidation — **landed (M3a–M3e); remaining V1 work is M3f**

Runtime adapters, understanding prompt and schema, claims/evidence, entity resolution with aliases, day consolidation (idempotent, catch-up), daily summary rendering with citations, eval harness and metrics.

*Acceptance (M3a–M3e, landed):* re-running consolidation twice yields identical row counts; a daily summary is visible for each fixture day; replay CI pipeline gate is green. The original "§11.2 met on fixtures" line is **retargeted to M3f** (deal grouping / deal summary, not generic project/decision/commitment/achievement F1).

*Tagged build:* not yet cut; land `v0.3.0-m3` on the M3e line if a historical tag is wanted. V1 acceptance moves to M3f.

### M3f — Deal grouping and per-deal daily summary — **remaining**

Retarget consolidation so the day's segments cluster by **sales opportunity / deal**, then emit one daily summary per deal (what happened, who was involved, next step; every claim cited). Add gold fixtures for multi-touch deals (email + call + doc/CRM in one day), look-alike deal names, and non-deal noise. Score the §11.2 gate as defined above. Markdown export of that day's deal summaries is enough; weekly/project/achievement reports stay parked.

*Acceptance:* deal grouping accuracy ≥ 0.85 and deal summary accuracy ≥ 0.85 on the fixture corpus with the bake-off model (the 0.85 gate is the allowance; there is no extra zero-tolerance merge rule); unsupported summary claims = 0 (citation failures only); re-running consolidation twice yields identical deal clusters and row counts; each matched gold deal has a visible cited summary; the §11.2 **fixture-validation pytest** under `tests/` is green in Linux CI (negative baselines < 0.85 including both keyword dumps and title-only group-by-company; honest-summary control ≥ 0.85; the 12/1/1/1 corpus is asserted to fail group-by-company); `privacy_bait` frames are `skipped` excluded_app/title and absent from stored output; parked `thresholds.json` rows are report-only; §11.3 privacy tests still pass. Parked extractors (§16) are not required. §3.5 retrieval stays.

*Tagged build:* `v0.3.1-m3f`.

### M4 — Dashboard on real pipeline output — **landed (M4a–M4b)**

Today (with observations), History, Projects/People, Memory with corrections, Privacy/Settings, menu bar in supervisor, deletion flows with audit.

*Acceptance (landed):* corrections change subsequent summaries on re-consolidation; delete-day removes tiers 1–4; every page usable at 1280 px wide with no Node toolchain.

V1 uses Today / History / Memory / People as the deal-journal surfaces. Generic Projects and the Reports editor are parked (§16); no further M4c dashboard milestone is required for V1. Deal groups produced by M3f must show on the existing **Today** page (acceptance sits on M3f). History remains a day list; deal work is per-day and is not grouped again on History.

*Tagged build:* not yet cut; land `v0.4.0-m4` on the M4b line if a historical tag is wanted.

### M5 — Live capture integration — **remaining**

Supervisor launches and monitors the helper; permission states; exclusions UI; work hours; pause/resume end-to-end; adaptive interval; power/thermal policy; crash recovery after `kill -9` of each process.

*Acceptance:* a full synthetic-content day on the developer's Mac produces **per-deal daily summaries** with spool median time-on-disk < 5 min; pausing produces zero frames; permission revocation is detected within one tick and surfaced.

*Tagged build:* `v0.5.0-m5`.

### M6 — Retrieval and reporting — **dropped from V1 (parked, not deleted)**

Hybrid search, Ask W.A.T.S.O.N with citations, weekly/monthly/project/achievement reports, editor with versions, Markdown export of those reports.

*Acceptance (parked, §16):* 30 scripted questions over a multi-week fixture corpus answered with correct dates and resolvable citations ≥ 90 %; weekly report for a fixture week cites only existing claims; edits persist across restarts.

Markdown export of V1 daily deal summaries is delivered under M3f/M7, not here. No V1 tag.

### M7 — Hardening for daily personal use (V1 definition of done) — **remaining**

Key management verified against the real Keychain, egress guard active in every process, 8 GB resource governance (sequential model loading, worker RSS cap, VLM off, backpressure), log rotation, low-disk pause, crash recovery of each process, launchd LaunchAgent for start-at-login with the menu bar, `watson models verify` against the pinned model manifest, catch-up consolidation exercised across a real sleep/close-lid night.

*Acceptance:* the developer has used W.A.T.S.O.N for ten consecutive working days with real work on screen; every day reached `consolidated` (including at least two via catch-up); the spool was empty each morning; the author **used that day's per-deal summaries** with edits rather than rewrites (emails / calls / docs about the same opportunity recognised as one deal); the eval gates in §11.2 still pass on fixtures; no privacy test (§11.3) regressed. **This closes V1.**

*Tagged build:* `v0.7.0-m7`.

### Deferred to V1.2 — Distribution and assurance documentation

Former M7 packaging (signed, notarised app bundle with embedded helper and in-process runtime, model manifest, clean-machine install, 16 GB and 24 GB profiles) and the former M8 documentation pack (architecture and data-flow diagrams, SBOM, model inventory with licences and hashes, permissions list, network behaviour statement with test evidence, retention/deletion policy, threat model, install/removal instructions, test results) are specified in detail in `WATSON_V1_2_COMMERCIALISATION_PLAN.md`. In the commercial context the documentation pack serves customers first (website and in-app privacy explanation) and is also offered as an "information for your employer" sheet for individuals who install W.A.T.S.O.N on a work Mac.

---

## 13. Risk register

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| TCC permission resets on every build | High without D5 | Blocks dogfooding | Signed stable helper (M0-B) |
| 8 GB development Mac cannot hold LLM + VLM + embeddings | Certain | Core promise | OCR-only extraction on 8 GB; sequential model loading; small LLM; bench harness `8gb` profile |
| Local VLM too slow/inaccurate on 16 GB (V1.2 profile) | High | Core promise | OCR-first (D2); VLM optional; measured at M0 |
| Author-only dogfooding hides usability problems for others | Medium | V1.2 adoption | Accept for V1.0; founding-member beta at V1.2 |
| LLM hallucinates completions/achievements | High | Trust (parked after V1) | Claims + evidence + citation verifier; zero-tolerance metric; parked in §16 |
| Entity resolution fragments or merges deals ("Acme Q4" vs "Acme Renewal"; email + call + doc of the same opportunity) | High | Usefulness of the daily deal summary | Alias table, embedding similarity, provisional entities, user merge; §11.2 grouping gate (assignment + pairwise precision); look-alike and multi-touch fixtures |
| Entity resolution fragments projects ("Acme", "the Acme deal", "Q4 proposal") | High | Usefulness of Projects page (parked) | Alias table, embedding similarity, provisional entities, user merge; parked in §16 |
| SQLCipher packaging friction | Medium | Encryption requirement | Plan B column encryption decided at M1 |
| Window titles/URLs leak sensitive context | Medium | Privacy | Title retention 30 days, title capture toggle, domain denylist |
| Spool grows while worker is down | Medium | Privacy exposure | Menu bar backlog warning, 24 h hard limit with audit, low-disk pause |
| Day-end on a closed laptop | High | Missing summaries | Catch-up consolidation (D12) |
| Sleep/wake and display changes break capture | Medium | Gaps | Helper handles `NSWorkspace` notifications; supervisor health checks |
| Apple Silicon vs Intel differences | Medium | Packaging scope | Apple Silicon only in V1; Intel is a separate target |
| Prompt changes silently regress quality | Medium | Trust | Versioned prompts; eval gate in CI |

---

## 14. Open decisions (owner: engineering, by milestone)

| # | Decision | Decide at |
|---|---|---|
| 1 | V1.0 **dev** runtime: Ollama | **Decided M0-A** — ADR 0001 |
| 2 | Default VLM/LLM/embedding models and quantisations (8 GB) | **Partial (Kieran, 2026-11-02 21:53/21:55 via CoS; gate restated 2026-11-03 10:38 via CoS)** — maximise accuracy; 7–8B allowed in V1. The default is the **largest** model that runs to completion on the 8 GB Mac **and meets the §11.2 gate** (deal grouping accuracy ≥ 0.85 and deal summary accuracy ≥ 0.85), however slow. Candidates, biggest first: `gemma2:9b` Q4, `llama3.1:8b` Q4, `qwen2.5:7b` Q4; go smaller only if a bigger one crashes or misses the gate. Code baseline until bake-off: `qwen2.5:3b`. `llama3.2:3b` remains selectable via `WATSON_LLM_MODEL` / `watson eval --llm-model`. Embeddings remain ADR 0001 provisional (`nomic-embed-text`). |
| 3 | Swift helper vs PyObjC fallback | **DECIDED (Watson Lead, M0-B)** — Swift helper with ScreenCaptureKit + Vision, built with `swiftc` under Command Line Tools only (no Xcode), self-signed `WATSON Dev` identity; TCC grant stable across rebuilds. [`docs/results/m0b-capture-spike.md`](results/m0b-capture-spike.md), [`docs/results/m0b/artefacts.md`](results/m0b/artefacts.md) |
| 4 | SQLCipher vs application-level column encryption | M1 |
| 5 | Minimum macOS version (SCK screenshot API availability suggests 14+) | **DECIDED** — minimum macOS 14 (tested on 14.5); no required APIs missing. [`docs/results/m0b-capture-spike.md`](results/m0b-capture-spike.md), [`docs/results/m0b/artefacts.md`](results/m0b/artefacts.md) |
| 6 | Whether to request Accessibility permission for browser URLs in V1, or defer | M5 |
| 7 | Tier-2 text default TTL (7 days proposed) | M3, after seeing re-consolidation needs |
| 8 | Dashboard framework details (HTMX vs plain fetch) | M1 |
| 9 | Whether the 8 GB LLM is good enough for consolidation, or whether V1 ships OCR + segments + rule-based summaries with the LLM only for Ask W.A.T.S.O.N | **DECIDED (Kieran, 2026-11-02 16:41 option A; superseded in part by 21:53/21:55 via CoS; 2026-11-03 02:59 via CoS; consolidation *output* restated 2026-11-03 10:38 via CoS)** — V1 keeps LLM consolidation. Consolidation output in V1 is **deal grouping + a per-deal daily summary**, not generic project/decision/commitment/achievement reports. **Primary** unchanged: p95 < 20 s/segment, worker+model peak < 3 GB, consolidation < 15 min. **Min-spec:** understand p95 recorded, not gated; memory gate = completes without crash or kill; `consolidate_s` recorded, not gated; peak worker+model reported, not gated. Deal grouping accuracy ≥ 0.85 and deal summary accuracy ≥ 0.85 stay gating on both. 7–9B timeout preset is 3600 s. **Kieran 2026-11-02 21:53 via CoS:** "Maximise for accuracy. Timing is not an issue as it will be on a more powerful Mac usually this is just the base minimum". **21:55:** "I would opt for the biggest one that fits on this Mac despite the timing". |
| 10 | Package runtime: llama.cpp vs MLX (in-process default, D6) | **M3**, owner Watson Lead — not measured at M0-A |
| 11 | V1 product scope: generic work journal vs sales-deal screenshot journal | **DECIDED (Kieran via CoS, 2026-11-03 10:38 UTC)** — V1 is screenshot capture → per-segment understanding → group the day's activity by sales opportunity/deal → daily summary per deal (what happened, who was involved, next step; each claim cited). "If someone emails me throughout the day it needs to be recognised as the same sales opportunity or deal." The previous spec is too complicated; V1 should take screenshots and summarise the day. Generic project, decision, commitment and achievement extraction and their reports move to §16 (not deleted). §11.2 gate is deal grouping accuracy ≥ 0.85 and deal summary accuracy ≥ 0.85. 8 GB min-spec and 32 GB primary rules unchanged. Default model = largest that completes on 8 GB and meets the gate. |

**D1–D12 impact of #11 (2026-11-03 10:38 UTC):**

| Decision | Effect |
|---|---|
| D1, D2, D4, D5, D6, D7, D9, D12 | **Unchanged.** Frontmost-window capture, OCR-first, delete-on-extract, multi-process, runtime adapter, encrypted SQLite, menu bar, work-day lifecycle, privacy and local-first all stay. |
| D3 | **Confirmed, not changed.** Segments remain the unit of understanding; deal grouping runs *after* per-segment understanding. |
| D8 | **Narrowed for V1.** Claims/evidence/assertion stay. V1 first-class outputs are cited deal-summary claims (what / who / next step). Generic `decision` / `commitment` / `achievement` extract and reports are parked (§16). |
| D10 | **Fixtures retargeted.** V1 gold is deal clusters + per-deal summaries: multi-touch (email/call/doc), look-alike names, non-deal noise. Project/decision/commitment/achievement truth is kept for parked work. |
| D11 | **Dashboard narrowed.** V1 surfaces are Today / History / Memory / People-on-deals. Generic Projects page and the Reports editor are parked. Server-rendered HTMX + local auth unchanged. |

#2 and #9 are the numbered rows this decision restates (gate metric and consolidation output). #4, #6, #7, #8, #10 are untouched.

---

## 15. Immediate next steps

M0–M2 are done and tagged. M3a–M3e and M4a–M4b have landed. Remaining V1 work, in order:

1. **M3f** — deal grouping + per-deal daily summary; gold fixtures (multi-touch, look-alike names, non-deal noise); implement and gate the §11.2 definitions in `docs/eval.md`. Do not loosen privacy tests.
2. Cut `v0.3.1-m3f` when M3f acceptance is green.
3. **M5** — live capture integration on the author's Mac; tagged `v0.5.0-m5`.
4. **M7** — harden for daily personal use; tagged `v0.7.0-m7`. V1 closes when the author uses ten consecutive days of per-deal summaries.

Do not start parked §16 work (Ask W.A.T.S.O.N, weekly/project/achievement reports, generic decision/commitment extract) in V1.

The order still honours the v1.1 intent — prove W.A.T.S.O.N can understand and remember a working day before it observes one — while answering Kieran's V1 question: did it screenshot the day and group the sales activity into deals?

---

## 16. Parked after V1

Nothing in this section is deleted. It is out of V1 scope (Kieran via CoS, 2026-11-03 10:38 UTC). Resume it after V1 closes, or fold pieces into V1.1 / V1.2 / V2 if a later plan calls for them.

### 16.1 Generic project extraction

Revision 2 treated **projects** as first-class entities alongside people. Understanding proposed `project_candidates`; the resolver linked them by alias + embedding; consolidation wrote per-project daily deltas (`entity_history`); the dashboard had a Projects page; eval scored **project identification F1 ≥ 0.80**.

V1 grouping is by **sales opportunity / deal**, not by generic project. The `entities` table, aliases, and Projects page that already landed stay in the repo. New V1 work must not gate on generic project F1. After V1, project entities can be re-enabled as a layer above deals (a project may contain several deals) without redesigning capture.

### 16.2 Decision extraction

Revision 2 consolidation step: *decision extraction across the whole day (cross-segment: a decision in chat about a document edited later).* Claim kind `decision`. Eval: decisions recall ≥ 0.70 / precision ≥ 0.85. Gold `truth.yaml` listed decision text + evidence frames.

A decision that **is** the next step or an observed outcome of a V1 deal may still appear as a cited deal-summary claim. Harvesting a day-wide decisions list, independent of deals, is parked.

### 16.3 Commitment extraction

Revision 2: cross-segment commitments (`kind: commitment`, `status: open/done/dropped`, `due_hint`). Eval: commitments recall ≥ 0.70 / precision ≥ 0.85. Item matching for commitment voice (`I will …`) is documented in `docs/eval.md` and remains the parked matcher.

V1 may emit a single **next step** per deal, cited. A general commitments inbox, due dates, and open/done/dropped tracking across projects are parked.

### 16.4 Achievement extraction

Revision 2: achievement candidates are outcomes with `observed` assertion and change evidence; never from `inferred` alone. Eval: **unsupported achievement claims = 0**; completed vs. not-completed accuracy ≥ 0.90. Duplicate-achievement merge keys and the completed-item matcher stay in the repo for parked scoring.

V1 deal summaries may say what happened on a deal that day, cited. A completed-work / achievement roll-up, "published / sent / approved" classification across the day, and achievement reports are parked.

### 16.5 Reports that consume the parked extractors

Former **M6** (dropped from V1, not deleted):

- Hybrid search and **Ask W.A.T.S.O.N** with citations (§7.3).
- Weekly / monthly / **project** / **achievement** reports.
- Report editor with version history, "copy as Markdown", "export .md" of those reports.
- Acceptance: 30 scripted questions over a multi-week fixture corpus at ≥ 90 % correct dates and resolvable citations; weekly report for a fixture week cites only existing claims; edits persist across restarts.

V1 Markdown export is limited to that day's per-deal summaries (M3f / M7).

### 16.6 Parked eval metrics (former §11.2 rows)

Kept so they can be restored without rewriting the gate:

| Metric | Target | Notes |
|---|---|---|
| Project identification F1 | ≥ 0.80 | Generic projects |
| People identification F1 | ≥ 0.75 | Still useful on deals; not a V1 gate |
| Completed vs. not-completed classification accuracy | ≥ 0.90 | Achievements |
| Decisions recall / precision | ≥ 0.70 / ≥ 0.85 | Day-wide decisions |
| Commitments recall / precision | ≥ 0.70 / ≥ 0.85 | Day-wide commitments |
| Unsupported achievement claims | 0 | No evidence frame in truth |
| Duplicate observations for the same activity | < 5 % | Over-segmentation; still measured by the current harness |
| Noise correctly classified `non_work` | ≥ 0.90 | Distinct from gold `none` (not-a-deal). A news-site segment can be `non_work` *and* `none` |

Until M3f, these rows (as committed in `thresholds.json`) still select the bake-off model. **At M3f they become report-only**; the model gate switches to deal grouping / deal summary. Structural 100 % evidence / 100 % citations, the §11.3 privacy tests, and **§3.5 understanding retrieval** were **not** parked.
