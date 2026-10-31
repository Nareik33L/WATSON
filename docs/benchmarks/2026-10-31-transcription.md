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

_Left for the human/lead to fill after the Mac run._

Review load time, real-time factor, WER on the 12-minute stand-up,
peak RSS, and whether silence/tone probes stay empty.

| Profile | Chosen engine | Size | Rationale |
| --- | --- | --- | --- |
| 8gb | _tbd_ |  |  |
| 16gb | _tbd_ |  |  |

Package transcription runtime (pywhispercpp vs mlx-whisper): _tbd_
