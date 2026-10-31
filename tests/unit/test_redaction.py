"""Deterministic and property tests for the §5.3 OCR redaction stage."""

from __future__ import annotations

import random
import string

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
