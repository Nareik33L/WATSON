# 0006 — A0 8 GB transcription runtime

**Status:** Accepted for the 8 GB V1.1 default; 16 GB deferred  
**Date:** 2026-10-31  
**Decides:** V1.1 §11 #1 (8 GB only) — mlx-whisper small.  
**Related:** V1.1 A5, A6, A8, §4; A0 bake-off  
**Evidence:** `docs/benchmarks/2026-10-31-transcription.md`, `tools/bench/results/transcribe-8gb-20261031T185225Z/run.json`  
**Owner of deferred items:** Watson Lead

## Decision

On the V1 machine (Apple M1, **8 GB**, macOS 14.5) the packaged transcription default is **mlx-whisper small**.

| Profile | Engine | Size | Stand-up WER | Mean WER | RTF | Peak RSS | Status |
|---|---|---|---|---|---|---|---|
| 8gb | mlx-whisper | small | **6.2%** | 3.1% (includes an 8-word clip at 0%) | 0.04 | 951 MiB | **Accepted** |
| 8gb | faster-whisper | small | 5.1% | 2.5% | 0.15 | 2.07 GiB | Rejected — RSS/RTF too high on 8 GB |
| 8gb | pywhispercpp | small | 67.9% | 34.0% | 0.06 | 951 MiB | Rejected — runaway repetition |
| 16gb | — | — | — | — | — | — | **Not measured** |

Whisper and the LLM are never resident together (A5 / §4 / A8 sequential load). Recheck that pair at V1.1 kickoff. Non-speech gating is per A6: Silero VAD, no-speech/compression gating, and an n-gram repetition detector.

A0 is not closed: the Swift `audio decode` round-trip and the 16 GB recommendation remain open.
