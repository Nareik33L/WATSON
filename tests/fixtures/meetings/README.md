# Committed A0 meeting fixtures

Edge-case audio is generated from `tools/fixture_gen/meetings/*.yaml` and committed here so Linux CI can exercise WER / RSS / hallucination plumbing without macOS `say`.

| Directory | File | Notes |
| --- | --- | --- |
| `silence_only/` | `audio.wav` + empty `transcript.txt` | 15 s silence |
| `tone_only/` | `audio.wav` + empty `transcript.txt` | 15 s 440 Hz tone |
| `one_second/` | `audio.wav` + empty `transcript.txt` | 1 s silence |
| `corrupt/` | truncated `audio.wav` | decode must fail |
| `unsupported/` | `audio.xyz` | unsupported container |

The 12-minute stand-up **script** is `tools/fixture_gen/meetings/standup_12m.yaml`. The Mac-render AAC (`standup_12m/audio.m4a`, ~3 MB) and reference transcript are committed; the 16 kHz WAV (~22 MB) is not. The whole `tests/fixtures/meetings` tree stays ≤ 8 MB. macOS 15 CI still renders every A0 script and uploads the directory as the `a0-meeting-fixtures` artifact.

Regenerate edges (Linux-safe):

```bash
uv run watson-fixturegen meetings render --all --out tests/fixtures/meetings
# dialogue rows fail on Linux; render edges individually or ignore that error:
uv run watson-fixturegen meetings render tools/fixture_gen/meetings/silence.yaml --out tests/fixtures/meetings/silence_only
```
