# WATSON M0-B certification artefacts (metadata only)

From: Device Ops, dev Mac (Apple M1, 8 GB, macOS 14.5)
Collected: 2026-10-31 18:33 Europe/London (GMT, which is UTC+0 today, so every `Z` time below is also London time)
Built clone: `~/WATSON-m0b` (detached HEAD 0555eb7). Nothing was rebuilt, re-granted, live-captured, `tccutil reset` or run with sudo while collecting this.
Privacy: no PNG content, OCR line text, window titles, app window names or URLs are included. The `png` and `sidecar` path keys are removed from captured events, and only whitelisted fields were extracted with python.

---

## 1. Toolchain

```
$ sw_vers
ProductName:		macOS
ProductVersion:		14.5
BuildVersion:		23F79
$ uname -m
arm64
$ sysctl -n machdep.cpu.brand_string hw.memsize
Apple M1
8589934592
$ swift --version
swift-driver version: 1.115.1 Apple Swift version 6.0.3 (swiftlang-6.0.3.1.10 clang-1600.0.30.1)
Target: arm64-apple-macosx14.0
$ xcode-select -p
/Library/Developer/CommandLineTools
```

## 2. Git revision of the built clone

```
$ git rev-parse HEAD
0555eb7c28e93a64f9395c6a3fa89b866671f54b
$ git rev-parse HEAD:helper
21f9bd2feb772e75a30abfdf78c74bead710b858
$ git status --short
?? 1h.err
?? 1h.jsonl
```

The helper tree is **21f9bd2, which matches what QA expects**.

HEAD history (`git reflog`):
```
0555eb7 HEAD@{2026-10-31 15:16:02 +0000}: checkout: moving from 35a2cd3... to 0555eb7
35a2cd3 HEAD@{2026-10-31 15:07:56 +0000}: checkout: moving from 595b7d5... to FETCH_HEAD
595b7d5 HEAD@{2026-10-31 15:05:27 +0000}: checkout: moving from 50229b0... to FETCH_HEAD
50229b0 HEAD@{2026-10-31 14:29:24 +0000}: checkout: moving from c9d78c9... to origin/main
```
Note: the last build log on disk (`/tmp/m0b-build.log`, 15:08) is from 35a2cd3 (helper tree 55c873e). HEAD moved to 0555eb7 at 15:16, before the 1-hour run started at 15:19. The two check-tcc rebuilds (16:22 and 16:24) ran at 0555eb7. All three builds produced the **same CDHash** (`ddd50aeb355651f86fb8450ee5e89f9abe381200`), so the compiled binary is byte-identical. The only change in 0555eb7 is where build.sh writes the overlay (outside `.build/release`).

## 3. Signing identity

```
$ security find-identity -v -p codesigning | grep "WATSON Dev"
  1) 56776A8118E0B57D5DF99A66178EC06223B11D8B "WATSON Dev"
```

## 4. Codesign and designated requirement

### /tmp/watson-capture-build1-codesign.txt
```
Executable=~/WATSON-m0b/helper/.build/release/watson-capture
Identifier=com.watson.capture
Format=Mach-O thin (arm64)
CodeDirectory v=20400 size=3627 flags=0x0(none) hashes=103+7 location=embedded
VersionPlatform=1
VersionMin=917504
VersionSDK=983552
Hash type=sha256 size=32
CandidateCDHash sha256=ddd50aeb355651f86fb8450ee5e89f9abe381200
CandidateCDHashFull sha256=ddd50aeb355651f86fb8450ee5e89f9abe381200e52cebc88fb9fa5edc3c2f2b
Hash choices=sha256
CMSDigest=ddd50aeb355651f86fb8450ee5e89f9abe381200e52cebc88fb9fa5edc3c2f2b
CMSDigestType=2
Executable Segment base=0
Executable Segment limit=180224
Executable Segment flags=0x1
Page size=4096
CDHash=ddd50aeb355651f86fb8450ee5e89f9abe381200
Signature size=1669
Authority=WATSON Dev
Signed Time=31 Oct 2026 at 16:22:47
Info.plist entries=11
TeamIdentifier=not set
Sealed Resources=none
Internal requirements count=1 size=96
```
### /tmp/watson-capture-build1-dr.txt
```
designated => identifier "com.watson.capture" and certificate leaf = H"56776a8118e0b57d5df99a66178ec06223b11d8b"
```
(build1-dr-err.txt: `Executable=~/WATSON-m0b/helper/.build/release/watson-capture`; build1-stderr.txt is empty)

### /tmp/watson-capture-build2-codesign.txt
```
Executable=~/WATSON-m0b/helper/.build/release/watson-capture
Identifier=com.watson.capture
Format=Mach-O thin (arm64)
CodeDirectory v=20400 size=3627 flags=0x0(none) hashes=103+7 location=embedded
VersionPlatform=1
VersionMin=917504
VersionSDK=983552
Hash type=sha256 size=32
CandidateCDHash sha256=ddd50aeb355651f86fb8450ee5e89f9abe381200
CandidateCDHashFull sha256=ddd50aeb355651f86fb8450ee5e89f9abe381200e52cebc88fb9fa5edc3c2f2b
Hash choices=sha256
CMSDigest=ddd50aeb355651f86fb8450ee5e89f9abe381200e52cebc88fb9fa5edc3c2f2b
CMSDigestType=2
Executable Segment base=0
Executable Segment limit=180224
Executable Segment flags=0x1
Page size=4096
CDHash=ddd50aeb355651f86fb8450ee5e89f9abe381200
Signature size=1669
Authority=WATSON Dev
Signed Time=31 Oct 2026 at 16:24:19
Info.plist entries=11
TeamIdentifier=not set
Sealed Resources=none
Internal requirements count=1 size=96
```
### /tmp/watson-capture-build2-dr.txt
```
designated => identifier "com.watson.capture" and certificate leaf = H"56776a8118e0b57d5df99a66178ec06223b11d8b"
```
(build2-dr-err.txt: `Executable=~/WATSON-m0b/helper/.build/release/watson-capture`; build2-stderr.txt is empty)

### Current binary (built 16:24 by check-tcc build 2), read at 18:33
```
$ codesign -dvv helper/.build/release/watson-capture
Executable=~/WATSON-m0b/helper/.build/release/watson-capture
Identifier=com.watson.capture
Format=Mach-O thin (arm64)
CodeDirectory v=20400 size=3627 flags=0x0(none) hashes=103+7 location=embedded
Signature size=1669
Authority=WATSON Dev
Signed Time=31 Oct 2026 at 16:24:19
Info.plist entries=11
TeamIdentifier=not set
Sealed Resources=none
Internal requirements count=1 size=96
$ codesign -dr - helper/.build/release/watson-capture
Executable=~/WATSON-m0b/helper/.build/release/watson-capture
designated => identifier "com.watson.capture" and certificate leaf = H"56776a8118e0b57d5df99a66178ec06223b11d8b"
```
**Result: OK.** Identifier=com.watson.capture and Authority=WATSON Dev in every build. The designated requirement is identical in build1, build2 and the current binary. The certificate leaf hash matches the WATSON Dev SHA-1. The CDHash is identical across builds.

## 5. check-tcc.sh

check-tcc.sh is **not** read-only: it runs `rm -rf .build` and rebuilds twice. Because rebuilds were not allowed for this task, it was **not re-run**. The output below comes from the original 16:22–16:24 run.

Original run: started from `~/terminals/60569.txt` (the same chained command as the 1-hour run), where only `tail -8` of its output was kept:
```
{"api":"CGPreflightScreenCaptureAccess","event":"preflight","granted":true,"identifier":"com.watson.capture","ts":"2026-10-31T16:24:20Z"}
ok build2: granted=true
=== compare identifiers + designated requirement ===
build1 identifier=com.watson.capture
build2 identifier=com.watson.capture
build1 designated requirement=designated => identifier "com.watson.capture" and certificate leaf = H"56776a8118e0b57d5df99a66178ec06223b11d8b"
build2 designated requirement=designated => identifier "com.watson.capture" and certificate leaf = H"56776a8118e0b57d5df99a66178ec06223b11d8b"
PASS: Screen Recording grant persisted across two rebuilds (identifier=com.watson.capture).
```
**MISSING:** the full stdout, i.e. everything before the tail-8 (the `=== TCC persistence` header, the build1/build2 build.sh output and the build1 "ok" line). Only the tail was captured. The per-build files the script writes with `tee` cover the missing part: the codesign and DR files are in §4, and the preflight files are below.

### /tmp/watson-capture-build1-preflight.jsonl
```
{"bundle_id":"com.watson.capture","event":"hello","mode":"preflight","pid":44115,"ts":"2026-10-31T16:22:48Z","version":"0.1.0"}
{"api":"CGPreflightScreenCaptureAccess","event":"preflight","granted":true,"identifier":"com.watson.capture","ts":"2026-10-31T16:22:48Z"}
```
### /tmp/watson-capture-build2-preflight.jsonl
```
{"bundle_id":"com.watson.capture","event":"hello","mode":"preflight","pid":44337,"ts":"2026-10-31T16:24:20Z","version":"0.1.0"}
{"api":"CGPreflightScreenCaptureAccess","event":"preflight","granted":true,"identifier":"com.watson.capture","ts":"2026-10-31T16:24:20Z"}
```

## 6. --preflight after the first grant

**MISSING:** the original post-grant preflight (README step 3, before the 1-hour run) is not in any surviving log. No `~/terminals` file covers steps 3–4.

As a substitute, here is a read-only re-run. `--preflight` only calls CGPreflightScreenCaptureAccess and does not capture. It ran at 2026-10-31 18:33 Europe/London on the current 0555eb7 binary:
```
$ helper/.build/release/watson-capture --preflight
{"bundle_id":"com.watson.capture","event":"hello","mode":"preflight","pid":49911,"ts":"2026-10-31T18:33:37Z","version":"0.1.0"}
{"api":"CGPreflightScreenCaptureAccess","event":"preflight","granted":true,"identifier":"com.watson.capture","ts":"2026-10-31T18:33:37Z"}
preflight_exit=0
```
The build1 and build2 preflights in §5 (16:22 and 16:24) also show granted=true after the grant.

## 7. --selftest

The original step-4 selftest output was not logged, so the selftest was re-run per README §4. It is synthetic (a fake 8×8 source, no ScreenCaptureKit or Vision), ran at 2026-10-31 18:33 Europe/London, and was followed by cleanup-spool.sh.

Command: `helper/.build/release/watson-capture --selftest --out /tmp/watson-selftest; echo "selftest_exit=$?"`

Full stdout below, with only the `png`/`sidecar` path keys dropped from the `captured` events:
```
{"bundle_id":"com.watson.capture","event":"hello","mode":"selftest","out":"/tmp/watson-selftest","pid":49913,"ts":"2026-10-31T18:33:37Z","version":"0.1.0"}
{"bundle_id":"com.example.selftest","capture_mode":"selftest","event":"captured","frame_id":"ef9609fc-c16b-4af9-9ec0-9f740441f4d0","ocr_lines":1,"phash":"ffffffffffffffff","ts":"2026-10-31T18:33:37Z"}
{"bundle_id":"com.example.selftest","capture_mode":"selftest","event":"captured","frame_id":"9717ae96-df71-48a8-a240-546abac3ae9f","ocr_lines":1,"phash":"ffffffffffffffff","ts":"2026-10-31T18:33:37Z"}
{"bundle_id":"com.example.selftest","capture_mode":"selftest","event":"captured","frame_id":"aa940977-b507-4371-a62a-2f42cda717b7","ocr_lines":1,"phash":"ffffffffffffffff","ts":"2026-10-31T18:33:37Z"}
{"event":"paused","ts":"2026-10-31T18:33:37Z"}
{"after_capture":true,"bundle_id":"com.example.selftest","event":"skipped","reason":"paused","ts":"2026-10-31T18:33:37Z"}
{"after_capture":false,"event":"skipped","reason":"paused","ts":"2026-10-31T18:33:37Z"}
{"after_capture":false,"event":"skipped","reason":"paused","ts":"2026-10-31T18:33:37Z"}
{"after_capture":false,"event":"skipped","reason":"paused","ts":"2026-10-31T18:33:37Z"}
{"after_capture":false,"event":"skipped","reason":"paused","ts":"2026-10-31T18:33:37Z"}
{"after_capture":false,"event":"skipped","reason":"paused","ts":"2026-10-31T18:33:37Z"}
{"event":"resumed","ts":"2026-10-31T18:33:37Z"}
{"bundle_id":"com.example.selftest","capture_mode":"selftest","event":"captured","frame_id":"c4d86a23-bd59-497c-8d23-5a23715fb16b","ocr_lines":1,"phash":"ffffffffffffffff","ts":"2026-10-31T18:33:37Z"}
{"bundle_id":"com.example.selftest","capture_mode":"selftest","event":"captured","frame_id":"76bc61b0-14cb-46da-8403-3966a702e52f","ocr_lines":1,"phash":"ffffffffffffffff","ts":"2026-10-31T18:33:37Z"}
{"captured_while_paused":0,"captured_while_running":5,"cleaned":false,"dropped_in_flight":1,"event":"selftest_result","ok":true,"out":"/tmp/watson-selftest","skipped_while_paused":6,"ts":"2026-10-31T18:33:37Z"}
selftest_exit=0
```
**Result: PASS, exit 0.** ok=true, captured_while_paused=0, captured_while_running=5, skipped_while_paused=6, dropped_in_flight=1, which is exactly what README §4 expects.

## 8. One-hour run (`~/watson-m0b/1h.jsonl`)

Note: `~/watson-m0b` and `~/WATSON-m0b` are the same directory, because APFS is case-insensitive.

### Exact command (from `~/terminals/60569.txt`, started from `~/WATSON-m0b`)
```
cd ~/WATSON-m0b && mkdir -p "$HOME/watson-m0b" && SP="$HOME/Library/Application Support/WATSON/spool" && (sleep 3700 | helper/.build/release/watson-capture --interval 45 --self-report-interval 60 --out "$SP" > "$HOME/watson-m0b/1h.jsonl" 2>"$HOME/watson-m0b/1h.err") & CP=$!; sleep 2; HP=$(pgrep -n -f "watson-capture --interval 45"); echo "helper pid $HP started $(date +%H:%M)"; sleep 3600; kill -INT $HP; sleep 5; wait $CP 2>/dev/null; ...then: last self_report, captured count, README §5 sidecar snippet (+ window_title_len only), cleanup-spool.sh "$SP" | tail -2, check-tcc.sh | tail -8
```
- stdin was a `sleep 3700` pipe, so the helper stayed attached and never hit EOF. It was stopped with SIGINT after 3600 s.
- Helper pid 41880. The `hello` event shows mode=capture, version=0.1.0, interval_s=45 and out=`~/Library/Application Support/WATSON/spool`.
- Default idle threshold of 180 s; no excluded bundles were added.
- `1h.err` is 0 bytes (no stderr).

### Start and stop
- First event (`hello`): 2026-10-31T15:19:35Z = 15:19:35 Europe/London (GMT)
- Last event (`stopped`): 2026-10-31T16:19:37Z = 16:19:37 Europe/London (GMT)
- Wrapper terminal: started 15:19:35, ended 16:24:20 Europe/London, which includes cleanup and check-tcc.

### Event counts
122 events in total: hello 1, captured 14, self_report 40, skipped 66 (reasons: idle 51, duplicate 15), stopped 1. There were no `error` or `stdin_closed` events.

### Every self_report line (whitelisted numeric fields only)
```
{"cpu_percent_avg": 1.64, "cpu_percent_last_interval": 1.61, "cpu_seconds": 1.511, "rss_bytes": 154894336, "rss_peak_bytes": 154894336, "uptime_s": 92.4, "frames_written": 3, "frames_skipped": 0, "duplicates": 0, "ts": "2026-10-31T15:21:08Z"}
{"cpu_percent_avg": 1.29, "cpu_percent_last_interval": 0.94, "cpu_seconds": 2.364, "rss_bytes": 174997504, "rss_peak_bytes": 174997504, "uptime_s": 183.5, "frames_written": 5, "frames_skipped": 0, "duplicates": 0, "ts": "2026-10-31T15:22:39Z"}
{"cpu_percent_avg": 1.16, "cpu_percent_last_interval": 0.91, "cpu_seconds": 3.192, "rss_bytes": 189267968, "rss_peak_bytes": 189267968, "uptime_s": 274.6, "frames_written": 7, "frames_skipped": 0, "duplicates": 0, "ts": "2026-10-31T15:24:10Z"}
{"cpu_percent_avg": 0.94, "cpu_percent_last_interval": 0.28, "cpu_seconds": 3.447, "rss_bytes": 189267968, "rss_peak_bytes": 189267968, "uptime_s": 364.9, "frames_written": 7, "frames_skipped": 2, "duplicates": 2, "ts": "2026-10-31T15:25:40Z"}
{"cpu_percent_avg": 0.81, "cpu_percent_last_interval": 0.25, "cpu_seconds": 3.671, "rss_bytes": 192118784, "rss_peak_bytes": 192118784, "uptime_s": 455.1, "frames_written": 7, "frames_skipped": 4, "duplicates": 3, "ts": "2026-10-31T15:27:10Z"}
{"cpu_percent_avg": 0.78, "cpu_percent_last_interval": 0.66, "cpu_seconds": 4.269, "rss_bytes": 192118784, "rss_peak_bytes": 192118784, "uptime_s": 545.9, "frames_written": 8, "frames_skipped": 5, "duplicates": 4, "ts": "2026-10-31T15:28:41Z"}
{"cpu_percent_avg": 0.77, "cpu_percent_last_interval": 0.7, "cpu_seconds": 4.904, "rss_bytes": 193249280, "rss_peak_bytes": 193249280, "uptime_s": 636.7, "frames_written": 9, "frames_skipped": 6, "duplicates": 5, "ts": "2026-10-31T15:30:12Z"}
{"cpu_percent_avg": 0.71, "cpu_percent_last_interval": 0.29, "cpu_seconds": 5.166, "rss_bytes": 196673536, "rss_peak_bytes": 196673536, "uptime_s": 727.1, "frames_written": 9, "frames_skipped": 8, "duplicates": 7, "ts": "2026-10-31T15:31:42Z"}
{"cpu_percent_avg": 0.66, "cpu_percent_last_interval": 0.22, "cpu_seconds": 5.362, "rss_bytes": 196673536, "rss_peak_bytes": 196673536, "uptime_s": 817.2, "frames_written": 9, "frames_skipped": 10, "duplicates": 7, "ts": "2026-10-31T15:33:12Z"}
{"cpu_percent_avg": 0.62, "cpu_percent_last_interval": 0.25, "cpu_seconds": 5.586, "rss_bytes": 196673536, "rss_peak_bytes": 196673536, "uptime_s": 907.4, "frames_written": 9, "frames_skipped": 12, "duplicates": 8, "ts": "2026-10-31T15:34:43Z"}
{"cpu_percent_avg": 0.59, "cpu_percent_last_interval": 0.29, "cpu_seconds": 5.848, "rss_bytes": 196673536, "rss_peak_bytes": 196673536, "uptime_s": 997.7, "frames_written": 9, "frames_skipped": 14, "duplicates": 10, "ts": "2026-10-31T15:36:13Z"}
{"cpu_percent_avg": 0.56, "cpu_percent_last_interval": 0.26, "cpu_seconds": 6.08, "rss_bytes": 196673536, "rss_peak_bytes": 196673536, "uptime_s": 1088, "frames_written": 9, "frames_skipped": 16, "duplicates": 11, "ts": "2026-10-31T15:37:43Z"}
{"cpu_percent_avg": 0.53, "cpu_percent_last_interval": 0.22, "cpu_seconds": 6.28, "rss_bytes": 196673536, "rss_peak_bytes": 196673536, "uptime_s": 1178.1, "frames_written": 9, "frames_skipped": 18, "duplicates": 11, "ts": "2026-10-31T15:39:13Z"}
{"cpu_percent_avg": 0.51, "cpu_percent_last_interval": 0.21, "cpu_seconds": 6.474, "rss_bytes": 196673536, "rss_peak_bytes": 196673536, "uptime_s": 1268.2, "frames_written": 9, "frames_skipped": 20, "duplicates": 11, "ts": "2026-10-31T15:40:43Z"}
{"cpu_percent_avg": 0.49, "cpu_percent_last_interval": 0.2, "cpu_seconds": 6.652, "rss_bytes": 196673536, "rss_peak_bytes": 196673536, "uptime_s": 1358.3, "frames_written": 9, "frames_skipped": 22, "duplicates": 11, "ts": "2026-10-31T15:42:14Z"}
{"cpu_percent_avg": 0.47, "cpu_percent_last_interval": 0.2, "cpu_seconds": 6.829, "rss_bytes": 196673536, "rss_peak_bytes": 196673536, "uptime_s": 1448.4, "frames_written": 9, "frames_skipped": 24, "duplicates": 11, "ts": "2026-10-31T15:43:44Z"}
{"cpu_percent_avg": 0.46, "cpu_percent_last_interval": 0.21, "cpu_seconds": 7.018, "rss_bytes": 196673536, "rss_peak_bytes": 196673536, "uptime_s": 1538.5, "frames_written": 9, "frames_skipped": 26, "duplicates": 11, "ts": "2026-10-31T15:45:14Z"}
{"cpu_percent_avg": 0.44, "cpu_percent_last_interval": 0.24, "cpu_seconds": 7.237, "rss_bytes": 196673536, "rss_peak_bytes": 196673536, "uptime_s": 1628.6, "frames_written": 9, "frames_skipped": 28, "duplicates": 11, "ts": "2026-10-31T15:46:44Z"}
{"cpu_percent_avg": 0.43, "cpu_percent_last_interval": 0.24, "cpu_seconds": 7.454, "rss_bytes": 196673536, "rss_peak_bytes": 196673536, "uptime_s": 1718.8, "frames_written": 9, "frames_skipped": 30, "duplicates": 11, "ts": "2026-10-31T15:48:14Z"}
{"cpu_percent_avg": 0.42, "cpu_percent_last_interval": 0.24, "cpu_seconds": 7.671, "rss_bytes": 196673536, "rss_peak_bytes": 196673536, "uptime_s": 1808.9, "frames_written": 9, "frames_skipped": 32, "duplicates": 11, "ts": "2026-10-31T15:49:44Z"}
{"cpu_percent_avg": 0.42, "cpu_percent_last_interval": 0.24, "cpu_seconds": 7.888, "rss_bytes": 196673536, "rss_peak_bytes": 196673536, "uptime_s": 1899, "frames_written": 9, "frames_skipped": 34, "duplicates": 11, "ts": "2026-10-31T15:51:14Z"}
{"cpu_percent_avg": 0.41, "cpu_percent_last_interval": 0.24, "cpu_seconds": 8.104, "rss_bytes": 196673536, "rss_peak_bytes": 196673536, "uptime_s": 1989.1, "frames_written": 9, "frames_skipped": 36, "duplicates": 11, "ts": "2026-10-31T15:52:44Z"}
{"cpu_percent_avg": 0.4, "cpu_percent_last_interval": 0.22, "cpu_seconds": 8.302, "rss_bytes": 196673536, "rss_peak_bytes": 196673536, "uptime_s": 2079.3, "frames_written": 9, "frames_skipped": 38, "duplicates": 11, "ts": "2026-10-31T15:54:15Z"}
{"cpu_percent_avg": 0.39, "cpu_percent_last_interval": 0.2, "cpu_seconds": 8.479, "rss_bytes": 196673536, "rss_peak_bytes": 196673536, "uptime_s": 2169.4, "frames_written": 9, "frames_skipped": 40, "duplicates": 11, "ts": "2026-10-31T15:55:45Z"}
{"cpu_percent_avg": 0.38, "cpu_percent_last_interval": 0.2, "cpu_seconds": 8.655, "rss_bytes": 196673536, "rss_peak_bytes": 196673536, "uptime_s": 2259.5, "frames_written": 9, "frames_skipped": 42, "duplicates": 11, "ts": "2026-10-31T15:57:15Z"}
{"cpu_percent_avg": 0.38, "cpu_percent_last_interval": 0.2, "cpu_seconds": 8.837, "rss_bytes": 196673536, "rss_peak_bytes": 196673536, "uptime_s": 2349.6, "frames_written": 9, "frames_skipped": 44, "duplicates": 11, "ts": "2026-10-31T15:58:45Z"}
{"cpu_percent_avg": 0.37, "cpu_percent_last_interval": 0.22, "cpu_seconds": 9.035, "rss_bytes": 196673536, "rss_peak_bytes": 196673536, "uptime_s": 2439.7, "frames_written": 9, "frames_skipped": 46, "duplicates": 11, "ts": "2026-10-31T16:00:15Z"}
{"cpu_percent_avg": 0.36, "cpu_percent_last_interval": 0.22, "cpu_seconds": 9.231, "rss_bytes": 196673536, "rss_peak_bytes": 196673536, "uptime_s": 2529.8, "frames_written": 9, "frames_skipped": 48, "duplicates": 11, "ts": "2026-10-31T16:01:45Z"}
{"cpu_percent_avg": 0.36, "cpu_percent_last_interval": 0.19, "cpu_seconds": 9.403, "rss_bytes": 196673536, "rss_peak_bytes": 196673536, "uptime_s": 2619.9, "frames_written": 9, "frames_skipped": 50, "duplicates": 11, "ts": "2026-10-31T16:03:15Z"}
{"cpu_percent_avg": 0.35, "cpu_percent_last_interval": 0.19, "cpu_seconds": 9.578, "rss_bytes": 196673536, "rss_peak_bytes": 196673536, "uptime_s": 2710, "frames_written": 9, "frames_skipped": 52, "duplicates": 11, "ts": "2026-10-31T16:04:45Z"}
{"cpu_percent_avg": 0.37, "cpu_percent_last_interval": 1, "cpu_seconds": 10.488, "rss_bytes": 196673536, "rss_peak_bytes": 196673536, "uptime_s": 2801.2, "frames_written": 11, "frames_skipped": 52, "duplicates": 11, "ts": "2026-10-31T16:06:16Z"}
{"cpu_percent_avg": 0.37, "cpu_percent_last_interval": 0.29, "cpu_seconds": 10.747, "rss_bytes": 196673536, "rss_peak_bytes": 196673536, "uptime_s": 2891.5, "frames_written": 11, "frames_skipped": 54, "duplicates": 13, "ts": "2026-10-31T16:07:47Z"}
{"cpu_percent_avg": 0.39, "cpu_percent_last_interval": 1.03, "cpu_seconds": 11.691, "rss_bytes": 196673536, "rss_peak_bytes": 196673536, "uptime_s": 2982.7, "frames_written": 13, "frames_skipped": 54, "duplicates": 13, "ts": "2026-10-31T16:09:18Z"}
{"cpu_percent_avg": 0.4, "cpu_percent_last_interval": 0.68, "cpu_seconds": 12.306, "rss_bytes": 196673536, "rss_peak_bytes": 196673536, "uptime_s": 3073.5, "frames_written": 14, "frames_skipped": 55, "duplicates": 14, "ts": "2026-10-31T16:10:49Z"}
{"cpu_percent_avg": 0.4, "cpu_percent_last_interval": 0.26, "cpu_seconds": 12.539, "rss_bytes": 196673536, "rss_peak_bytes": 196673536, "uptime_s": 3163.7, "frames_written": 14, "frames_skipped": 57, "duplicates": 15, "ts": "2026-10-31T16:12:19Z"}
{"cpu_percent_avg": 0.39, "cpu_percent_last_interval": 0.22, "cpu_seconds": 12.738, "rss_bytes": 196673536, "rss_peak_bytes": 196673536, "uptime_s": 3253.8, "frames_written": 14, "frames_skipped": 59, "duplicates": 15, "ts": "2026-10-31T16:13:49Z"}
{"cpu_percent_avg": 0.39, "cpu_percent_last_interval": 0.21, "cpu_seconds": 12.925, "rss_bytes": 196673536, "rss_peak_bytes": 196673536, "uptime_s": 3343.9, "frames_written": 14, "frames_skipped": 61, "duplicates": 15, "ts": "2026-10-31T16:15:19Z"}
{"cpu_percent_avg": 0.38, "cpu_percent_last_interval": 0.21, "cpu_seconds": 13.113, "rss_bytes": 196673536, "rss_peak_bytes": 196673536, "uptime_s": 3434, "frames_written": 14, "frames_skipped": 63, "duplicates": 15, "ts": "2026-10-31T16:16:49Z"}
{"cpu_percent_avg": 0.38, "cpu_percent_last_interval": 0.2, "cpu_seconds": 13.292, "rss_bytes": 196673536, "rss_peak_bytes": 196673536, "uptime_s": 3524.1, "frames_written": 14, "frames_skipped": 65, "duplicates": 15, "ts": "2026-10-31T16:18:19Z"}
{"cpu_percent_avg": 0.37, "cpu_percent_last_interval": 0.2, "cpu_seconds": 13.444, "rss_bytes": 196673536, "rss_peak_bytes": 196673536, "uptime_s": 3602, "frames_written": 14, "frames_skipped": 66, "duplicates": 15, "ts": "2026-10-31T16:19:37Z"}
```
Final self_report (emitted on quit, verbatim from the run log): `{"cpu_percent_avg":0.37,"cpu_percent_last_interval":0.20000000000000001,"cpu_seconds":13.444000000000001,"duplicates":15,"event":"self_report","frames_skipped":66,"frames_written":14,"rss_bytes":196673536,"rss_peak_bytes":196673536,"ts":"2026-10-31T16:19:37Z","uptime_s":3602}`

**Acceptance:** cpu_percent_avg = **0.37 (< 2, PASS)**; uptime_s = 3602 (≈3600); rss_peak_bytes = 196,673,536 (about 187.6 MiB); frames_written 14; frames_skipped 66 (51 idle, 15 duplicate).

### CSV
**MISSING / none:** no CSV was produced for M0-B; there is no `*.csv` in `~/watson-m0b`. The numbers above come straight from the helper's own self_report events. Per README, `cpu_percent_avg` = (user+system CPU seconds) / wall seconds × 100 since start; check: 13.444 / 3602 × 100 = 0.373. The tables in this file were extracted from `1h.jsonl` with python by event type, keeping only the listed keys.

## 9. Sidecar proof

### One `captured` event with ocr_lines > 0 (from 1h.jsonl, whitelisted fields only)
```
{"frame_id": "e2e2c3f4-f53d-48e3-b2fa-a8e7ad3eec89", "ts": "2026-10-31T15:19:36Z", "bundle_id": "com.anysphere.sand", "capture_mode": "frontmost", "ocr_lines": 45, "phash_present": true}
```
- The `captured` event has no width or height keys; its full key set is bundle_id, capture_mode, event, frame_id, ocr_lines, phash, png, sidecar and ts. The sidecar summary below gives the dimensions.
- ocr_lines across all 14 captured events: 45, 43, 43, 43, 42, 43, 42, 45, 51, 51, 43, 50, 46, 51 (all > 0).

### README §5 sidecar metadata summary
This was taken on the Mac **before** cleanup, by the README §5 snippet inside the 1-hour wrapper (`~/terminals/60569.txt`):
```
{"frame_id": "590ce618-8f1e-40c4-817f-9e0c48e13bf7", "captured_at": "2026-10-31T15:20:22Z", "bundle_id": "com.anysphere.sand", "width": 2080, "height": 1520, "capture_mode": "frontmost", "ocr_line_count": 43, "phash_present": true, "window_title_len": 8}
```
(`window_title_len` is a character count only; the snippet never printed the value itself.) There are **no sidecars left on disk**, because the spool was cleaned at the end of the run (§10), so no new summary can be made.

## 10. cleanup-spool.sh

Allowed roots in the script: `/tmp/watson-m0b-grant`, `/tmp/watson-selftest` and `$HOME/Library/Application Support/WATSON/spool`.

### Grant spool (/tmp/watson-m0b-grant)
**MISSING:** the cleanup output from the original grant step was not logged. The root does not exist now (`ls: /tmp/watson-m0b-grant: No such file or directory`).

### 1-hour spool (from `~/terminals/60569.txt`, tail -2, 16:19–16:22 Europe/London)
```
removed: ~/Library/Application Support/WATSON/spool
PASS: listed spools are empty (paths gone).
```

### Selftest spool (original step 4)
**MISSING:** not logged. Here is the cleanup from today's selftest re-run (18:33 Europe/London):
```
$ helper/scripts/cleanup-spool.sh /tmp/watson-selftest
removed: /private/tmp/watson-selftest
PASS: listed spools are empty (paths gone).
cleanup_exit=0
```

### Spool roots now (18:33 Europe/London)
```
$ ls -la /tmp/watson-selftest /tmp/watson-m0b-grant "$HOME/Library/Application Support/WATSON/spool"
ls: /tmp/watson-m0b-grant: No such file or directory
ls: /tmp/watson-selftest: No such file or directory
~/Library/Application Support/WATSON/spool:
total 0
drwx------@ 2 user  staff   64 Oct 31 18:30 .
drwx------  7 user  staff  224 Oct 31 18:32 ..
```
Note: the WATSON `spool` directory exists again but is **empty**. It was re-created at 18:30 Europe/London by the separate M1 work in `~/WATSON` (the same parent also holds logs, models, session and watson.db). It is not left over from M0-B.

## 11. Decision notes

- **Decision 3 (Swift helper vs PyObjC):** yes, `helper/scripts/build.sh` works under **CLT only** (CLT 16.2 / Swift 6.0.3, no Xcode.app) using its default swiftc path. SwiftPM is not used, because the CLT ManifestAPI link is broken.
  - The CLT SwiftBridging VFS overlay (PR #10) is required and was applied automatically. From `/tmp/m0b-build.log`:
    ```
    CLT SwiftBridging overlay applied: ~/WATSON-m0b/helper/.build/release/clt-overlay/overlay.yaml
    swiftc: swift-driver version: 1.115.1 Apple Swift version 6.0.3 (swiftlang-6.0.3.1.10 clang-1600.0.30.1)
    sdk: /Library/Developer/CommandLineTools/SDKs/MacOSX.sdk
    building (release) with swiftc -target arm64-apple-macosx14.0 -swift-version 6 + embedded ~/WATSON-m0b/helper/Info.plist
    binary: ~/WATSON-m0b/helper/.build/release/watson-capture
    .../watson-capture: valid on disk
    .../watson-capture: satisfies its Designated Requirement
    signed: ~/WATSON-m0b/helper/.build/release/watson-capture  identity='WATSON Dev'  identifier=com.watson.capture
    ok: .../watson-capture CFBundleIdentifier=com.watson.capture
    BUILD_EXIT 0
    ```
  - Before the overlay, at 50229b0 (PR #7 only), the build failed with `CATapDescription.h:13:9: error: could not build module 'Foundation'` (BUILD_EXIT 1, `~/terminals/60565.txt`).
  - Later builds succeeded: 595b7d5 at 15:05, 35a2cd3 at 15:08, and the check-tcc builds at 0555eb7 at 16:22 and 16:24 (Europe/London).
  - ScreenCaptureKit and Vision produced sidecars: 14 `captured` events, all with ocr_lines > 0 (42–51), and capture_mode=frontmost.
  - The Screen Recording grant survived two clean rebuilds (check-tcc PASS).
- **Decision 5 (macOS 14 minimum):** **no API was missing on macOS 14.5.** The build targets arm64-apple-macosx14.0, and SCScreenshotManager, Vision OCR, preflight and the lock/idle probes all worked at runtime. The 1-hour run's stderr (`1h.err`) is 0 bytes, and there were no `error` events.
