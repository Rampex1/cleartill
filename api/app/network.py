"""The simulated card network (design §5.5): outcomes come from the tokenized card."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Approved:
    pass


@dataclass(frozen=True)
class Declined:
    code: str
    decline_code: str | None = None


@dataclass(frozen=True)
class RequiresAuth:
    pass


_DECLINES = {
    "generic_decline": Declined("card_declined", "generic_decline"),
    "insufficient_funds": Declined("card_declined", "insufficient_funds"),
    "expired_card": Declined("expired_card"),
    "incorrect_cvc": Declined("incorrect_cvc"),
    "processing_error": Declined("processing_error"),
}


def authorize(
    sim_outcome: str, amount: int, three_ds_passed: bool = False
) -> Approved | Declined | RequiresAuth:
    if sim_outcome in _DECLINES:
        return _DECLINES[sim_outcome]
    if sim_outcome == "requires_3ds":
        return Approved() if three_ds_passed else RequiresAuth()
    if sim_outcome == "success":
        return Approved()
    raise ValueError(f"unknown simulated outcome {sim_outcome!r}")
