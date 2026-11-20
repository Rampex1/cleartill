"""Stripe-style form encoding: nested dicts as a[b], lists as a[0]."""


def _scalar(value) -> str:
    if value is True:
        return "true"
    if value is False:
        return "false"
    return str(value)


def encode(params: dict, prefix: str = "") -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for key, value in params.items():
        name = f"{prefix}[{key}]" if prefix else str(key)
        if value is None:
            continue
        if isinstance(value, dict):
            pairs += encode(value, name)
        elif isinstance(value, (list, tuple)):
            pairs += encode({str(i): item for i, item in enumerate(value)}, name)
        else:
            pairs.append((name, _scalar(value)))
    return pairs
