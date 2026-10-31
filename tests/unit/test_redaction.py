"""Deterministic and property tests for the §5.3 OCR redaction stage."""

from __future__ import annotations

import random
import string

import pytest

from watson.pipeline.redaction import (
    PLACEHOLDER_CARD,
    PLACEHOLDER_EMAIL,
    PLACEHOLDER_IBAN,
    PLACEHOLDER_OTP,
    PLACEHOLDER_SECRET,
    luhn_valid,
    redact_ocr_text,
)

CLEAN = (
    "Priya Natarajan wrote about the Acme Q4 proposal.\n"
    "Agreed to move the demo to 14 Nov. Send revised pricing by Friday."
)


def test_clean_work_text_is_unchanged() -> None:
    assert redact_ocr_text(CLEAN) == CLEAN
    assert redact_ocr_text("") == ""


def test_email_is_replaced() -> None:
    text = "From: Priya Natarajan <priya@acme.example> please reply"
    redacted = redact_ocr_text(text)
    assert "priya@acme.example" not in redacted
    assert PLACEHOLDER_EMAIL in redacted
    assert "Priya Natarajan" in redacted


def test_luhn_card_is_replaced_non_luhn_is_kept() -> None:
    card = "4111 1111 1111 1111"
    assert luhn_valid(card)
    text = f"Invoice card {card} amount 1200"
    redacted = redact_ocr_text(text)
    assert card not in redacted
    assert PLACEHOLDER_CARD in redacted
    assert "1200" in redacted

    not_card = "1234 5678 9012 3456"
    assert not luhn_valid(not_card)
    kept = redact_ocr_text(f"PO {not_card} filed")
    assert not_card in kept
    assert PLACEHOLDER_CARD not in kept


# Every OTP leak and keep case from QA rounds 1–3, plus near-variants.
# Columns: input, expected redacted output, reason (round / shape).
OTP_CORPUS: list[tuple[str, str, str]] = [
    # Round 1 leaks
    ("Your code is 739104", "Your code is [OTP]", "r1: code is N"),
    ("Code: 482913", "Code: [OTP]", "r1: Code: N"),
    ("Your security code: 482913", "Your security code: [OTP]", "r1: security code"),
    (
        "482913 is your verification code",
        "[OTP] is your verification code",
        "r1: N is your verification code",
    ),
    (
        "G-482913 is your Google verification code",
        "[OTP] is your Google verification code",
        "r1: G-prefix Google verification",
    ),
    ("Passcode: 7391", "Passcode: [OTP]", "r1 nit: Passcode"),
    ("PIN: 4829", "PIN: [OTP]", "r1: PIN label"),
    ("one-time code 482913", "one-time code [OTP]", "r1: one-time code"),
    ("one time password 739104", "one time password [OTP]", "r1: one time password"),
    ("OTP 739104", "OTP [OTP]", "r1: OTP keyword"),
    ("739104 is your code", "[OTP] is your code", "r1: N is your code"),
    (
        "Your one-time passcode is 482913",
        "Your one-time passcode is [OTP]",
        "r1: one-time passcode is N",
    ),
    ("login code 482913", "login code [OTP]", "r1: login code"),
    ("2FA 739104", "2FA [OTP]", "r1: 2FA"),
    ("authentication code 482913", "authentication code [OTP]", "r1: authentication code"),
    ("Your PIN is 4829", "Your PIN is [OTP]", "r1: Your PIN is N"),
    ("enter the passcode 7391", "enter the passcode [OTP]", "r1: passcode after filler"),
    ("4829 is your PIN", "[OTP] is your PIN", "r1: N is your PIN"),
    ("security PIN 7391", "security PIN [OTP]", "r1: security PIN"),
    ("auth code: 482913", "auth code: [OTP]", "r1: auth code"),
    (
        "Verification code 847291 expires soon",
        "Verification code [OTP] expires soon",
        "r1: verification code N expires",
    ),
    ("code:482913", "code:[OTP]", "r1 variant: no space after colon"),
    ("Code\n482913", "Code\n[OTP]", "r1 variant: label then next-line code"),
    # Round 2 grouped leaks
    ("Your WhatsApp code: 482-913", "Your WhatsApp code: [OTP]", "r2: WhatsApp 3+3 hyphen"),
    ("Your code is 482 913", "Your code is [OTP]", "r2: code is 3+3 space"),
    ("Passcode: 7391-0428", "Passcode: [OTP]", "r2: passcode 4+4 hyphen"),
    ("OTP 48 29 13", "OTP [OTP]", "r2: OTP 2+2+2 space"),
    ("PIN 48-29-13", "PIN [OTP]", "r2: PIN 2+2+2 hyphen"),
    ("one-time code 4829 1374", "one-time code [OTP]", "r2: one-time 4+4 space"),
    (
        "G-482-913 is your Google verification code",
        "[OTP] is your Google verification code",
        "r2: G-prefix 3+3",
    ),
    ("Code\n482 913", "Code\n[OTP]", "r2: next-line grouped 3+3"),
    # Round 3 leaks (tight window had closed these)
    ("Your code\n• 482913 •", "Your code\n• [OTP] •", "r3: label then bulleted code"),
    (
        "Your Apple ID Code is: 739104. Don't share it.",
        "Your Apple ID Code is: [OTP]. Don't share it.",
        "r3: Apple ID Code SMS",
    ),
    ("Your code is: 774411", "Your code is: [OTP]", "r3: code is: N"),
    (
        "Your verification code is: 482913",
        "Your verification code is: [OTP]",
        "r3: verification code is: N",
    ),
    (
        "551234 is your Instagram code",
        "[OTP] is your Instagram code",
        "r3: Instagram code after brand",
    ),
    (
        "551234 is your Uber confirmation code",
        "[OTP] is your Uber confirmation code",
        "r3: Uber confirmation code",
    ),
    (
        "551234 is your Facebook confirmation code",
        "[OTP] is your Facebook confirmation code",
        "r3: Facebook confirmation code",
    ),
    (
        "Use 662931 as Microsoft account security code",
        "Use [OTP] as Microsoft account security code",
        "r3: Use N as Microsoft security code",
    ),
    (
        "Your verification code is\n482913",
        "Your verification code is\n[OTP]",
        "r3: label ending in is, code next line",
    ),
    ("Your code is:\n482913", "Your code is:\n[OTP]", "r3: label ending in colon"),
    (
        "Verification code\n482913 (expires in 10 min)",
        "Verification code\n[OTP] (expires in 10 min)",
        "r3: label then code plus expiry",
    ),
    (
        "Your login code for Slack is 482913",
        "Your login code for Slack is [OTP]",
        "r3: login code for Slack is N",
    ),
    (
        "Your Apple ID Code is: 482 913",
        "Your Apple ID Code is: [OTP]",
        "r3: Apple ID Code grouped 3+3",
    ),
    # Near-variants
    (
        "Your Google verification code is 482913",
        "Your Google verification code is [OTP]",
        "nv: Google",
    ),
    ("WhatsApp code: 482-913", "WhatsApp code: [OTP]", "nv: WhatsApp short"),
    ("Use 662931 as your login code", "Use [OTP] as your login code", "nv: Use N as login code"),
    # Must-not-redact: ordinary numbers and known non-secret shapes
    (
        "Demo on 14 Nov section 3 amount 1200",
        "Demo on 14 Nov section 3 amount 1200",
        "keep: date/section/amount",
    ),
    ("zip code 90210", "zip code 90210", "keep: zip code"),
    ("postal code 90210", "postal code 90210", "keep: postal code"),
    ("area code 21255", "area code 21255", "keep: area code"),
    ("error code 404", "error code 404", "keep: error code"),
    ("status code 200", "status code 200", "keep: status code"),
    ("promo code 48291", "promo code 48291", "keep: promo code"),
    ("coupon code 12345", "coupon code 12345", "keep: coupon code"),
    ("QR code 998877", "QR code 998877", "keep: QR code"),
    ("discount code 482913", "discount code 482913", "keep: discount code"),
    ("source code 482913", "source code 482913", "keep: source code"),
    ("PO 12345678 filed", "PO 12345678 filed", "keep: PO number"),
    ("GB20 Q4 proposal", "GB20 Q4 proposal", "keep: quarter label"),
    ("Invoice 482913 due Friday", "Invoice 482913 due Friday", "keep: invoice number"),
    ("room 1200 at 14:00", "room 1200 at 14:00", "keep: room and time"),
    (
        "The verification is done. Call 482913.",
        "The verification is done. Call 482913.",
        "keep: keyword in previous sentence",
    ),
    ("Dial 482913 for the front desk", "Dial 482913 for the front desk", "keep: unlabeled dial"),
    ("PR 1234 code review", "PR 1234 code review", "r2: PR + code review"),
    ("code freeze on 2026-11-03", "code freeze on 2026-11-03", "r2: code freeze + ISO date"),
    ("£1200 after code changes", "£1200 after code changes", "r2: amount after code changes"),
    (
        "Login page loads in 1200 ms",
        "Login page loads in 1200 ms",
        "r2: login page timing, not login code",
    ),
    ("Pin the 1234 roadmap", "Pin the 1234 roadmap", "r2: pin-the verb"),
    (
        "Open PR 482913 for the code review",
        "Open PR 482913 for the code review",
        "keep: PR ticket + code review",
    ),
    (
        "code freeze on 14 Nov after 1200",
        "code freeze on 14 Nov after 1200",
        "keep: code freeze then ordinary number",
    ),
    ("issue 1234 assigned", "issue 1234 assigned", "keep: issue ticket"),
    ("ticket 482913 filed", "ticket 482913 filed", "keep: ticket id"),
    ("Code: 2026 roadmap", "Code: 2026 roadmap", "keep: year, date-like"),
    ("code 1234 lines", "code 1234 lines", "keep: code N lines"),
    ("code coverage is 1234", "code coverage is 1234", "keep: code coverage"),
    ("code style 1234", "code style 1234", "keep: code style"),
    # Round 5: year-like codes must still be redacted when labelled
    ("Your PIN: 1987", "Your PIN: [OTP]", "r5: year-like PIN after label"),
    ("Your code is 2024", "Your code is [OTP]", "r5: year-like code is N"),
    ("Verification code: 2019", "Verification code: [OTP]", "r5: year-like verification code"),
    ("Passcode 1999", "Passcode [OTP]", "r5: year-like passcode"),
    ("PIN 2000", "PIN [OTP]", "r5: year-like PIN"),
    ("Your Uber code: 2024", "Your Uber code: [OTP]", "r5: brand + year-like code"),
    (
        "2019 is your verification code",
        "[OTP] is your verification code",
        "r5: year-like N is your code",
    ),
    ("Your PIN\n1987", "Your PIN\n[OTP]", "r5: label line, year-like code next line"),
    (
        "Verification code:\n2019",
        "Verification code:\n[OTP]",
        "r5: colon label line, year-like next line",
    ),
    ("Your code is\n2024", "Your code is\n[OTP]", "r5: 'is' label line, year-like next line"),
    ("Your code is 2048-1301", "Your code is [OTP]", "r5: 4+4 grouped starting 20"),
    ("Your code: 20-19-87", "Your code: [OTP]", "r5: 2+2+2 grouped starting 20"),
    ("Passcode: 2025-07", "Passcode: [OTP]", "r5: hyphenated year-like passcode"),
    ("Your code: 2019/4821", "Your code: [OTP]", "r5: slash-split code starting 19/20"),
    # Round 5 nits (optional)
    ("Code #482913", "Code #[OTP]", "r5 nit: hash before code"),
    ("Line 1234 of the code", "Line 1234 of the code", "r5 fp: line number"),
    ("Invoice 48213 for code audit", "Invoice 48213 for code audit", "r5 fp: invoice number"),
    (
        "Meeting 14:30-15:30 PIN-protected room 4012",
        "Meeting 14:30-15:30 PIN-protected room 4012",
        "r5 fp: room number",
    ),
    ("See code at line 4821", "See code at line 4821", "r5 fp: line ref"),
]


@pytest.mark.parametrize(
    "text, expected, reason",
    OTP_CORPUS,
    ids=[row[2] for row in OTP_CORPUS],
)
def test_otp_corpus_from_all_qa_rounds(text: str, expected: str, reason: str) -> None:
    assert redact_ocr_text(text) == expected, reason


@pytest.mark.parametrize(
    "iban",
    [
        "GB82 WEST 1234 5698 7654 32",
        "DE89 3704 0044 0532 0130 00",
        "GB82WEST12345698765432",
        "gb82 west 1234 5698 7654 32",
        "NO93 8601 1117 947",
        "NL91 ABNA 0417 1643 00",
    ],
)
def test_spaced_and_compact_iban_is_replaced(iban: str) -> None:
    redacted = redact_ocr_text(f"Pay to {iban} today")
    assert iban not in redacted
    assert iban.replace(" ", "") not in redacted.replace(" ", "")
    assert PLACEHOLDER_IBAN in redacted
    assert "Pay to" in redacted


@pytest.mark.parametrize(
    "nino",
    ["QQ 12 34 56 C", "QQ123456C", "qq 12 34 56 c", "QQ-12-34-56-C"],
)
def test_uk_nino_with_and_without_spaces(nino: str) -> None:
    redacted = redact_ocr_text(f"NI number {nino} on file")
    assert nino not in redacted
    assert nino.replace(" ", "").replace("-", "") not in redacted.replace(" ", "")
    assert PLACEHOLDER_SECRET in redacted


@pytest.mark.parametrize(
    "iban",
    [
        "GB83 WEST 1234 5698 7654 32",
        "GB00 WEST 1234 5698 7654 32",
        "GB83WEST12345698765432",
        "gb83 west 1234 5698 7654 32",
    ],
)
def test_shape_valid_iban_with_failed_checksum_is_still_redacted(iban: str) -> None:
    redacted = redact_ocr_text(f"Pay to {iban} today")
    assert iban not in redacted
    assert iban.replace(" ", "") not in redacted.replace(" ", "")
    assert PLACEHOLDER_SECRET in redacted
    assert PLACEHOLDER_IBAN not in redacted
    assert "Pay to" in redacted


def test_partial_nino_is_kept() -> None:
    fragment = "Ref QQ 12 34 on the cover"
    assert redact_ocr_text(fragment) == fragment


def test_card_keeps_trailing_space() -> None:
    redacted = redact_ocr_text("card 4111111111111111 amount")
    assert "4111111111111111" not in redacted
    assert f"{PLACEHOLDER_CARD} amount" in redacted


def test_iban_api_key_jwt_and_password_are_secrets() -> None:
    iban = "GB82WEST12345698765432"
    key = "sk-abcdefghijklmnopqrstuvwxyz123456"
    jwt = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0In0.signaturexx"
    text = f"IBAN {iban}\nAuthorization: Bearer {key}\nPassword: hunter2-extra\nToken {jwt}"
    redacted = redact_ocr_text(text)
    assert iban not in redacted
    assert key not in redacted
    assert jwt not in redacted
    assert "hunter2-extra" not in redacted
    assert PLACEHOLDER_IBAN in redacted
    assert redacted.count(PLACEHOLDER_SECRET) >= 3


def test_password_next_line_value_only() -> None:
    form = "Sign in\nPassword\ncorrect-horse\nNext field"
    redacted = redact_ocr_text(form)
    assert "correct-horse" not in redacted
    assert PLACEHOLDER_SECRET in redacted
    assert "Next field" in redacted

    prose = "Password\nSend revised pricing to Priya by Friday"
    assert redact_ocr_text(prose) == prose


def test_national_id_and_known_key_prefixes() -> None:
    text = (
        "SSN 123-45-6789 "
        "AKIAIOSFODNN7EXAMPLE "
        "ghp_abcdefghijklmnopqrstuvwxyz1234 "
        "sk_live_abcdefghijklmnopqrstuv"
    )
    redacted = redact_ocr_text(text)
    assert "123-45-6789" not in redacted
    assert "AKIAIOSFODNN7EXAMPLE" not in redacted
    assert "ghp_abcdefghijklmnopqrstuvwxyz1234" not in redacted
    assert "sk_live_abcdefghijklmnopqrstuv" not in redacted
    assert redacted.count(PLACEHOLDER_SECRET) >= 4


def test_redaction_is_idempotent() -> None:
    text = "Card 4111111111111111 email alex@contoso.example Password: s3cretValue"
    once = redact_ocr_text(text)
    assert redact_ocr_text(once) == once
    assert "4111111111111111" not in once
    assert "alex@contoso.example" not in once
    assert "s3cretValue" not in once


def _luhn_number(rng: random.Random, length: int = 16) -> str:
    body = [rng.choice((2, 3, 4, 5, 6))] + [rng.randint(0, 9) for _ in range(length - 2)]
    # Choose the check digit so Luhn passes.
    for check in range(10):
        candidate = "".join(str(d) for d in [*body, check])
        if luhn_valid(candidate):
            return candidate
    raise AssertionError("could not build a Luhn number")


def _random_carrier(rng: random.Random) -> str:
    words = (
        "Acme Q4 proposal demo pricing Friday Priya Tom onboarding guide "
        "revised section published internal notes"
    ).split()
    return " ".join(rng.choice(words) for _ in range(rng.randint(4, 12)))


def _inject(carrier: str, secret: str, rng: random.Random) -> str:
    if rng.random() < 0.5:
        return f"{carrier} {secret} {carrier}"
    return f"{secret}\n{carrier}"


def test_property_injected_secrets_never_survive() -> None:
    """Seeded fuzz: any injected matching secret is gone; placeholder remains."""
    rng = random.Random(20261031)
    cases: list[tuple[str, str, str]] = []
    for _ in range(80):
        card = _luhn_number(rng)
        cases.append((card, PLACEHOLDER_CARD, f"card {card}"))
        email_user = "".join(rng.choice(string.ascii_lowercase) for _ in range(8))
        email = f"{email_user}@acme.example"
        cases.append((email, PLACEHOLDER_EMAIL, f"From {email}"))
        otp = f"{rng.randint(100000, 999999)}"
        cases.append((otp, PLACEHOLDER_OTP, f"Verification code {otp}"))
        password = "Pw" + "".join(rng.choice(string.ascii_letters) for _ in range(10))
        cases.append((password, PLACEHOLDER_SECRET, f"Password: {password}"))
        alphabet = string.ascii_lowercase + string.digits
        token = "sk-" + "".join(rng.choice(alphabet) for _ in range(28))
        cases.append((token, PLACEHOLDER_SECRET, f"api_key={token}"))

    for secret, placeholder, labelled in cases:
        carrier = _random_carrier(rng)
        text = _inject(carrier, labelled, rng)
        redacted = redact_ocr_text(text)
        assert secret not in redacted, text
        assert placeholder in redacted, text
        assert redact_ocr_text(redacted) == redacted
        # Carrier words still give the model context.
        assert any(word in redacted for word in carrier.split())
