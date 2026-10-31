"""Render a meeting script to WAV / optional m4a / transcript / truth.yaml."""

from __future__ import annotations

import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from watson_fixturegen.meetings.audio import (
    concat_pcm,
    duration_s,
    mix_white_noise,
    silence_pcm,
    tone_pcm,
    write_pcm16_wav,
)
from watson_fixturegen.meetings.macos import (
    MacOSRequiredError,
    afconvert_m4a,
    is_macos,
    resolve_voice,
    say_to_pcm,
)
from watson_fixturegen.meetings.script import (
    Line,
    MeetingScript,
    estimate_line_duration_s,
    load_meeting_script,
)
from watson_fixturegen.meetings.transcript import dump_meeting_truth, render_transcript, write_text

Progress = Callable[[str], None]


@dataclass(frozen=True)
class RenderResult:
    name: str
    kind: str
    out_dir: Path
    wav_path: Path | None
    m4a_path: Path | None
    transcript_path: Path
    truth_path: Path
    duration_ms: int
    dry_run: bool


def render_meeting(
    script_path: Path,
    out_dir: Path,
    *,
    dry_run: bool = False,
    progress: Progress | None = None,
) -> RenderResult:
    script = load_meeting_script(script_path)
    out_dir.mkdir(parents=True, exist_ok=True)
    if script.kind == "dialogue":
        return _render_dialogue(script, out_dir, dry_run=dry_run, progress=progress)
    if script.kind in {"silence", "one_second"}:
        seconds = 1.0 if script.kind == "one_second" else float(script.duration_s or 15.0)
        pcm = silence_pcm(seconds, script.sample_rate)
        return _write_edge(script, out_dir, pcm, dry_run=dry_run, progress=progress)
    if script.kind == "tone":
        pcm = tone_pcm(
            float(script.duration_s or 15.0),
            frequency_hz=script.frequency_hz,
            sample_rate=script.sample_rate,
        )
        return _write_edge(script, out_dir, pcm, dry_run=dry_run, progress=progress)
    if script.kind == "corrupt":
        return _write_corrupt(script, out_dir, dry_run=dry_run)
    if script.kind == "unsupported":
        return _write_unsupported(script, out_dir, dry_run=dry_run)
    raise ValueError(f"unsupported meeting kind {script.kind!r}")


def render_meeting_scripts(
    scripts: list[Path],
    out_root: Path,
    *,
    dry_run: bool = False,
    progress: Progress | None = None,
) -> list[RenderResult]:
    results: list[RenderResult] = []
    for path in scripts:
        script = load_meeting_script(path)
        dest = out_root / script.name
        results.append(render_meeting(path, dest, dry_run=dry_run, progress=progress))
    return results


def _render_dialogue(
    script: MeetingScript,
    out_dir: Path,
    *,
    dry_run: bool,
    progress: Progress | None,
) -> RenderResult:
    if dry_run:
        estimated, total_s = _estimate_rows(script)
        return _finish(
            script,
            out_dir,
            pcm=None,
            rows=estimated,
            duration_s=total_s,
            dry_run=True,
            progress=progress,
        )

    if not is_macos():
        raise MacOSRequiredError()

    chunks: list[bytes] = []
    rows: list[dict[str, object]] = []
    measured: list[tuple[Line, bytes, int]] = []
    with tempfile.TemporaryDirectory(prefix="watson-meetings-") as tmp:
        tmp_dir = Path(tmp)
        available = None
        try:
            from watson_fixturegen.meetings.macos import list_say_voices

            available = list_say_voices()
        except (OSError, RuntimeError):
            available = None
        for index, line in enumerate(script.lines):
            if progress:
                progress(f"{script.name} line {index + 1}/{len(script.lines)}")
            voice = resolve_voice(line.voice or script.default_voice, available)
            pcm = b""
            if line.text.strip():
                wav = tmp_dir / f"line-{index:04d}.wav"
                pcm = say_to_pcm(line.text, voice, wav, sample_rate=script.sample_rate)
            pause_ms = line.pause_ms if line.pause_ms is not None else script.default_pause_ms
            measured.append((line, pcm, pause_ms))

    speech_s = sum(duration_s(pcm, script.sample_rate) for _, pcm, _ in measured)
    pause_s = sum(pause_ms / 1000.0 for _, _, pause_ms in measured)
    extra = 0.0
    if script.target_duration_s is not None:
        extra = max(0.0, script.target_duration_s - speech_s - pause_s)
    n_pauses = max(1, len(measured))
    extra_each = extra / n_pauses

    cursor = 0.0
    for line, pcm, pause_ms in measured:
        start = cursor
        if pcm:
            chunks.append(pcm)
            cursor += duration_s(pcm, script.sample_rate)
        end = cursor
        if line.text.strip():
            rows.append(
                {
                    "start_s": start,
                    "end_s": end,
                    "speaker": line.speaker,
                    "text": line.text.strip(),
                }
            )
        silence = (pause_ms / 1000.0) + extra_each
        if silence > 0:
            chunks.append(silence_pcm(silence, script.sample_rate))
            cursor += silence

    pcm_out = concat_pcm(chunks)
    if script.noise is not None:
        pcm_out = mix_white_noise(pcm_out, script.noise)
    return _finish(
        script,
        out_dir,
        pcm=pcm_out,
        rows=rows,
        duration_s=duration_s(pcm_out, script.sample_rate),
        dry_run=False,
        progress=progress,
    )


def _estimate_rows(script: MeetingScript) -> tuple[list[dict[str, object]], float]:
    speech_s = sum(estimate_line_duration_s(line.text) for line in script.lines)
    pause_s = sum(
        (line.pause_ms if line.pause_ms is not None else script.default_pause_ms) / 1000.0
        for line in script.lines
    )
    extra = 0.0
    if script.target_duration_s is not None:
        extra = max(0.0, script.target_duration_s - speech_s - pause_s)
    extra_each = extra / max(1, len(script.lines))
    rows: list[dict[str, object]] = []
    cursor = 0.0
    for line in script.lines:
        spoken = estimate_line_duration_s(line.text)
        start = cursor
        cursor += spoken
        if line.text.strip():
            rows.append(
                {
                    "start_s": start,
                    "end_s": cursor,
                    "speaker": line.speaker,
                    "text": line.text.strip(),
                }
            )
        pause = (line.pause_ms if line.pause_ms is not None else script.default_pause_ms) / 1000.0
        cursor += pause + extra_each
    return rows, cursor


def _write_edge(
    script: MeetingScript,
    out_dir: Path,
    pcm: bytes,
    *,
    dry_run: bool,
    progress: Progress | None,
) -> RenderResult:
    if progress:
        progress(f"{script.name}: {script.kind}")
    return _finish(
        script,
        out_dir,
        pcm=None if dry_run else pcm,
        rows=[],
        duration_s=duration_s(pcm, script.sample_rate),
        dry_run=dry_run,
        progress=progress,
    )


def _write_corrupt(script: MeetingScript, out_dir: Path, *, dry_run: bool) -> RenderResult:
    wav_path = out_dir / "audio.wav"
    if not dry_run:
        # RIFF header claiming a WAV, then truncated garbage so decoders fail.
        wav_path.write_bytes(b"RIFF\x00\x00\x00\x00WAVEfmt \x10\x00" + b"\xff" * 24)
    return _sidecar_only(script, out_dir, wav_path if not dry_run else None, dry_run=dry_run)


def _write_unsupported(script: MeetingScript, out_dir: Path, *, dry_run: bool) -> RenderResult:
    suffix = script.container if script.container.startswith(".") else f".{script.container}"
    path = out_dir / f"audio{suffix}"
    if not dry_run:
        path.write_bytes(b"this is not a media container\n")
    return _sidecar_only(script, out_dir, path if not dry_run else None, dry_run=dry_run)


def _sidecar_only(
    script: MeetingScript,
    out_dir: Path,
    media_path: Path | None,
    *,
    dry_run: bool,
) -> RenderResult:
    transcript_path = out_dir / "transcript.txt"
    truth_path = out_dir / "truth.yaml"
    write_text(transcript_path, "")
    write_text(truth_path, dump_meeting_truth(script, duration_ms=0))
    return RenderResult(
        name=script.name,
        kind=script.kind,
        out_dir=out_dir,
        wav_path=media_path if media_path and media_path.suffix == ".wav" else None,
        m4a_path=None,
        transcript_path=transcript_path,
        truth_path=truth_path,
        duration_ms=0,
        dry_run=dry_run,
    )


def _finish(
    script: MeetingScript,
    out_dir: Path,
    *,
    pcm: bytes | None,
    rows: list[dict[str, object]],
    duration_s: float,
    dry_run: bool,
    progress: Progress | None,
) -> RenderResult:
    wav_path: Path | None = None
    m4a_path: Path | None = None
    if pcm is not None:
        wav_path = out_dir / "audio.wav"
        write_pcm16_wav(wav_path, pcm, sample_rate=script.sample_rate)
        if is_macos():
            try:
                m4a_path = afconvert_m4a(wav_path, out_dir / "audio.m4a")
            except (MacOSRequiredError, RuntimeError, OSError) as exc:
                if progress:
                    progress(f"{script.name}: afconvert skipped ({exc})")
                m4a_path = None
    duration_ms = int(round(duration_s * 1000))
    transcript_path = out_dir / "transcript.txt"
    truth_path = out_dir / "truth.yaml"
    write_text(transcript_path, render_transcript(rows))
    write_text(truth_path, dump_meeting_truth(script, duration_ms=duration_ms))
    return RenderResult(
        name=script.name,
        kind=script.kind,
        out_dir=out_dir,
        wav_path=wav_path,
        m4a_path=m4a_path,
        transcript_path=transcript_path,
        truth_path=truth_path,
        duration_ms=duration_ms,
        dry_run=dry_run,
    )
