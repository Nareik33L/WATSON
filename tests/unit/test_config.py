"""Config schema, TOML load, env overrides, and keyed validation errors."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from watson.cli import main
from watson.config import ConfigError, load_config
from watson.config.schema import (
    APPLICATION_SUPPORT_WATSON,
    BAKEOFF_LLM_MODELS_LARGEST_FIRST,
    DEFAULT_DENIED_DOMAINS,
    DEFAULT_EXCLUDED_BUNDLE_IDS,
    DEFAULT_LLM_MODEL,
    DEFAULT_WINDOW_TITLE_PATTERNS,
    FALLBACK_LLM_MODEL,
    LARGE_LLM_TIMEOUT_S,
    IntelligenceConfig,
    llm_preset,
)
from watson.pipeline.status import StatusReport


def test_defaults_use_application_support_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("WATSON_DATA_DIR", raising=False)
    monkeypatch.delenv("WATSON_CONFIG_PATH", raising=False)
    # Point HOME at tmp so we do not touch the real Application Support tree.
    monkeypatch.setenv("HOME", str(tmp_path))
    config = load_config()
    assert config.data_dir == Path(tmp_path) / APPLICATION_SUPPORT_WATSON
    assert config.db_path == config.data_dir / "watson.db"
    assert config.spool_dir == config.data_dir / "spool"
    assert config.capture.interval_seconds == 45
    assert config.capture.dedup_threshold == 4
    assert config.segmentation.similarity_threshold == 0.25
    assert config.segmentation.close_after_seconds == 20 * 60
    assert config.exclusions.bundle_ids == DEFAULT_EXCLUDED_BUNDLE_IDS
    assert config.retention.window_title_days == 30
    assert config.retention.tier2_grace_days == 7
    assert config.retention.spool_hard_limit_hours == 24
    assert config.pipeline.backlog_batch_size == 32
    assert config.pipeline.day_end == "18:30"
    assert config.pipeline.recovery_deadline_hours == 2
    assert config.retention.derived_days is None
    assert config.intelligence.runtime == "ollama"
    assert config.intelligence.llm_model == "qwen2.5:3b"
    assert DEFAULT_LLM_MODEL == "qwen2.5:3b"
    assert FALLBACK_LLM_MODEL == "llama3.2:3b"
    assert config.intelligence.embedding_model == "nomic-embed-text"
    assert config.intelligence.vlm_enabled is False
    assert config.intelligence.ollama_host == "http://127.0.0.1:11434"
    assert config.intelligence.context_window == 4096
    assert config.intelligence.ollama.timeout_s == 300.0
    assert config.intelligence.ollama.num_predict == 512
    assert config.intelligence.ollama.unload_between_phases is True
    assert config.intelligence.ollama_num_ctx() == 4096
    assert config.intelligence.entity_similarity_threshold == 0.80
    assert config.intelligence.retrieve_top_k == 5


def test_toml_and_env_overrides(data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_path = data_dir / "config.toml"
    config_path.write_text(
        f"""
data_dir = "{data_dir}"

[capture]
interval_seconds = 60

[exclusions]
bundle_ids = ["com.example.secret"]
denied_domains = ["example.bank"]

[retention]
window_title_days = 14

[intelligence]
runtime = "fake"
context_window = 4096

[intelligence.ollama]
timeout_s = 180
num_ctx = 2048
num_predict = 512
unload_between_phases = false
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("WATSON_CAPTURE_INTERVAL", "90")
    monkeypatch.setenv("WATSON_SPOOL_DIR", str(data_dir / "custom-spool"))
    config = load_config()
    assert config.capture.interval_seconds == 90
    assert config.spool_dir == data_dir / "custom-spool"
    assert "com.example.secret" in config.exclusions.bundle_ids
    assert "com.apple.Passwords" in config.exclusions.bundle_ids
    assert set(DEFAULT_EXCLUDED_BUNDLE_IDS).issubset(config.exclusions.bundle_ids)
    assert "example.bank" in config.exclusions.denied_domains
    assert set(DEFAULT_DENIED_DOMAINS).issubset(config.exclusions.denied_domains)
    assert config.retention.window_title_days == 14
    assert config.db_path == data_dir / "watson.db"
    assert config.intelligence.runtime == "fake"
    assert config.intelligence.context_window == 4096
    assert config.intelligence.ollama.timeout_s == 180
    assert config.intelligence.ollama.num_ctx == 2048
    assert config.intelligence.ollama.num_predict == 512
    assert config.intelligence.ollama.unload_between_phases is False
    assert config.intelligence.ollama_num_ctx() == 2048


def test_bad_key_is_named_in_the_error(data_dir: Path) -> None:
    (data_dir / "config.toml").write_text(
        """
[capture]
interval_seconds = "fast"
""",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match=r"capture\.interval_seconds") as raised:
        load_config()
    assert "Invalid config" in str(raised.value)


def test_user_exclusions_merge_defaults_and_strip(
    data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path = data_dir / "config.toml"
    config_path.write_text(
        f"""
data_dir = "{data_dir}"

[exclusions]
bundle_ids = ["com.example.secret", "com.apple.Passwords ", "  "]
window_title_patterns = ["Vault unlock"]
allowed_bundle_ids = ["com.apple.FaceTime"]
""",
        encoding="utf-8",
    )
    config = load_config()
    assert "com.example.secret" in config.exclusions.bundle_ids
    assert "com.apple.Passwords" in config.exclusions.bundle_ids
    assert "com.1password.1password" in config.exclusions.bundle_ids
    assert "com.apple.FaceTime" not in config.exclusions.bundle_ids
    assert all(item == item.strip() and item for item in config.exclusions.bundle_ids)
    assert "Vault unlock" in config.exclusions.window_title_patterns
    assert set(DEFAULT_WINDOW_TITLE_PATTERNS).issubset(config.exclusions.window_title_patterns)
    assert config.exclusions.allowed_bundle_ids == ("com.apple.FaceTime",)
    assert config.exclusions.overridden_default_bundle_ids() == ("com.apple.FaceTime",)


def test_allowed_bundle_ids_warn_and_surface_overridden_defaults(
    data_dir: Path,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    (data_dir / "config.toml").write_text(
        f"""
data_dir = "{data_dir}"

[exclusions]
allowed_bundle_ids = ["com.apple.Passwords", "COM.APPLE.FACETIME", "com.example.harmless"]
""",
        encoding="utf-8",
    )
    with caplog.at_level(logging.WARNING, logger="watson.config"):
        config = load_config()
    overridden = config.exclusions.overridden_default_bundle_ids()
    assert overridden == ("com.apple.FaceTime", "com.apple.Passwords")
    assert "com.apple.Passwords" not in config.exclusions.bundle_ids
    assert "com.apple.FaceTime" not in config.exclusions.bundle_ids
    assert "com.1password.1password" in config.exclusions.bundle_ids
    warnings = caplog.text
    assert "com.apple.Passwords" in warnings
    assert "com.apple.FaceTime" in warnings
    assert "un-excludes built-in default" in warnings
    assert "com.example.harmless" not in warnings

    report = StatusReport({}, {}, None, exclusion_overrides=overridden)
    lines = "\n".join(report.lines())
    assert "exclusions.override.0: com.apple.FaceTime" in lines
    assert "exclusions.override.1: com.apple.Passwords" in lines
    assert "com.example.harmless" not in lines
    payload = report.as_dict()
    assert payload["exclusion_overrides"] == ["com.apple.FaceTime", "com.apple.Passwords"]

    assert main(["status"]) == 0
    status_out = capsys.readouterr().out
    assert "exclusions.override.0: com.apple.FaceTime" in status_out
    assert "exclusions.override.1: com.apple.Passwords" in status_out


def test_non_array_exclusion_list_is_config_error(data_dir: Path) -> None:
    (data_dir / "config.toml").write_text(
        f"""
data_dir = "{data_dir}"

[exclusions]
bundle_ids = "com.example.secret"
""",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match=r"exclusions"):
        load_config()


def test_spool_hard_limit_hours_rejects_above_24(data_dir: Path) -> None:
    (data_dir / "config.toml").write_text(
        f"""
data_dir = "{data_dir}"

[retention]
spool_hard_limit_hours = 25
""",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match=r"retention\.spool_hard_limit_hours") as raised:
        load_config()
    assert "Invalid config" in str(raised.value)


def test_unknown_intelligence_runtime_is_named(data_dir: Path) -> None:
    (data_dir / "config.toml").write_text(
        f'data_dir = "{data_dir}"\n[intelligence]\nruntime = "cloud"\n',
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match=r"intelligence\.runtime"):
        load_config()


def test_unknown_key_is_named(data_dir: Path) -> None:
    (data_dir / "config.toml").write_text(
        """
[capture]
not_a_real_field = 1
""",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match=r"capture\.not_a_real_field"):
        load_config()


def test_watson_llm_model_env_overrides_default(
    data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WATSON_LLM_MODEL", FALLBACK_LLM_MODEL)
    config = load_config()
    assert config.intelligence.llm_model == FALLBACK_LLM_MODEL
    monkeypatch.delenv("WATSON_LLM_MODEL")
    again = load_config()
    assert again.intelligence.llm_model == DEFAULT_LLM_MODEL


@pytest.mark.parametrize("model", ["qwen2.5:7b", "llama3.1:8b", "gemma2:9b"])
def test_large_llm_presets_use_900s_timeout(model: str) -> None:
    intel = IntelligenceConfig().with_llm_model(model)
    assert intel.llm_model == model
    assert intel.ollama.timeout_s == LARGE_LLM_TIMEOUT_S
    assert intel.ollama_num_ctx() == 4096
    assert llm_preset(model).timeout_s == LARGE_LLM_TIMEOUT_S


def test_gemma2_9b_variant_and_unknown_model_use_long_timeout(
    data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WATSON_LLM_MODEL", "gemma2:9b")
    config = load_config()
    assert config.intelligence.llm_model == "gemma2:9b"
    assert config.intelligence.ollama.timeout_s == LARGE_LLM_TIMEOUT_S
    variant = IntelligenceConfig().with_llm_model("gemma2:9b-instruct-q4_0")
    assert variant.ollama.timeout_s == LARGE_LLM_TIMEOUT_S
    unknown = IntelligenceConfig().with_llm_model("custom-7b")
    assert unknown.ollama.timeout_s == LARGE_LLM_TIMEOUT_S
    three_b = IntelligenceConfig().with_llm_model(FALLBACK_LLM_MODEL)
    assert three_b.ollama.timeout_s == 300.0
    assert BAKEOFF_LLM_MODELS_LARGEST_FIRST == ("gemma2:9b", "llama3.1:8b", "qwen2.5:7b")
    explicit = IntelligenceConfig(
        llm_model="gemma2:9b",
        ollama={"timeout_s": 1200, "num_ctx": 2048},
    )
    assert explicit.ollama.timeout_s == 1200.0
    assert explicit.ollama.num_ctx == 2048
