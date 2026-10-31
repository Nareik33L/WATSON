"""Scan fixture text for real-looking secrets and real-world brand leakage."""

from __future__ import annotations

import re
from collections.abc import Iterable

_SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("private_key_block", re.compile(r"BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY")),
    ("aws_access_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("github_pat", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b")),
    ("slack_token", re.compile(r"\bxox[abprs]-")),
    ("stripe_key", re.compile(r"\b(?:sk|pk)_(?:live|test)_[A-Za-z0-9]{16,}\b")),
    ("openai_key", re.compile(r"\bsk-[A-Za-z0-9]{20,}\b")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b")),
    ("iban", re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{12,30}\b")),
    ("luhn_card", re.compile(r"\b(?:\d[ -]?){13,19}\b")),
    ("otp_near_label", re.compile(r"(?i)\b(?:verification code|one-time|otp)\b.{0,12}\b\d{6}\b")),
)

# Real consumer/cloud brands and mail hosts that must not appear in fixtures.
_BRAND_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(rf"\b{name}\b", re.IGNORECASE)
    for name in (
        "gmail",
        "google",
        "microsoft",
        "outlook",
        "office365",
        "slack",
        "zoom",
        "notion",
        "dropbox",
        "facebook",
        "instagram",
        "whatsapp",
        "linkedin",
        "twitter",
        "netflix",
        "amazon",
        "apple.com",
        "icloud",
        "github",
        "gitlab",
        "bitbucket",
        "chatgpt",
        "openai",
        "1password",
        "lastpass",
        "vscode",
        "vs code",
        "visual studio",
        "chrome",
        "chromium",
        "safari",
        "firefox",
        "excel",
        "googledocs",
        "google docs",
        "keynote",
        "facetime",
        "imessage",
    )
)

_LUHN_NAME = "luhn_card"


def find_secret_hits(text: str) -> list[str]:
    hits: list[str] = []
    for name, pattern in _SECRET_PATTERNS:
        for match in pattern.finditer(text):
            candidate = match.group(0)
            if name == _LUHN_NAME and not _looks_like_card(candidate):
                continue
            hits.append(f"{name}:{candidate}")
    for pattern in _BRAND_PATTERNS:
        found = pattern.search(text)
        if found:
            hits.append(f"brand:{found.group(0)}")
    return hits


def scan_texts(texts: Iterable[str]) -> list[str]:
    hits: list[str] = []
    for text in texts:
        hits.extend(find_secret_hits(text))
    return hits


def _looks_like_card(raw: str) -> bool:
    digits = [int(ch) for ch in raw if ch.isdigit()]
    if len(digits) < 13 or len(digits) > 19:
        return False
    if digits[0] not in {2, 3, 4, 5, 6}:
        return False
    return _luhn_ok(digits)


def _luhn_ok(digits: list[int]) -> bool:
    total = 0
    parity = len(digits) % 2
    for index, digit in enumerate(digits):
        value = digit
        if index % 2 == parity:
            value *= 2
            if value > 9:
                value -= 9
        total += value
    return total % 10 == 0
