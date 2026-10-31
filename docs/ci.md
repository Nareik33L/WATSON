# GitHub Actions budget

WATSON is a **private** repo. GitHub's free allowance is **2,000 minutes per month**. macOS minutes count **10x** (one minute on `macos-15` bills as ten). That allowance is already used up when the Mac jobs run on every push and pull request.

Linux CI stays on push/PR. The three macOS workflows do not.

Device Ops runs the Mac checks on Kieran's Mac before each milestone tag. GitHub macOS is a backup, not the merge gate.

## Which jobs run when

| Workflow | Job | Runner | When it runs |
|---|---|---|---|
| `required` | `required` | ubuntu | Every push and pull request (including docs-only). Tiny always-green check so a required status still exists when the full suite is skipped. |
| `CI` | `ruff, mypy, pytest` and `OS-level no-network replay` | ubuntu | Push and pull request, except docs-only changes (`docs/**`, root `*.md`, `docs/results/**`). |
| `Fixture regen` | `playwright fixture regen` | ubuntu | Push and pull request that touch `tools/fixture_gen/**`, `tests/fixtures/**`, or the workflow file. |
| `DB smoke` | `SQLCipher smoke (macos-15)` | macos-15 | `workflow_dispatch`, push of tags matching `v*`, or Sunday 03:00 UTC if `main` had a commit in the last 7 days. |
| `Helper` | `swiftc + SwiftPM + tests` | macos-15 | Same as DB smoke. |
| `Meetings A0` | `render fixtures + tiny smoke` | macos-15 | Same as DB smoke. |
| `macOS gate` | `skip if main is idle` | ubuntu | Called by the three Mac workflows. On schedule it inspects `main` and skips the macOS job (no Mac runner) when there were no commits in 7 days. Dispatch and `v*` tags always proceed. |

Linux workflows use `concurrency: {group: ${{ github.workflow }}-${{ github.ref }}, cancel-in-progress: true}` so stacked pushes on the same ref cancel the older run. Every job has `timeout-minutes`. Linux jobs cache uv (`astral-sh/setup-uv` `enable-cache`) and pip (`~/.cache/pip`).

If branch protection is used, require **`required / required`**. Do not require the macOS jobs or they will stay pending on every PR.

## Trigger the Mac jobs by hand

GitHub UI: **Actions** → the workflow (`DB smoke`, `Helper`, or `Meetings A0`) → **Run workflow**.

```bash
gh workflow run "DB smoke" --ref main
gh workflow run Helper --ref main
gh workflow run "Meetings A0" --ref main
```

A tag matching `v*` (for example `v1.0.0`) starts all three. The Sunday schedule starts them only when `main` moved in the previous week.

## Why this shape

October 2026 already had 350 workflow runs on this repo. Most of the spend was macos-15 (`Helper` 67 runs, `DB smoke` 63, `Meetings A0` 17) because each billed minute is multiplied by ten, and GitHub rounds each job up to a whole minute.
