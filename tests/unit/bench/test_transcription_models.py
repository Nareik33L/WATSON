from __future__ import annotations

import multiprocessing
import sys
import types
from pathlib import Path

import pytest

from watson_bench.transcription.adapters import MlxWhisperRuntime, clear_mlx_model_holder
from watson_bench.transcription.fetch import fetch_transcription_model
from watson_bench.transcription.models import engine_model_dir, model_ready
from watson_bench.transcription.run import rss_probe_main


def test_model_ready_uses_canonical_engine_size_dir(tmp_path: Path) -> None:
    assert not model_ready(tmp_path, "faster-whisper", "tiny")
    dest = engine_model_dir(tmp_path, "faster-whisper", "tiny")
    dest.mkdir(parents=True)
    (dest / "model.bin").write_bytes(b"weights")
    (dest / "config.json").write_text("{}", encoding="utf-8")
    assert model_ready(tmp_path, "faster-whisper", "tiny")


def test_hf_cache_layout_is_not_the_resolver_path(tmp_path: Path) -> None:
    cache = tmp_path / "faster-whisper" / "models--Systran--faster-whisper-tiny"
    cache.mkdir(parents=True)
    (cache / "model.bin").write_bytes(b"weights")
    assert not model_ready(tmp_path, "faster-whisper", "tiny")
    assert engine_model_dir(tmp_path, "faster-whisper", "tiny") != cache


def test_fetch_fake_names_canonical_dir(tmp_path: Path) -> None:
    message = fetch_transcription_model("fake", "tiny", tmp_path)
    assert "fake" in message


def test_faster_whisper_fetch_writes_canonical_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dest = engine_model_dir(tmp_path, "faster-whisper", "tiny")
    seen: dict[str, str] = {}

    def fake_download(size: str, output_dir: str | None = None, **_kwargs: object) -> str:
        assert output_dir is not None
        seen["size"] = size
        seen["output_dir"] = output_dir
        path = Path(output_dir)
        path.mkdir(parents=True, exist_ok=True)
        (path / "model.bin").write_bytes(b"weights")
        (path / "config.json").write_text("{}", encoding="utf-8")
        return output_dir

    utils = types.ModuleType("faster_whisper.utils")
    utils.download_model = fake_download  # type: ignore[attr-defined]
    pkg = types.ModuleType("faster_whisper")
    pkg.utils = utils  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "faster_whisper", pkg)
    monkeypatch.setitem(sys.modules, "faster_whisper.utils", utils)
    monkeypatch.setenv("WATSON_ALLOW_NETWORK", "1")

    message = fetch_transcription_model("faster-whisper", "tiny", tmp_path)
    assert seen["size"] == "tiny"
    assert seen["output_dir"] == str(dest)
    assert str(dest) in message
    assert "models--Systran" not in message
    assert model_ready(tmp_path, "faster-whisper", "tiny")


def test_mlx_load_populates_holder_and_unload_clears_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dest = engine_model_dir(tmp_path, "mlx-whisper", "tiny")
    dest.mkdir(parents=True)
    (dest / "weights.npz").write_bytes(b"x" * 64)

    class Holder:
        model: object | None = "stale"
        model_path: str | None = "stale"

    loaded: list[str] = []

    def fake_load(path: str, dtype: object = None) -> str:
        del dtype
        loaded.append(path)
        return f"loaded:{path}"

    monkeypatch.setattr(
        "watson_bench.transcription.adapters.mlx_model_holder",
        lambda: Holder,
    )
    monkeypatch.setattr(
        "watson_bench.transcription.adapters._mlx_load_model",
        lambda: fake_load,
    )
    monkeypatch.setattr(
        "watson_bench.transcription.adapters._mlx_dtype",
        lambda: "f16",
    )

    runtime = MlxWhisperRuntime()
    runtime.load("tiny", tmp_path)
    assert loaded == [str(dest)]
    assert Holder.model == f"loaded:{dest}"
    assert Holder.model_path == str(dest)
    assert runtime._loaded is True
    runtime.unload()
    assert Holder.model is None
    assert Holder.model_path is None
    assert runtime._loaded is False
    loaded.clear()
    runtime.load("tiny", tmp_path)
    assert loaded == [str(dest)]
    assert Holder.model == f"loaded:{dest}"
    runtime.unload()


def test_clear_mlx_holder_without_package() -> None:
    clear_mlx_model_holder()


def _measure_spawned_rss(nbytes: int) -> int:
    ctx = multiprocessing.get_context("spawn")
    queue = ctx.Queue()
    proc = ctx.Process(target=rss_probe_main, args=(nbytes, queue))
    proc.start()
    value = queue.get(timeout=30)
    proc.join(timeout=10)
    assert proc.exitcode == 0
    assert isinstance(value, int)
    return value


def test_spawned_child_rss_does_not_inherit_sibling_peak() -> None:
    fat = _measure_spawned_rss(200 * 1024 * 1024)
    thin = _measure_spawned_rss(0)
    assert fat >= 180 * 1024 * 1024
    assert thin < 150 * 1024 * 1024
    assert fat - thin > 100 * 1024 * 1024
