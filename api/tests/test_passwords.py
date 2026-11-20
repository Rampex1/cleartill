from app.passwords import hash_password, verify_password


def test_hash_is_salted_scrypt():
    a, b = hash_password("correct horse"), hash_password("correct horse")
    assert a != b
    assert a.startswith("scrypt$") and b.startswith("scrypt$")


def test_verify_right_password():
    assert verify_password("correct horse", hash_password("correct horse")) is True


def test_verify_wrong_password():
    assert verify_password("wrong horse", hash_password("correct horse")) is False


def test_verify_empty_password():
    assert verify_password("", hash_password("correct horse")) is False


def test_verify_tampered_hash():
    stored = hash_password("correct horse")
    head, _, last = stored.rpartition("$")
    flipped = ("0" if last[0] != "0" else "1") + last[1:]
    assert verify_password("correct horse", f"{head}${flipped}") is False
