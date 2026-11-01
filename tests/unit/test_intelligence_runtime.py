"""Fake + Ollama runtimes, config selection, and §7.4 hold-on-missing."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from urllib.request import ProxyHandler, Request, build_opener, getproxies_environment

import pytest

from tests.replay_support import TEST_KEY
from watson.config import load_config
from watson.config.schema import IntelligenceConfig
from watson.intelligence.runtime import (
    UNDERSTAND_JOB_KIND,
    FakeRuntime,
    OllamaRuntime,
    RuntimeUnavailable,
    build_runtime,
    hold_if_unavailable,
)
from watson.intelligence.runtime import http as http_mod
from watson.intelligence.runtime.http import (
    NoRedirectHandler,
    canonical_ollama_url,
    direct_opener,
    request_json,
)
from watson.intelligence.schemas import UNDERSTANDING_JSON_SCHEMA, UnderstandingOutput
from watson.memory.db import connect
from watson.memory.migrate import upgrade_head
from watson.pipeline.jobs import JobState, enqueue, get_job

RECORDINGS = Path(__file__).resolve().parents[1] / "fixtures" / "runtime" / "recordings.json"
_PROXY = "http://203.0.113.10:8080"
_STUB_CHAT = {
    "activity_type": "knowledge_work",
    "description": "stub",
    "confidence": 0.5,
    "claims": [],
    "project_candidates": [],
    "people_candidates": [],
}


class _MemResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._raw = json.dumps(payload).encode()

    def read(self) -> bytes:
        return self._raw

    def __enter__(self) -> _MemResponse:
        return self

    def __exit__(self, *_args: object) -> None:
        return None


def _install_transport(
    monkeypatch: pytest.MonkeyPatch, routes: dict[str, dict[str, Any]]
) -> list[Request]:
    """Route HTTP in-process. No real socket — safe when netns ``lo`` is DOWN."""
    opened: list[Request] = []
    opener = direct_opener()

    def fake_open(req: Request, timeout: float | None = None) -> _MemResponse:
        del timeout
        opened.append(req)
        url = req.full_url
        host = urlparse(url).hostname or ""
        assert host in {"127.0.0.1", "localhost", "::1"}, url
        for suffix, payload in routes.items():
            if url.endswith(suffix):
                return _MemResponse(payload)
        raise AssertionError(f"unexpected URL {url}")

    monkeypatch.setattr(opener, "open", fake_open)
    monkeypatch.setattr(http_mod, "direct_opener", lambda: opener)
    return opened


def _ollama(host: str = "http://127.0.0.1:11434") -> OllamaRuntime:
    return OllamaRuntime(IntelligenceConfig(runtime="ollama", ollama_host=host))


def test_fake_runtime_replays_recorded_fixture() -> None:
    runtime = FakeRuntime(recordings=RECORDINGS)
    runtime.probe()
    parsed = UnderstandingOutput.model_validate(
        runtime.complete(
            [{"role": "user", "content": "Please send revised pricing by Friday."}],
            UNDERSTANDING_JSON_SCHEMA,
        )
    )
    assert parsed.claims[0].assertion.value == "unconfirmed"
    assert parsed.claims[0].kind.value == "commitment"
    assert runtime.embed(["hello"]) == [[0.1, 0.2, 0.3, 0.4]]
    assert runtime.embed(["other"])[0][1] == float(len("other"))


def test_build_runtime_respects_config_and_env(
    data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (data_dir / "config.toml").write_text(
        f'data_dir = "{data_dir}"\n[intelligence]\nruntime = "ollama"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("WATSON_INTELLIGENCE_RUNTIME", "fake")
    config = load_config()
    assert config.intelligence.llm_model == "llama3.2:3b"
    assert config.intelligence.vlm_enabled is False
    bundle = build_runtime(config)
    assert bundle.name == "fake"
    with pytest.raises(RuntimeUnavailable, match="8 GB"):
        bundle.vision.describe(b"", {})


def test_ollama_adapter_uses_format_schema(monkeypatch: pytest.MonkeyPatch) -> None:
    opened = _install_transport(
        monkeypatch,
        {
            "/api/tags": {"models": [{"name": "llama3.2:3b"}, {"name": "nomic-embed-text"}]},
            "/api/chat": {"message": {"content": json.dumps(_STUB_CHAT)}},
            "/api/embed": {"embeddings": [[0.0, 1.0]]},
        },
    )
    runtime = _ollama()
    runtime.probe()
    assert (
        runtime.complete([{"role": "user", "content": "x"}], UNDERSTANDING_JSON_SCHEMA)[
            "activity_type"
        ]
        == "knowledge_work"
    )
    assert runtime.embed(["a", "b"]) == [[0.0, 1.0]]
    chat = next(req for req in opened if req.full_url.endswith("/api/chat"))
    body = json.loads(chat.data or b"{}")
    assert body["model"] == "llama3.2:3b" and body["format"] == UNDERSTANDING_JSON_SCHEMA
    embed = next(req for req in opened if req.full_url.endswith("/api/embed"))
    assert json.loads(embed.data or b"{}")["model"] == "nomic-embed-text"
    assert json.loads(embed.data or b"{}")["input"] == ["a", "b"]


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("127.0.0.1:11434", "http://127.0.0.1:11434"),
        ("http://127.0.0.1:1", "http://127.0.0.1:1"),
        ("http://[::1]:11434", "http://[::1]:11434"),
    ],
)
def test_canonical_ollama_url_accepts_loopback(raw: str, expected: str) -> None:
    assert canonical_ollama_url(raw) == expected


def test_ollama_refuses_remote_and_names_missing_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(RuntimeUnavailable, match="loopback"):
        _ollama("http://example.com:11434")
    with pytest.raises(RuntimeUnavailable, match="unsupported scheme"):
        canonical_ollama_url("ftp://127.0.0.1:11434")
    _install_transport(monkeypatch, {"/api/tags": {"models": [{"name": "nomic-embed-text"}]}})
    with pytest.raises(RuntimeUnavailable, match="llama3.2:3b") as raised:
        _ollama().probe()
    assert "ollama pull llama3.2:3b" in (raised.value.fetch_command or "")


def test_request_json_ignores_proxy_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ("http_proxy", "HTTP_PROXY", "https_proxy", "HTTPS_PROXY", "ALL_PROXY", "all_proxy"):
        monkeypatch.setenv(key, _PROXY)
    detected = getproxies_environment()
    assert any("203.0.113.10" in str(value) for value in detected.values())
    default_proxies = [h.proxies for h in build_opener().handlers if isinstance(h, ProxyHandler)]
    assert any("203.0.113.10" in str(value) for row in default_proxies for value in row.values())
    ours = direct_opener()
    # Empty ProxyHandler({}) replaces the default but is not retained (no *_open).
    assert not any(isinstance(handler, ProxyHandler) for handler in ours.handlers)
    assert any(isinstance(handler, NoRedirectHandler) for handler in ours.handlers)

    opened = _install_transport(monkeypatch, {"/api/tags": {"models": []}})
    assert request_json("http://127.0.0.1:11434", "GET", "/api/tags") == {"models": []}
    assert opened[0].full_url == "http://127.0.0.1:11434/api/tags"
    assert "203.0.113.10" not in opened[0].full_url
    with pytest.raises(RuntimeUnavailable, match="loopback"):
        request_json("http://example.com:11434", "GET", "/api/tags")
    assert len(opened) == 1


def test_direct_opener_refuses_redirects_without_a_socket() -> None:
    opener = direct_opener()
    handler = next(item for item in opener.handlers if isinstance(item, NoRedirectHandler))
    request = Request("http://127.0.0.1:11434/api/tags")
    with pytest.raises(RuntimeUnavailable, match="redirect"):
        handler.redirect_request(
            request,
            None,
            302,
            "Found",
            {"Location": "http://example.com/steal"},
            "http://example.com/steal",
        )
    with pytest.raises(RuntimeUnavailable, match="redirect"):
        handler.redirect_request(
            request,
            None,
            301,
            "Moved",
            {"Location": "http://127.0.0.1/other"},
            "http://127.0.0.1/other",
        )


def test_missing_runtime_holds_understand_jobs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "watson.db"
    upgrade_head(path, TEST_KEY)
    conn = connect(path, TEST_KEY, load_vec=False)
    try:
        job = enqueue(conn, kind=UNDERSTAND_JOB_KIND, payload={}, idempotency_key="understand:s1")
        extract = enqueue(conn, kind="extract", payload={}, idempotency_key="extract:f1")

        def boom(*_a: object, **_k: object) -> dict[str, Any]:
            raise RuntimeUnavailable("Ollama is not reachable at http://127.0.0.1:1")

        monkeypatch.setattr(http_mod, "request_json", boom)
        held = hold_if_unavailable(
            conn, IntelligenceConfig(runtime="ollama", ollama_host="http://127.0.0.1:1")
        )
        assert held.held and held.reason and "Ollama is not reachable" in held.reason
        queued = get_job(conn, job.id)
        assert queued.state is JobState.PENDING and queued.last_error == held.reason
        assert queued.attempts == 0
        assert get_job(conn, extract.id).last_error is None
        ok = hold_if_unavailable(conn, IntelligenceConfig(runtime="fake"))
        assert ok.held is False and ok.jobs_updated == 0
    finally:
        conn.close()
