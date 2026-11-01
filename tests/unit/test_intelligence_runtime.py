"""Fake + Ollama runtimes, config selection, and §7.4 hold-on-missing."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

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
from watson.intelligence.runtime.http import canonical_ollama_url
from watson.intelligence.schemas import UNDERSTANDING_JSON_SCHEMA, UnderstandingOutput
from watson.memory.db import connect
from watson.memory.migrate import upgrade_head
from watson.pipeline.jobs import JobState, enqueue, get_job

RECORDINGS = Path(__file__).resolve().parents[1] / "fixtures" / "runtime" / "recordings.json"


class _Stub(ThreadingHTTPServer):
    def __init__(self, models: list[str]) -> None:
        super().__init__(("127.0.0.1", 0), _Handler)
        self.models = models
        self.chat: dict[str, Any] = {
            "activity_type": "knowledge_work",
            "description": "stub",
            "confidence": 0.5,
            "claims": [],
            "project_candidates": [],
            "people_candidates": [],
        }
        self.embeddings: list[list[float]] = [[0.0, 1.0]]
        self.requests: list[tuple[str, dict[str, Any]]] = []


class _Handler(BaseHTTPRequestHandler):
    server: _Stub

    def log_message(self, *_args: object) -> None:
        return

    def do_GET(self) -> None:
        if self.path.split("?", 1)[0] == "/api/tags":
            self._json(200, {"models": [{"name": n} for n in self.server.models]})
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self) -> None:
        raw = self.rfile.read(int(self.headers.get("Content-Length") or "0"))
        body = json.loads(raw.decode() or "{}") if raw else {}
        if not isinstance(body, dict):
            body = {}
        self.server.requests.append((self.path, body))
        if self.path == "/api/chat":
            self._json(200, {"message": {"content": json.dumps(self.server.chat)}})
        elif self.path == "/api/embed":
            self._json(200, {"embeddings": self.server.embeddings})
        else:
            self._json(404, {"error": "not found"})

    def _json(self, status: int, payload: dict[str, Any]) -> None:
        data = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def _serve(models: list[str]) -> _Stub:
    server = _Stub(models)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


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


def test_ollama_adapter_uses_format_schema_against_loopback_stub() -> None:
    server = _serve(["llama3.2:3b", "nomic-embed-text"])
    try:
        runtime = OllamaRuntime(
            IntelligenceConfig(
                runtime="ollama",
                ollama_host=f"http://127.0.0.1:{server.server_address[1]}",
            )
        )
        runtime.probe()
        assert runtime.complete([{"role": "user", "content": "x"}], UNDERSTANDING_JSON_SCHEMA)[
            "activity_type"
        ] == "knowledge_work"
        assert runtime.embed(["a", "b"]) == [[0.0, 1.0]]
    finally:
        server.shutdown()
        server.server_close()
    chat = next(body for path, body in server.requests if path == "/api/chat")
    assert chat["model"] == "llama3.2:3b" and chat["format"] == UNDERSTANDING_JSON_SCHEMA
    embed = next(body for path, body in server.requests if path == "/api/embed")
    assert embed["model"] == "nomic-embed-text" and embed["input"] == ["a", "b"]


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


def test_ollama_refuses_remote_and_names_missing_model() -> None:
    with pytest.raises(RuntimeUnavailable, match="loopback"):
        OllamaRuntime(IntelligenceConfig(runtime="ollama", ollama_host="http://example.com:11434"))
    with pytest.raises(RuntimeUnavailable, match="unsupported scheme"):
        canonical_ollama_url("ftp://127.0.0.1:11434")
    server = _serve(["nomic-embed-text"])
    try:
        runtime = OllamaRuntime(
            IntelligenceConfig(
                runtime="ollama",
                ollama_host=f"http://127.0.0.1:{server.server_address[1]}",
            )
        )
        with pytest.raises(RuntimeUnavailable, match="llama3.2:3b") as raised:
            runtime.probe()
        cmd = raised.value.fetch_command or ""
        assert "ollama pull llama3.2:3b" in cmd
    finally:
        server.shutdown()
        server.server_close()


def test_missing_runtime_holds_understand_jobs(tmp_path: Path) -> None:
    path = tmp_path / "watson.db"
    upgrade_head(path, TEST_KEY)
    conn = connect(path, TEST_KEY, load_vec=False)
    try:
        job = enqueue(conn, kind=UNDERSTAND_JOB_KIND, payload={}, idempotency_key="understand:s1")
        extract = enqueue(conn, kind="extract", payload={}, idempotency_key="extract:f1")
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
