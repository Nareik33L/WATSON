"""watson-fixturegen CLI: render a scenario into a golden-day fixture."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from watson_fixturegen.render import render_scenario


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        return args.handler(args)
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="watson-fixturegen",
        description=(
            "Render fictional app windows from a scenario YAML into PNG frames, "
            "helper-schema sidecars, truth.yaml, and sample50.txt."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)
    render = sub.add_parser("render", help="Render a scenario into a fixture directory")
    render.add_argument("scenario", type=Path, help="Scenario YAML path")
    render.add_argument(
        "--out",
        type=Path,
        required=True,
        help="Output directory (e.g. tests/fixtures/day_acme_q4)",
    )
    render.add_argument("--seed", type=int, default=None, help="Override scenario seed")
    render.add_argument(
        "--dry-run",
        action="store_true",
        help="Expand the schedule and write truth/sample50 without launching Chromium",
    )
    render.set_defaults(handler=_cmd_render)
    return parser


def _cmd_render(args: argparse.Namespace) -> int:
    def progress(done: int, total: int, frame_id: str) -> None:
        print(f"[{done}/{total}] {frame_id}", file=sys.stderr)

    result = render_scenario(
        args.scenario,
        args.out,
        seed=args.seed,
        dry_run=args.dry_run,
        progress=None if args.dry_run else progress,
    )
    mode = "dry-run" if args.dry_run else "render"
    print(f"{mode}: {result.frame_count} frames -> {result.out_dir}")
    print(f"truth: {result.truth_path}")
    print(f"sample50: {result.sample_path}")
    if not args.dry_run:
        print(f"frames: {result.frames_dir}")
        if result.manifest_path is not None:
            print(f"manifest: {result.manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
