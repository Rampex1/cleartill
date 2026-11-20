# /// script
# requires-python = ">=3.11"
# dependencies = ["cleartill"]
#
# [tool.uv.sources]
# cleartill = { path = "../sdk" }
# ///
"""End-to-end smoke test, and the README quickstart.

    docker compose up -d --build --wait
    uv run scripts/smoke.py http://localhost:8000

Signs up a fresh merchant, then takes real money paths through the API, the SDK, hosted
checkout (with 3D Secure) and signed webhooks. Exits non-zero on the first failure."""

import argparse
import http.server
import platform
import sys
import threading
import time
import uuid
from datetime import UTC, datetime
from urllib.parse import urlsplit

import cleartill
import httpx2

DASHBOARD = {"X-Cleartill-Dashboard": "1"}
EXP_YEAR = str(datetime.now(UTC).year + 5)
MAX_WEBHOOK_BODY = 1_000_000
WEBHOOK_WAIT_SECONDS = 30


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

    def __init__(self, bind: str, port: int):
        self.received: list[tuple[bytes, str]] = []
        received = self.received

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                if length > MAX_WEBHOOK_BODY:
                    self.send_response(413)
                    self.end_headers()
                    return
                received.append(
                    (self.rfile.read(length), self.headers.get("Cleartill-Signature", ""))
                )
                self.send_response(200)
                self.end_headers()

            def log_message(self, *args):
                pass

        self.server = http.server.ThreadingHTTPServer((bind, port), Handler)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()


def default_bind() -> str:
    # Docker Desktop (macOS, Windows) forwards host.docker.internal to the host's loopback;
    # on Linux the containers reach the host through the bridge, so listen there too.
    return "0.0.0.0" if platform.system() == "Linux" else "127.0.0.1"


def wait_for_completed(receiver: Receiver, secret: str, sessions: set[str]) -> None:
    deadline = time.monotonic() + WEBHOOK_WAIT_SECONDS
    seen_sessions: set[str] = set()
    events: set[str] = set()
    invalid = 0
    while time.monotonic() < deadline and not sessions <= seen_sessions:
        time.sleep(0.5)
        seen_sessions, events, invalid = set(), set(), 0
        for body, header in list(receiver.received):
            try:
                event = cleartill.Webhook.construct_event(body, header, secret)
            except cleartill.SignatureVerificationError:
                invalid += 1  # not ours (or forged): never trusted, but reported
                continue
            events.add(event["id"])  # at-least-once delivery: dedupe on the event id
            if event["type"] == "checkout.session.completed":
                seen_sessions.add(event["data"]["object"]["id"])
    check(
        sessions <= seen_sessions,
        f"expected checkout.session.completed for {sorted(sessions)}, got {sorted(seen_sessions)} "
        f"({len(events)} valid events, {invalid} deliveries with bad signatures)",
    )


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
    parser.add_argument("--webhook-port", type=int, default=0, help="0 picks a free port")
    parser.add_argument("--bind", default=default_bind(), help="receiver bind address")
    args = parser.parse_args()
    base = args.base_url.rstrip("/")
    local = urlsplit(base).hostname in {"localhost", "127.0.0.1"}
    webhooks = not args.no_webhooks and (local or args.webhook_host != "host.docker.internal")
    http = httpx2.Client(base_url=base, timeout=30)

    step(f"health check {base}")
    check(http.get("/healthz").json().get("status") == "ok", "API is not healthy")

    step("sign up a merchant")
    r = http.post(
        "/dashboard-api/signup",
        data={"email": f"smoke-{uuid.uuid4().hex[:12]}@example.com", "password": uuid.uuid4().hex},
        headers=DASHBOARD,
    )
    check(r.status_code == 200, f"signup failed: {r.status_code} {r.text}")
    keys = r.json()

    with cleartill.Client(keys["secret_key"], base_url=base) as client:
        endpoint = None
        try:
            if webhooks:
                step("register a webhook endpoint")
                receiver = Receiver(args.bind, args.webhook_port)
                url = f"http://{args.webhook_host}:{receiver.port}/webhooks"
                try:
                    endpoint = client.webhook_endpoints.create(url=url, enabled_events=["*"])
                except cleartill.InvalidRequestError as exc:
                    raise SystemExit(
                        f"FAILED: the API won't send webhooks to {url} ({exc.message}). "
                        "Rerun with --no-webhooks, or point --webhook-host/--webhook-port at "
                        "an address the API can reach."
                    ) from None
            else:
                step("skip webhooks (not a local API; pass --webhook-host to check them)")
            completed = run_payments(client, http)
            if endpoint is not None:
                step("signed webhooks arrive")
                wait_for_completed(receiver, endpoint["secret"], completed)
        finally:
            if endpoint is not None:  # don't leave an endpoint retrying a dead port for days
                client.webhook_endpoints.delete(endpoint["id"])
    http.close()

    # Never print the keys: this also runs against deployments, and CI logs are shared.
    print(f"\nOK: {base} works end to end (merchant {keys['merchant']['id']}).")
    return 0


def run_payments(client: cleartill.Client, http: httpx2.Client) -> set[str]:
    """The money paths; returns the checkout sessions that should complete."""
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
    check(
        captured["status"] == "succeeded" and captured["amount_received"] == 4000,
        f"partial capture: {captured['status']} {captured['amount_received']}",
    )
    refund = client.refunds.create(payment_intent=held["id"], amount=1000)
    check(refund["status"] == "succeeded" and refund["amount"] == 1000, "refund")

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
    usd = sum(
        b["amount"] for b in balance["pending"] + balance["available"] if b["currency"] == "usd"
    )
    check(usd > 0, f"no usd balance after captures: {balance}")

    def session() -> dict:
        item = {"currency": "usd", "unit_amount": 1500, "product_data": {"name": "T-shirt"}}
        return client.checkout.sessions.create(
            mode="payment",
            line_items=[{"price_data": item, "quantity": 2}],
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
    return {cs["id"], cs3["id"]}


if __name__ == "__main__":
    sys.exit(main())
