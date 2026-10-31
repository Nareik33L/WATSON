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
# Compact IBAN, or the usual grouped display (4-character clumps).
_IBAN_COMPACT = re.compile(r"\b([A-Za-z]{2}\d{2}[A-Za-z0-9]{11,30})\b")
_IBAN_GROUPED = re.compile(
    r"\b([A-Za-z]{2}\d{2}(?:[ \t][A-Za-z0-9]{4}){2,7}(?:[ \t][A-Za-z0-9]{1,4})?)\b"
)
_CARD = re.compile(r"(?<!\w)(?:\d[ \-]?){13,19}(?!\d)")
_SSN = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
_NATIONAL_ID_COMPACT = re.compile(r"\b\d{6}[- ]?\d{4,6}\b")
# UK NINO shape: two letters, six digits, one letter (A–D), spaces or hyphens optional.
_NINO = re.compile(
    r"\b([A-Za-z]{2}[ \t-]*\d{2}[ \t-]*\d{2}[ \t-]*\d{2}[ \t-]*[A-Da-d])\b"
)
# Compact 4–8 digits, or common splits (3+3, 4+4, 2+2+2, 4+2). Optional G- prefix.
# ISO YYYY-MM-DD is skipped separately so 4+2 does not eat 2026-11-03.
_OTP_TOKEN = re.compile(
    r"(?i)(?<![A-Za-z0-9])(?:[A-Z]-)?(?:"
    r"\d{2}[ \t/-]\d{2}[ \t/-]\d{2}(?![0-9])"
    r"|\d{3}[ \t/-]\d{3}(?![0-9])"
    r"|\d{4}[ \t/-]\d{4}(?![0-9])"
    r"|\d{4}[ \t/-]\d{2}(?![0-9])"
    r"|\d{4,8}(?![0-9/-])"
    r")"
)
_ISO_DATE = re.compile(r"(?:19|20)\d{2}[-/](?:0?[1-9]|1[0-2])[-/](?:0?[1-9]|[12]\d|3[01])")
_OTP_ORDINARY_PREFIX = frozenset(
    {"line", "lines", "invoice", "invoices", "room", "po", "amount", "section"}
)
_OTP_WINDOW = 8
_OTP_HINTS = frozenset(
    {
        "code",
        "codes",
        "pin",
        "pins",
        "passcode",
        "passcodes",
        "otp",
        "2fa",
        "verification",
        "confirmation",
        "one-time",
        "onetime",
    }
)
_OTP_FILLERS = frozenset(
    {
        ":",
        "=",
        "is",
        "your",
        "as",
        "the",
        "a",
        "an",
        "for",
        "account",
        "id",
        "use",
        "#",
    }
)
_OTP_FALSE_PREFIX = frozenset(
    {
        "zip",
        "postal",
        "area",
        "country",
        "error",
        "status",
        "exit",
        "http",
        "source",
        "qr",
        "promo",
        "coupon",
        "discount",
        "dial",
        "dialling",
        "dialing",
    }
)
_OTP_FALSE_SUFFIX = frozenset(
    {
        "freeze",
        "review",
        "coverage",
        "style",
        "changes",
        "change",
        "base",
        "bases",
        "lines",
    }
)
_OTP_TICKET_PREFIX = frozenset({"pr", "issue", "ticket", "bug"})
_OTP_LEADING_PUNCT = re.compile(r"^[\s•*·●.\-–—()[\]]*$")
_OTP_LABEL_END = re.compile(r"(?i)(?::|=|is)\s*$")
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
    redacted = _IBAN_GROUPED.sub(_replace_iban, redacted)
    redacted = _IBAN_COMPACT.sub(_replace_iban, redacted)
    redacted = _CARD.sub(_replace_card, redacted)
    redacted = _SSN.sub(PLACEHOLDER_SECRET, redacted)
    redacted = _NINO.sub(PLACEHOLDER_SECRET, redacted)
    redacted = _NATIONAL_ID_COMPACT.sub(_replace_national_id, redacted)
    redacted = _redact_otps(redacted)
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
    raw = match.group(0)
    candidate = raw.rstrip(" -")
    trailing = raw[len(candidate) :]
    if not luhn_valid(candidate):
        return raw
    return PLACEHOLDER_CARD + trailing


def iban_compact(raw: str) -> str:
    return re.sub(r"[\s\t]", "", raw).upper()


def iban_shape_ok(compact: str) -> bool:
    """Country code + check digits + 11–30 alphanumerics, length 15–34."""
    return bool(re.fullmatch(r"[A-Z]{2}\d{2}[A-Z0-9]{11,30}", compact)) and (
        15 <= len(compact) <= 34
    )


def iban_checksum_ok(compact: str) -> bool:
    """ISO 13616: rearrange, A=10…Z=35, value mod 97 == 1."""
    if not iban_shape_ok(compact):
        return False
    rearranged = compact[4:] + compact[:4]
    numeric = "".join(
        str(ord(char) - 55) if char.isalpha() else char for char in rearranged
    )
    return int(numeric) % 97 == 1


def _replace_iban(match: re.Match[str]) -> str:
    # Checksum is a confidence signal, not a gate: an OCR slip must not keep
    # a shape-valid account number. Valid checksum → [IBAN]; else [SECRET].
    compact = iban_compact(match.group(1))
    if not iban_shape_ok(compact):
        return match.group(0)
    if iban_checksum_ok(compact):
        return PLACEHOLDER_IBAN
    return PLACEHOLDER_SECRET


def _redact_otps(text: str) -> str:
    """Replace 4–8 digit OTP runs near a keyword, or under a label line."""
    pieces: list[str] = []
    last = 0
    for match in _OTP_TOKEN.finditer(text):
        if (
            match.start() >= 2
            and text[match.start() - 1] == ":"
            and text[match.start() - 2].isdigit()
        ):
            continue
        if _ISO_DATE.match(text, match.start()):
            continue
        if not _otp_related(text, match.start(), match.end()):
            continue
        pieces.append(text[last : match.start()])
        pieces.append(PLACEHOLDER_OTP)
        last = match.end()
    pieces.append(text[last:])
    return "".join(pieces)


def _otp_related(text: str, start: int, end: int) -> bool:
    """A keyword/label wins; year-like keep only for unlabelled follow-on words."""
    raw = text[start:end]
    line_start = text.rfind("\n", 0, start) + 1
    newline_at = text.find("\n", end)
    line_end = len(text) if newline_at < 0 else newline_at
    line = text[line_start:line_end]
    local_start = start - line_start
    local_end = end - line_start
    if _ticket_number(line, local_start):
        return False
    before_text = re.split(r"[.!?]", line[:local_start])[-1]
    after_text = re.split(r"[.!?]", line[local_end:])[0]
    before = _otp_tokens(before_text)
    after = _otp_tokens(after_text)
    if before and before[-1] in _OTP_ORDINARY_PREFIX:
        return False
    if _code_has_nonsecret_partner(before, after):
        return False
    labelled = _window_has_hint(list(reversed(before))) or _window_has_hint(after)
    if not labelled and line_start > 0 and _OTP_LEADING_PUNCT.fullmatch(line[:local_start]):
        prev_end = line_start - 1
        prev_start = text.rfind("\n", 0, prev_end) + 1
        labelled = _previous_line_is_otp_label(text[prev_start:prev_end])
    if not labelled:
        return False
    # Year/date exemption: compact 19xx/20xx followed by a non-hint word (2026 roadmap).
    # Grouped forms and end-of-line / next-line labelled codes always redact.
    if (
        not _is_grouped_otp(raw)
        and _year_like(raw)
        and _nonfiller_follows(after)
        and not _window_has_hint(after)
    ):
        return False
    return True


def _otp_tokens(fragment: str) -> list[str]:
    return [
        token.lower()
        for token in re.findall(r"[A-Za-z]+(?:-[A-Za-z]+)+|[A-Za-z0-9]+|[:=#]", fragment)
    ]


def _is_grouped_otp(raw: str) -> bool:
    return any(char in raw for char in " \t/-")


def _year_like(raw: str) -> bool:
    digits = "".join(char for char in raw if char.isdigit())
    return len(digits) >= 4 and digits.startswith(("19", "20"))


def _nonfiller_follows(after: list[str]) -> bool:
    return any(word not in _OTP_FILLERS for word in after)


def _ticket_number(line: str, local_start: int) -> bool:
    before = _otp_tokens(line[:local_start])
    if not before:
        return False
    if before[-1] in _OTP_TICKET_PREFIX:
        return True
    return before[-1] == "#" and (
        len(before) == 1 or before[-2] in _OTP_TICKET_PREFIX
    )


def _code_has_nonsecret_partner(before: list[str], after: list[str]) -> bool:
    """``code 1234 lines`` / ``zip code 1234`` are not secrets."""
    left = before[-1] if before else ""
    right = after[0] if after else ""
    if left in {"code", "codes"} and right in _OTP_FALSE_SUFFIX:
        return True
    if right in {"code", "codes"} and left in _OTP_FALSE_SUFFIX:
        return True
    if left in {"code", "codes"} and len(before) >= 2 and before[-2] in _OTP_FALSE_PREFIX:
        return True
    return False


def _window_has_hint(toward: list[str]) -> bool:
    """Walk up to ``_OTP_WINDOW`` tokens away from the number looking for a keyword."""
    passed_suffix = False
    passed_false_prefix = False
    for index, word in enumerate(toward[:_OTP_WINDOW]):
        if word in _OTP_TICKET_PREFIX:
            return False
        if word in _OTP_FALSE_SUFFIX:
            passed_suffix = True
            continue
        if word in _OTP_FALSE_PREFIX:
            passed_false_prefix = True
            continue
        if word in _OTP_FILLERS:
            continue
        if word in {"password", "passwords"} and _one_time_nearby(toward[index + 1 :]):
            return True
        if word == "time" and _one_time_nearby(toward[index + 1 :]):
            return True
        if word not in _OTP_HINTS:
            continue
        nxt = toward[index + 1] if index + 1 < len(toward) else ""
        if word in {"code", "codes"} and (
            passed_false_prefix
            or passed_suffix
            or nxt in _OTP_FALSE_SUFFIX
            or nxt in _OTP_FALSE_PREFIX
        ):
            return False
        if word in {"pin", "pins"} and toward[:index] and toward[0] in {"the", "a", "this", "that"}:
            return False
        return True
    return False


def _one_time_nearby(further: list[str]) -> bool:
    head = further[:3]
    return any(word in {"one", "time", "one-time", "onetime"} for word in head)


def _previous_line_is_otp_label(prev: str) -> bool:
    stripped = prev.strip()
    if not stripped or _OTP_TOKEN.search(stripped):
        return False
    words = _otp_tokens(stripped)
    if not any(_label_hint(word, words, index) for index, word in enumerate(words)):
        return False
    if _OTP_LABEL_END.search(stripped):
        return True
    return bool(words) and words[-1] in _OTP_HINTS | {"password", "passwords"}


def _label_hint(word: str, words: list[str], index: int) -> bool:
    if word in _OTP_FALSE_PREFIX and index + 1 < len(words) and words[index + 1] in {
        "code",
        "codes",
    }:
        return False
    nxt = words[index + 1] if index + 1 < len(words) else ""
    if word in {"code", "codes"} and nxt in _OTP_FALSE_SUFFIX:
        return False
    if word in _OTP_HINTS:
        return True
    return word in {"password", "passwords"} and _one_time_nearby(list(reversed(words[:index])))


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
