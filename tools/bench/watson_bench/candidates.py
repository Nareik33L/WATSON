"""Load D6 candidate YAML (one file per role; 8gb and 16gb profiles)."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml

from watson_bench.paths import candidates_dir

ROLES = ("vlm", "llm", "embeddings")
PROFILES = ("8gb", "16gb")
RUNTIMES = ("ollama", "llamacpp", "mlx", "fake")
REQUIRED_FIELDS = ("model_id", "runtime", "quantisation", "context_window", "min_ram_gb")


@dataclass(frozen=True)
class Candidate:
    """One bake-off candidate. Status is always a candidate until M0-A is scored."""

    id: str
    role: str
    profile: str
    model_id: str
    runtime: str
    quantisation: str
    context_window: int
    min_ram_gb: int
    status: str = "candidate"
    notes: str = ""
    filename: str | None = None
    mmproj: str | None = None
    approx_download_gb: float | None = None
    family: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        return data


class CandidateConfigError(ValueError):
    """Invalid candidate YAML."""


def load_candidates(
    *,
    profile: str | None = None,
    role: str | None = None,
    runtime: str | None = None,
    ids: Iterable[str] | None = None,
    directory: Path | None = None,
) -> list[Candidate]:
    root = directory or candidates_dir()
    if not root.is_dir():
        raise CandidateConfigError(f"candidates directory not found: {root}")

    selected: list[Candidate] = []
    id_filter = set(ids) if ids is not None else None
    for path in sorted(root.glob("*.yaml")):
        selected.extend(_load_role_file(path))

    out: list[Candidate] = []
    for candidate in selected:
        if profile is not None and candidate.profile != profile:
            continue
        if role is not None and candidate.role != role:
            continue
        if runtime is not None and candidate.runtime != runtime:
            continue
        if id_filter is not None and candidate.id not in id_filter:
            continue
        out.append(candidate)
    return out


def _load_role_file(path: Path) -> list[Candidate]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise CandidateConfigError(f"{path}: expected a mapping")
    role = raw.get("role")
    if role not in ROLES:
        raise CandidateConfigError(f"{path}: role must be one of {ROLES}, got {role!r}")
    profiles = raw.get("profiles")
    if not isinstance(profiles, dict):
        raise CandidateConfigError(f"{path}: missing profiles mapping")

    found: list[Candidate] = []
    for profile_name, block in profiles.items():
        profile = str(profile_name)
        if profile not in PROFILES:
            raise CandidateConfigError(f"{path}: unknown profile {profile!r}")
        if not isinstance(block, dict):
            raise CandidateConfigError(f"{path}: profile {profile} must be a mapping")
        rows = block.get("candidates")
        if not isinstance(rows, list):
            raise CandidateConfigError(f"{path}: profile {profile} missing candidates list")
        for index, row in enumerate(rows):
            found.append(_parse_candidate(path, role, profile, index, row))
    return found


def _parse_candidate(
    path: Path, role: str, profile: str, index: int, row: object
) -> Candidate:
    if not isinstance(row, dict):
        raise CandidateConfigError(f"{path}: {profile}[{index}] must be a mapping")
    missing = [name for name in REQUIRED_FIELDS if name not in row]
    if missing:
        raise CandidateConfigError(
            f"{path}: {profile}[{index}] missing D6 fields: {', '.join(missing)}"
        )
    ident = str(row.get("id") or f"{role}-{profile}-{index}")
    runtime = str(row["runtime"])
    if runtime not in RUNTIMES:
        raise CandidateConfigError(f"{path}: {ident} has unknown runtime {runtime!r}")
    known = {
        "id",
        "model_id",
        "runtime",
        "quantisation",
        "context_window",
        "min_ram_gb",
        "status",
        "notes",
        "filename",
        "mmproj",
        "approx_download_gb",
        "family",
    }
    extra = {key: value for key, value in row.items() if key not in known}
    return Candidate(
        id=ident,
        role=role,
        profile=profile,
        model_id=str(row["model_id"]),
        runtime=runtime,
        quantisation=str(row["quantisation"]),
        context_window=int(row["context_window"]),
        min_ram_gb=int(row["min_ram_gb"]),
        status=str(row.get("status") or "candidate"),
        notes=str(row.get("notes") or ""),
        filename=str(row["filename"]) if row.get("filename") else None,
        mmproj=str(row["mmproj"]) if row.get("mmproj") else None,
        approx_download_gb=(
            float(row["approx_download_gb"]) if row.get("approx_download_gb") is not None else None
        ),
        family=str(row.get("family") or ""),
        extra=extra,
    )
