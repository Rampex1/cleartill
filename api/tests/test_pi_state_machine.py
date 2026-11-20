"""M9a: the PaymentIntent status guard."""

from types import SimpleNamespace

import pytest

from app import payment_intents
from app.errors import ApiError

STATUSES = [
    "requires_payment_method",
    "requires_confirmation",
    "requires_action",
    "processing",
    "requires_capture",
    "canceled",
    "succeeded",
]
ALLOWED = {
    "update": {"requires_payment_method", "requires_confirmation"},
    "cancel": {
        "requires_payment_method",
        "requires_confirmation",
        "requires_action",
        "requires_capture",
    },
}


@pytest.mark.parametrize("action", sorted(ALLOWED))
def test_allowed_from_table(action):
    assert payment_intents.ALLOWED_FROM[action] == ALLOWED[action]


@pytest.mark.parametrize("status", STATUSES)
@pytest.mark.parametrize("action", sorted(ALLOWED))
def test_require_status(action, status):
    pi = SimpleNamespace(status=status)
    if status in ALLOWED[action]:
        payment_intents.require_status(pi, action)
        return
    with pytest.raises(ApiError) as exc:
        payment_intents.require_status(pi, action)
    assert exc.value.status == 400
    assert exc.value.code == "payment_intent_unexpected_state"
