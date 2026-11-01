# WATSON A0 transcription bake-off — 2026-10-31

Harness-generated report. **Engine/size pairs are candidates, not chosen defaults.**
A human fills Recommendation after the development-Mac run (plan A0 / A5).

Informs V1.1 §11 #1: **DECIDED** for the 8 GB profile (mlx-whisper small). 16 GB is still open.

## Hardware

- macOS 14.5 / Apple M1 / 8 GB RAM / arm64 / Python 3.12.16
- RAM profile requested: `8gb`
- Fixtures: 7 from `tests/fixtures/meetings`

## Results

| Engine | Size | Status | load s | RTF | WER | peak RSS | audio s | wall s |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| pywhispercpp | base | ok | 0.18 | 0.03 | 9.1% | 561 MiB | 754.47 | 23.46 |
| pywhispercpp | small | ok | 0.29 | 0.06 | 34.0% | 951 MiB | 754.47 | 43.60 |
| mlx-whisper | base | ok | 0.11 | 0.03 | 11.0% | 552 MiB | 754.47 | 19.46 |
| mlx-whisper | small | ok | 0.29 | 0.04 | 3.1% | 951 MiB | 754.47 | 29.82 |
| faster-whisper | base | ok | 0.19 | 0.06 | 10.9% | 1.38 GiB | 754.47 | 46.29 |
| faster-whisper | small | ok | 0.44 | 0.15 | 2.5% | 2.07 GiB | 754.47 | 116.54 |

## Hallucination probes (silence / tone)

| Engine | Size | silence | tone |
| --- | --- | --- | --- |
| pywhispercpp | base | FAIL (1) | FAIL (1) |
| pywhispercpp | small | FAIL (1) | FAIL (1) |
| mlx-whisper | base | empty | empty |
| mlx-whisper | small | FAIL (1) | empty |
| faster-whisper | base | FAIL (1) | empty |
| faster-whisper | small | FAIL (1) | empty |

## Skips and errors

None.

## Recommendation

Filled by Watson Lead after the 8 GB Mac run. See **Decision** below.

| Profile | Chosen engine | Size | Rationale |
| --- | --- | --- | --- |
| 8gb | mlx-whisper | small | Stand-up WER 6.2% (mean 3.1% includes an 8-word clip at 0%), RTF 0.04, ~951 MiB. Unloads before the LLM (A5 / §4). |
| 16gb | _tbd_ |  | Not measured on this machine. |

Package transcription runtime (pywhispercpp vs mlx-whisper): **mlx-whisper**

## Decision

Watson Lead, 2026-10-31, for V1.1. Recorded as ADR 0006 and V1.1 §11 #1.

Pick **mlx-whisper small** for the 8 GB profile: stand-up WER **6.2%** (mean 3.1% includes an 8-word clip at 0%), RTF 0.04 and about 951 MiB peak RSS on the M1 8 GB. faster-whisper small is 2.5% mean WER but needs 2.07 GiB and runs at RTF 0.15 — too much on 8 GB even with A5/§4 sequential load (Whisper and the LLM are never resident together). pywhispercpp small is rejected for runaway repetition. Silence and tone hallucinate 1 word, so V1.1 must gate non-speech per A6 (Silero VAD + no-speech/compression gating + n-gram repetition detector). Recheck Whisper plus LLM memory fit at V1.1 kickoff.

New committed audio is 2.89 MiB of synthetic `say` AAC (the whole `tests/fixtures/meetings` tree is 3.90 MiB).
