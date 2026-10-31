# WATSON A0 transcription bake-off — 2026-10-31

Harness-generated report. **Engine/size pairs are candidates, not chosen defaults.**
A human fills Recommendation after the development-Mac run (plan A0 / A5).

Informs V1.1 open decision 1: whisper.cpp vs mlx-whisper; sizes per RAM tier.

## Hardware

- macOS 14.5 / Apple M1 / 8 GB RAM / arm64 / Python 3.12.16
- RAM profile requested: `8gb`
- Fixtures: 7 from `tests/fixtures/meetings`

## Results

| Engine | Size | Status | load s | RTF | WER | peak RSS | audio s | wall s |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| pywhispercpp | base | ok | 10.03 | 0.03 | 9.1% | 522 MiB | 754.47 | 23.86 |
| pywhispercpp | small | ok | 0.31 | 0.06 | 34.0% | 945 MiB | 754.47 | 44.10 |
| mlx-whisper | base | error | — | — | — | — | — | — |
| mlx-whisper | small | error | — | — | — | — | — | — |
| faster-whisper | base | ok | 0.18 | 0.06 | 10.8% | 1.29 GiB | 754.47 | 47.95 |
| faster-whisper | small | ok | 0.45 | 0.15 | 2.5% | 1.69 GiB | 754.47 | 113.48 |

## Hallucination probes (silence / tone)

| Engine | Size | silence | tone |
| --- | --- | --- | --- |
| pywhispercpp | base | FAIL (1) | FAIL (1) |
| pywhispercpp | small | FAIL (1) | FAIL (1) |
| faster-whisper | base | FAIL (1) | empty |
| faster-whisper | small | FAIL (1) | empty |

## Skips and errors

- `mlx-whisper/base` error: 'function' object has no attribute 'ModelHolder'
- `mlx-whisper/small` error: 'function' object has no attribute 'ModelHolder'

## Recommendation

_Left for the human/lead to fill after the Mac run._

Review load time, real-time factor, WER on the 12-minute stand-up,
peak RSS, and whether silence/tone probes stay empty.

| Profile | Chosen engine | Size | Rationale |
| --- | --- | --- | --- |
| 8gb | _tbd_ |  |  |
| 16gb | _tbd_ |  |  |

Package transcription runtime (pywhispercpp vs mlx-whisper): _tbd_
