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
        if isinstance(value, float):
            # Amounts are integer cents; "20.0" would only come back as a generic 400.
            raise TypeError(f"{name}: use an int (amounts are in cents), not a float")
        if isinstance(value, (dict, list, tuple)) and not value:
            # An empty value is sent, not dropped: `metadata={}` clears all metadata on the
            # server, and an empty list gets the server's validation error.
            pairs.append((name, ""))
        elif isinstance(value, dict):
            pairs += encode(value, name)
        elif isinstance(value, (list, tuple)):
            pairs += encode({str(i): item for i, item in enumerate(value)}, name)
        else:
            pairs.append((name, _scalar(value)))
    return pairs
