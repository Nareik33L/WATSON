"""AST scan: logger calls must not receive raw screen-content fields.

Catches identifiers, attributes, subscripts, ``str()``, ``.format()``,
f-strings, ``%`` formatting, ``+`` concatenation, and tuple/positional args.
A ``ContentField(...)`` wrap is the allowed path and is not walked into.
"""

from __future__ import annotations

import ast
from collections.abc import Iterable
from pathlib import Path

FORBIDDEN = frozenset({"frame_text", "merged_text", "snippet"})
LOG_METHODS = frozenset(
    {"debug", "info", "warning", "warn", "error", "exception", "critical", "log"}
)


def is_logger_call(node: ast.Call) -> bool:
    func = node.func
    return isinstance(func, ast.Attribute) and func.attr in LOG_METHODS


def is_content_field_wrap(node: ast.AST) -> bool:
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if isinstance(func, ast.Name) and func.id == "ContentField":
        return True
    return isinstance(func, ast.Attribute) and func.attr == "ContentField"


def forbidden_in(node: ast.AST | None) -> str | None:
    """Return the forbidden field name if ``node`` carries raw screen content."""
    if node is None:
        return None
    if is_content_field_wrap(node):
        return None
    if isinstance(node, ast.Name) and node.id in FORBIDDEN:
        return node.id
    if isinstance(node, ast.Attribute) and node.attr in FORBIDDEN:
        return node.attr
    if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value in FORBIDDEN:
        # Bare string "frame_text" is a key/label, not content — only flag as a
        # subscript/dict key via the Subscript/Dict branches below.
        return None
    if isinstance(node, ast.JoinedStr):
        for value in node.values:
            if isinstance(value, ast.FormattedValue):
                found = forbidden_in(value.value)
                if found:
                    return found
        return None
    if isinstance(node, ast.Subscript):
        sl = node.slice
        if isinstance(sl, ast.Constant) and sl.value in FORBIDDEN:
            return str(sl.value)
        return forbidden_in(node.value) or forbidden_in(sl)
    if isinstance(node, ast.Call):
        found = forbidden_in(node.func)
        if found:
            return found
        for arg in node.args:
            found = forbidden_in(arg)
            if found:
                return found
        for keyword in node.keywords:
            found = forbidden_in(keyword.value)
            if found:
                return found
        return None
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Mod)):
        return forbidden_in(node.left) or forbidden_in(node.right)
    if isinstance(node, ast.Dict):
        for key, value in zip(node.keys, node.values, strict=False):
            if (
                isinstance(key, ast.Constant)
                and key.value in FORBIDDEN
                and not is_content_field_wrap(value)
            ):
                # extra={"snippet": snippet} — the value is what matters; the
                # key name alone is not content. Still walk the value.
                pass
            found = forbidden_in(value)
            if found:
                return found
        return None
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        for elt in node.elts:
            found = forbidden_in(elt)
            if found:
                return found
        return None
    if isinstance(node, ast.Starred):
        return forbidden_in(node.value)
    if isinstance(node, ast.IfExp):
        return forbidden_in(node.body) or forbidden_in(node.orelse) or forbidden_in(node.test)
    if isinstance(node, ast.BoolOp):
        for value in node.values:
            found = forbidden_in(value)
            if found:
                return found
        return None
    if isinstance(node, ast.UnaryOp):
        return forbidden_in(node.operand)
    if isinstance(node, ast.Compare):
        found = forbidden_in(node.left)
        if found:
            return found
        for comparator in node.comparators:
            found = forbidden_in(comparator)
            if found:
                return found
        return None
    return None


def scan_source(source: str, *, filename: str = "<mem>") -> list[str]:
    """Return human-readable violations for logger calls in ``source``."""
    tree = ast.parse(source, filename=filename)
    violations: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not is_logger_call(node):
            continue
        for arg in node.args:
            found = forbidden_in(arg)
            if found:
                violations.append(f"{filename}:{node.lineno} passes raw {found}")
        for keyword in node.keywords:
            found = forbidden_in(keyword.value)
            if found:
                violations.append(f"{filename}:{node.lineno} passes raw {found}")
    return violations


def scan_path(path: Path) -> list[str]:
    return scan_source(path.read_text(encoding="utf-8"), filename=str(path))


def scan_paths(paths: Iterable[Path]) -> list[str]:
    found: list[str] = []
    for path in paths:
        found.extend(scan_path(path))
    return found
