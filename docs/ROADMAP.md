# WATSON release ladder

Each release has one question it must answer. Scope that does not help answer that question moves to the release where it does.

| Release | Question it answers | Plan | Ships when |
|---|---|---|---|
| **V1.0 — Personal build** | Does WATSON work, every day, for its author on an 8 GB Apple Silicon Mac? | `WATSON_V1_ENGINEERING_PLAN.md`, milestones M0–M7 | The author has relied on it for ten consecutive working days and produced two weekly reports from its output (M7 acceptance) |
| **V1.1 — Meeting intelligence** | Can imported, authorised recordings feed the same memory as the screen? | `WATSON_V1_1_AUDIO_MEETINGS_PLAN.md`, milestones A0–A3 | Author has processed ten real authorised recordings; eval gates hold |
| **V1.2 — Commercialisation** | Can an individual buy it, install it on a clean Mac, activate it and keep it forever without a subscription or a server? | `WATSON_V1_2_COMMERCIALISATION_PLAN.md`, milestones C-M0–C-M6 | Notarised build, offline-verifiable licensing, onboarding, documents and website live; founding-member beta through two update cycles |
| **V2.0 — Personal refinement** | What do paying customers need next? (candidates: speaker diarization, live-microphone assessment, 16 GB+ visual understanding, more report types) | Open decisions carried from the V1.1 and V1.2 plans | Paid upgrade; not planned in detail |
| **V3.0+ — WATSON Organisation** | Can teams share approved artefacts and integrate business tools without touching personal memory? | v1.2 scope §2.2 and §10; boundary enforced from V1.2 (`core` / `commerce` / `sharing`) | Separate subscription product; not planned in detail |

## Rules that hold across every release

1. Local-first: work content is processed and stored locally; no cloud AI.
2. Individual-first: the person owns the memory and decides what is shared.
3. Raw captures and recordings are temporary; useful, evidenced claims are what persist.
4. No autonomous actions, no email sending, no covert capture or recording.
5. Evidence-based: every statement WATSON repeats cites a claim with evidence.
6. Nothing in a later release weakens a privacy guarantee made in an earlier one.
7. Personal is bought once and never depends on a server to run or to read its own data.

## Numbering note

The V1.0 plan was written before the release ladder existed; its original "M7 packaging" and "M8 IT pack" are delivered in V1.2, and its definition of done is personal daily use. The V1.1 meetings plan was briefly labelled V2 in a draft; it is V1.1.
