# 0005 — Dashboard interactivity

**Status:** Accepted  
**Date:** 2026-10-31  
**Decides:** Engineering plan §14 #8 (HTMX vs plain fetch)  
**Related:** D11, §6, §8, §11.3, M1, `docs/ROADMAP.md` V1.0 8 GB personal build

## Decision

Use **HTMX** for partial refresh of the status page. Do not introduce a Node toolchain, a bundler, or a JavaScript framework. Do not load scripts or styles from a CDN.

The dashboard is FastAPI + Jinja2 + a few hundred lines of CSS, as D11 already specified. HTMX is vendored at `src/watson/dashboard/static/htmx.min.js` (htmx.org 2.0.7, BSD Zero Clause, sha256 `60231ae6ba9db3825eb15a261122d5f55921c4d53b66bf637dc18b4ee27c79f9`). `htmx.config.includeIndicatorStyles` is set false in `dashboard.js` so CSP does not have to allow injected style. The status page polls `/status/partial` over the same origin. There is no runtime network fetch of frontend assets.

Plain `fetch` would also have worked for one poll. HTMX is the smaller total surface for the pages that follow in M2–M4 (Today timeline, Memory corrections, Privacy actions) because those are form posts and fragment swaps, not a client-rendered app. That matches D11 and keeps the 8 GB V1.0 personal build off npm.

## Rejected alternative

**Plain `fetch` + vanilla JS.** Acceptable for a single status poll, but every later mutation would reimplement CSRF headers, swap targets, and error states that HTMX already expresses as attributes on server-rendered markup.

## Security (unchanged by this choice)

- Bind `127.0.0.1` only; any other host is refused before listen.
- Per-install secret: macOS Keychain; Linux/CI `WATSON_DASHBOARD_TOKEN` or a `0600` file under `session/`. It only signs session cookies and never appears in a URL or log.
- `watson dashboard` mints a single-use launch code (60 s). The opened URL is `/auth?code=…`; first use exchanges it for a session cookie. Reuse and expiry are rejected.
- Session cookie: HMAC-SHA256 over `sid:csrf:issued_at:epoch`, MAC split at a fixed 32-byte length, compared with `hmac.compare_digest`. `HttpOnly; SameSite=Strict; Path=/; Max-Age`. Sessions expire; a process restart rotates `epoch` and invalidates cookies.
- Host and Origin must be `127.0.0.1:<port>` when the listen port is known (defeats DNS rebinding and cross-site posts).
- CSRF token on POST; HTMX sends it as `X-CSRF-Token`. A token from session A is rejected in session B.
- Strict CSP: `default-src 'none'` with `'self'` only for script, style, img, connect, and form-action. No `unsafe-inline`, no `*`.
- Status payloads are the same counts `watson status` prints. Raw OCR and content fields (`frame_text`, `merged_text`, `snippet`, `text_redacted`) are never rendered.

## Consequences

- `watson dashboard` serves the status page on `127.0.0.1:8000` with automatic port fallback.
- Frontend updates are a file copy into `static/`. No build step, no CDN pin to drift.
- M4 pages stay server-rendered. A later SPA would be a new decision.
