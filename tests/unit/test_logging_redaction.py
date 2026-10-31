"""ContentField redaction and AST guard against raw screen-content logs."""

from __future__ import annotations

import ast
import io
import json
import logging
from pathlib import Path

from watson.privacy.logging import ContentField, configure_logging

SRC_ROOT = Path(__file__).resolve().parents[2] / "src"
FORBIDDEN = frozenset({"frame_text", "merged_text", "snippet"})
LOG_METHODS = frozenset(
    {"debug", "info", "warning", "warn", "error", "exception", "critical", "log"}
)


def test_content_field_stringifies_to_length_and_hash() -> None:
    field = ContentField("hello screen")
    text = str(field)
    assert "hello screen" not in text
    assert "len=12" in text
    assert "hash=" in text
    assert "hello screen" not in repr(field)


def test_json_formatter_redacts_at_debug() -> None:
    buf = io.StringIO()
    configure_logging(level=logging.DEBUG, stream=buf)
    log = logging.getLogger("watson.redact")
    planted = "VISIBLE_ON_SCREEN_xyz"
    log.debug("extracted %s", ContentField(planted))
    log.info("note", extra={"body": ContentField(planted)})
    output = buf.getvalue()
    assert planted not in output
    lines = [json.loads(line) for line in output.strip().splitlines()]
    assert any("len=" in line["msg"] and "hash=" in line["msg"] for line in lines)
    assert any("len=" in json.dumps(line.get("extra", {})) for line in lines)


def _is_logger_call(node: ast.Call) -> bool:
    func = node.func
    return isinstance(func, ast.Attribute) and func.attr in LOG_METHODS


def _forbidden_name(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name) and node.id in FORBIDDEN:
        return node.id
    if isinstance(node, ast.Attribute) and node.attr in FORBIDDEN:
        return node.attr
    if isinstance(node, ast.JoinedStr):
        for value in node.values:
            if isinstance(value, ast.FormattedValue):
                found = _forbidden_name(value.value)
                if found:
                    return found
    return None


def _walk_arg(node: ast.expr) -> str | None:
    direct = _forbidden_name(node)
    if direct:
        return direct
    if isinstance(node, ast.Dict):
        for value in node.values:
            if value is None:
                continue
            found = _walk_arg(value)
            if found:
                return found
    return None


def test_src_loggers_do_not_pass_raw_screen_fields() -> None:
    violations: list[str] = []
    for path in SRC_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not _is_logger_call(node):
                continue
            for arg in node.args:
                found = _walk_arg(arg)
                if found:
                    violations.append(f"{path}:{node.lineno} passes raw {found}")
            for keyword in node.keywords:
                if keyword.value is None:
                    continue
                found = _walk_arg(keyword.value)
                if found:
                    violations.append(f"{path}:{node.lineno} passes raw {found}")
    assert violations == []
