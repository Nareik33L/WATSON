# Committed A0 meeting fixtures

Edge-case audio is generated from `tools/fixture_gen/meetings/*.yaml` and committed here so Linux CI can exercise WER / RSS / hallucination plumbing without macOS `say`.

| Directory | File | Notes |
| --- | --- | --- |
| `silence_only/` | `audio.wav` + empty `transcript.txt` | 15 s silence |
| `tone_only/` | `audio.wav` + empty `transcript.txt` | 15 s 440 Hz tone |
| `one_second/` | `audio.wav` + empty `transcript.txt` | 1 s silence |
| `corrupt/` | truncated `audio.wav` | decode must fail |
| `unsupported/` | `audio.xyz` | unsupported container |

The 12-minute stand-up **script** is `tools/fixture_gen/meetings/standup_12m.yaml`. Its WAV (~22 MB) and AAC (~3 MB) are **not** committed: this checkout cannot run `say`. macOS 15 CI renders every A0 script and uploads `tests/fixtures/meetings` as the `a0-meeting-fixtures` artifact. If a later Mac render of compressed audio + transcripts stays ≤ 8 MB, those files can be committed; until then artifacts only.

Regenerate edges (Linux-safe):

```bash
uv run watson-fixturegen meetings render --all --out tests/fixtures/meetings
# dialogue rows fail on Linux; render edges individually or ignore that error:
uv run watson-fixturegen meetings render tools/fixture_gen/meetings/silence.yaml --out tests/fixtures/meetings/silence_only
```
