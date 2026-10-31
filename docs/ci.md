# GitHub Actions budget

WATSON is a **private** repo. GitHub's free allowance is **2,000 minutes per month**. macOS minutes count **10x** (one minute on `macos-15` bills as ten). That allowance is already used up.

Linux CI runs on pull requests and on pushes to `main` or `v*` tags. Feature-branch pushes do not start a second Linux run; the `pull_request` event covers them. The three macOS workflows are not on push/PR (they used to be path-filtered, not "every push").

There is no always-green stub check. There is no branch protection yet; when it exists, require the real `CI` jobs (`ruff, mypy, pytest` and `OS-level no-network replay`), not a placeholder.

## Milestone certification (Mac checks)

The Mac checks (**DB smoke**, **Helper**, **Meetings A0**) must be green on the merge commit before a `v*-mN` tag is pushed.

Do that either with `gh workflow run <wf> --ref main` while `main` is at that SHA, confirming `headSha` matches, or by Device Ops on Kieran's Mac with the results recorded.

A tag-triggered run can't gate its own tag.

## Which jobs run when

| Workflow | Job | Runner | When it runs |
|---|---|---|---|
| `CI` | `ruff, mypy, pytest` and `OS-level no-network replay` | ubuntu | `pull_request`, and `push` to `main` or `v*` tags, except docs-only changes (`docs/**`, root `*.md`, `docs/results/**`). |
| `Fixture regen` | `playwright fixture regen` | ubuntu | `pull_request`, and `push` to `main` or `v*` tags, when `tools/fixture_gen/**`, `tests/fixtures/**`, or the workflow file change. |
| `DB smoke` | `SQLCipher smoke (macos-15)` | macos-15 | `workflow_dispatch`, push of tags matching `v*`, or Sunday 03:00 UTC if `main` had a commit in the last 7 days. |
| `Helper` | `swiftc + SwiftPM + tests` | macos-15 | Same as DB smoke. |
| `Meetings A0` | `render fixtures + tiny smoke` | macos-15 | Same as DB smoke. |
| `macOS gate` | `skip if main is idle` | ubuntu | Called by the three Mac workflows. On schedule it inspects `main` and skips the macOS job (no Mac runner) when there were no commits in 7 days. Dispatch and `v*` tags always proceed. |

Linux concurrency uses `group: ${{ github.workflow }}-${{ github.ref }}` with `cancel-in-progress` only for pull requests and non-main, non-tag refs. Pushes to `main` and `v*` tags are never cancelled. Every job has `timeout-minutes`. Caching is uv-only (`astral-sh/setup-uv` `enable-cache`); no pip cache, and no models, fixtures, databases, or spool data.

`paths-ignore` cannot match product code. A docs-only PR that renames `docs/WATSON_V1_ENGINEERING_PLAN.md` would skip CI, but `tests/unit/bench/test_paths.py` requires that file — pair a plan rename with a non-docs change so CI still runs.

## Trigger the Mac jobs by hand

GitHub UI: **Actions** → the workflow (`DB smoke`, `Helper`, or `Meetings A0`) → **Run workflow** (ref `main`).

```bash
# main must already be at the merge SHA you are certifying
gh workflow run "DB smoke" --ref main
gh workflow run Helper --ref main
gh workflow run "Meetings A0" --ref main
# confirm each run's headSha equals that merge commit
gh run list --workflow "DB smoke" --limit 1 --json headSha,status,conclusion
```

A tag matching `v*` (for example `v0.2.0-m2`) starts all three, but that run cannot certify the tag that started it. The Sunday schedule starts them only when `main` moved in the previous week.

## Why this shape

October 2026 already had 350 workflow runs. Most of the spend was macos-15 because each billed minute is multiplied by ten, and GitHub rounds each job up to a whole minute. At today's pace, Linux alone (about 450–500 billed minutes a day, largely because every PR commit also ran on `push`) would use the 2,000-minute allowance in 3–4 days. Restricting `push` to `main` and `v*` tags halves that Linux duplicate. Mac under dispatch / tag / weekly is about 300–700 minutes a month.
