#!/usr/bin/env python3
"""Walk and delete one or more already-validated WATSON spool roots.

Invoked by cleanup-spool.sh after that script has refused /, $HOME,
ancestors, relative paths, and symlink escapes. This file does not take
a free-form cleanup policy of its own.

Scan first with os.scandir. Accept only:
  * date directories matching yyyy-MM-dd (re.fullmatch)
  * files matching [A-Za-z0-9_-]+.(png|json) (re.fullmatch)
Any other entry — including names with newlines or other control
characters — aborts with nothing removed.

Unlink and rmdir only by absolute path that stays inside the resolved
root. Never uses the shell, so a newline in a filename cannot become
another path.
"""

from __future__ import annotations

import os
import re
import sys

DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
FILE_RE = re.compile(r"[A-Za-z0-9_-]+\.(png|json)")


def _die(message: str, code: int = 2) -> None:
    sys.stderr.write(message + "\n")
    sys.exit(code)


def _has_control(name: str) -> bool:
    return any(ord(c) < 32 or c == "\x7f" for c in name)


def _show(path: str) -> str:
    if _has_control(path):
        return repr(path)
    return path


def _inside(path: str, root: str) -> str | None:
    """Return the absolute path if it stays under realpath(root), else None.

    Resolves only the parent so a basename with a newline is not split or
    followed as a symlink.
    """
    root_r = os.path.realpath(root)
    name = os.path.basename(path)
    if name in ("", ".", "..") or "/" in name or "\x00" in name:
        return None
    parent_r = os.path.realpath(os.path.dirname(os.path.abspath(path)))
    full = os.path.join(parent_r, name)
    if full == root_r or full.startswith(root_r + os.sep):
        return full
    return None


def _scan(root: str) -> tuple[list[str], list[str], list[str]]:
    unexpected: list[str] = []
    files: list[str] = []
    dirs: list[str] = []

    if os.path.islink(root):
        unexpected.append(root)
        return unexpected, files, dirs
    if not os.path.isdir(root):
        unexpected.append(root)
        return unexpected, files, dirs

    with os.scandir(root) as entries:
        for entry in entries:
            name = entry.name
            path = entry.path
            if (
                _has_control(name)
                or DATE_RE.fullmatch(name) is None
                or entry.is_symlink()
                or not entry.is_dir(follow_symlinks=False)
            ):
                unexpected.append(path)
                continue
            dirs.append(path)
            with os.scandir(path) as inner:
                for child in inner:
                    cname = child.name
                    cpath = child.path
                    if (
                        _has_control(cname)
                        or FILE_RE.fullmatch(cname) is None
                        or child.is_symlink()
                        or not child.is_file(follow_symlinks=False)
                    ):
                        unexpected.append(cpath)
                        continue
                    files.append(cpath)
    return unexpected, files, dirs


def _chmod_u_write(path: str) -> None:
    try:
        mode = os.stat(path, follow_symlinks=False).st_mode
        os.chmod(path, mode | 0o200)
    except OSError:
        pass


def _unlink_inside(path: str, root: str) -> None:
    full = _inside(path, root)
    if full is None:
        _die(f"REFUSE: path escapes root: {_show(path)}")
    _chmod_u_write(full)
    os.unlink(full)


def _rmdir_inside(path: str, root: str) -> None:
    full = _inside(path, root)
    if full is None:
        _die(f"REFUSE: path escapes root: {_show(path)}")
    _chmod_u_write(full)
    os.rmdir(full)


def _clean(root: str, files: list[str], dirs: list[str]) -> None:
    for path in files:
        _unlink_inside(path, root)
    for path in dirs:
        _rmdir_inside(path, root)
    if os.path.islink(root):
        _die(f"REFUSE: spool root is a symlink: {root}", 1)
    _chmod_u_write(root)
    os.rmdir(root)
    if os.path.exists(root):
        _die(f"FAIL: still exists: {root}")


def main(argv: list[str]) -> int:
    roots = argv[1:]
    if roots and roots[0] == "--":
        roots = roots[1:]
    if not roots:
        _die("usage: cleanup_spool.py -- ROOT [ROOT ...]", 2)

    plans: list[tuple[str, list[str] | None, list[str] | None]] = []
    unexpected_any: list[tuple[str, list[str]]] = []

    for raw in roots:
        if not os.path.isabs(raw):
            _die(f"REFUSE: relative path: {raw}", 1)
        if raw == "/":
            _die("REFUSE: /", 1)
        if os.path.islink(raw):
            _die(f"REFUSE: spool root is a symlink: {raw}", 1)
        if not os.path.lexists(raw):
            plans.append((raw, None, None))
            continue
        if not os.path.isdir(raw):
            _die(f"FAIL: not a directory: {raw}")
        unexpected, files, dirs = _scan(raw)
        if unexpected:
            unexpected_any.append((raw, unexpected))
            continue
        plans.append((raw, files, dirs))

    if unexpected_any:
        sys.stderr.write("FAIL: unexpected entries (nothing deleted):\n")
        for _root, entries in unexpected_any:
            for path in entries:
                sys.stderr.write(f"  {_show(path)}\n")
        return 2

    for root, files, dirs in plans:
        if files is None or dirs is None:
            print(f"removed: {root}")
            continue
        _clean(root, files, dirs)
        print(f"removed: {root}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
