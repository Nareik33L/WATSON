"""watson-bench CLI: hardware, list, fetch, run, report."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from datetime import date
from pathlib import Path

from watson_bench.candidates import PROFILES, ROLES, RUNTIMES, load_candidates
from watson_bench.fetch import FetchError, fetch_candidate
from watson_bench.frames import load_frame_id_list
from watson_bench.hardware import HardwareInfo, detect_hardware, format_hardware
from watson_bench.paths import default_models_dir, default_report_path, set_repo_root
from watson_bench.report import load_run_manifest, write_report
from watson_bench.run import run_bakeoff


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if getattr(args, "repo_root", None) is not None:
        set_repo_root(args.repo_root)
    try:
        return args.handler(args)
    except (FetchError, ValueError, FileNotFoundError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="watson-bench",
        description=(
            "M0-A model/hardware bake-off. Fetch is the only command that downloads. "
            "run talks only to a local Ollama server on localhost."
        ),
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=None,
        help="WATSON checkout (default: cwd / git root). Results and reports use this tree.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    hardware = sub.add_parser("hardware", help="Print detected chip, RAM, and OS")
    hardware.set_defaults(handler=_cmd_hardware)

    listing = sub.add_parser("list", help="List candidate models for a RAM profile")
    _add_filters(listing, profile_required=True)
    listing.set_defaults(handler=_cmd_list)

    fetch = sub.add_parser(
        "fetch",
        help="Download candidate weights (explicit network). Does not run inference.",
    )
    _add_filters(fetch, profile_required=True)
    fetch.add_argument(
        "--models-dir",
        type=Path,
        default=None,
        help="Where to store GGUF/MLX files (Ollama uses its own store)",
    )
    fetch.set_defaults(handler=_cmd_fetch)

    run = sub.add_parser("run", help="Benchmark candidates. No downloads.")
    _add_filters(run, profile_required=True)
    run.add_argument(
        "--frames",
        type=Path,
        default=None,
        help="Directory of PNG + optional OCR sidecars (recurses if needed)",
    )
    run.add_argument(
        "--frame-list",
        type=Path,
        default=None,
        help="Optional frame-id list (e.g. tests/fixtures/<name>/sample50.txt)",
    )
    run.add_argument("--out", type=Path, default=None, help="Run output directory")
    run.add_argument("--models-dir", type=Path, default=None)
    run.add_argument(
        "--score-sample",
        type=int,
        default=50,
        help="Max frames to hand-score (default 50)",
    )
    run.add_argument(
        "--report",
        nargs="?",
        const="auto",
        default=None,
        help="Write a markdown report. Optional path; default docs/benchmarks/<date>.md",
    )
    run.set_defaults(handler=_cmd_run)

    report = sub.add_parser("report", help="Render docs/benchmarks/<date>.md from a run directory")
    report.add_argument("--run-dir", type=Path, required=True)
    report.add_argument("--out", type=Path, default=None)
    report.set_defaults(handler=_cmd_report)
    return parser


def _add_filters(parser: argparse.ArgumentParser, *, profile_required: bool) -> None:
    parser.add_argument(
        "--profile",
        required=profile_required,
        choices=PROFILES,
        help="8gb (M1 8 GB Mac) or 16gb (plan baseline)",
    )
    parser.add_argument("--role", action="append", choices=ROLES, dest="roles")
    parser.add_argument(
        "--runtime",
        action="append",
        choices=RUNTIMES,
        dest="runtimes",
        help="Repeatable. fake runs YAML candidates through the in-process stub.",
    )
    parser.add_argument("--id", action="append", dest="ids", help="Restrict to these candidate ids")


def _cmd_hardware(_args: argparse.Namespace) -> int:
    info = detect_hardware()
    print(format_hardware(info))
    print(json.dumps(info.to_dict(), indent=2))
    return 0


def _cmd_list(args: argparse.Namespace) -> int:
    rows = _filtered(args)
    if not rows:
        print("no candidates matched", file=sys.stderr)
        return 1
    print(f"{'id':<36} {'role':<12} {'runtime':<10} {'quant':<10} {'min_ram':>7}  model_id")
    for candidate in rows:
        print(
            f"{candidate.id:<36} {candidate.role:<12} {candidate.runtime:<10} "
            f"{candidate.quantisation:<10} {candidate.min_ram_gb:>5} GB  {candidate.model_id}"
        )
    print()
    print("All rows are CANDIDATES TO BE CONFIRMED — not chosen defaults.")
    return 0


def _cmd_fetch(args: argparse.Namespace) -> int:
    models_dir = args.models_dir or default_models_dir()
    rows = _filtered(args)
    if not rows:
        print("no candidates matched", file=sys.stderr)
        return 1
    failed = 0
    for candidate in rows:
        try:
            message = fetch_candidate(candidate, models_dir)
        except FetchError as exc:
            print(f"error: {exc}", file=sys.stderr)
            failed += 1
            continue
        print(message)
    return 1 if failed else 0


def _cmd_run(args: argparse.Namespace) -> int:
    report_path = _resolve_report_flag(args.report)
    frame_ids = load_frame_id_list(args.frame_list) if args.frame_list is not None else None
    out = run_bakeoff(
        profile=args.profile,
        frames_dir=args.frames,
        frame_ids=frame_ids,
        out_dir=args.out,
        models_dir=args.models_dir,
        runtimes=args.runtimes,
        roles=args.roles,
        ids=args.ids,
        score_sample=args.score_sample,
        report_path=report_path,
    )
    print(f"wrote {out / 'run.json'}")
    if (out / "scoring.csv").is_file():
        print(f"wrote {out / 'scoring.csv'}")
    if report_path is not None:
        print(f"wrote {report_path}")
    return 0


def _cmd_report(args: argparse.Namespace) -> int:
    manifest = load_run_manifest(args.run_dir)
    hardware = HardwareInfo(**manifest["hardware"])
    out = args.out or default_report_path(date.today().isoformat())
    write_report(
        out,
        hardware=hardware,
        profile=str(manifest.get("profile") or ""),
        results=list(manifest.get("results") or []),
        frames_dir=str(manifest.get("frames_dir") or ""),
        frame_count=int(manifest.get("frame_count") or 0),
        scoring_csv=manifest.get("scoring_csv"),
    )
    print(f"wrote {out}")
    return 0


def _filtered(args: argparse.Namespace):
    rows = load_candidates(profile=args.profile, ids=args.ids)
    if args.roles:
        allowed_roles = set(args.roles)
        rows = [row for row in rows if row.role in allowed_roles]
    runtimes = args.runtimes
    if runtimes and runtimes != ["fake"]:
        allowed_runtimes = set(runtimes)
        rows = [row for row in rows if row.runtime in allowed_runtimes]
    return rows


def _resolve_report_flag(value: str | None) -> Path | None:
    if value is None:
        return None
    if value == "auto":
        return default_report_path(date.today().isoformat())
    return Path(value)
