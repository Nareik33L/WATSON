# fixture_gen

Renders fictional desktop apps from HTML templates with headless Chromium and writes a golden day fixture: PNG frames, helper-schema sidecars, `truth.yaml`, and a 50-frame sample list.

This is plan §15.4 / D10. It does not call `src/watson` or the Swift helper. Replay (`watson replay`) will consume the committed fixture in M1.

## Sidecar contract

Every frame is `frames/<yyyy-mm-dd>/<frame_id>.png` plus `<frame_id>.json`. The JSON matches [`schemas/sidecar.schema.json`](../../schemas/sidecar.schema.json) (draft 2020-12; also summarised in [`helper/README.md`](../../helper/README.md)).

- `frame_id` is a deterministic UUIDv5: namespace = UUIDv5(`NAMESPACE_URL`, `watson.fixture:<name>:<seed>`), name = frame index (`1`, `2`, …). Those ids are what `truth.yaml` and `sample50.txt` reference. Human-readable `fixture_index` ranges live only on `expected_segments`, not in sidecars.
- `url` is always `null` (helper M0-B; browser URLs wait for Accessibility, M5 / §14 #6). Scenario windows may still carry a URL for template chrome and truth matching.
- `capture_mode` is `fixture`.
- OCR lines come from `[data-ocr]` DOM boxes (`confidence` 1.0, `bbox` as `[x_min, y_min, x_max, y_max]`).
- `phash` is a 64-bit average hash of the **saved** RGB PNG (after PNG8 quantisation).
- `captured_at` is ISO-8601 UTC (`…Z`).

`tests/fixtures/<name>/manifest.json` lists `sha256`, `width`, and `height` for every PNG. Default CI checks the manifest hashes, sidecar dimensions, recomputed phash, and that there are no orphan PNGs or sidecars.

## Install

```bash
# from the repo root
uv sync --group fixturegen
uv run playwright install chromium
```

The `dev` group includes `fixturegen` so mypy and unit tests can import the package. Fonts (Inter + OFL) and bundled scenarios ship as package data inside `watson_fixturegen` and are resolved with `importlib.resources`. Default CI does **not** install Chromium; PRs that touch `tools/fixture_gen` or `tests/fixtures` run `pytest --run-fixture-regen`.

## Render a day

Committed days: `day_acme_q4` (focused single-project), `day_fragmented_multi` (three projects, frequent switches, lunch gap, mid-day idle, excluded private window), `day_meeting_heavy`, `day_sensitive_traps` (fake banking tab, fake password manager, planted card/secret/OTP), and `day_near_empty`.

```bash
uv run watson-fixturegen render \
  tools/fixture_gen/watson_fixturegen/scenarios/day_acme_q4.yaml \
  --out tests/fixtures/day_acme_q4
uv run watson-fixturegen render \
  tools/fixture_gen/watson_fixturegen/scenarios/day_fragmented_multi.yaml \
  --out tests/fixtures/day_fragmented_multi
uv run watson-fixturegen render \
  tools/fixture_gen/watson_fixturegen/scenarios/day_meeting_heavy.yaml \
  --out tests/fixtures/day_meeting_heavy
uv run watson-fixturegen render \
  tools/fixture_gen/watson_fixturegen/scenarios/day_sensitive_traps.yaml \
  --out tests/fixtures/day_sensitive_traps
uv run watson-fixturegen render \
  tools/fixture_gen/watson_fixturegen/scenarios/day_near_empty.yaml \
  --out tests/fixtures/day_near_empty
```

Same scenario + seed (default `meta.seed`) yields byte-identical sidecar **text** fields, `truth.yaml`, and `sample50.txt`. OCR `bbox` values may move by ±2 px and `phash` Hamming distance may be ≤ 4 across machines.

**Byte-identical PNGs** are required only on the pinned platform: **Playwright 1.55.0 Chromium on linux x86_64** (the CI image). Other OS / GPU / font-stack combinations are not a PNG golden. Templates load bundled Inter (SIL OFL 1.1) from the `watson_fixturegen` package (`fonts/`), fix the viewport to `meta.width` × `meta.height`, set `deviceScaleFactor` 1, and disable animations.

`--dry-run` expands the §3.6 cadence (45 s default, 30 s after a window change, 120 s after three stable frames) and writes `truth.yaml` + `sample50.txt` without launching a browser.

## Add a scenario

1. Copy `scenarios/day_acme_q4.yaml`.
2. Set `meta.name`, `meta.date`, `meta.seed`.
3. Declare `windows` (`template` is one of `mail`, `document`, `chat`, `spreadsheet`, `browser`, `ide`, `videocall`) using only fictional firms, people, and `*.example` hosts. No real brands, logos, or PII. Set `excluded: true` on a window the helper should drop (private browsing, password manager on the default exclusion list). Those frames **are rendered** and sidecar-marked (`is_private_browsing`, excluded bundle id) so replay can exercise the Python ingest guard; `truth.yaml` lists them under `skipped`. They must never land in `frames` or content tables.
4. Add a `timeline` of blocks (`window`, `start`, `end`, `states` with `at` + template `content`). Gaps between blocks are idle (no frames).
5. Fill `truth` using the §11.1 keys. Point each decision at frames with `evidence_contains` (matched against window title, URL, and content strings) or explicit `evidence_frames`.
6. Render into `tests/fixtures/<name>/`. `expected_segments` are generated from D3: maximal consecutive runs of `(bundle_id, normalised window title)`.

## Regenerate the committed fixtures

```bash
uv run watson-fixturegen render \
  tools/fixture_gen/watson_fixturegen/scenarios/day_acme_q4.yaml \
  --out tests/fixtures/day_acme_q4
uv run watson-fixturegen render \
  tools/fixture_gen/watson_fixturegen/scenarios/day_fragmented_multi.yaml \
  --out tests/fixtures/day_fragmented_multi
uv run watson-fixturegen render \
  tools/fixture_gen/watson_fixturegen/scenarios/day_meeting_heavy.yaml \
  --out tests/fixtures/day_meeting_heavy
uv run watson-fixturegen render \
  tools/fixture_gen/watson_fixturegen/scenarios/day_sensitive_traps.yaml \
  --out tests/fixtures/day_sensitive_traps
uv run watson-fixturegen render \
  tools/fixture_gen/watson_fixturegen/scenarios/day_near_empty.yaml \
  --out tests/fixtures/day_near_empty
```

CI runs this check by default when `tools/fixture_gen` or `tests/fixtures` change (needs Chromium):

```bash
uv run pytest tests/eval/test_fixture_regen.py --run-fixture-regen
```

That re-renders into a temp dir and asserts exact equality for sidecar text fields, `truth.yaml`, and `sample50.txt`; bbox within ±2 px; and phash Hamming distance ≤ 4. PNG bytes are compared only when you additionally want a platform-local check — they are not part of the default regen assertion.

Each committed day under `tests/fixtures/` must stay ≤ 10 MB. CI fails a day that exceeds 12 MB. No Git LFS.

## Meeting audio (V1.1 A0)

`watson-fixturegen meetings render <script>` turns YAML in [`meetings/`](meetings/) into 16 kHz mono WAV, optional AAC `.m4a` (`afconvert` ~32 kbps), a timestamped reference transcript, and `truth.yaml`. Dialogue TTS is **macOS-only** (`say` + distinct voice per speaker). Linux fails clearly; `--dry-run` still validates scripts.

A0 scripts: 12-minute stand-up plus silence, tone, 1-second, corrupt, and unsupported-container edges. The 45- and 90-minute dialogues wait for A1. See [`meetings/README.md`](meetings/README.md) for the YAML fields and what is committed versus uploaded as a macOS CI artifact.

```bash
uv run watson-fixturegen meetings render --all --out tests/fixtures/meetings
# Linux / no `say`:
uv run watson-fixturegen meetings render --all --out /tmp/meetings --dry-run
```

## M0-A sample list

`tests/fixtures/<name>/sample50.txt` is 50 evenly spaced frame ids for the hand-scored quality check. `watson-bench run` accepts `--frame-list` pointing at that file (and will recurse for PNGs if `--frames` is the fixture root or `frames/` directory).
