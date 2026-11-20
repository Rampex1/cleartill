# ruff: noqa: F811  (pytest fixtures imported from test_webhook_worker)
"""Regression tests for the M14 webhook worker review: whole-exchange deadline, raw capped
body, no poison rows, pinning with a hostname, claims never shared, spent claim tokens."""

import gzip
import socket
import threading
import time

from app import delivery, urls, webhooks, worker
from app.db import SessionLocal
from tests.test_webhook_worker import (  # noqa: F401  (fixtures reused)
    Receiver,
    insecure,
    merchant,
    receiver_factory,
    row,
    setup_delivery,
)


def _closing(handler):
    def run(conn):
        with conn:
            handler(conn)

    return run


def _raw_server(handler):
    """A TCP server whose connection handler we fully control."""
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen()
    port = sock.getsockname()[1]

    def serve():
        while True:
            try:
                conn, _ = sock.accept()
            except OSError:
                return
            threading.Thread(target=_closing(handler), args=(conn,), daemon=True).start()

    threading.Thread(target=serve, daemon=True).start()
    return sock, port


def test_dribbled_headers_hit_the_whole_send_deadline(insecure, monkeypatch):
    monkeypatch.setattr(delivery, "MAX_TOTAL_SECONDS", 2)
    stop = threading.Event()

    def dribble(conn):
        conn.recv(65536)
        conn.sendall(b"HTTP/1.1 200 OK\r\nX-A: ")
        while not stop.is_set():
            try:
                conn.sendall(b"a")
            except OSError:
                return
            time.sleep(0.3)

    sock, port = _raw_server(dribble)
    try:
        started = time.monotonic()
        result = delivery.send(f"http://127.0.0.1:{port}/", "{}", {})
        assert time.monotonic() - started < 5
        assert result.status_code is None and result.error == "timeout"
    finally:
        stop.set()
        sock.close()


def test_compressed_body_is_read_raw_and_capped(insecure):
    bomb = gzip.compress(b"\0" * (50 * 1024 * 1024))

    def serve(conn):
        conn.recv(65536)
        head = (
            f"HTTP/1.1 200 OK\r\nContent-Encoding: gzip\r\nContent-Length: {len(bomb)}\r\n\r\n"
        ).encode()
        try:
            conn.sendall(head + bomb)
        except OSError:
            pass

    sock, port = _raw_server(serve)
    try:
        started = time.monotonic()
        result = delivery.send(f"http://127.0.0.1:{port}/", "{}", {})
        assert result.ok and time.monotonic() - started < 5
    finally:
        sock.close()


def test_unexpected_send_error_is_recorded_not_poisonous(monkeypatch, insecure):
    class Boom:
        def __init__(self, *a, **k):
            raise RuntimeError("boom")

    monkeypatch.setattr(delivery.httpcore2, "ConnectionPool", Boom)
    result = delivery.send("http://127.0.0.1:9/", "{}", {})
    assert result.status_code is None and result.error == "send_error"


def test_idn_host_is_sent_as_punycode_and_pinned(insecure, receiver_factory, monkeypatch):
    receiver = receiver_factory(200)
    monkeypatch.setattr(urls, "_resolve", lambda host, port: ["127.0.0.1"])
    result = delivery.send(f"http://bücher.example:{receiver.port}/hook", "{}", {})
    assert result.ok
    assert receiver.requests[0]["headers"]["Host"] == f"xn--bcher-kva.example:{receiver.port}"


def test_hostname_is_pinned_to_the_vetted_ip(insecure, receiver_factory, monkeypatch):
    receiver = receiver_factory(200)
    monkeypatch.setattr(urls, "_resolve", lambda host, port: ["127.0.0.1"])
    assert delivery.send(f"http://hooks.example:{receiver.port}/x", "{}", {}).ok
    assert receiver.requests[0]["headers"]["Host"] == f"hooks.example:{receiver.port}"
    assert receiver.requests[0]["path"] == "/x"


def test_ipv4_addresses_are_tried_first():
    assert delivery._ipv4_first(["2606:2800::1", "93.184.216.34"]) == [
        "93.184.216.34",
        "2606:2800::1",
    ]


def test_two_claimers_never_get_the_same_row(engine, merchant, insecure, receiver_factory):
    receiver = receiver_factory(200)
    _, delivery_id = setup_delivery(merchant, receiver.url)
    claims = []
    barrier = threading.Barrier(2)

    def take():
        with SessionLocal() as s:
            barrier.wait()
            for _ in range(50):
                c = delivery.claim(s)
                if c is None or c.delivery_id == delivery_id:
                    claims.append(c)
                    return

    threads = [threading.Thread(target=take) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    ours = [c for c in claims if c is not None and c.delivery_id == delivery_id]
    assert len(ours) == 1


def test_a_failed_finish_spends_the_claim(engine, merchant, insecure, receiver_factory):
    receiver = receiver_factory(500)
    _, delivery_id = setup_delivery(merchant, receiver.url)
    with SessionLocal() as s:
        for _ in range(50):
            c = delivery.claim(s)
            if c and c.delivery_id == delivery_id:
                break
        result = delivery.send(c.url, c.payload, {})
        assert delivery.finish(s, c, result) is True
        assert delivery.finish(s, c, result) is False
    assert row(engine, delivery_id).attempts == 1


def test_verify_accepts_bytes():
    header = webhooks.sign("whsec_x", 1000, '{"a":1}')
    assert webhooks.verify(b'{"a":1}', header, "whsec_x", now=1000)


def test_run_once_counts_deliveries(engine, merchant, insecure, receiver_factory):
    receiver = receiver_factory(200)
    setup_delivery(merchant, receiver.url)
    assert worker.run_once() >= 1
    assert receiver.requests
