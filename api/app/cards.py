"""Card tokenization. The number and CVC live only in this call's memory: we keep brand,
last4, expiry, a keyed fingerprint and the simulated outcome. Error messages never contain
any part of the number."""

import re
from dataclasses import dataclass

from app import clock, config
from app.errors import ApiError

SIM_OUTCOMES = {
    "4242424242424242": "success",
    "4000000000000002": "generic_decline",
    "4000000000009995": "insufficient_funds",
    "4000000000000069": "expired_card",
    "4000000000000127": "incorrect_cvc",
    "4000000000000119": "processing_error",
    "4000002760003184": "requires_3ds",
}
MAX_YEARS_AHEAD = 50


@dataclass(frozen=True)
class Card:
    brand: str
    last4: str
    exp_month: int
    exp_year: int
    fingerprint: str
    sim_outcome: str


def _card_error(code: str, param: str, message: str) -> ApiError:
    return ApiError(402, "card_error", message, code=code, param=param)


def luhn_valid(number: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(number)):
        d = int(ch)
        if i % 2:
            d = d * 2 - 9 if d > 4 else d * 2
        total += d
    return total % 10 == 0


def brand_for(number: str) -> str:
    if number.startswith("4"):
        return "visa"
    if number[:2] in ("34", "37"):
        return "amex"
    if 51 <= int(number[:2]) <= 55 or 2221 <= int(number[:4]) <= 2720:
        return "mastercard"
    if number.startswith(("6011", "65")) or 644 <= int(number[:3]) <= 649:
        return "discover"
    return "unknown"


def sim_outcome_for(number: str) -> str:
    return SIM_OUTCOMES.get(number, "success")


def fingerprint(number: str) -> str:
    return config.keyed_hash("card-fingerprint", number)[:32]


def _parse_int(value: str) -> int | None:
    # [0-9], not \d: Unicode digits (fullwidth, Arabic-Indic) must not count as card digits.
    return int(value) if re.fullmatch(r"[0-9]{1,4}", value or "") else None


def tokenize(number: str, exp_month: str, exp_year: str, cvc: str) -> Card:
    digits = re.sub(r"[ -]", "", number or "")
    if not re.fullmatch(r"[0-9]{12,19}", digits):
        raise _card_error("invalid_number", "card[number]", "The card number is not valid.")
    if not luhn_valid(digits):
        raise _card_error("incorrect_number", "card[number]", "The card number is incorrect.")

    month = _parse_int(exp_month)
    if month is None or not 1 <= month <= 12:
        raise _card_error(
            "invalid_expiry_month", "card[exp_month]", "The expiration month is invalid."
        )
    year = _parse_int(exp_year)
    if year is not None and year < 100:
        year += 2000
    now = clock.now()
    if year is None or year > now.year + MAX_YEARS_AHEAD or (year, month) < (now.year, now.month):
        raise _card_error(
            "invalid_expiry_year", "card[exp_year]", "The expiration year is invalid."
        )

    brand = brand_for(digits)
    cvc_length = 4 if brand == "amex" else 3
    if not re.fullmatch(rf"[0-9]{{{cvc_length}}}", cvc or ""):
        raise _card_error("invalid_cvc", "card[cvc]", "The security code is invalid.")

    return Card(
        brand=brand,
        last4=digits[-4:],
        exp_month=month,
        exp_year=year,
        fingerprint=fingerprint(digits),
        sim_outcome=sim_outcome_for(digits),
    )


def payment_method_json(pm) -> dict:
    return {
        "id": pm.id,
        "object": "payment_method",
        "type": "card",
        "card": {
            "brand": pm.brand,
            "last4": pm.last4,
            "exp_month": pm.exp_month,
            "exp_year": pm.exp_year,
            "fingerprint": pm.fingerprint,
        },
        "created": int(pm.created.timestamp()),
        "livemode": False,
    }
