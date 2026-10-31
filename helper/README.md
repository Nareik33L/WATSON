# helper — `watson-capture` (M0-B)

Signed Swift CLI that owns screen capture. It is the D5 / D1 spike: **frontmost window only** (ScreenCaptureKit `SCContentFilter`, never the full desktop), Vision OCR, PNG + JSON sidecar, pause over stdin, lock / sleep / idle probes, and a **stable code signature** so the Screen Recording TCC grant survives rebuilds.

This directory is a SwiftPM package. There is no `.xcodeproj`. It is meant to build with **Command Line Tools only** (no full Xcode) on the development Mac: Apple M1, 8 GB, macOS 14.5, Swift 6.0.3.

Python under `src/watson` is not used here. The helper has **no network code**.

## Layout

```
helper/
├── Package.swift                 macOS 14+, product watson-capture
├── Info.plist                    CFBundleIdentifier com.watson.capture
├── entitlements.plist            empty (TCC is not an entitlement)
├── Sources/watson-capture/       Capture.swift, OCR.swift, Filters.swift, Protocol.swift
├── Sources/watson-capture-cli/   main.swift
├── Tests/WatsonCaptureTests/     protocol, sidecar, filters, selftest, permissions
└── scripts/                      build.sh, sign.sh, check-tcc.sh, cleanup-spool.sh, test-cleanup-spool.sh
```

CI (`swift build` + unit tests + `--selftest`) runs only when this directory changes. Live ScreenCaptureKit and the TCC grant are **Mac-only**; this runbook is what the person at the Mac executes.

---

## Sidecar schema (§3.1)

The machine-readable **source of truth** is [`schemas/sidecar.schema.json`](../schemas/sidecar.schema.json) (JSON Schema draft 2020-12). The table below is a summary of that file.

Each captured frame writes `<spool>/<yyyy-MM-dd>/<frame_id>.png` and `<frame_id>.json`. Files are `0600`; directories are `0700` and Time Machine-excluded.

```json
{
  "frame_id": "8f14e45f-ceea-4d87-9c6b-0d1e0e0e0e0e",
  "captured_at": "2026-10-31T12:00:00Z",
  "display_id": 1,
  "bundle_id": "com.apple.Safari",
  "app_name": "Safari",
  "window_title": "Inbox",
  "window_id": 12345,
  "is_private_browsing": false,
  "url": null,
  "ocr": {
    "lines": [
      { "text": "Hello", "bbox": [12, 44, 300, 62], "confidence": 0.98 }
    ]
  },
  "phash": "0123456789abcdef",
  "width": 1440,
  "height": 900,
  "capture_mode": "frontmost"
}
```

| Field | Type | Notes |
|---|---|---|
| `frame_id` | string | Lowercase UUID |
| `captured_at` | string | ISO-8601 UTC |
| `display_id` | number \| null | `CGDirectDisplayID` |
| `bundle_id`, `app_name` | string | Frontmost application |
| `window_title` | string | Empty if the OS withholds it |
| `window_id` | number | `CGWindowID` |
| `is_private_browsing` | bool | Best effort from the title (`Private Browsing` / `Incognito` / `InPrivate`) |
| `url` | string \| null | **Always null in M0-B.** Accessibility for browser URLs is plan decision 6 (M5) |
| `ocr.lines[].text` | string | Vision OCR, language correction off (verbatim) |
| `ocr.lines[].bbox` | `[x_min, y_min, x_max, y_max]` | Integer pixels, top-left origin. Same shape as `tools/bench/samples/frames/*.json` |
| `ocr.lines[].confidence` | number | 0…1 |
| `phash` | string | 16 lowercase hex chars; 64-bit average hash |
| `width`, `height` | number | PNG pixel size |
| `capture_mode` | string | `"frontmost"` (D1), `"selftest"`, or `"fixture"` (synthetic frames from `tools/fixture_gen`) |

Skipped frames are **not** written. They appear only as stdout `skipped` events.

---

## Stdin / stdout protocol (`Protocol.swift`)

One JSON object per line. Extra keys on commands are ignored (`duration_minutes` is a supervisor concern; the helper stays paused until `resume`).

**stdin**

```json
{"cmd":"pause"}
{"cmd":"resume"}
{"cmd":"status"}
{"cmd":"quit"}
```

`command` is accepted as an alias of `cmd`.

**stdout events** (always include `event` and `ts`)

| `event` | When |
|---|---|
| `hello` | Startup (`mode` is `capture` / `selftest` / `preflight`) |
| `paused` / `resumed` | After the matching command |
| `status` | `paused`, `screen_recording`, `locked`, `display_asleep`, `idle_s`, counters |
| `skipped` | `reason`: `paused`, `locked`, `display_asleep`, `idle`, `excluded_app`, `excluded_title`, `sharing_none`, `duplicate`, `no_window`, `screen_recording_denied` |
| `captured` | A PNG + sidecar was written (`frame_id`, paths) |
| `self_report` | CPU / peak RSS (see below) |
| `selftest_result` | `--selftest` verdict (`ok`, `captured_while_paused`, `dropped_in_flight`, …) |
| `stdin_closed` | stdin EOF — helper quits (supervisor gone) |
| `preflight` | `--preflight` (`granted`, `identifier`) |
| `error` | Parse or runtime failure; the process keeps running except on fatal startup errors |
| `stopped` | Clean quit |

Human diagnostics go to **stderr**. Do not log window titles or OCR text there.

### Pre-filters (§3.2), cheapest first

1. Paused / screen locked (`CGSSessionScreenIsLocked`) / display asleep (`NSWorkspace` + `CGDisplayIsAsleep`). Work hours are **not** in the helper — the supervisor pauses it.
2. User idle > 3 min (`CGEventSourceSecondsSinceLastEventType`; override with `--idle-seconds`).
3. Frontmost bundle ID in the exclusion list (password managers, Messages, FaceTime, Photos, Health, Keychain Access).
4. Window title matches `Private Browsing`, `Incognito`, `InPrivate`, `1Password`, `Sign in`, `Verification code`, `password`.
5. `kCGWindowSharingState == none`.
6. Perceptual hash Hamming distance ≤ 4 vs. the previous frame from the same `window_id`.

`--selftest` uses the **same** `CaptureEngine` + `handleStdinLine` path as live mode, with a fake 8×8 source (no ScreenCaptureKit / Vision). Pause is sent as `{"cmd":"pause"}`. One tick holds `screenshot` until pause is acknowledged, then the in-memory frame is dropped (`skipped` + `after_capture: true`). Without `--out`, the temp spool is deleted before exit.

`CaptureEngine.tick` re-checks pause / lock / sleep / idle immediately before `Spool.write` (after PNG encode). The remaining race is a pause that lands between that last check and the atomic rename: the frame can still appear on disk (typically tens of milliseconds). There is no further check once the rename starts.

---

## Flags

```
watson-capture --interval 45 \
  --out "$HOME/Library/Application Support/WATSON/spool" \
  --self-report-interval 60 \
  --idle-seconds 180 \
  --exclude-bundle com.example.extra

watson-capture --selftest --out /tmp/watson-selftest
watson-capture --preflight
```

`self_report` fields for the 1-hour < 2 % CPU check:

- `cpu_percent_avg` — `(user+system CPU seconds) / wall seconds * 100` since start. **This is the number to read.**
- `cpu_percent_last_interval` — same, over the last `--self-report-interval`
- `cpu_seconds`, `uptime_s`, `rss_peak_bytes` (macOS `ru_maxrss`; also duplicated as `rss_bytes`), `frames_written`, `frames_skipped`

---

## Mac runbook

Run every command from the **repository root** unless noted. Command Line Tools only — do not open Xcode.

### 0. Prerequisites

```bash
sw_vers
# ProductVersion should be 14.x (14.5 on the development Mac)

swift --version
# Apple Swift version 6.0.3  (CLT is enough)

xcode-select -p
# /Library/Developer/CommandLineTools   is fine
# a full Xcode.app path is also fine; this package does not need it

security find-identity -v -p codesigning
```

### 1. Create the local signing identity `WATSON Dev` (once)

TCC keys off **certificate + identifier**. Use a named local identity. **Do not commit certificates, keychains, or `.p12` files.**

1. Open **Keychain Access** (Spotlight: “Keychain Access”). It ships with macOS; it is not Xcode.
2. Menu **Keychain Access → Certificate Assistant → Create a Certificate…**
3. Name: `WATSON Dev`
4. Identity Type: **Self Signed Root**
5. Certificate Type: **Code Signing**
6. Create. Confirm it appears in the **login** keychain.
7. Recheck:

```bash
security find-identity -v -p codesigning
# expect a line containing: "WATSON Dev"
```

To use a different name:

```bash
export WATSON_CODESIGN_IDENTITY="Your Cert Name"
# or pass --identity 'Your Cert Name' to the scripts
```

### 2. Build and sign

```bash
chmod +x helper/scripts/*.sh
helper/scripts/build.sh
# equivalent:
#   helper/scripts/build.sh --release --identity 'WATSON Dev'
```

That runs `swift build -c release` from `helper/`, embeds `helper/Info.plist` via

```
-Xlinker -sectcreate -Xlinker __TEXT -Xlinker __info_plist -Xlinker helper/Info.plist
```

and codesigns with `--identifier com.watson.capture --timestamp=none`. Offline-safe: no Apple timestamp server.

Unsigned (CI-style) build:

```bash
cd helper
swift build
swift test
.build/debug/watson-capture --selftest
```

Confirm the signature:

```bash
codesign -dv --verbose=4 helper/.build/release/watson-capture
# Identifier=com.watson.capture
# Authority=WATSON Dev
```

### 3. First-run Screen Recording grant

`--preflight` only calls `CGPreflightScreenCaptureAccess`. It does **not** capture.

```bash
helper/.build/release/watson-capture --preflight
# stdout includes: {"event":"preflight",...,"granted":false,"identifier":"com.watson.capture"}
```

If `granted` is false, start a short live run so macOS can prompt. This writes **real** PNG + OCR (Tier 0). Delete them immediately after:

```bash
helper/.build/release/watson-capture --interval 15 --out /tmp/watson-m0b-grant
# Allow Screen Recording when prompted.
# If no prompt: System Settings → Privacy & Security → Screen Recording
#   → enable watson-capture (or com.watson.capture)
# Ctrl-C after you allow it.
helper/scripts/cleanup-spool.sh /tmp/watson-m0b-grant
```

`cleanup-spool.sh` accepts only the three runbook spool roots (absolute paths, `realpath`-resolved, must sit under `/tmp` or `$HOME/Library/Application Support/WATSON`). It refuses `/`, `$HOME`, any ancestor of `$HOME`, relative paths, and symlink escapes. It deletes only `yyyy-MM-dd/*.{png,json}` and then empty directories — never `rm -rf`. Unexpected files abort the script and nothing is removed.

Then:

```bash
helper/.build/release/watson-capture --preflight
# expect granted=true and exit 0
```

### 4. `--selftest` (no screen capture)

```bash
helper/.build/release/watson-capture --selftest --out /tmp/watson-selftest
echo "exit=$?"
helper/scripts/cleanup-spool.sh /tmp/watson-selftest
```

Expect `selftest_result` with `"ok":true`, `"captured_while_paused":0`, `"captured_while_running":5`, `"skipped_while_paused":6`, `"dropped_in_flight":1`, and exit 0. This is the §11.3 pause proof (including an in-flight drop) and is what CI runs (`--selftest` with no `--out` also deletes its temp spool).

### 5. One-hour CPU run (the M0-B acceptance number)

Put a **non-sensitive** window frontmost (Notes, a dummy document). Do not leave Mail / a password manager / a banking tab in front.

```bash
mkdir -p "$HOME/watson-m0b"
helper/.build/release/watson-capture \
  --interval 45 \
  --self-report-interval 60 \
  --out "$HOME/Library/Application Support/WATSON/spool" \
  | tee "$HOME/watson-m0b/1h.jsonl"
```

Leave it for **one hour**. Ctrl-C. Then **delete the spool before anything leaves the Mac** (D4 / Tier 0 — raw frames must not linger):

```bash
helper/scripts/cleanup-spool.sh "$HOME/Library/Application Support/WATSON/spool"
```

Pause / resume during the hour if you want:

```bash
# from another terminal, if you attached stdin to a pipe; or type into the helper:
# {"cmd":"pause"}
# {"cmd":"resume"}
# {"cmd":"status"}
```

Read the last `self_report` (also emitted on quit):

```bash
grep '"event":"self_report"' "$HOME/watson-m0b/1h.jsonl" | tail -n 1
```

Acceptance: `cpu_percent_avg` **< 2**, `uptime_s` ≈ 3600. Do **not** keep or send PNG / OCR text. If you still have one sidecar on disk *before* cleanup, print metadata only:

```bash
python3 - <<'PY'
import json, glob, os
files = glob.glob(os.path.expanduser(
    "~/Library/Application Support/WATSON/spool/*/*.json"))
if not files:
    raise SystemExit("no sidecar; skip this snippet")
d = json.loads(open(files[0], encoding="utf-8").read())
print(json.dumps({
    "frame_id": d["frame_id"],
    "captured_at": d["captured_at"],
    "bundle_id": d["bundle_id"],
    "width": d["width"],
    "height": d["height"],
    "capture_mode": d["capture_mode"],
    "ocr_line_count": len(d.get("ocr", {}).get("lines", [])),
    "phash_present": bool(d.get("phash")),
    "window_title_len": len(d.get("window_title") or ""),
}, indent=2))
PY
helper/scripts/cleanup-spool.sh "$HOME/Library/Application Support/WATSON/spool"
```

### 6. Two-rebuild TCC persistence check

Grant must already be true (`--preflight` exit 0). Then:

```bash
helper/scripts/check-tcc.sh
# or: helper/scripts/check-tcc.sh --identity 'WATSON Dev'
```

The script cleans, builds + signs, `--preflight`, cleans, builds + signs, `--preflight` again. It never runs `tccutil reset`. Expect:

```
PASS: Screen Recording grant persisted across two rebuilds (identifier=com.watson.capture).
```

If build 2 flips to `granted=false`, that is the §13 TCC risk materialising — send both `/tmp/watson-capture-build{1,2}-codesign.txt` and the preflight JSONL.

### 7. What to send back

**Metadata and toolchain only. Never send PNG, OCR line text, window titles, or URLs.**

1. `sw_vers` and `swift --version`
2. `xcode-select -p` (CLT vs Xcode.app)
3. `security find-identity -v -p codesigning` (redact unrelated certs; keep the `WATSON Dev` line)
4. `codesign -dv --verbose=4 helper/.build/release/watson-capture` and `codesign -d -r- …`
5. `--selftest` stdout (`selftest_result` is enough; synthetic, no real screen)
6. `--preflight` after the first grant
7. Last `self_report` from the 1-hour run (`cpu_percent_avg`, `rss_peak_bytes`, `uptime_s`, frame counts)
8. `helper/scripts/check-tcc.sh` output (PASS/FAIL + identifiers + designated requirements)
9. One **metadata-only** sidecar summary from the snippet in §5 (ids, sizes, `ocr_line_count` — not `ocr.lines` or `window_title`)
10. Confirmation that `cleanup-spool.sh` printed `PASS` for the grant spool, the 1-hour spool, and `/tmp/watson-selftest`
11. Decision notes:
    - **Decision 3** (Swift helper vs PyObjC): did `swift build` under CLT work? Did SCK + Vision produce a sidecar (`captured` event with `ocr_lines` > 0 is enough)?
    - **Decision 5** (macOS 14 minimum): any API missing on 14.5?

Do not send certificates. Do not send `1h.jsonl` if it contains `window_title` or OCR (the stock events do not).

---

## Signing / TCC details

| Knob | Value |
|---|---|
| Bundle / codesign identifier | `com.watson.capture` |
| Default identity | `WATSON Dev` (`WATSON_CODESIGN_IDENTITY` or `--identity`) |
| Info.plist | Embedded in `__TEXT,__info_plist` by `scripts/build.sh` |
| Entitlements | `helper/entitlements.plist` (empty; no sandbox) |
| Timestamp | `--timestamp=none` (offline) |
| Hardened runtime | Off for this local identity (M7 packaging will add it) |

`tccutil` can only **reset** a grant (`tccutil reset ScreenCapture com.watson.capture`). Do not do that during the persistence check. The check is `CGPreflightScreenCaptureAccess` on the newly signed binary.

---

## Decisions this spike is meant to settle

- **Decision 3 — Swift helper vs PyObjC.** Default is this package (D5). PyObjC remains plan B only if CLT + SCK + persistent TCC fail on the Mac.
- **Decision 5 — Minimum macOS 14.** `SCScreenshotManager.captureImage` is a 14.0 API. `Package.swift` declares `.macOS(.v14)`.

Open: Accessibility for `url` (decision 6, M5). Work-hours pause belongs to the supervisor (M5).
