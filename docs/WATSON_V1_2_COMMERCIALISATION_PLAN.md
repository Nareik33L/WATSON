# WATSON V1.2 — Commercialisation, Licensing & B2C Launch: Engineering Plan (Revision 2)

**Supersedes:** WATSON V1.2 Product Scope — Commercialisation, Licensing & B2C Launch Preparation (v1.2, proposed)
**Depends on:** `WATSON_V1_ENGINEERING_PLAN.md` (V1.0, including the packaging and documentation work deferred from it) and `WATSON_V1_1_AUDIO_MEETINGS_PLAN.md` (V1.1)
**Status:** Proposed engineering baseline

The commercial intent of the v1.2 scope stands: WATSON Personal is bought once, belongs to the individual, runs locally, never needs a subscription or a cloud account, and keeps working offline. This plan changes how that is engineered. The main shifts are: licensing is **offline-verifiable and soft-enforced** so it can never lock a customer out of their own history; tax, checkout, key issuance and refunds are **outsourced to a merchant of record** so no licence server has to exist on launch day; the macOS app becomes a **native Swift shell** around the Python core so signing, notarisation, login items, permissions, updates and onboarding behave like a normal Mac app; models are **downloaded from a signed manifest on first run** rather than shipped in the installer; and the Personal/Organisation boundary is enforced in **package structure and the egress guard**, not in prose.

---

## 0. Summary of changes from the v1.2 scope

| Area | v1.2 scope | Revision 2 | Why |
|---|---|---|---|
| Licence enforcement | "Licence validation", activation, conflicts | **Ed25519-signed licence file verified offline; soft enforcement; unlicensed state is read-only, never locked out** | A £70 product should not carry an enforcement system that can harm paying customers; scope already requires that validation failure never blocks local data |
| Activation server | Implied | **None required at launch.** Optional device-accounting activation later, also soft | Removes the single most expensive thing to build, run and secure |
| Payments | "Established payment provider" | **Merchant of record** (handles VAT/sales tax, checkout, licence keys, customer portal, refunds) | UK seller to worldwide consumers means tax in dozens of jurisdictions; MoR removes that and the key-issuance service |
| App shell | Python app packaged with PyInstaller | **Native Swift menu-bar app** hosting the Python core; dashboard shown in a WKWebView window | Login item, TCC prompts, Sparkle updates, notarisation and onboarding are all simpler and more reliable from a native shell; the Swift helper already exists |
| Models | "Download or install" | **Signed model manifest, first-run download with resume + SHA-256, optional offline model pack**; model licences inventoried | Multi-GB models don't belong in a DMG; redistribution terms differ per model |
| Trial | Not addressed | **14-day full trial, then read-only** (history, search, export keep working; capture and import stop) | Lets customers evaluate on their own data; read-only is the honest fallback for lapsed/absent licences |
| Updates | "Update and release process" | **Sparkle with EdDSA-signed appcast; update checks opt-in; perpetual-fallback entitlement model** | Standard, auditable, works with direct distribution; honours "no outbound by default" |
| Network activity | "Avoid" | **Exactly four documented flows** (activation-optional, model download, update check, diagnostics upload), each behind the existing egress guard allow-list and each user-initiated or opt-in | Makes the privacy claim testable |
| Hardware support | "Assess Intel where practical" | **Apple Silicon only, macOS 14+, 8 GB minimum (reduced profile), 16 GB recommended**; Intel declared unsupported | No MLX on Intel, poor llama.cpp performance, shrinking base; honesty beats a half-working tier |
| Personal/Organisation boundary | Principles | **Package layering (`core` → `commerce` → `sharing` extension point), capability object, egress allow-list, export audit** | Architecture boundaries that are enforced by imports and tests survive; principles do not |
| Launch | Website + purchase + install | **Founding-member private beta first**, then public listing | The founding tier exists to validate; use it before launch, not after |
| Legal/privacy | Document list | Same list plus **UK consumer-law specifics** (14-day digital-content cooling-off and its waiver, refund terms), **data-controller scoping** (customer account data only; no work content ever reaches the company), **continuity commitment** if WATSON ceases trading | These are the questions customers and reviewers actually ask |

Everything in v1.2 §2 (editions), §3.1 (pricing proposals), §12 (exclusions) and §15 (product definition) stands.

---

## 1. Key decisions

### C1 — Licence = signed file, verified offline, soft-enforced

A licence is a small JSON document signed with Ed25519 (private key offline, public key embedded in the app):

```json
{
  "lid": "WP-7F3K-…",
  "edition": "personal",
  "tier": "founding",
  "issued": "2027-01-15",
  "email_hash": "sha256:…",
  "updates": {"major": 1},
  "devices": 3,
  "v": 1
}
```

- Verification is pure local cryptography. No clock dependency beyond `issued`; no expiry.
- `updates` encodes entitlement: `{"major": 1}` = every 1.x release forever; `{"until": "2028-01-15"}` = any release published before that date, usable forever (perpetual fallback). The app refuses to run a build whose `release_date`/major exceeds entitlement **only at update-install time**, never retroactively.
- `devices` is informational at launch (C2).
- The licence file is stored in the Keychain (primary) and as a `0600` copy in Application Support for recovery; a customer can also re-download it from the merchant's customer portal.

**Enforcement states:**

| State | Capture / import | History, search, Ask, reports, export | Banner |
|---|---|---|---|
| Trial (≤ 14 days from first run) | On | On | "Trial: N days left" |
| Licensed | On | On | none |
| Unlicensed (trial over, no/invalid licence) | **Off** | **On** | "Add a licence to resume capture" |
| Entitlement exceeded (newer build than licence covers) | n/a — build never installs | n/a | Update dialog explains the upgrade |

Nothing is ever deleted or hidden because of licence state. Clock rollback to extend a trial is detected cheaply (last-seen timestamp monotonic check) and simply ends the trial; it is not fought further.

### C2 — No activation server at launch; optional device accounting later

The scope asks for activation, conflict protection and transfer. The honest trade-off:

- **Launch (V1.2):** "activation" = pasting or opening the licence file. Device limit is stated in the EULA and shown in-app, not enforced. Transfer = copy the file. Replacement machine = copy the file or re-download from the portal. Zero server, zero outage risk, zero support tickets about fingerprints changing after an OS upgrade.
- **Later (if abuse is observed):** an activation endpoint that counts per-install random IDs (never hardware fingerprints) and issues a second signed "activation token". Still soft: if the endpoint is unreachable the licence alone keeps working. The app design leaves the slot (`LicenseState.activation: Optional[…]`) so this is additive.

### C3 — Merchant of record for checkout, tax, keys, portal and refunds

Evaluate two or three established merchant-of-record providers for downloadable software (criteria: UK entity support, worldwide VAT/GST handling, licence-key generation via webhook or API, customer self-service portal, variant stock limits for the founding tier, refund handling, payout terms, fees). The provider's webhook calls a **small issuance function** (serverless) that signs the licence with the offline-held key material via a KMS/HSM-backed key and returns the file; the provider emails it with install instructions. If the provider can host key files natively, no function is needed.

This means the company's only online systems at launch are a static website, the provider's hosted checkout, and (optionally) one signing function. Stripe-direct remains plan B if a provider's terms are unacceptable; it adds tax registration and a key-issuance service.

### C4 — Native Swift shell around the Python core

The existing `watson-capture` Swift package grows into **WATSON.app**: a menu-bar application that owns

- login item (`SMAppService`), menu bar state and pause controls,
- Screen Recording permission flow with deep links to System Settings,
- launching, monitoring and restarting the Python core (supervisor, worker, dashboard) from `Contents/Resources/core/` (PyInstaller one-folder output, every Mach-O signed),
- a single **WKWebView window** that loads the local dashboard with the session cookie pre-set — so onboarding, settings and reports feel like app windows, not browser tabs,
- Sparkle updates, About/version, diagnostics bundle export, licence file import.

Everything else stays in Python and the web dashboard. The Python core gains no new UI framework; `rumps` is removed. The supervisor's process management logic moves to Swift or remains in Python under the shell's control — decided at C-M1 after a spike, with a preference for the shell only supervising the Python supervisor process.

### C5 — Signing, hardened runtime, notarisation as a CI artefact

- Developer ID Application certificate; hardened runtime; entitlements limited to what the Python/llama.cpp stack needs (`allow-unsigned-executable-memory` and `disable-library-validation` only if the spike proves them necessary, documented either way).
- Every `.so`/`.dylib`/binary in the bundle signed inside-out; `codesign --verify --deep --strict` and `spctl --assess` in CI; `notarytool submit --wait`; ticket stapled; DMG also signed and notarised.
- The build is reproducible from a tag in CI on a macOS runner; the signing identity and notarisation credentials live in CI secrets; the licence-signing private key does **not** live in CI.

### C6 — Models from a signed manifest, downloaded on first run

- `models/manifest.json` (signed with the same Ed25519 scheme) lists per RAM profile: model id, role, file name, size, SHA-256, licence name, licence URL, source URL on the company's own storage/CDN.
- First run detects RAM (`8gb` / `16gb` / `24gb+` profile), shows size and licence for each file, downloads with resume, verifies hashes, records the manifest version. Re-verification is `watson models verify` (exists from V1.0).
- An **offline model pack** (`WATSON-Models-<profile>-<version>.dmg`) is offered for users without a fast connection or who want zero network after install.
- Model licence terms are reviewed before inclusion; any model whose licence restricts redistribution is downloaded by the user from the original source via a guided flow rather than mirrored.

### C7 — Four network flows, nothing else

| Flow | Trigger | Host | Default | Guard |
|---|---|---|---|---|
| Model download | First run / "Add profile" | company CDN | user-initiated | egress allow-list entry active only during the flow |
| Update check | "Check for updates" or opt-in auto-check | company appcast host | **off** | same |
| Diagnostics upload | "Send diagnostics" after preview | company intake | **off**, per-send consent | same; bundle is content-free (§6) |
| Activation (C2, future) | user | company API | n/a at launch | same |

Purchase happens in the browser on the provider's checkout, not in the app. The V1.0 runtime egress guard remains the enforcement point; the privacy test suite asserts that a fully set-up app with auto-update off opens **no** sockets to non-loopback addresses during a replayed day.

### C8 — Personal/Organisation boundary enforced by structure

```
watson.core        capture, pipeline, intelligence, memory, reporting, dashboard   — no network, no licence awareness
watson.commerce    licence verification, trial state, entitlement, update policy    — depends on core; core never imports it
watson.sharing     ExportAdapter protocol + LocalExportAdapter (Markdown, clipboard, file)  — Personal ships only local adapters
watson.app         wiring: builds a Capabilities object from edition + licence and passes it down
```

- `Capabilities` is the only place edition logic lives (`can_capture`, `can_import_meetings`, `export_adapters`, `edition_label`). No `if edition == …` elsewhere; an import-linter rule enforces that `core` imports neither `commerce` nor `sharing`.
- Every export writes an `audit_events(artifact_exported, adapter, artifact_id)` row — the hook Organisation will later use for "approved sharing", and the record a Personal user can inspect today.
- Identity is a `Principal` with a single local implementation; data tables already carry stable UUIDv7 IDs, so a future sync layer needs no schema rewrite.
- Organisation is a separate distributable that *adds* adapters and a principal implementation; it cannot read Personal memory without the user's explicit per-artefact action through the same export path. This is tested: a fake "org adapter" in the test suite cannot reach any table except through `ExportAdapter.export(artifact)`.

### C9 — Founding members are the beta

The founding tier (£20, limited quantity) is sold first, privately, to people who agree to test. Launch sequence: founding checkout live → founding members install from notarised builds → two update cycles delivered via Sparkle → public listing of the standard licence. Founding feedback is collected via the diagnostics bundle and a feedback form, not telemetry.

---

## 2. Onboarding (first-run) design

A state machine persisted in `settings` so a quit mid-way resumes where it left off.

```
welcome → what_watson_keeps_and_deletes → licence_or_trial → hardware_check(profile)
       → storage_location → models(download|offline pack|later) → screen_recording_permission
       → work_hours_and_exclusions → optional_meeting_import (V1.1) → done(open dashboard)
```

Design rules:

- Each screen states one thing and has one primary action. No step requires a terminal.
- `what_watson_keeps_and_deletes` is the plain-language version of the retention tiers (frames deleted in minutes; text expires; claims and summaries kept; nothing leaves the Mac). The same text is the website's privacy explanation, generated from one source file so they cannot drift.
- `storage_location` warns if the chosen folder is inside iCloud Drive, Dropbox, OneDrive or Google Drive paths and offers the default instead.
- `hardware_check` sets the model profile and explains what 8 GB means ("text understanding only; no visual descriptions").
- `screen_recording_permission` explains why, shows the frontmost-window-only default, deep-links to System Settings, detects the grant live, and never starts capture until granted and the user clicks "Start".
- "Later" is always available for models and permission; the dashboard shows what is pending.

The onboarding is driven end-to-end in UI tests with a fresh user account on CI (`sysadminctl` to create, delete after).

---

## 3. Packaging and installation specifics

- **Deliverables per release:** `WATSON-<version>.dmg` (app), `WATSON-Models-<profile>-<manifest>.dmg` (optional), appcast entry, release notes, SBOM, checksums, GPG/minisign signature of the checksums.
- **Bundle contents:** `WATSON.app/Contents/MacOS/WATSON` (Swift shell), `Contents/Resources/core/` (Python 3.12 runtime + site-packages + `watson` + sqlcipher3 + sqlite-vec + llama.cpp/MLX runtime), `Contents/Resources/prompts/`, `Contents/Resources/manifest.json`, `Contents/Frameworks/Sparkle.framework`.
- **Data locations unchanged** from V1.0 (`~/Library/Application Support/WATSON`), so a developer install upgrades in place.
- **Upgrade:** Alembic migrations run on first launch of a new version after an automatic pre-migration backup of `watson.db` (encrypted, same key) to `backups/`; downgrade is blocked with a clear message if the schema is newer than the app.
- **Uninstall:** "Remove WATSON…" in the app menu offers *keep my data* (removes app + login item + Keychain token; leaves data folder with a README) or *delete everything* (V1.0 delete-all path, then removes the folder and Keychain items, audit event written to a final log line). A standalone `Uninstall WATSON.app` is included in the DMG for when the main app will not start.
- **Compatibility:** macOS 14, 15 and the current major release tested on clean user accounts; Apple Silicon only; 8 GB minimum (reduced profile), 16 GB recommended; ~6 GB disk for app + 16 GB-profile models. The System Requirements page is generated from the same table the hardware check uses.
- **No developer tools:** CI's clean-account test runs on a machine image without Xcode or Command Line Tools.

---

## 4. Website and purchase journey

- Static site (any static generator), hosted on a CDN, no cookies beyond the provider's checkout. Pages per the scope: overview, benefits, screenshots/short demo video, features, privacy and local-processing explanation (generated from the onboarding source text), system requirements (generated), pricing with founding offer and stock indicator, FAQ, support, legal pages, download, release notes, `/.well-known/security.txt`.
- Purchase: site → provider checkout (founding variant with limited stock, standard variant) → provider emails licence + download link → customer installs → drops licence file into the app.
- The download page serves the notarised DMG and publishes checksums; the app's Sparkle appcast lives on the same host.
- Accessibility and plain-language review of privacy copy before launch.

---

## 5. Legal, privacy and customer documentation

Required documents (owner: founder with legal review; engineering supplies the technical facts):

| Document | Engineering input |
|---|---|
| EULA | Perpetual definition; device limit (informational at launch); transfer by file; update entitlement as encoded in licence; **continuity clause**: if WATSON ceases commercial operation, a build that treats all installations as licensed is published; no remote kill-switch exists |
| Privacy policy | Scope: website visits, purchase records held by the merchant, support emails, optional diagnostics bundles. **The company never receives work content, screenshots, audio, transcripts, summaries or memory**; the app's local data handling is described for transparency but the company is not a processor of it |
| Terms and refund policy | UK Consumer Contracts Regulations: 14-day cancellation for digital content, waived with express consent at download; practical policy proposal: 14-day no-questions refund regardless, since the trial exists |
| Support policy | Response targets; supported versions (current and previous minor); security-fix policy (backported to current minor for the entitlement period) |
| Data retention explanation | Generated from the retention tier table (V1.0 §5, V1.1 A2) |
| Local AI explanation | Model inventory with names, sizes, licences, what each does; statement that models run in-process on the Mac |
| System requirements, install, troubleshooting, licence guidance | Generated/maintained in `docs/customer/` and published by CI |
| Information for your employer | One page: what is captured, what is retained, what leaves the Mac (nothing), permissions used, how to pause/exclude/delete — the former IT pack condensed, with the full technical pack available on request |

Claims discipline: no "military-grade", no "unhackable", no "forensically erased". Deletion wording reuses V1.1 §4 ("unlinks and verifies; freed blocks rely on FileVault").

---

## 6. Support, diagnostics and maintenance

- **Version identity:** SemVer + build number in About, `watson --version`, diagnostics and crash logs; the running Python core and Swift shell versions must match or the shell refuses to start the core.
- **Diagnostics bundle:** `watson doctor --bundle` (and the About window button) produces a zip with: versions, macOS version, chip and RAM, config with paths and secrets removed, permission states, model manifest status and hashes, database schema version and row counts only, job queue states, last 500 lines of **redacted** logs, crash reports from `~/Library/Logs/DiagnosticReports` for WATSON processes. A preview screen lists every file before the user chooses to send it. A unit test asserts the bundle contains no `frame_text`, `merged_text`, `snippet`, transcript or report text.
- **Crash handling:** no third-party crash SDK. The shell records crash counts locally and offers the diagnostics flow.
- **Updates:** Sparkle, EdDSA-signed appcast, delta updates, release notes rendered from `CHANGELOG.md`; checks opt-in; critical security fixes flagged in the appcast so the app shows a stronger prompt if the user has checks enabled.
- **Vulnerability reporting:** `security@` mailbox, `security.txt`, acknowledgement target, fix policy in the support policy.
- **Backup guidance:** the data folder is Time Machine-safe (spool excluded); restore = copy folder back and keep the Keychain key — documented, with a "Export recovery key" option that writes the SQLCipher key to a user-chosen location after a warning (needed for restores to a new Mac without Keychain migration).

---

## 7. Testing and acceptance

| Area | Test |
|---|---|
| Licence crypto | Valid file accepted; tampered payload/signature rejected; wrong edition rejected; `updates.until` and `updates.major` evaluated correctly at update-install time only |
| Enforcement | Trial → unlicensed transitions; read-only mode blocks capture/import and keeps every read path and export; clock rollback ends trial; no data change in any transition |
| Zero-server offline | Fresh install with network disabled after model pack install: trial, licence import, 30 simulated days, all features |
| Packaging | `codesign --verify --deep --strict`, `spctl --assess --type execute`, notarisation ticket present, Gatekeeper first-launch on a clean account on each supported macOS |
| Upgrade/downgrade | 1.0 → 1.2 data upgrade with backup; downgrade blocked; licence survives upgrade |
| Uninstall | Both paths leave exactly the documented state |
| Network | Replayed day with updates off: no non-loopback sockets; each of the four flows opens only its allow-listed host |
| Onboarding | UI-driven run on a clean account completes without a terminal; resume after quit at each step |
| Diagnostics | Bundle content scan for work content |
| Boundary | Import-linter: `core` imports neither `commerce` nor `sharing`; fake org adapter test |
| Beta | Founding members: ≥ 10 installs on hardware other than the author's; ≥ 2 Sparkle updates delivered; no data-loss report |

V1.2 is complete when the scope's §13 criteria hold **and** the founding-member beta has run through two update cycles.

---

## 8. Milestones

### C-M0 — Decisions and accounts

Merchant-of-record selection; Apple Developer Program enrolment; domain, trademark search, company and VAT position confirmed; pricing and entitlement decisions recorded (see §9 recommendations); model licence review for the chosen V1.0/V1.1 models; confirm Apple `say` voice licensing before shipping any TTS-rendered meeting fixtures (may be non-commercial).
*Acceptance:* decision log in `docs/decisions/`; certificates issued; provider test-mode checkout works.

### C-M1 — Swift shell and signed build pipeline (long pole; start first)

WATSON.app shell with menu bar, login item, permission flow, core process management, WKWebView dashboard window; PyInstaller core with all binaries signed; hardened-runtime entitlement spike; CI job producing a notarised DMG from a tag.
*Acceptance:* notarised DMG launches on a clean macOS 14 account, passes Gatekeeper, captures a synthetic day; CPU/RSS within V1.0 targets.

### C-M2 — Licensing

`watson.commerce`: licence schema, Ed25519 verification, Keychain storage, trial state, read-only mode, entitlement check in the update path; issuance function signed against a test key; provider webhook end to end in test mode.
*Acceptance:* §7 licence and enforcement tests pass; a test-mode purchase delivers a working licence file.

### C-M3 — Models and onboarding

Signed manifest, downloader with resume/verify, offline model pack build, onboarding state machine and screens, hardware profile detection, storage-location checks.
*Acceptance:* clean-account onboarding completes end to end without a terminal, on both the download and offline-pack paths, on 8 GB and 16 GB machines.

### C-M4 — Updates, uninstall, diagnostics

Sparkle integration and appcast publishing in CI; pre-migration backup; downgrade block; uninstall flows; diagnostics bundle with preview; recovery-key export.
*Acceptance:* 1.2.0-beta.1 → beta.2 via Sparkle on a clean account; uninstall tests; bundle content scan passes.

### C-M5 — Boundary and privacy hardening

`core`/`commerce`/`sharing` split with import-linter; `Capabilities`; export audit; four-flow egress allow-list; no-socket replay test against the packaged app.
*Acceptance:* §7 network and boundary tests pass against the notarised build.

### C-M6 — Website, documents, launch

Static site with generated privacy/requirements pages, legal documents reviewed, support mailbox and `security.txt`, founding checkout live; **founding-member beta** through two update cycles; public listing.
*Acceptance:* scope §13 criteria plus beta exit criteria in §7.

---

## 9. Commercial decisions: engineering recommendations

The scope lists decisions to finalise. Engineering's recommendations, for the founder to accept or change:

| Decision | Recommendation | Reason |
|---|---|---|
| Founding quantity / window | 100 licences or 60 days, whichever first | Enough beta signal; small enough to support personally |
| Founding entitlement | All 1.x **and** 2.0 free | Rewards risk; trivially encoded as `{"major": 2}` |
| Standard entitlement | All 1.x releases (`{"major": 1}`) | Simpler to explain and support than date windows; 2.0 is the paid upgrade |
| Device limit | 3, informational at launch | Covers desktop + laptop + replacement without a server |
| Transfer | By file; deactivation not required | No server |
| Refunds | 14 days, no questions | Trial already exists; removes most disputes |
| Security fixes | Backported to the current minor for as long as 1.x is sold, plus 12 months | Clear, bounded |
| Supported Macs | Apple Silicon, macOS 14+, 8 GB minimum (reduced), 16 GB recommended | Matches tested reality |
| Payment | Merchant of record | Tax and key issuance handled |
| Continuity | EULA commitment to publish a licence-free build if trading ceases | Credible perpetual promise; costs nothing now |
| Distribution | Direct download (DMG + Sparkle); not the Mac App Store | Sandbox, helper processes, in-process ML runtime and the licence model fit poorly with App Store rules |

---

## 10. Risks

| Risk | Mitigation |
|---|---|
| Hardened runtime breaks Python/llama.cpp/MLX loading | Entitlement spike in C-M1 before anything else; documented minimal entitlement set |
| Notarisation rejects an unsigned nested binary | Inside-out signing script with verification in CI; no manual signing |
| Merchant provider cannot limit founding stock or issue files | Selection criterion in C-M0; fallback: manual issuance for 100 founding licences |
| Model licence forbids redistribution | Licence review in C-M0; guided original-source download path |
| 8 GB customers disappointed by reduced profile | Hardware check explains limits before purchase (website requirements page) and at first run; refund policy |
| Sparkle update with a schema migration fails mid-way | Pre-migration backup, transactional migrations, downgrade block with restore instructions |
| Piracy of the soft-enforced licence | Accepted for a £70 product; C2 leaves room for device accounting if it becomes material |
| Legal documents overclaim | Engineering review of every technical sentence; generated pages from source tables |

## 11. Open decisions

| # | Decision | Decide at |
|---|---|---|
| 1 | Merchant-of-record provider | C-M0 |
| 2 | Process supervision in Swift shell vs. Python supervisor under the shell | C-M1 spike |
| 3 | Entitlement encoding: major-version vs. date (recommendation: major) | C-M0 |
| 4 | Whether to mirror all models or guide users to original sources for restricted ones | C-M0 licence review |
| 5 | Auto-update checks: fully opt-in vs. opt-out with first-run prompt | C-M4 |
| 6 | Device accounting server: never, or triggered by a measured abuse threshold | post-launch |

## 12. Immediate next steps

1. Enrol in the Apple Developer Program and run the hardened-runtime/notarisation spike on the current V1.0 build; this is the longest lead item and the most likely to change the packaging approach.
2. Shortlist merchant-of-record providers against the C-M0 criteria and run a test-mode checkout that delivers a file.
3. Record the §9 recommendations as accepted or amended in `docs/decisions/`.
4. Write the single-source privacy explanation text that onboarding, the website and the "information for your employer" sheet all render from.
