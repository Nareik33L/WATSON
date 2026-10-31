"""CLI smoke tests."""

from __future__ import annotations

import pytest

from watson import cli


def test_cli_imports_and_version(capsys: pytest.CaptureFixture[str]) -> None:
    assert callable(cli.main)

    with pytest.raises(SystemExit) as exited:
        cli.main(["--version"])

    assert exited.value.code == 0
    assert "watson" in capsys.readouterr().out


@pytest.mark.parametrize("command", ["run", "replay", "models", "db", "doctor"])
def test_stub_commands_not_implemented(
    command: str, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main([command]) == 1
    assert "not implemented" in capsys.readouterr().err
