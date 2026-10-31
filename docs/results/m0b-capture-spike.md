# WATSON M0-B capture spike — 2026-10-31

Records the M0-B run on the V1 machine (Apple M1, **8 GB**, macOS 14.5). Watson QA certified M0 against the metadata-only artefact set in [`docs/results/m0b/artefacts.md`](m0b/artefacts.md).

Informs §14 open decisions:

3. Swift helper vs PyObjC fallback — **DECIDED (Watson Lead, M0-B).**
5. Minimum macOS version — **DECIDED** (macOS 14).

## Hardware and toolchain

- Apple M1, 8 GB, macOS 14.5 (23F79), arm64
- Swift 6.0.3 (`swift-driver` 1.115.1, swiftlang-6.0.3.1.10)
- Command Line Tools 16.2 (`xcode-select -p` = `/Library/Developer/CommandLineTools`); no Xcode.app
- Helper at `0555eb7` (= main `722b58b`). Helper tree `21f9bd2`. Built with `helper/scripts/build.sh` (`swiftc`, not SwiftPM). The CLT SwiftBridging overlay (PR #10) is required and was applied.

## Signing and designated requirement

- Identity: `WATSON Dev` (SHA-1 prefix `56776A81`; no TeamIdentifier)
- Identifier: `com.watson.capture`
- Designated requirement: `identifier "com.watson.capture" and certificate leaf = H"56776a8118e0b57d5df99a66178ec06223b11d8b"`
- Identical identifier, authority, DR, and CDHash (`ddd50aeb355651f86fb8450ee5e89f9abe381200`) on both check-tcc rebuilds and the binary left on disk

## Selftest

`--selftest` (synthetic 8×8 source; no ScreenCaptureKit or Vision). Exit 0, `ok=true`:

| Field | Value |
|---|---|
| `captured_while_paused` | 0 |
| `captured_while_running` | 5 |
| `skipped_while_paused` | 6 |
| `dropped_in_flight` | 1 |

## Preflight

`CGPreflightScreenCaptureAccess` `granted=true` on the post-grant binary (build1 16:22, build2 16:24, and a read-only re-run at 18:33 Europe/London).

## One-hour soak

15:19–16:19 Europe/London, interval 45 s. Final `self_report` (emitted on quit):

| Field | Value | Acceptance |
|---|---|---|
| `cpu_percent_avg` | 0.37 | < 2 — **PASS** |
| `cpu_seconds` | 13.4 | — |
| `rss_peak_bytes` | 196,673,536 | — |
| `frames_written` | 14 | — |
| `duplicates` | 15 | — |
| `frames_skipped` | 66 (51 idle, 15 duplicate) | — |
| `uptime_s` | 3602 | 1 h duration evidence |

No `error` or `stdin_closed` events. `1h.err` is empty.

## Sidecar

README §5 metadata summary (before cleanup; title value never printed):

- `capture_mode` = `frontmost`
- 2080 × 1520
- OCR lines 43
- `phash` present
- `window_title_len` 8

All 14 captured events had `ocr_lines` > 0 (42–51). The last captured event had `ocr_lines` 51.

## check-tcc

**PASS.** Screen Recording grant persisted across two rebuilds (`granted=true` on both). Identifier and designated requirement were identical.

## cleanup-spool

**PASS.** The 1-hour spool was removed; listed roots were empty. The later selftest re-run also cleaned `/tmp/watson-selftest` (exit 0).

## Decisions

**§14 #3 — Swift helper vs PyObjC (Watson Lead, M0-B).** Use the Swift helper. ScreenCaptureKit + Vision, built with `swiftc` under Command Line Tools only (no Xcode), self-signed `WATSON Dev` identity. TCC grant was stable across two rebuilds on this binary.

**§14 #5 — Minimum macOS version.** Minimum is **macOS 14** (tested on 14.5). No required APIs were missing: the build targets `arm64-apple-macosx14.0`; `SCScreenshotManager`, Vision OCR, preflight, and lock/idle probes all ran. The soak produced no `error` events and empty stderr.

## Open caveat

Every build shared one CDHash, so a TCC grant surviving a code-changing rebuild is unproven and must be re-checked at M5 live capture.

## Raw artefacts

Metadata-only artefact set Watson QA certified M0 against: [`docs/results/m0b/artefacts.md`](m0b/artefacts.md). No PNG content, OCR line text, window titles, app window names, or URLs.
