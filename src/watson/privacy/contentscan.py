"""Scan dashboard code, templates, and HTTP bodies for raw content fields.

Reuses the ContentField AST idea from :mod:`watson.privacy.logscan`: forbidden
identifiers (``frame_text``, ``merged_text``, ``snippet``) must not reach a
response unless wrapped in ``ContentField``. Templates and HTTP bodies are
token-scanned for the same names plus ``text_redacted`` and ``ocr``.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterable
from pathlib import Path

from watson.privacy.logscan import FORBIDDEN, is_content_field_wrap

RESPONSE_FORBIDDEN = frozenset({*FORBIDDEN, "text_redacted", "ocr"})
_TOKEN = re.compile(r"\b(" + "|".join(sorted(RESPONSE_FORBIDDEN)) + r")\b")


class _OutputVisitor(ast.NodeVisitor):
    def __init__(self, filename: str) -> None:
        self.filename = filename
        self.violations: list[str] = []

    def visit_Call(self, node: ast.Call) -> None:
        if is_content_field_wrap(node):
            return
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        if node.id in FORBIDDEN:
            self.violations.append(f"{self.filename}:{node.lineno} uses raw {node.id}")

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if node.attr in FORBIDDEN:
            self.violations.append(f"{self.filename}:{node.lineno} uses raw {node.attr}")
            return
        self.generic_visit(node)

    def visit_Constant(self, node: ast.Constant) -> None:
        if isinstance(node.value, str) and node.value in FORBIDDEN:
            self.violations.append(f"{self.filename}:{node.lineno} uses raw {node.value}")


def scan_output_source(source: str, *, filename: str = "<mem>") -> list[str]:
    """Flag raw content-field identifiers in Python that can reach a response."""
    tree = ast.parse(source, filename=filename)
    visitor = _OutputVisitor(filename)
    visitor.visit(tree)
    return visitor.violations


def scan_output_path(path: Path) -> list[str]:
    return scan_output_source(path.read_text(encoding="utf-8"), filename=str(path))


def scan_output_paths(paths: Iterable[Path]) -> list[str]:
    found: list[str] = []
    for path in paths:
        found.extend(scan_output_path(path))
    return found


def content_field_tokens(text: str) -> list[str]:
    """Return forbidden field names that appear as whole tokens in ``text``."""
    return sorted(set(_TOKEN.findall(text)))


def scan_text(text: str, *, filename: str = "<mem>") -> list[str]:
    return [f"{filename}: content field {name}" for name in content_field_tokens(text)]


# Re-export so callers can wrap a value they must mention without leaking it.
__all__ = [
    "RESPONSE_FORBIDDEN",
    "content_field_tokens",
    "scan_output_path",
    "scan_output_paths",
    "scan_output_source",
    "scan_text",
]
