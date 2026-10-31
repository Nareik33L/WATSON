# WATSON V1 — Engineering Plan (Revision 2)

**Supersedes:** WATSON V1 Product Requirements & Engineering Specification v1.1
**Status:** Proposed engineering baseline
**Platform:** macOS (Apple Silicon first), local-first, offline-capable
**Core language:** Python, with a small Swift capture helper

This document keeps the product definition, principles and non-goals of v1.1 unchanged. It revises *how* WATSON is built. The changes fall into four groups:

1. **Privacy exposure is reduced by an order of magnitude** — frames are deleted minutes after capture rather than at end of day, only the frontmost window is captured by default, and extracted text is itself tiered and expiring.
2. **The AI pipeline is restructured** around OCR-first extraction and *activity segments* rather than per-screenshot vision-model calls. This is cheaper, more accurate, and gives verbatim evidence for provenance.
3. **The process model and platform integration are made realistic** — a signed Swift helper owns capture, inference runs in an isolated worker, the menu bar shows capture state, and packaging/TCC-permission pitfalls are addressed up front.
4. **Development is driven by a replay harness and a synthetic golden dataset**, so the memory and reporting engine can be built and measured before any real screen is ever captured — which is what the v1.1 "immediate next step" asked for, but the v1.1 milestones did not deliver.

---

## 0. Summary of changes from v1.1

| Area | v1.1 | Revision 2 | Why |
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

Everything in v1.1 §3.2 (exclusions), §21 (principles) and §22 (product definition) stands unchanged.

### 0.1 Release framing: V1 is the build that works for its author

V1.0 is a **personal build**: WATSON running every working day on the developer's own Mac (Apple Silicon, **8 GB**, macOS 14+), producing summaries and reports the developer actually uses. It is complete when the author relies on it, not when it can be handed to someone else. Meeting/audio intelligence follows as **V1.1**, and everything needed to sell and distribute it to other people (packaging, notarisation, licensing, onboarding, customer documentation) is **V1.2 — Commercialisation** (`WATSON_V1_2_COMMERCIALISATION_PLAN.md`). The full ladder is in `docs/ROADMAP.md`.

Consequences for this plan:

- **Hardware target is 8 GB first.** The VLM is off by default on 8 GB (OCR-only extraction, D2 already allows this); the LLM is a ~3–4B-class model at Q4 with a 4–8k context; the embedding model is ≤ 0.5B; models load sequentially, never together. 16 GB+ settings are a configuration profile exercised by the bench harness, not a V1 requirement. The 16 GB figures elsewhere in this document are retained as the profile targets for V1.2.
- **Packaging in V1 is "runs from the repo on this Mac"**: `uv run watson run`, a launchd LaunchAgent for start-at-login, and the signed helper. No PyInstaller bundle, no clean-machine install, no Intel.
- **Security controls that protect the author's own data stay in V1** (encrypted DB with Keychain key, loopback-only dashboard with token auth, egress guard, redacted logs, spool permissions). Controls whose purpose is third-party assurance (SBOM, threat-model document, installation/removal instructions, security documentation for customers and employers) move to V1.2.
- **Dogfooding is the acceptance test.** From M5 onward the developer runs WATSON daily with real work on screen; the synthetic fixtures remain the regression gate, but the definition of done for V1 is the author's own weekly report, generated by WATSON and edited rather than rewritten.
- Milestones M0–M6 are unchanged. M7 becomes *hardening for daily personal use*; the former M7 packaging work and M8 documentation pack are now V1.2.

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

Every statement WATSON may later repeat is a **claim** row with:

- `kind`: `activity | outcome | decision | commitment | achievement | person_interaction`
- `assertion`: `observed | inferred | unconfirmed | user_confirmed | user_corrected | user_rejected`
- `confidence`
- links to one or more **evidence** rows (segment ID, frame time range, short verbatim OCR snippet, optional VLM description)

Summaries and reports are rendered *from* claims and cite them. The dashboard lets the employee confirm, correct or reject a claim; corrections propagate to future reports. This implements v1.1 §6.4, §7.4 and §11.4 as data rather than as prompt instructions.

### D9 — Menu bar presence is mandatory

A menu bar item (via `rumps` in the supervisor) shows: capturing / paused / blocked (no permission) / backlog, with Pause (15 min, 1 h, rest of day), Open dashboard, Quit. This is the "visible capture status" requirement made real, and the fastest route to "pause immediately".

### D10 — Replay harness and synthetic golden dataset are the primary development loop

`watson replay --fixtures tests/fixtures/day_acme_q4/` runs the full pipeline on a directory of frames + sidecars with no capture involved. Fixtures are generated by rendering fictional "apps" (mail, docs, chat, spreadsheet, browser, IDE) as HTML with Playwright into PNGs, with a ground-truth YAML describing projects, people, decisions, commitments, completed vs. uncompleted work and off-topic noise.

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

A segment becomes eligible once it is closed (window changed) or exceeds 20 minutes. The LLM receives: app/window/URL, dwell, change statistics, merged text (trimmed), optional visual description, and **retrieved context**: top-k matching project and people entries from memory (hybrid search over the segment text). It returns an **observation** with candidate claims, each tagged `observed`/`inferred`/`unconfirmed` and each pointing at the evidence snippet lines it relied on.

Entity linking: the model proposes `project_candidates` and `people_candidates` as free text; a resolver maps them to existing entities by alias table + embedding similarity, creating new entities only above a threshold and marking them `provisional` for user review. The model never emits database IDs.

### 3.6 Adaptive capture and backpressure

- Interval: 45 s default; stretches to 120 s when the last three frames were duplicates; tightens to 30 s on window change. Configurable bounds.
- Backpressure: if spool depth exceeds a threshold, the worker disables VLM for new frames (OCR-only degraded mode) and the menu bar reports it. If depth keeps growing, capture interval increases. Observations are never dropped.
- Power/thermal: on battery or at `ProcessInfo.thermalState >= .serious`, VLM is disabled and consolidation defers to AC power unless the user forces it.

### 3.7 Day consolidation

Trigger: configured day-end, or catch-up on launch. Idempotent by `(working_day_id, pipeline_version)`; re-running replaces the previous output in a transaction rather than appending.

1. Confirm all frames for the day are `extracted` or terminally `failed`; otherwise wait/retry up to the recovery deadline, then proceed with a recorded gap.
2. Close open segments; run understanding on any pending.
3. Cluster observations into *activities* (same project/people/topic across apps).
4. Project/people memory update: for each touched entity, generate a delta ("what changed about this project today") grounded in the day's claims; append to entity history.
5. Decision/commitment extraction across the whole day (cross-segment: a commitment in chat about a document edited later).
6. Achievement candidates: outcomes with `observed` assertion and change evidence; never from `inferred` alone.
7. Daily narrative generated from claims; each paragraph carries claim IDs.
8. Verify persistence (`PRAGMA wal_checkpoint`, re-read summary row).
9. Apply `frame_text` retention (§5), sweep spool, audit.
10. Set day `consolidated`.

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
| `claims` | Atomic statements WATSON may repeat | `observation_id`, `working_day_id`, `kind`, `text`, `assertion`, `confidence`, `project_id`, `status` (for commitments: `open/done/dropped`), `due_hint` |
| `evidence` | Why a claim is believed | `claim_id`, `segment_id`, `frame_id`, `snippet` (≤ 300 chars, retained), `snippet_kind` (`ocr/visual`) |
| `entities` | Projects and people (single table, `type`) | `type`, `canonical_name`, `description`, `first_seen`, `last_seen`, `provisional`, `merged_into_id` |
| `entity_aliases` | Names the model has used for an entity | `entity_id`, `alias`, `source` |
| `claim_entities` | Many-to-many | `claim_id`, `entity_id`, `role` |
| `entity_history` | Per-day deltas for projects/people | `entity_id`, `working_day_id`, `delta_text`, `claim_ids` |
| `daily_summaries` | Rendered narrative + structured sections | `working_day_id`, `narrative_md`, `sections_json`, `claim_ids`, `pipeline_version` |
| `reports` | Weekly/monthly/project/achievement reports | `type`, `period_start`, `period_end`, `entity_id`, `generated_md`, `claim_ids` |
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

- Bundle-ID exclusion list, pre-populated with password managers, banking apps, Messages, FaceTime, Photos, Health-type apps; user-editable.
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

| Role | Interface | Dev default | Package default (decided at M0) |
|---|---|---|---|
| OCR | Swift Vision in helper | — | — |
| Visual description | `VisionRuntime.describe(image, schema)` | Ollama | llama.cpp or MLX VLM, ~2–8B class |
| Understanding / consolidation / reports / Q&A | `LanguageRuntime.complete(messages, schema)` | Ollama | llama.cpp or MLX LLM, ~7–9B class at Q4–Q5, 8k+ context |
| Embeddings | `EmbeddingRuntime.embed(texts)` | Ollama | in-process small embedding model (≤ 1B) |

Model identifiers are configuration. Candidate families are enumerated and benchmarked at M0 rather than named here, because the local-model landscape changes faster than this document.

### 7.2 Prompting and output

- Prompts are versioned files under `intelligence/prompts/<task>/<version>.md` with a `prompt_version` recorded on every output row.
- Every call uses schema-constrained generation; outputs are validated with Pydantic. Failure → one retry with the validation error appended → job failure.
- Prompts include explicit calibration instructions mirroring v1.1 §6.4: outcomes require visible evidence of completion; "sent", "approved", "agreed" require the corresponding text; otherwise `unconfirmed`.
- Reports and answers are generated from a provided list of claims with IDs and must cite IDs inline (`[c:…]`); a post-processor verifies every cited ID exists and strips sentences whose citations are missing. Uncited material claims are rendered in an "unverified" section rather than in the body.

### 7.3 Retrieval (Ask WATSON, reports)

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

Sections as v1.1 §10.2, with these additions:

- **Today** shows the live segment timeline (app, window, dwell) even before any LLM output exists, so the user sees what WATSON saw within minutes.
- **Memory** exposes claims with their assertion badges and evidence snippets; one-click confirm / correct / reject / "not work"; project and person merge/rename.
- **Reports** have an editor with version history, "copy as Markdown", and "export .md". Nothing else leaves the app.
- **Privacy** shows: permission state, spool depth and oldest frame age, tier-2 text volume and next purge, exclusion lists, work hours, and the deletion controls with confirmation and audit trail.
- **Status bar** on every page: capturing / paused / blocked, worker health, model health.

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

`tools/fixture_gen` renders fictional applications from templates (mail client, document editor, chat, spreadsheet, browser with fake intranet, IDE, video call placeholder) into frame sequences with sidecars. Each day fixture ships a `truth.yaml`:

```yaml
projects: [Acme Q4 Proposal, Internal Onboarding Guide]
people: [Priya Natarajan (Acme), Tom Ashworth (Delivery)]
decisions:
  - text: Agreed to move the Acme demo to 14 Nov
    evidence_frames: [f0412, f0413]
commitments:
  - text: Send revised pricing to Priya by Friday
    status: open
completed: [Onboarding Guide section 3 published]
not_completed: [Acme proposal (still editing at day end)]
noise: [news site 10:42–10:49, personal calendar glance]
```

At least five day fixtures for V1: a focused single-project day, a fragmented multi-project day, a meeting-heavy day, a day with sensitive-content traps (fake banking tab, fake password manager), and a near-empty day.

### 11.2 Metrics and thresholds (gate in CI on fixtures)

| Metric | Target |
|---|---|
| Project identification F1 | ≥ 0.80 |
| People identification F1 | ≥ 0.75 |
| Completed vs. not-completed classification accuracy | ≥ 0.90 |
| Decisions recall / precision | ≥ 0.70 / ≥ 0.85 |
| Commitments recall / precision | ≥ 0.70 / ≥ 0.85 |
| **Unsupported achievement claims** (no evidence frame in truth) | **0** |
| Duplicate observations for the same activity | < 5 % |
| Noise correctly classified `non_work` | ≥ 0.90 |
| Claims with ≥ 1 evidence row | 100 % (structural) |
| Report citations resolvable | 100 % (structural) |

Thresholds are targets for the chosen model at M3; they are adjusted with evidence, not quietly.

### 11.3 Privacy tests

- Sensitive-trap fixture: no `frame_text` row for excluded apps/titles; redaction placeholders present for card/secret patterns.
- Spool empty after replay; `frames.deletion_verified = 1` for every extracted frame; audit rows present.
- No-network test (§6).
- Log scan test (§5.4).
- Dashboard bind test: connection to the host's LAN IP refused; request with foreign `Origin` rejected; request without cookie rejected.
- Pause test: helper emits no frames while paused (helper has a `--selftest` mode).

### 11.4 Performance tests (M0 then regression)

Per frame: OCR time, VLM time; per day: consolidation time, peak RSS of worker, database growth; capture helper CPU over an hour.

Targets on the **8 GB V1 machine**: helper < 2 % CPU average; OCR < 1 s/frame; VLM disabled; understanding < 20 s/segment; consolidation < 15 min; worker peak RSS < 3 GB with only one model resident at a time; the machine remains usable for normal work while the worker runs; database growth < 20 MB/day.

Targets for the **16 GB V1.2 profile**: as above plus VLM < 15 s/frame when used; consolidation < 10 min; worker peak RSS < 6 GB.

### 11.5 Manual dogfooding

Before M5 completes, the developer runs WATSON on their own machine with only synthetic/authorised content on screen, for several full days, reviewing the Memory page and corrections each evening. Findings feed prompt and resolver changes with corresponding fixture additions.

---

## 12. Milestones (re-sequenced) with acceptance criteria

Each milestone ends with a tagged build and its acceptance tests green.

### M0 — De-risk the two unknowns (in parallel)

**A. Hardware and model bake-off.** Benchmark candidate VLM/LLM/embedding models on the development Mac across the three runtimes: tokens/s, time per frame, RSS, quality on 50 sampled fixture frames (hand-scored). Output: `docs/benchmarks/<date>.md` and a chosen default per role with a 16 GB fallback.

**B. Capture spike.** Swift helper that captures the frontmost window via ScreenCaptureKit, runs Vision OCR, writes PNG + sidecar, honours pause over stdin, detects lock/sleep/idle. Signed with a stable identifier; verify TCC permission survives a rebuild. Measure CPU.

*Acceptance:* benchmark report with recommendation; helper runs for one hour on a test desktop producing correct sidecars with < 2 % CPU; permission persists across two rebuilds.

### M1 — Foundation and development loop

Repo, `pyproject`, CLI, config schema, encrypted SQLite with Alembic, `jobs` queue, structured redacted logging, FastAPI skeleton with auth and status page, `watson replay` reading a fixture directory, `fixture_gen` producing the first two day fixtures, CI running unit + privacy-log tests.

*Acceptance:* `watson replay` ingests a fixture into `frames` with deletion verification and audit; dashboard status page shows counts; no-network test passes.

### M2 — Extraction and segmentation (no LLM)

Redaction, perceptual-hash dedup, segmentation, tier-2 retention, Today timeline (segments only), spool sweeper, backlog handling.

*Acceptance:* all five fixtures segment with ≤ 5 % over-segmentation vs. truth; sensitive-trap fixture produces no text rows for excluded content; Today page shows the live timeline from replay.

### M3 — Understanding, memory and consolidation

Runtime adapters, understanding prompt and schema, claims/evidence, entity resolution with aliases, day consolidation (idempotent, catch-up), daily summary rendering with citations, eval harness and metrics.

*Acceptance:* metrics in §11.2 met on fixtures with the M0-chosen model; re-running consolidation twice yields identical row counts; daily summary visible for each fixture day.

### M4 — Dashboard on real pipeline output

Today (with observations), History, Projects/People, Memory with corrections, Privacy/Settings, menu bar in supervisor, deletion flows with audit.

*Acceptance:* corrections change subsequent summaries on re-consolidation; delete-day removes tiers 1–4 and re-renders affected reports; every page usable at 1280 px wide with no Node toolchain.

### M5 — Live capture integration

Supervisor launches and monitors the helper; permission states; exclusions UI; work hours; pause/resume end-to-end; adaptive interval; power/thermal policy; crash recovery after `kill -9` of each process.

*Acceptance:* a full synthetic-content day on the developer's Mac produces a summary with spool median time-on-disk < 5 min; pausing produces zero frames; permission revocation is detected within one tick and surfaced.

### M6 — Retrieval and reporting

Hybrid search, Ask WATSON with citations, weekly/monthly/project/achievement reports, editor with versions, Markdown export.

*Acceptance:* 30 scripted questions over a multi-week fixture corpus answered with correct dates and resolvable citations ≥ 90 %; weekly report for a fixture week cites only existing claims; edits persist across restarts.

### M7 — Hardening for daily personal use (V1 definition of done)

Key management verified against the real Keychain, egress guard active in every process, 8 GB resource governance (sequential model loading, worker RSS cap, VLM off, backpressure), log rotation, low-disk pause, crash recovery of each process, launchd LaunchAgent for start-at-login with the menu bar, `watson models verify` against the pinned model manifest, catch-up consolidation exercised across a real sleep/close-lid night.

*Acceptance:* the developer has used WATSON for ten consecutive working days with real work on screen; every day reached `consolidated` (including at least two via catch-up); the spool was empty each morning; the author produced two weekly reports from WATSON output with edits rather than rewrites; the eval gates in §11.2 still pass on fixtures; no privacy test regressed. **This closes V1.**

### Deferred to V1.2 — Distribution and assurance documentation

Former M7 packaging (signed, notarised app bundle with embedded helper and in-process runtime, model manifest, clean-machine install, 16 GB and 24 GB profiles) and the former M8 documentation pack (architecture and data-flow diagrams, SBOM, model inventory with licences and hashes, permissions list, network behaviour statement with test evidence, retention/deletion policy, threat model, install/removal instructions, test results) are specified in detail in `WATSON_V1_2_COMMERCIALISATION_PLAN.md`. In the commercial context the documentation pack serves customers first (website and in-app privacy explanation) and is also offered as an "information for your employer" sheet for individuals who install WATSON on a work Mac.

---

## 13. Risk register

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| TCC permission resets on every build | High without D5 | Blocks dogfooding | Signed stable helper (M0-B) |
| 8 GB development Mac cannot hold LLM + VLM + embeddings | Certain | Core promise | OCR-only extraction on 8 GB; sequential model loading; small LLM; bench harness `8gb` profile |
| Local VLM too slow/inaccurate on 16 GB (V1.2 profile) | High | Core promise | OCR-first (D2); VLM optional; measured at M0 |
| Author-only dogfooding hides usability problems for others | Medium | V1.2 adoption | Accept for V1.0; founding-member beta at V1.2 |
| LLM hallucinates completions/achievements | High | Trust | Claims + evidence + citation verifier; zero-tolerance metric |
| Entity resolution fragments projects ("Acme", "the Acme deal", "Q4 proposal") | High | Usefulness of Projects page | Alias table, embedding similarity, provisional entities, user merge |
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
| 1 | Package runtime: llama.cpp vs MLX | M0 |
| 2 | Default VLM/LLM/embedding models and quantisations | M0 |
| 3 | Swift helper vs PyObjC fallback | M0-B result |
| 4 | SQLCipher vs application-level column encryption | M1 |
| 5 | Minimum macOS version (SCK screenshot API availability suggests 14+) | M0-B |
| 6 | Whether to request Accessibility permission for browser URLs in V1, or defer | M5 |
| 7 | Tier-2 text default TTL (7 days proposed) | M3, after seeing re-consolidation needs |
| 8 | Dashboard framework details (HTMX vs plain fetch) | M1 |
| 9 | Whether the 8 GB LLM is good enough for consolidation, or whether V1 ships OCR + segments + rule-based summaries with the LLM only for Ask WATSON | M3, from the `8gb` bench profile and §11.2 results |

---

## 15. Immediate next steps

1. Create the repository layout in §10 with `pyproject.toml`, CI, and this document.
2. Start M0-A: write `tools/bench/` and run the model bake-off on the development Mac; record hardware (chip, RAM, macOS version).
3. Start M0-B: scaffold `helper/` and prove frontmost-window capture + Vision OCR + persistent TCC grant.
4. Build `tools/fixture_gen` with the first fictional app templates and the "focused single-project day" fixture, so M1's `watson replay` has real input on day one.

The order honours the v1.1 intent — prove WATSON can understand and remember a working day before it observes one — while retiring the two risks that would otherwise surface late: whether the chosen model is fast enough, and whether macOS will let the app capture at all.
