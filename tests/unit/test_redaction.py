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


@pytest.mark.parametrize(
    "text, secret",
    [
        ("Your code is 739104", "739104"),
        ("Code: 482913", "482913"),
        ("Your security code: 482913", "482913"),
        ("482913 is your verification code", "482913"),
        ("G-482913 is your Google verification code", "482913"),
        ("PIN: 4829", "4829"),
        ("Passcode: 7391", "7391"),
        ("one-time code 482913", "482913"),
        ("one time password 739104", "739104"),
        ("OTP 739104", "739104"),
        ("739104 is your code", "739104"),
        ("Your one-time passcode is 482913", "482913"),
        ("login code 482913", "482913"),
        ("2FA 739104", "739104"),
        ("authentication code 482913", "482913"),
        ("Your PIN is 4829", "4829"),
        ("enter the passcode 7391", "7391"),
        ("4829 is your PIN", "4829"),
        ("security PIN 7391", "7391"),
        ("auth code: 482913", "482913"),
    ],
)
def test_otp_phrasings_before_and_after_the_number(text: str, secret: str) -> None:
    redacted = redact_ocr_text(text)
    assert secret not in redacted
    assert PLACEHOLDER_OTP in redacted


@pytest.mark.parametrize(
    "text",
    [
        "Demo on 14 Nov section 3 amount 1200",
        "zip code 90210",
        "postal code 90210",
        "area code 21255",
        "error code 404",
        "status code 200",
        "promo code 48291",
        "coupon code 12345",
        "QR code 998877",
        "discount code 482913",
        "source code 482913",
        "PO 12345678 filed",
        "GB20 Q4 proposal",
        "Invoice 482913 due Friday",
        "room 1200 at 14:00",
        "The verification is done. Call 482913.",
        "Dial 482913 for the front desk",
    ],
)
def test_ordinary_numbers_are_not_treated_as_otps(text: str) -> None:
    assert redact_ocr_text(text) == text
    assert PLACEHOLDER_OTP not in redact_ocr_text(text)


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


def test_iban_shaped_without_checksum_and_partial_nino_are_kept() -> None:
    fake_iban = "GB00 WEST 1234 5698 7654 32"
    fragment = "Ref QQ 12 34 on the cover"
    assert redact_ocr_text(f"Pay to {fake_iban} today") == f"Pay to {fake_iban} today"
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


def test_otp_near_verification_label() -> None:
    text = "Verification code 847291 expires soon"
    redacted = redact_ocr_text(text)
    assert "847291" not in redacted
    assert PLACEHOLDER_OTP in redacted
    assert "Verification code" in redacted


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
