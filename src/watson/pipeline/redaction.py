"""Redact secrets and PII from OCR text before any tier-2 write (plan §5.3).

Replacements are typed placeholders so later stages still see context.
This module is deterministic and has no network I/O.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

PLACEHOLDER_CARD = "[CARD]"
PLACEHOLDER_SECRET = "[SECRET]"
PLACEHOLDER_EMAIL = "[EMAIL]"
PLACEHOLDER_IBAN = "[IBAN]"
PLACEHOLDER_OTP = "[OTP]"

# Plan §5.3 names cards and generic secrets. Emails are not listed there;
# they are included because they are PII that otherwise lands in frame_text.
_PLACEHOLDERS = frozenset(
    {
        PLACEHOLDER_CARD,
        PLACEHOLDER_SECRET,
        PLACEHOLDER_EMAIL,
        PLACEHOLDER_IBAN,
        PLACEHOLDER_OTP,
    }
)

_EMAIL = re.compile(
    r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b",
)
_IBAN = re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b")
_CARD = re.compile(r"(?<!\w)(?:\d[ \-]?){13,19}(?!\d)")
_SSN = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
_NATIONAL_ID_COMPACT = re.compile(r"\b\d{6}[- ]?\d{4,6}\b")
_OTP_LABELED = re.compile(
    r"(?i)\b(?:verification code|one[-\s]?time(?:\s+pass(?:word|code))?|"
    r"otp|2fa|auth(?:entication)? code|login code)\b"
    r"(?:[^\n\d]{0,16})(\d{4,8})\b"
)
_PASSWORD_SAME_LINE = re.compile(
    r"(?i)\b(pass(?:word|phrase|wd)|pwd)\b([ \t]*[:=][ \t]*|[ \t]+)(\S+)"
)
_PASSWORD_LABEL_ONLY = re.compile(r"(?i)^\s*(?:pass(?:word|phrase|wd)|pwd)\s*:?\s*$")
_AWS_ACCESS = re.compile(r"\bAKIA[0-9A-Z]{16}\b")
_GITHUB_PAT = re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b")
_SLACK = re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b")
_STRIPE = re.compile(r"\b(?:sk|pk|rk)_(?:live|test)_[A-Za-z0-9]{16,}\b")
_OPENAI = re.compile(r"\bsk-[A-Za-z0-9]{20,}\b")
_JWT = re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b")
_PEM = re.compile(
    r"-----BEGIN (?:RSA |OPENSSH |EC |DSA )?PRIVATE KEY-----"
    r".*?"
    r"-----END (?:RSA |OPENSSH |EC |DSA )?PRIVATE KEY-----",
    re.DOTALL,
)
_BEARER = re.compile(r"(?i)\b(?:bearer|authorization)\s*[:=]?\s*(\S{12,})")
_API_ASSIGN = re.compile(
    r"(?i)\b(?:api[_-]?key|access[_-]?token|secret[_-]?key|auth[_-]?token)"
    r"\s*[:=]\s*([A-Za-z0-9_\-./+=]{12,})"
)
_HIGH_ENTROPY = re.compile(r"(?<![A-Za-z0-9])[A-Za-z0-9_\-/=+]{24,}(?![A-Za-z0-9])")

_MIN_ENTROPY_BITS_PER_CHAR = 3.2


@dataclass(frozen=True)
class RedactionHit:
    kind: str
    placeholder: str
    start: int
    end: int


def redact_ocr_text(text: str) -> str:
    """Return OCR text with §5.3 secrets/PII replaced by typed placeholders."""
    if not text:
        return text
    redacted = text
    redacted = _PEM.sub(PLACEHOLDER_SECRET, redacted)
    redacted = _JWT.sub(PLACEHOLDER_SECRET, redacted)
    redacted = _AWS_ACCESS.sub(PLACEHOLDER_SECRET, redacted)
    redacted = _GITHUB_PAT.sub(PLACEHOLDER_SECRET, redacted)
    redacted = _SLACK.sub(PLACEHOLDER_SECRET, redacted)
    redacted = _STRIPE.sub(PLACEHOLDER_SECRET, redacted)
    redacted = _OPENAI.sub(PLACEHOLDER_SECRET, redacted)
    redacted = _BEARER.sub(
        lambda match: match.group(0).replace(match.group(1), PLACEHOLDER_SECRET),
        redacted,
    )
    redacted = _API_ASSIGN.sub(
        lambda match: match.group(0).replace(match.group(1), PLACEHOLDER_SECRET),
        redacted,
    )
    redacted = _IBAN.sub(PLACEHOLDER_IBAN, redacted)
    redacted = _CARD.sub(_replace_card, redacted)
    redacted = _SSN.sub(PLACEHOLDER_SECRET, redacted)
    redacted = _NATIONAL_ID_COMPACT.sub(_replace_national_id, redacted)
    redacted = _OTP_LABELED.sub(
        lambda match: match.group(0).replace(match.group(1), PLACEHOLDER_OTP),
        redacted,
    )
    redacted = _PASSWORD_SAME_LINE.sub(_replace_password_same_line, redacted)
    redacted = _redact_password_next_line(redacted)
    redacted = _EMAIL.sub(PLACEHOLDER_EMAIL, redacted)
    redacted = _HIGH_ENTROPY.sub(_replace_high_entropy, redacted)
    return redacted


def luhn_valid(raw: str) -> bool:
    """True when ``raw`` is a 13–19 digit sequence that passes Luhn."""
    digits = [int(char) for char in raw if char.isdigit()]
    if len(digits) < 13 or len(digits) > 19:
        return False
    if digits[0] not in {2, 3, 4, 5, 6}:
        return False
    total = 0
    parity = len(digits) % 2
    for index, digit in enumerate(digits):
        value = digit * 2 if index % 2 == parity else digit
        if value > 9:
            value -= 9
        total += value
    return total % 10 == 0


def _replace_card(match: re.Match[str]) -> str:
    candidate = match.group(0)
    if not luhn_valid(candidate):
        return candidate
    return PLACEHOLDER_CARD


def _replace_national_id(match: re.Match[str]) -> str:
    digits = [char for char in match.group(0) if char.isdigit()]
    if len(digits) < 9:
        return match.group(0)
    return PLACEHOLDER_SECRET


def _replace_password_same_line(match: re.Match[str]) -> str:
    value = match.group(3)
    if value in _PLACEHOLDERS or value.startswith("["):
        return match.group(0)
    return f"{match.group(1)}{match.group(2)}{PLACEHOLDER_SECRET}"


def _redact_password_next_line(text: str) -> str:
    lines = text.split("\n")
    out: list[str] = []
    redact_next = False
    for line in lines:
        if redact_next:
            if _looks_like_password_value(line):
                out.append(re.sub(r"\S+", PLACEHOLDER_SECRET, line, count=1))
            else:
                out.append(line)
            redact_next = False
            continue
        out.append(line)
        if _PASSWORD_LABEL_ONLY.match(line):
            redact_next = True
    return "\n".join(out)


def _replace_high_entropy(match: re.Match[str]) -> str:
    token = match.group(0)
    if token in _PLACEHOLDERS or token.startswith("[") and token.endswith("]"):
        return token
    if not _looks_like_secret_token(token):
        return token
    return PLACEHOLDER_SECRET


def _looks_like_password_value(line: str) -> bool:
    stripped = line.strip()
    if not stripped or stripped in _PLACEHOLDERS or _PASSWORD_LABEL_ONLY.match(line):
        return False
    if len(stripped) > 64:
        return False
    # Form values are a single token (or mask glyphs). A sentence is not a password.
    if " " in stripped and not all(char in "•*·●.• " for char in stripped):
        return False
    return True


def _looks_like_secret_token(token: str) -> bool:
    if len(token) < 24:
        return False
    classes = 0
    if re.search(r"[a-z]", token):
        classes += 1
    if re.search(r"[A-Z]", token):
        classes += 1
    if re.search(r"\d", token):
        classes += 1
    if re.search(r"[_\-/=+]", token):
        classes += 1
    if classes < 2:
        return False
    return _shannon_entropy(token) >= _MIN_ENTROPY_BITS_PER_CHAR


def _shannon_entropy(token: str) -> float:
    counts: dict[str, int] = {}
    for char in token:
        counts[char] = counts.get(char, 0) + 1
    length = len(token)
    return -sum((n / length) * math.log2(n / length) for n in counts.values())
