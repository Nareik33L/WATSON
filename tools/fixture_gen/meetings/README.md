# Meeting fixture scripts (V1.1 §8.1 / A0)

YAML dialogues and edge-case scripts for the transcription bake-off. Render with:

```bash
uv run watson-fixturegen meetings render tools/fixture_gen/meetings/standup_12m.yaml \
  --out tests/fixtures/meetings/standup_12m
# or every A0 script:
uv run watson-fixturegen meetings render --all --out tests/fixtures/meetings
```

Dialogue rendering needs macOS `say` (one voice per speaker) and `afconvert` (AAC ~32 kbps `.m4a`). On Linux the command fails with a clear error. `--dry-run` validates a script and writes `truth.yaml` plus a timestamped reference transcript without calling `say`.

## A0 set

| Script | Kind | Notes |
| --- | --- | --- |
| `standup_12m.yaml` | dialogue | ~12-minute fictional Rivermark Labs stand-up (`target_duration_s: 720`) |
| `speech_short.yaml` | dialogue | ~2 s known-text WER/RTF probe (`say` on macOS; Linux commits a tone placeholder + transcript) |
| `silence.yaml` | silence | 15 s silence (A6 hallucination probe) |
| `tone.yaml` | tone | 15 s 440 Hz tone (music-like probe) |
| `one_second.yaml` | one_second | 1 s clip |
| `corrupt.yaml` | corrupt | truncated RIFF |
| `unsupported.yaml` | unsupported | `.xyz` non-container |

The 45-minute customer call and 90-minute workshop scripts are A1, not this slice.

## YAML format

```yaml
kind: dialogue   # or silence | tone | one_second | corrupt | unsupported
meta:
  name: standup_12m
  title: ...
  date: "2026-10-31"
  language: en
  target_duration_s: 720   # dialogue only; extra silence is spread across pauses
speakers:
  Nora Voss:
    voice: Samantha        # macOS `say -v` name
    role: engineering manager
mix:
  pause_ms: 650
  noise: {kind: white, amplitude: 0.006, seed: 31}
lines:
  - speaker: Nora Voss
    line: Good morning everyone.
    pause_ms: 800          # optional
    stage: rustles a paper # optional; not spoken
truth:
  participants: [Nora Voss]
  decisions: [{text: ...}]
  commitments: [{text: ..., owner: ..., deadline: ...}]
  project_updates: [{text: ...}]
  discussion_points: [{text: ...}]
  traps:
    - {kind: suggestion-not-decision, text: ...}
    - {kind: retracted-deadline, text: ...}
    - {kind: absent-person-mentioned, name: Helen Cho}
```

All names and companies are fictional. Output per fixture: 16 kHz mono `audio.wav`, `audio.m4a` on a Mac, `transcript.txt` with `[mm:ss.mmm-mm:ss.mmm] Speaker: line` timestamps, and `truth.yaml`.

## What is committed

Edge-case WAV + empty transcripts + `truth.yaml` are generated on Linux and committed (well under 8 MB). `speech_short` commits a short non-silent placeholder WAV plus the known transcript (`synthetic: true` so it does not enter aggregate WER). Linux fake-runtime tests still score per-fixture WER; macOS CI re-renders it with `say` (no `synthetic` flag) and includes it in the bake-off WER. The 12-minute stand-up **AAC and transcript are committed** (`tests/fixtures/meetings/standup_12m/`, Mac render, whole set ≤ 8 MB). `say` is still macOS-only; Linux CI skips the stand-up clip (no WAV) and uses the committed edge fixtures. macOS CI renders every A0 script and uploads the directory as an artifact.

Do not commit the 16 kHz stand-up WAV (about 22 MB).

Apple `say` voice licensing may be non-commercial. The committed AAC is a synthetic fixture for CI, not a redistributable voice pack — review before V1.2 ships anything that includes it.
