"""Card numbers must never be persisted, logged, or echoed back."""

import logging
import re
from urllib.parse import urlencode

from sqlalchemy import text

from app import seed

FORM = {"Content-Type": "application/x-www-form-urlencoded"}
TEST_CARDS = [
    "4242424242424242",
    "4000000000000002",
    "4000000000009995",
    "4000000000000069",
    "4000000000000127",
    "4000000000000119",
    "4000002760003184",
    "5555555555554444",
    "378282246310005",
]
MALFORMED = ["4242424242424241", "4242abcd42424242", "4242424242424242424242424"]
EXPIRED = "4000000000000002"


def test_card_numbers_never_stored_logged_or_returned(client, db, caplog):
    caplog.set_level(logging.INFO)
    caplog.set_level(logging.INFO, logger="cleartill")
    _, _, pk = seed.seed(db, "a@example.com")
    h = {"Authorization": f"Bearer {pk}", **FORM}

    def post(number, exp_year="2030"):
        cvc = "1234" if number.startswith(("34", "37")) else "123"
        content = urlencode(
            {
                "type": "card",
                "card[number]": number,
                "card[exp_month]": "12",
                "card[exp_year]": exp_year,
                "card[cvc]": cvc,
            }
        )
        return client.post("/v1/payment_methods", content=content, headers=h)

    responses = [post(n) for n in TEST_CARDS + MALFORMED]
    responses.append(post(EXPIRED, exp_year="2000"))

    assert [r.status_code for r in responses] == [200] * len(TEST_CARDS) + [402] * 4

    inputs = TEST_CARDS + MALFORMED
    needles = set(inputs)
    for n in inputs:
        needles.update(re.findall(r"\d{12,}", n))

    tables = db.execute(
        text("SELECT table_name FROM information_schema.tables WHERE table_schema='public'")
    ).scalars()
    dumps = {}
    for t in tables:
        rows = db.execute(text(f'SELECT row_to_json(t)::text FROM "{t}" t')).scalars().all()
        dumps[t] = "\n".join(rows)
    assert "payment_methods" in dumps
    assert dumps["payment_methods"].count('"sim_outcome"') == len(TEST_CARDS)

    haystacks = {f"table {t}": d for t, d in dumps.items()}
    haystacks["logs"] = caplog.text
    for i, r in enumerate(responses):
        haystacks[f"response {i}"] = r.text

    for where, hay in haystacks.items():
        for needle in needles:
            assert needle not in hay, f"card number {needle} leaked into {where}"

    for r in responses:
        if r.status_code != 200:
            assert not re.search(r"\d{12,}", r.json()["error"]["message"]), r.text
