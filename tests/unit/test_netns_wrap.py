"""Netns wrapper keeps a non-root uid (sudo+setpriv or mapped userns)."""

from __future__ import annotations

import pytest

from watson.privacy.netns import wrap_in_netns


def test_netns_wrapper_does_not_map_caller_to_root() -> None:
    argv = wrap_in_netns(["true"])
    if argv is None:
        pytest.skip("network namespace unavailable")
    assert "-rn" not in argv
    joined = " ".join(argv)
    if argv[0] == "sudo":
        assert "setpriv" in argv
        assert any(part.startswith("--reuid=") for part in argv)
        assert "--reuid=0" not in argv
    else:
        assert "--map-user=" in joined
        assert "--map-root-user" not in argv
        assert "-r" not in argv
