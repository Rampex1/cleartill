import pytest

from app import cards, network

CARDS = {
    "4242424242424242": "success",
    "4000000000000002": "generic_decline",
    "4000000000009995": "insufficient_funds",
    "4000000000000069": "expired_card",
    "4000000000000127": "incorrect_cvc",
    "4000000000000119": "processing_error",
    "4000002760003184": "requires_3ds",
}


@pytest.mark.parametrize(
    "outcome,expected",
    [
        ("success", network.Approved()),
        ("generic_decline", network.Declined("card_declined", "generic_decline")),
        ("insufficient_funds", network.Declined("card_declined", "insufficient_funds")),
        ("expired_card", network.Declined("expired_card", None)),
        ("incorrect_cvc", network.Declined("incorrect_cvc", None)),
        ("processing_error", network.Declined("processing_error", None)),
        ("requires_3ds", network.RequiresAuth()),
    ],
)
def test_authorize_outcome(outcome, expected):
    assert network.authorize(outcome, 1000) == expected


def test_authorize_3ds_without_auth_requires_auth():
    assert network.authorize("requires_3ds", 1000, three_ds_passed=False) == network.RequiresAuth()


def test_authorize_3ds_after_auth_is_approved():
    assert network.authorize("requires_3ds", 1000, three_ds_passed=True) == network.Approved()


@pytest.mark.parametrize("number", [*CARDS, "5555555555554444", "378282246310005"])
def test_luhn_valid_for_test_cards(number):
    assert cards.luhn_valid(number) is True


def test_luhn_invalid_for_bad_check_digit():
    assert cards.luhn_valid("4242424242424241") is False


@pytest.mark.parametrize("number,outcome", CARDS.items())
def test_sim_outcome_for_test_card(number, outcome):
    assert cards.sim_outcome_for(number) == outcome


@pytest.mark.parametrize("number", ["5555555555554444", "378282246310005", "4111111111111111"])
def test_sim_outcome_for_other_valid_card_is_success(number):
    assert cards.sim_outcome_for(number) == "success"
