"""OS-level no-network check (plan §6 / §11.3).

Creates a loopback-only network namespace (sudo unshare --net + setpriv,
or unshare -rn). WATSON_REQUIRE_NETNS=1 makes a missing namespace fail
instead of skip so CI cannot silently drop this test.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from tests.replay_support import TEST_KEY, write_mini_fixture
from watson.privacy.netns import REQUIRE_NETNS_ENV, netns_available, run_in_netns


def test_replay_and_db_status_under_netns(tmp_path: Path) -> None:
    if not netns_available():
        if os.environ.get(REQUIRE_NETNS_ENV) == "1":
            pytest.fail(
                "WATSON_REQUIRE_NETNS=1 but a network namespace could not be created"
            )
        pytest.skip("network namespace unavailable")

    probe = run_in_netns([sys.executable, "-m", "watson.privacy.netns", "--probe"])
    assert probe.returncode == 0, probe.stderr
    assert "netns-ok" in probe.stdout

    fixture = write_mini_fixture(tmp_path, frames=1)
    data_dir = tmp_path / "data"
    env = os.environ.copy()
    env["WATSON_DATA_DIR"] = str(data_dir)
    env["WATSON_DB_KEY"] = TEST_KEY.hex()
    env.pop("WATSON_ALLOW_NETWORK", None)
    watson = Path(sys.executable).resolve().parent / "watson"
    replay_inner = (
        [str(watson), "replay", str(fixture), "--instant"]
        if watson.is_file()
        else [sys.executable, "-m", "watson.cli", "replay", str(fixture), "--instant"]
    )
    status_inner = (
        [str(watson), "db", "status"]
        if watson.is_file()
        else [sys.executable, "-m", "watson.cli", "db", "status"]
    )
    replay = run_in_netns(replay_inner, env=env)
    assert replay.returncode == 0, replay.stderr
    assert "deletion_verified: 1" in replay.stdout
    status = run_in_netns(status_inner, env=env)
    assert status.returncode == 0, status.stderr
    assert "db_status: ok" in status.stdout
