"""Dashboard auth: token, session cookie, Host/Origin, CSRF."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.replay_support import TEST_KEY, open_test_db, write_mini_fixture
from watson.cli import main
from watson.config import load_config
from watson.dashboard.app import CSP, create_app
from watson.dashboard.auth import SESSION_COOKIE
from watson.dashboard.tokens import (
    ENV_TOKEN,
    EnvTokenProvider,
    FileTokenProvider,
    InMemoryTokenProvider,
    KeychainTokenProvider,
    TokenError,
    get_token_provider,
    set_token_provider,
)
from watson.pipeline.replay import replay_fixture
from watson.pipeline.status import collect_status

INSTALL_TOKEN = "test-install-token-not-a-secret-for-ci"


def _client(token: str = INSTALL_TOKEN) -> TestClient:
    app = create_app(token=token, config=load_config())
    return TestClient(app, base_url="http://127.0.0.1", raise_server_exceptions=True)


def _authed(client: TestClient, token: str = INSTALL_TOKEN) -> None:
    response = client.get(f"/auth?token={token}", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/"


def test_unauthenticated_html_redirects() -> None:
    client = _client()
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/auth"


def test_unauthenticated_api_and_htmx_are_401() -> None:
    client = _client()
    api = client.get("/api/status")
    assert api.status_code == 401
    htmx = client.get("/status/partial", headers={"HX-Request": "true"})
    assert htmx.status_code == 401


def test_wrong_host_is_400() -> None:
    client = _client()
    response = client.get("/", headers={"Host": "evil.example"})
    assert response.status_code == 400
    assert "invalid host" in response.text


def test_foreign_origin_is_400() -> None:
    client = _client()
    _authed(client)
    response = client.get("/", headers={"Origin": "http://evil.example"})
    assert response.status_code == 400


def test_auth_sets_httponly_samesite_strict_cookie() -> None:
    client = _client()
    response = client.get(f"/auth?token={INSTALL_TOKEN}", follow_redirects=False)
    assert response.status_code == 303
    cookie = response.cookies.get(SESSION_COOKIE)
    assert cookie
    header = response.headers.get("set-cookie", "")
    assert "HttpOnly" in header
    assert "SameSite=strict" in header or "samesite=strict" in header.lower()
    assert "Secure" not in header


def test_wrong_token_is_401() -> None:
    client = _client()
    response = client.get("/auth?token=nope")
    assert response.status_code == 401


def test_authed_status_matches_watson_status(
    data_dir: Path, key_provider: object, tmp_path: Path
) -> None:
    fixture = write_mini_fixture(tmp_path, frames=1)
    conn = open_test_db(data_dir)
    try:
        replay_fixture(fixture, conn, instant=True, spool_dir=tmp_path / "spool")
        report = collect_status(conn)
        client = _client()
        _authed(client)
        page = client.get("/")
        assert page.status_code == 200
        body = page.text
        assert report.capture_state in body
        assert str(report.queue_depth) in body
        assert str(report.deletion_verified_count) in body
        assert "day_mini" in body
        payload = client.get("/api/status").json()
        assert payload["capture_state"] == report.capture_state
        assert payload["deletion_verified_count"] == report.deletion_verified_count
        assert payload["queue_depth"] == report.queue_depth
        assert "frame_text" not in payload
        assert "ocr" not in payload
    finally:
        conn.close()


def test_post_requires_csrf_and_origin(
    data_dir: Path, key_provider: object
) -> None:
    client = _client()
    _authed(client)
    missing_origin = client.post("/status/refresh")
    assert missing_origin.status_code == 400
    missing_csrf = client.post(
        "/status/refresh",
        headers={"Origin": "http://127.0.0.1"},
    )
    assert missing_csrf.status_code == 403
    page = client.get("/")
    assert 'name="csrf_token"' in page.text
    start = page.text.index('name="csrf_token" value="') + len('name="csrf_token" value="')
    end = page.text.index('"', start)
    csrf = page.text[start:end]
    assert csrf
    ok = client.post(
        "/status/refresh",
        headers={"Origin": "http://127.0.0.1", "X-CSRF-Token": csrf},
        follow_redirects=False,
    )
    assert ok.status_code == 303


def test_file_and_env_token_providers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    set_token_provider(None)
    monkeypatch.setattr("watson.dashboard.tokens.sys.platform", "linux")
    monkeypatch.delenv(ENV_TOKEN, raising=False)
    path = tmp_path / "session" / "auth_token"
    file_provider = FileTokenProvider(path)
    first = file_provider.get_token()
    assert path.is_file()
    assert path.stat().st_mode & 0o777 == 0o600
    assert file_provider.get_token() == first
    assert first not in repr(file_provider)

    path.chmod(0o644)
    with pytest.raises(TokenError, match="0600"):
        FileTokenProvider(path).get_token()
    path.chmod(0o600)

    monkeypatch.setenv(ENV_TOKEN, "env-token")
    env = get_token_provider(tmp_path / "session")
    assert isinstance(env, EnvTokenProvider)
    assert env.get_token() == "env-token"
    assert "env-token" not in repr(env)

    monkeypatch.setattr("watson.dashboard.tokens.sys.platform", "darwin")
    darwin = get_token_provider()
    assert isinstance(darwin, KeychainTokenProvider)
    set_token_provider(InMemoryTokenProvider(INSTALL_TOKEN))
    assert get_token_provider().get_token() == INSTALL_TOKEN
    set_token_provider(None)


def test_keychain_token_roundtrip(monkeypatch: pytest.MonkeyPatch) -> None:
    store: dict[tuple[str, str], str] = {}

    def get_password(service: str, account: str) -> str | None:
        return store.get((service, account))

    def set_password(service: str, account: str, password: str) -> None:
        store[(service, account)] = password

    monkeypatch.setattr("keyring.get_password", get_password)
    monkeypatch.setattr("keyring.set_password", set_password)
    provider = KeychainTokenProvider(service="ai.watson.test", account="dash")
    first = provider.get_token()
    assert provider.get_token() == first
    assert first == store[("ai.watson.test", "dash")]
    assert first not in repr(provider)


def test_cli_dashboard_refuses_non_loopback(
    data_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["dashboard", "--host", "0.0.0.0"]) == 2
    err = capsys.readouterr().err
    assert "127.0.0.1" in err
    assert "0.0.0.0" in err
    assert TEST_KEY.hex() not in err


def test_csp_header_present() -> None:
    client = _client()
    _authed(client)
    response = client.get("/")
    assert response.headers["content-security-policy"] == CSP
    assert "cdn" not in response.text.lower()
    assert "/static/htmx.min.js" in response.text
