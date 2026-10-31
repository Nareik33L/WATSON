"""Dashboard auth: launch code, session cookie, Host/Origin, CSRF."""

from __future__ import annotations

import base64
import io
import logging
import threading
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
import uvicorn
from fastapi.testclient import TestClient

from tests.replay_support import TEST_KEY, open_test_db, write_mini_fixture
from watson.cli import main
from watson.config import load_config
from watson.dashboard.app import CSP, create_app
from watson.dashboard.auth import (
    SESSION_COOKIE,
    SESSION_TTL_SECONDS,
    LaunchCodes,
    new_session,
    read_session,
    sign_session,
)
from watson.dashboard.serve import run_dashboard
from watson.dashboard.tokens import (
    ENV_TOKEN,
    MIN_TOKEN_LENGTH,
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
from watson.privacy.logging import configure_logging

INSTALL_TOKEN = "test-install-token-not-a-secret-for-ci"
TEST_PORT = 8000
ORIGIN = f"http://127.0.0.1:{TEST_PORT}"


class _Clock:
    def __init__(self, now: float) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def _client(
    token: str = INSTALL_TOKEN,
    *,
    port: int = TEST_PORT,
    session_epoch: int | None = None,
    session_ttl: int = SESSION_TTL_SECONDS,
    clock: _Clock | None = None,
    launch_codes: LaunchCodes | None = None,
) -> TestClient:
    app = create_app(
        token=token,
        config=load_config(),
        port=port,
        session_epoch=session_epoch,
        session_ttl=session_ttl,
        clock=clock,
        launch_codes=launch_codes,
    )
    return TestClient(app, base_url=f"http://127.0.0.1:{port}", raise_server_exceptions=True)


def _authed(client: TestClient) -> str:
    code = client.app.state.launch_codes.mint()
    response = client.get(f"/auth?code={code}", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/"
    return code


def _csrf(page_text: str) -> str:
    marker = 'name="csrf_token" value="'
    start = page_text.index(marker) + len(marker)
    end = page_text.index('"', start)
    csrf = page_text[start:end]
    assert csrf
    return csrf


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


@pytest.mark.parametrize(
    "host",
    ["127.0.0.1.evil.com", "localhost", "127.0.0.1:9999", "127.0.0.1"],
)
def test_host_rebinding_and_wrong_port_are_400(host: str) -> None:
    client = _client()
    response = client.get("/", headers={"Host": host})
    assert response.status_code == 400
    assert "invalid host" in response.text


def test_foreign_origin_is_400() -> None:
    client = _client()
    _authed(client)
    response = client.get("/", headers={"Origin": "http://evil.example"})
    assert response.status_code == 400


def test_origin_without_port_rejected_when_port_known() -> None:
    client = _client()
    _authed(client)
    response = client.get("/", headers={"Origin": "http://127.0.0.1"})
    assert response.status_code == 400


def test_auth_sets_httponly_samesite_strict_cookie() -> None:
    client = _client()
    _authed(client)
    cookie = client.cookies.get(SESSION_COOKIE)
    assert cookie
    response = client.get("/")
    assert response.status_code == 200
    # Re-mint to inspect Set-Cookie flags on a fresh exchange.
    fresh = _client()
    code = fresh.app.state.launch_codes.mint()
    issued = fresh.get(f"/auth?code={code}", follow_redirects=False)
    header = issued.headers.get("set-cookie", "")
    assert "HttpOnly" in header
    assert "SameSite=strict" in header or "samesite=strict" in header.lower()
    assert "Secure" not in header
    assert "Max-Age=" in header


def test_wrong_launch_code_is_401() -> None:
    client = _client()
    response = client.get("/auth?code=nope")
    assert response.status_code == 401


def test_install_secret_in_url_is_401() -> None:
    client = _client()
    response = client.get(f"/auth?token={INSTALL_TOKEN}")
    assert response.status_code == 401
    assert client.cookies.get(SESSION_COOKIE) is None


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


def test_post_requires_csrf_and_origin(data_dir: Path, key_provider: object) -> None:
    client = _client()
    _authed(client)
    missing_origin = client.post("/status/refresh")
    assert missing_origin.status_code == 400
    missing_csrf = client.post("/status/refresh", headers={"Origin": ORIGIN})
    assert missing_csrf.status_code == 403
    page = client.get("/")
    csrf = _csrf(page.text)
    ok = client.post(
        "/status/refresh",
        headers={"Origin": ORIGIN, "X-CSRF-Token": csrf},
        follow_redirects=False,
    )
    assert ok.status_code == 303


def test_form_field_csrf_is_accepted() -> None:
    client = _client()
    _authed(client)
    csrf = _csrf(client.get("/").text)
    ok = client.post(
        "/status/refresh",
        headers={"Origin": ORIGIN},
        data={"csrf_token": csrf},
        follow_redirects=False,
    )
    assert ok.status_code == 303


def test_cross_session_csrf_is_rejected() -> None:
    app = create_app(token=INSTALL_TOKEN, config=load_config(), port=TEST_PORT)
    client_a = TestClient(app, base_url=ORIGIN, raise_server_exceptions=True)
    client_b = TestClient(app, base_url=ORIGIN, raise_server_exceptions=True)
    _authed(client_a)
    _authed(client_b)
    csrf_a = _csrf(client_a.get("/").text)
    rejected = client_b.post(
        "/status/refresh",
        headers={"Origin": ORIGIN, "X-CSRF-Token": csrf_a},
        follow_redirects=False,
    )
    assert rejected.status_code == 403


def test_post_foreign_or_null_origin_is_400() -> None:
    client = _client()
    _authed(client)
    csrf = _csrf(client.get("/").text)
    foreign = client.post(
        "/status/refresh",
        headers={"Origin": "http://evil.example", "X-CSRF-Token": csrf},
    )
    assert foreign.status_code == 400
    null_origin = client.post(
        "/status/refresh",
        headers={"Origin": "null", "X-CSRF-Token": csrf},
    )
    assert null_origin.status_code == 400


def test_forged_and_tampered_cookie_are_rejected() -> None:
    client = _client()
    _authed(client)
    raw = client.cookies.get(SESSION_COOKIE)
    assert raw
    decoded = base64.urlsafe_b64decode(raw.encode("ascii"))
    tampered = base64.urlsafe_b64encode(decoded[:-1] + bytes([decoded[-1] ^ 0x01])).decode("ascii")
    client.cookies.set(SESSION_COOKIE, tampered)
    assert client.get("/api/status").status_code == 401
    client.cookies.set(SESSION_COOKIE, "forged-not-a-session")
    assert client.get("/api/status").status_code == 401


def test_session_cookie_roundtrip_never_rejects_valid_mac() -> None:
    secret = "x" * 32
    epoch = 7
    now = 1_700_000_000
    for _ in range(500):
        session = new_session()
        raw = sign_session(session, secret, epoch=epoch, issued_at=now)
        got = read_session(raw, secret, epoch=epoch, now=now, ttl=3600)
        assert got is not None
        assert got.sid == session.sid
        assert got.csrf == session.csrf


def test_mac_containing_dot_is_accepted() -> None:
    """Raw HMAC-SHA256 contains 0x2e ~12% of the time; do not rsplit on it."""
    secret = "x" * 32
    for _ in range(10_000):
        session = new_session()
        raw = sign_session(session, secret, epoch=1, issued_at=1)
        decoded = base64.urlsafe_b64decode(raw.encode("ascii"))
        mac = decoded[-32:]
        if b"." not in mac:
            continue
        got = read_session(raw, secret, epoch=1, now=1, ttl=3600)
        assert got is not None
        assert got.sid == session.sid
        return
    raise AssertionError("did not observe an HMAC containing 0x2e")


def test_launch_code_reuse_is_rejected() -> None:
    client = _client()
    code = _authed(client)
    again = client.get(f"/auth?code={code}")
    assert again.status_code == 401


def test_launch_code_expiry_is_rejected() -> None:
    clock = _Clock(0.0)
    codes = LaunchCodes(ttl_seconds=60, clock=clock)
    client = _client(launch_codes=codes)
    code = codes.mint()
    clock.now = 61
    response = client.get(f"/auth?code={code}")
    assert response.status_code == 401
    assert client.cookies.get(SESSION_COOKIE) is None


def test_expired_session_is_rejected() -> None:
    clock = _Clock(1_000.0)
    client = _client(clock=clock, session_ttl=60, session_epoch=1)
    _authed(client)
    assert client.get("/api/status").status_code == 200
    clock.now = 1_061.0
    assert client.get("/api/status").status_code == 401


def test_restart_rotates_sessions() -> None:
    first = _client(session_epoch=1)
    _authed(first)
    raw = first.cookies.get(SESSION_COOKIE)
    assert raw
    second = _client(session_epoch=2)
    second.cookies.set(SESSION_COOKIE, raw)
    assert second.get("/api/status").status_code == 401


def test_file_and_env_token_providers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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

    link = tmp_path / "session" / "linked"
    link.symlink_to(path)
    with pytest.raises(TokenError, match="symlink"):
        FileTokenProvider(link).get_token()

    env_value = "env-token-long-enough"
    monkeypatch.setenv(ENV_TOKEN, env_value)
    env = get_token_provider(tmp_path / "session")
    assert isinstance(env, EnvTokenProvider)
    assert env.get_token() == env_value
    assert env_value not in repr(env)

    monkeypatch.setenv(ENV_TOKEN, "short")
    with pytest.raises(TokenError, match="at least"):
        get_token_provider(tmp_path / "session")

    monkeypatch.setattr("watson.dashboard.tokens.sys.platform", "darwin")
    darwin = get_token_provider()
    assert isinstance(darwin, KeychainTokenProvider)
    set_token_provider(InMemoryTokenProvider(INSTALL_TOKEN))
    assert get_token_provider().get_token() == INSTALL_TOKEN
    set_token_provider(None)


def test_short_in_memory_token_rejected() -> None:
    with pytest.raises(TokenError, match="at least"):
        InMemoryTokenProvider("x" * (MIN_TOKEN_LENGTH - 1))


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
    assert INSTALL_TOKEN not in err


def test_dashboard_mints_launch_code_and_never_logs_secret(
    data_dir: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    set_token_provider(InMemoryTokenProvider(INSTALL_TOKEN))
    opened: list[str] = []
    monkeypatch.setattr(uvicorn, "run", lambda *args, **kwargs: None)
    import webbrowser

    monkeypatch.setattr(webbrowser, "open", lambda url: opened.append(url))
    buf = io.StringIO()
    configure_logging(level=logging.DEBUG, stream=buf)
    try:
        assert run_dashboard(open_browser=True) == 0
    finally:
        set_token_provider(None)
    captured = capsys.readouterr()
    combined = buf.getvalue() + captured.out + captured.err
    assert INSTALL_TOKEN not in combined
    assert "auth?token=" not in combined
    assert "/auth?code=" in captured.out
    assert opened
    assert INSTALL_TOKEN not in opened[0]
    query = parse_qs(urlparse(opened[0]).query)
    assert "token" not in query
    assert query["code"][0]
    assert query["code"][0] != INSTALL_TOKEN


def test_sqlcipher_error_on_status_is_empty_not_500(
    data_dir: Path, key_provider: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sqlcipher3 import dbapi2 as sqlcipher

    from watson.dashboard import app as dashboard_app

    conn = open_test_db(data_dir)
    conn.close()

    def boom(_conn: object) -> object:
        raise sqlcipher.DatabaseError("malformed")

    monkeypatch.setattr(dashboard_app, "collect_status", boom)
    client = _client()
    _authed(client)
    response = client.get("/api/status")
    assert response.status_code == 200
    payload = response.json()
    assert payload["frames_by_state"] == {}
    assert payload["jobs_by_state"] == {}
    assert payload["last_replay"] is None


def test_launch_code_consume_is_single_use_under_threads() -> None:
    codes = LaunchCodes(ttl_seconds=60)
    code = codes.mint()
    wins: list[bool] = []

    def attempt() -> None:
        if codes.consume(code):
            wins.append(True)

    workers = [threading.Thread(target=attempt) for _ in range(16)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join()
    assert wins == [True]
    assert codes.consume(code) is False


def test_csp_header_present() -> None:
    client = _client()
    _authed(client)
    response = client.get("/")
    assert response.headers["content-security-policy"] == CSP
    assert "cdn" not in response.text.lower()
    assert "/static/htmx.min.js" in response.text
    assert "/static/dashboard.js" in response.text
