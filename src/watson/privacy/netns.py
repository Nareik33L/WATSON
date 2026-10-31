"""Enter a network namespace that has only loopback (plan §6 / §11.3).

Ubuntu 24.04 CI blocks ``unshare -rn`` (AppArmor restricts unprivileged
user namespaces, so writing ``/proc/self/uid_map`` fails). The reliable
path is ``sudo unshare --net`` (no user ns) then ``setpriv`` back to the
invoking uid/gid. ``unshare -rn`` remains a fallback when userns works.
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
from collections.abc import Mapping, Sequence

REQUIRE_NETNS_ENV = "WATSON_REQUIRE_NETNS"
_PRESERVE_ENV = (
    "PATH",
    "VIRTUAL_ENV",
    "PYTHONPATH",
    "PYTHONNOUSERSITE",
    "HOME",
    "WATSON_DATA_DIR",
    "WATSON_DB_KEY",
    "WATSON_CONFIG_PATH",
    "WATSON_DB_PATH",
    "WATSON_SPOOL_DIR",
    "WATSON_LOGS_DIR",
    "WATSON_MODELS_DIR",
    "WATSON_SESSION_DIR",
    "WATSON_ALLOW_NETWORK",
    "WATSON_REQUIRE_NETNS",
    "WATSON_CAPTURE_INTERVAL",
    "WATSON_MEETING_FIXTURES_DIR",
    "WATSON_TRANSCRIBE_INPROCESS",
    "HF_HUB_OFFLINE",
    "TRANSFORMERS_OFFLINE",
    "WATSON_DASHBOARD_TOKEN",
)


class NetnsError(RuntimeError):
    """A network namespace could not be created or was not isolated."""


def interface_names() -> set[str]:
    """Netns-aware interface names. Do not use /sys/class/net (host sysfs)."""
    return {name for _index, name in socket.if_nameindex()}


def assert_namespace_isolated() -> None:
    """Raise if this process can see a non-loopback interface or reach off-box."""
    names = interface_names()
    if names != {"lo"}:
        raise NetnsError(f"expected only lo, got {sorted(names)}")
    try:
        socket.create_connection(("1.1.1.1", 80), timeout=0.4)
    except OSError:
        return
    raise NetnsError("non-loopback connect succeeded inside the network namespace")


def wrap_in_netns(inner: Sequence[str]) -> list[str] | None:
    """Return argv that runs ``inner`` in a loopback-only netns, or None."""
    argv = [str(part) for part in inner]
    uid = os.getuid()
    gid = os.getgid()

    if (
        shutil.which("sudo")
        and shutil.which("unshare")
        and shutil.which("setpriv")
        and _ok(["sudo", "-n", "unshare", "--net", "--", "true"])
    ):
        preserve = [f"--preserve-env={name}" for name in _PRESERVE_ENV]
        return [
            "sudo",
            "-n",
            *preserve,
            "unshare",
            "--net",
            "--",
            "setpriv",
            f"--reuid={uid}",
            f"--regid={gid}",
            "--init-groups",
            "--",
            *argv,
        ]

    if shutil.which("unshare") and _ok(["unshare", "-rn", "true"]):
        return ["unshare", "-rn", "--", *argv]
    return None


def netns_available() -> bool:
    return wrap_in_netns(["true"]) is not None


def run_in_netns(
    inner: Sequence[str],
    *,
    env: Mapping[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    wrapped = wrap_in_netns(inner)
    if wrapped is None:
        raise NetnsError("could not create a network namespace")
    return subprocess.run(
        wrapped,
        check=False,
        capture_output=True,
        text=True,
        env=dict(os.environ if env is None else env),
    )


def _ok(cmd: list[str]) -> bool:
    try:
        completed = subprocess.run(cmd, check=False, capture_output=True, text=True)
    except OSError:
        return False
    return completed.returncode == 0


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args[:1] == ["--probe"]:
        try:
            assert_namespace_isolated()
        except NetnsError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        print("netns-ok")
        return 0
    if args[:1] == ["--assert-isolated"]:
        args = [sys.executable, "-m", "watson.privacy.netns", "--probe"]
    elif args[:1] == ["--"]:
        args = args[1:]
    if not args:
        print("usage: python -m watson.privacy.netns -- <command>", file=sys.stderr)
        return 2
    wrapped = wrap_in_netns(args)
    if wrapped is None:
        print("could not create a network namespace with only loopback", file=sys.stderr)
        return 2
    completed = subprocess.run(wrapped, check=False)
    return int(completed.returncode)


if __name__ == "__main__":
    raise SystemExit(main())
