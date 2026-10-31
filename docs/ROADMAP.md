# WATSON release ladder

Each release has one question it must answer. Scope that does not help answer that question moves to the release where it does.

| Release | Question it answers | Plan | Ships when |
|---|---|---|---|
| **V1 — Personal build** | Does WATSON work, every day, for its author on an 8 GB Apple Silicon Mac? | `WATSON_V1_ENGINEERING_PLAN.md`, milestones M0–M7 | The author has relied on it for ten consecutive working days and produced two weekly reports from its output (M7 acceptance) |
| **V1.5 — Distribution** | Can someone else run it, and would IT approve it? | `WATSON_V1_ENGINEERING_PLAN.md`, section "V1.5 — Distribution and IT approval" | Clean-machine offline install passes; IT pack reviewed by a second person |
| **V2 — Meeting intelligence** | Can imported, authorised recordings feed the same memory as the screen? | `WATSON_V2_AUDIO_MEETINGS_PLAN.md`, milestones A0–A3 | Author has processed ten real authorised recordings; eval gates hold |
| **V2.1 — Meeting refinements** | Who said what, and can live microphone use ever be acceptable? | Open decisions in the V2 plan (diarization, live microphone assessment) | Separate privacy, permissions and technical assessment; not scheduled |
| **V3 — Business deployment** | Can an organisation deploy and configure it without touching personal memory? | Original specification §20 (managed deployment, IT-managed configuration, approved model distribution, department profiles) | Not planned in detail |
| **V4 — Enterprise work intelligence** | Can organisation-level insight exist with explicit, transparent employee permission? | Original specification §20 | Not planned in detail |

## Rules that hold across every release

1. Local-first: work content is processed and stored locally; no cloud AI.
2. Employee-first: the employee owns the memory and decides what is shared.
3. Raw captures and recordings are temporary; useful, evidenced claims are what persist.
4. No autonomous actions, no email sending, no covert capture or recording.
5. Evidence-based: every statement WATSON repeats cites a claim with evidence.
6. Nothing in a later release weakens a privacy guarantee made in an earlier one.

## Numbering note

The meeting/audio scope was originally circulated as "V1.1". It is V2 here because V1 is deliberately limited to the author's own daily use and V1.5 to distribution; meetings add a new data source and new consent obligations, which belong after both.
