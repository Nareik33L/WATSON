from __future__ import annotations

from pathlib import Path

import pytest

from watson_bench.candidates import CandidateConfigError, load_candidates


def test_loads_d6_fields_for_both_profiles() -> None:
    eight = load_candidates(profile="8gb")
    sixteen = load_candidates(profile="16gb")
    assert eight
    assert sixteen
    assert {row.role for row in eight} == {"vlm", "llm", "embeddings"}
    for row in eight + sixteen:
        assert row.model_id
        assert row.runtime in {"ollama", "llamacpp", "mlx"}
        assert row.quantisation
        assert row.context_window > 0
        assert row.min_ram_gb in {8, 16}
        assert row.status == "candidate"


def test_filters_runtime_and_role() -> None:
    rows = load_candidates(profile="8gb", role="vlm", runtime="ollama")
    assert len(rows) == 1
    assert rows[0].id == "ollama-qwen25vl-3b"


def test_rejects_unknown_profile(tmp_path: Path) -> None:
    path = tmp_path / "vlm.yaml"
    path.write_text(
        "role: vlm\nprofiles:\n  24gb:\n    candidates: []\n",
        encoding="utf-8",
    )
    with pytest.raises(CandidateConfigError, match="unknown profile"):
        load_candidates(directory=tmp_path)
