"""watson-fixturegen CLI: render a scenario into a golden-day fixture."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from watson_fixturegen.meetings.macos import MacOSRequiredError
from watson_fixturegen.meetings.render import RenderResult, render_meeting
from watson_fixturegen.paths import meetings_scripts_dir
from watson_fixturegen.render import render_scenario


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        return args.handler(args)
    except (OSError, ValueError, RuntimeError, MacOSRequiredError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="watson-fixturegen",
        description=(
            "Render fictional app windows from a scenario YAML into PNG frames, "
            "helper-schema sidecars, truth.yaml, and sample50.txt. "
            "Meeting dialogues: `meetings render` (macOS `say`)."
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

    meetings = sub.add_parser(
        "meetings",
        help="Render scripted meeting audio (V1.1 §8.1). Dialogue TTS is macOS-only.",
    )
    meet_sub = meetings.add_subparsers(dest="meetings_command", required=True)
    meet_render = meet_sub.add_parser(
        "render",
        help="Render a YAML meeting script to WAV, optional m4a, transcript, truth.yaml",
    )
    meet_render.add_argument(
        "script",
        type=Path,
        nargs="?",
        default=None,
        help="Meeting script YAML (omit when using --all)",
    )
    meet_render.add_argument(
        "--all",
        action="store_true",
        help="Render every YAML in tools/fixture_gen/meetings/",
    )
    meet_render.add_argument(
        "--out",
        type=Path,
        required=True,
        help="Output directory (or parent directory with --all)",
    )
    meet_render.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate the script and write truth/transcript estimates without `say`",
    )
    meet_render.set_defaults(handler=_cmd_meetings_render)
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


def _cmd_meetings_render(args: argparse.Namespace) -> int:
    def progress(message: str) -> None:
        print(message, file=sys.stderr)

    if args.all:
        root = meetings_scripts_dir()
        scripts = sorted(root.glob("*.yaml"))
        if not scripts:
            raise FileNotFoundError(f"no meeting scripts in {root}")
        failed = 0
        for path in scripts:
            try:
                from watson_fixturegen.meetings.script import load_meeting_script

                dest = args.out / load_meeting_script(path).name
                result = render_meeting(
                    path, dest, dry_run=args.dry_run, progress=progress
                )
            except MacOSRequiredError as exc:
                print(f"error: {path.name}: {exc}", file=sys.stderr)
                failed += 1
                continue
            _print_meeting_result(result)
        return 1 if failed else 0
    if args.script is None:
        raise ValueError("pass a script path or --all")
    result = render_meeting(
        args.script, args.out, dry_run=args.dry_run, progress=progress
    )
    _print_meeting_result(result)
    return 0


def _print_meeting_result(result: RenderResult) -> None:
    print(
        f"{'dry-run' if result.dry_run else 'render'}: {result.name} "
        f"({result.kind}, {result.duration_ms} ms) -> {result.out_dir}"
    )
    print(f"truth: {result.truth_path}")
    print(f"transcript: {result.transcript_path}")
    if result.wav_path is not None:
        print(f"wav: {result.wav_path}")
    if result.m4a_path is not None:
        print(f"m4a: {result.m4a_path}")


if __name__ == "__main__":
    raise SystemExit(main())
