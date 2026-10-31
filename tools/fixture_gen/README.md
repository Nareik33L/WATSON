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

The `dev` group includes `fixturegen` so mypy and unit tests can import the package. Default CI does **not** install Chromium and does not regenerate PNGs.

## Render a day

```bash
uv run watson-fixturegen render \
  tools/fixture_gen/scenarios/day_acme_q4.yaml \
  --out tests/fixtures/day_acme_q4
```

Same scenario + seed (default `meta.seed`) yields byte-identical sidecar **text** fields, `truth.yaml`, and `sample50.txt`. OCR `bbox` values may move by ±2 px and `phash` Hamming distance may be ≤ 4 across machines.

**Byte-identical PNGs** are required only on the pinned platform: **Playwright 1.55.0 Chromium on linux x86_64** (the CI image). Other OS / GPU / font-stack combinations are not a PNG golden. Templates load bundled Inter (SIL OFL 1.1) from `tools/fixture_gen/fonts`, fix the viewport to `meta.width` × `meta.height`, set `deviceScaleFactor` 1, and disable animations.

`--dry-run` expands the §3.6 cadence (45 s default, 30 s after a window change, 120 s after three stable frames) and writes `truth.yaml` + `sample50.txt` without launching a browser.

## Add a scenario

1. Copy `scenarios/day_acme_q4.yaml`.
2. Set `meta.name`, `meta.date`, `meta.seed`.
3. Declare `windows` (`template` is one of `mail`, `document`, `chat`, `spreadsheet`, `browser`, `ide`, `videocall`) using only fictional firms, people, and `*.example` hosts. No real brands, logos, or PII.
4. Add a `timeline` of blocks (`window`, `start`, `end`, `states` with `at` + template `content`). Gaps between blocks are idle (no frames).
5. Fill `truth` using the §11.1 keys. Point each decision at frames with `evidence_contains` (matched against window title, URL, and content strings) or explicit `evidence_frames`.
6. Render into `tests/fixtures/<name>/`. `expected_segments` are generated from D3: maximal consecutive runs of `(bundle_id, normalised window title)`.

## Regenerate the committed fixture

```bash
uv run watson-fixturegen render \
  tools/fixture_gen/scenarios/day_acme_q4.yaml \
  --out tests/fixtures/day_acme_q4
```

Optional check (needs Chromium; skipped in default pytest):

```bash
uv run pytest tests/eval/test_fixture_regen.py --run-fixture-regen
```

That re-renders into a temp dir and asserts exact equality for sidecar text fields, `truth.yaml`, and `sample50.txt`; bbox within ±2 px; and phash Hamming distance ≤ 4. PNG bytes are compared only when you additionally want a platform-local check — they are not part of the default regen assertion.

Each committed day under `tests/fixtures/` must stay ≤ 10 MB. CI fails a day that exceeds 12 MB. No Git LFS.

## M0-A sample list

`tests/fixtures/<name>/sample50.txt` is 50 evenly spaced frame ids for the hand-scored quality check. `watson-bench run` accepts `--frame-list` pointing at that file (and will recurse for PNGs if `--frames` is the fixture root or `frames/` directory).
