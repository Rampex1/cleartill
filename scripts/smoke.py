# /// script
# requires-python = ">=3.11"
# dependencies = ["httpx2>=2.16"]
# ///
"""End-to-end smoke test, and the README quickstart.

    docker compose up -d --build --wait
    uv run --with ./sdk scripts/smoke.py http://localhost:8000

Signs up a fresh merchant, then takes real money paths through the API, the SDK, hosted
checkout (with 3D Secure) and signed webhooks. Exits non-zero on the first failure."""

import argparse
import http.server
import sys
import threading
import time
import uuid
from datetime import UTC, datetime

import cleartill
import httpx2

DASHBOARD = {"X-Cleartill-Dashboard": "1"}
EXP_YEAR = str(datetime.now(UTC).year + 5)


def step(name: str) -> None:
    print(f"• {name}", flush=True)


def check(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(f"FAILED: {message}")


def card(number: str) -> dict:
    return {
        "card[number]": number,
        "card[exp_month]": "12",
        "card[exp_year]": EXP_YEAR,
        "card[cvc]": "123",
    }


class Receiver:
    """A tiny local webhook endpoint that records raw bodies and signature headers."""

    def __init__(self):
        self.received: list[tuple[bytes, str]] = []
        received = self.received

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                received.append((body, self.headers.get("Cleartill-Signature", "")))
                self.send_response(200)
                self.end_headers()

            def log_message(self, *args):
                pass

        self.server = http.server.ThreadingHTTPServer(("0.0.0.0", 0), Handler)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("base_url", nargs="?", default="http://localhost:8000")
    parser.add_argument(
        "--no-webhooks",
        action="store_true",
        help="skip the webhook check (when the API can't reach this machine)",
    )
    parser.add_argument(
        "--webhook-host",
        default="host.docker.internal",
        help="how the API's worker reaches this machine (default: %(default)s)",
    )
    args = parser.parse_args()
    base = args.base_url.rstrip("/")
    http = httpx2.Client(base_url=base, timeout=30)

    step(f"health check {base}")
    check(http.get("/healthz").json().get("status") == "ok", "API is not healthy")

    step("sign up a merchant")
    email = f"smoke-{uuid.uuid4().hex[:12]}@example.com"
    r = http.post(
        "/dashboard-api/signup",
        data={"email": email, "password": uuid.uuid4().hex},
        headers=DASHBOARD,
    )
    check(r.status_code == 200, f"signup failed: {r.status_code} {r.text}")
    keys = r.json()
    client = cleartill.Client(keys["secret_key"], base_url=base)

    receiver, secret = None, None
    if not args.no_webhooks:
        step("register a webhook endpoint")
        receiver = Receiver()
        url = f"http://{args.webhook_host}:{receiver.port}/webhooks"
        secret = client.webhook_endpoints.create(url=url, enabled_events=["*"])["secret"]

    step("customer")
    customer = client.customers.create(email="ada@example.com", metadata={"plan": "pro"})
    check(customer["id"].startswith("cus_"), "no customer id")

    step("payment with pm_card_visa (automatic capture)")
    pi = client.payment_intents.create(
        amount=2000,
        currency="usd",
        customer=customer["id"],
        payment_method="pm_card_visa",
        confirm=True,
    )
    check(pi["status"] == "succeeded", f"payment status {pi['status']}")

    step("authorize, capture part, refund part")
    held = client.payment_intents.create(
        amount=5000,
        currency="usd",
        capture_method="manual",
        payment_method="pm_card_visa",
        confirm=True,
    )
    check(held["status"] == "requires_capture", f"hold status {held['status']}")
    captured = client.payment_intents.capture(held["id"], amount_to_capture=4000)
    check(captured["amount_received"] == 4000, "partial capture")
    refund = client.refunds.create(payment_intent=held["id"], amount=1000)
    check(refund["amount"] == 1000, "refund")

    step("a declined card raises CardError")
    try:
        client.payment_intents.create(
            amount=1500, currency="usd", payment_method="pm_card_visa_chargeDeclined", confirm=True
        )
        check(False, "decline did not raise")
    except cleartill.CardError as exc:
        check(exc.decline_code == "generic_decline", f"decline code {exc.decline_code}")

    step("balance")
    balance = client.balance.retrieve()
    check(balance["pending"][0]["amount"] > 0, "nothing pending after captures")

    def session() -> dict:
        return client.checkout.sessions.create(
            mode="payment",
            line_items=[
                {
                    "price_data": {
                        "currency": "usd",
                        "unit_amount": 1500,
                        "product_data": {"name": "T-shirt"},
                    },
                    "quantity": 2,
                },
            ],
            success_url="https://merchant.example/thanks?session={CHECKOUT_SESSION_ID}",
            cancel_url="https://merchant.example/cart",
        )

    step("hosted checkout with 4242")
    cs = session()
    r = http.post(f"/checkout-api/sessions/{cs['id']}/pay", data=card("4242424242424242"))
    check(r.status_code == 200, f"checkout pay: {r.status_code} {r.text}")
    check(r.json()["redirect"].endswith(f"session={cs['id']}"), "success redirect")
    check(client.checkout.sessions.retrieve(cs["id"])["status"] == "complete", "session complete")

    step("hosted checkout with 3D Secure")
    cs3 = session()
    r = http.post(f"/checkout-api/sessions/{cs3['id']}/pay", data=card("4000002760003184"))
    check(r.status_code == 200 and "/checkout/3ds/" in r.json()["redirect"], "3DS redirect")
    client_secret = r.json()["redirect"].split("client_secret=")[1]
    r = http.post(
        f"/checkout-api/3ds/{cs3['payment_intent']}",
        data={"client_secret": client_secret, "result": "approve"},
    )
    check(r.status_code == 200, f"3DS approve: {r.status_code} {r.text}")
    check(client.checkout.sessions.retrieve(cs3["id"])["status"] == "complete", "3DS session")

    if receiver is not None:
        step("signed webhooks arrive")
        deadline = time.monotonic() + 30
        completed = []
        while time.monotonic() < deadline and len(completed) < 2:
            time.sleep(0.5)
            completed = []
            for body, header in list(receiver.received):
                event = cleartill.Webhook.construct_event(body, header, secret)
                if event["type"] == "checkout.session.completed":
                    completed.append(event["data"]["object"]["id"])
        check(
            sorted(completed) == sorted([cs["id"], cs3["id"]]),
            f"expected 2 checkout.session.completed webhooks, got {completed} "
            f"({len(receiver.received)} deliveries)",
        )

    # Never print the keys: this also runs against deployments, and CI logs are shared.
    print(f"\nOK: {base} works end to end (merchant {keys['merchant']['id']}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
