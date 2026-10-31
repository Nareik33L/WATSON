"""Run the M0-A bake-off against a frames directory."""

from __future__ import annotations

import json
import os
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from watson_bench.adapters.base import AdapterError, BenchRuntime, get_runtime
from watson_bench.candidates import Candidate, load_candidates
from watson_bench.frames import Frame, load_frames
from watson_bench.hardware import HardwareInfo, detect_hardware
from watson_bench.metrics import RssSampler, find_process_pids, mean, timed
from watson_bench.network import allowed_ollama_port, localhost_only
from watson_bench.paths import default_models_dir, default_results_dir, samples_dir
from watson_bench.quality import sample_frames, write_frame_output, write_scoring_csv
from watson_bench.report import write_report


def run_bakeoff(
    *,
    profile: str,
    frames_dir: Path | None = None,
    frame_ids: list[str] | None = None,
    out_dir: Path | None = None,
    models_dir: Path | None = None,
    runtimes: list[str] | None = None,
    roles: list[str] | None = None,
    ids: list[str] | None = None,
    score_sample: int = 50,
    report_path: Path | None = None,
    hardware: HardwareInfo | None = None,
) -> Path:
    hardware = hardware or detect_hardware()
    frames_dir = frames_dir or samples_dir()
    models_dir = models_dir or default_models_dir()
    frames = load_frames(frames_dir, frame_ids=frame_ids)
    scored = sample_frames(frames, limit=score_sample)
    candidates = _select_candidates(profile, runtimes, roles, ids)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out_dir = out_dir or (default_results_dir() / f"{profile}-{stamp}")
    out_dir.mkdir(parents=True, exist_ok=True)
    frame_out = out_dir / "frames"
    scoring_csv = out_dir / "scoring.csv"

    results: list[dict[str, Any]] = []
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
    with localhost_only(allowed_port=allowed_ollama_port()):
        for candidate in candidates:
            results.append(
                _run_one(
                    candidate,
                    frames=frames,
                    scored=scored,
                    models_dir=models_dir,
                    frame_out=frame_out,
                    scoring_csv=scoring_csv,
                    use_fake="fake" in (runtimes or []),
                )
            )

    manifest = {
        "started_at": stamp,
        "finished_at": datetime.now(UTC).isoformat(),
        "profile": profile,
        "hardware": hardware.to_dict(),
        "frames_dir": str(frames_dir),
        "frame_count": len(frames),
        "score_sample": len(scored),
        "scoring_csv": str(scoring_csv) if scoring_csv.is_file() else None,
        "results": results,
    }
    (out_dir / "run.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    if report_path is not None:
        write_report(
            report_path,
            hardware=hardware,
            profile=profile,
            results=results,
            frames_dir=str(frames_dir),
            frame_count=len(frames),
            scoring_csv=manifest["scoring_csv"],
        )
    return out_dir


def _select_candidates(
    profile: str,
    runtimes: list[str] | None,
    roles: list[str] | None,
    ids: list[str] | None,
) -> list[Candidate]:
    selected: list[Candidate] = []
    wanted_runtimes = set(runtimes) if runtimes else None
    wanted_roles = set(roles) if roles else None
    fake_only = wanted_runtimes == {"fake"}
    for candidate in load_candidates(profile=profile, ids=ids):
        if wanted_roles and candidate.role not in wanted_roles:
            continue
        if fake_only:
            selected.append(candidate)
            continue
        if wanted_runtimes and candidate.runtime not in wanted_runtimes:
            continue
        selected.append(candidate)
    if not selected:
        raise ValueError("no candidates matched the requested filters")
    return selected


def _run_one(
    candidate: Candidate,
    *,
    frames: list[Frame],
    scored: list[Frame],
    models_dir: Path,
    frame_out: Path,
    scoring_csv: Path,
    use_fake: bool,
) -> dict[str, Any]:
    runtime_name = "fake" if use_fake else candidate.runtime
    row = {
        "candidate": asdict(candidate),
        "adapter": runtime_name,
        "skipped": False,
        "skip_reason": None,
        "error": None,
        "load_time_s": None,
        "tokens_per_s": None,
        "time_per_frame_s": None,
        "time_per_call_s": None,
        "embedding_texts_per_s": None,
        "peak_rss_bytes": None,
        "n_calls": 0,
        "completion_tokens": 0,
    }
    try:
        runtime: BenchRuntime = get_runtime(runtime_name)
    except Exception as exc:
        row["skipped"] = True
        row["skip_reason"] = f"{runtime_name} could not start: {exc}"
        return row
    skip = runtime.availability(candidate, models_dir)
    if skip is not None:
        row["skipped"] = True
        row["skip_reason"] = skip.reason
        return row

    extra_pids = find_process_pids("ollama") if runtime_name == "ollama" else []
    sampler = RssSampler(extra_pids=extra_pids)
    sampler.start()
    try:
        with timed() as load_elapsed:
            runtime.load(candidate, models_dir)
        row["load_time_s"] = load_elapsed()
        if candidate.role == "vlm":
            _run_vlm(runtime, candidate, scored, frame_out, scoring_csv, row)
        elif candidate.role == "llm":
            _run_llm(runtime, candidate, scored, frame_out, scoring_csv, row)
        else:
            _run_embeddings(runtime, candidate, frames, row)
    except (AdapterError, OSError, ValueError, RuntimeError) as exc:
        row["error"] = str(exc)
    finally:
        try:
            runtime.unload()
        except Exception:
            pass
        row["peak_rss_bytes"] = sampler.stop()
    return row


def _run_vlm(
    runtime: BenchRuntime,
    candidate: Candidate,
    frames: list[Frame],
    frame_out: Path,
    scoring_csv: Path,
    row: dict[str, Any],
) -> None:
    latencies: list[float] = []
    speeds: list[float] = []
    tokens = 0
    for frame in frames:
        call = runtime.describe(candidate, frame)
        latencies.append(call.metrics.latency_s)
        tokens += call.metrics.completion_tokens
        if call.metrics.tokens_per_s:
            speeds.append(call.metrics.tokens_per_s)
        write_frame_output(
            frame_out,
            frame.frame_id,
            candidate.id,
            {
                "frame_id": frame.frame_id,
                "candidate_id": candidate.id,
                "role": "vlm",
                "output": call.output,
                "latency_s": call.metrics.latency_s,
                "completion_tokens": call.metrics.completion_tokens,
            },
        )
    write_scoring_csv(scoring_csv, frames=frames, candidate_id=candidate.id, role="vlm")
    row["n_calls"] = len(latencies)
    row["completion_tokens"] = tokens
    row["time_per_frame_s"] = mean(latencies)
    row["tokens_per_s"] = mean(speeds)


def _run_llm(
    runtime: BenchRuntime,
    candidate: Candidate,
    frames: list[Frame],
    frame_out: Path,
    scoring_csv: Path,
    row: dict[str, Any],
) -> None:
    latencies: list[float] = []
    speeds: list[float] = []
    tokens = 0
    for frame in frames:
        call = runtime.complete(candidate, frame)
        latencies.append(call.metrics.latency_s)
        tokens += call.metrics.completion_tokens
        if call.metrics.tokens_per_s:
            speeds.append(call.metrics.tokens_per_s)
        write_frame_output(
            frame_out,
            frame.frame_id,
            candidate.id,
            {
                "frame_id": frame.frame_id,
                "candidate_id": candidate.id,
                "role": "llm",
                "output": call.output,
                "latency_s": call.metrics.latency_s,
                "completion_tokens": call.metrics.completion_tokens,
            },
        )
    write_scoring_csv(scoring_csv, frames=frames, candidate_id=candidate.id, role="llm")
    row["n_calls"] = len(latencies)
    row["completion_tokens"] = tokens
    row["time_per_call_s"] = mean(latencies)
    row["tokens_per_s"] = mean(speeds)


def _run_embeddings(
    runtime: BenchRuntime,
    candidate: Candidate,
    frames: list[Frame],
    row: dict[str, Any],
) -> None:
    texts = [frame.ocr_text or frame.window_title or frame.frame_id for frame in frames]
    call = runtime.embed(candidate, texts)
    row["n_calls"] = 1
    row["completion_tokens"] = 0
    latency = call.metrics.latency_s
    row["time_per_call_s"] = latency
    if latency > 0:
        row["embedding_texts_per_s"] = len(texts) / latency
    vectors = call.output
    row["embedding_dim"] = len(vectors[0]) if isinstance(vectors, list) and vectors else 0
