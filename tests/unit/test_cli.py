"""CLI smoke tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from watson import cli
from watson.memory.keys import InMemoryKeyProvider, set_key_provider

TEST_KEY = bytes.fromhex("11" * 32)


def test_cli_imports_and_version(capsys: pytest.CaptureFixture[str]) -> None:
    assert callable(cli.main)

    with pytest.raises(SystemExit) as exited:
        cli.main(["--version"])

    assert exited.value.code == 0
    assert "watson" in capsys.readouterr().out


@pytest.mark.parametrize("command", ["run", "replay", "models"])
def test_stub_commands_not_implemented(command: str, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main([command]) == 1
    assert "not implemented" in capsys.readouterr().err


def test_doctor_and_db_commands(
    data_dir: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = InMemoryKeyProvider(TEST_KEY)
    set_key_provider(provider)
    monkeypatch.setattr("watson.memory.keys.get_key_provider", lambda: provider)

    assert cli.main(["doctor"]) == 0
    doctor = capsys.readouterr().out
    assert str(data_dir) in doctor
    assert "db_status: missing" in doctor
    assert TEST_KEY.hex() not in doctor
    assert "config_path:" in doctor
    assert "spool_dir:" in doctor

    assert cli.main(["db", "upgrade"]) == 0
    upgraded = capsys.readouterr().out
    assert "0001_m1_foundation" in upgraded
    assert TEST_KEY.hex() not in upgraded

    assert cli.main(["db", "status"]) == 0
    status = capsys.readouterr().out
    assert "db_status: ok" in status
    assert "db_journal_mode: wal" in status
    assert "db_fts5: available" in status
    assert "db_sqlite_vec:" in status
    assert "alembic_status: head" in status
    assert TEST_KEY.hex() not in status

    assert cli.main(["doctor"]) == 0
    after = capsys.readouterr().out
    assert "db_status: ok" in after
    assert "key_provider: in-memory" in after
    set_key_provider(None)


def test_db_without_subcommand_is_help(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["db"]) == 1
    assert "upgrade" in capsys.readouterr().err


def test_bad_config_exits_2(data_dir: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (data_dir / "config.toml").write_text(
        '[capture]\ninterval_seconds = "nope"\n',
        encoding="utf-8",
    )
    assert cli.main(["doctor"]) == 2
    err = capsys.readouterr().err
    assert "capture.interval_seconds" in err
