from __future__ import annotations

import http.client
import json
import socket
import threading

import pytest

from services.api import main, settings
from services.api.main import ApiHandler


@pytest.fixture()
def api_port():
    server = main.ApiServer(("127.0.0.1", 0), ApiHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_port
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_handler_uses_http_11_and_keepalive_timeout() -> None:
    assert ApiHandler.protocol_version == "HTTP/1.1"
    assert ApiHandler.timeout == settings.API_KEEPALIVE_TIMEOUT_SECONDS
    assert main.ApiServer.request_queue_size >= 128


def test_two_get_requests_reuse_one_connection(api_port) -> None:
    conn = http.client.HTTPConnection("127.0.0.1", api_port, timeout=10)
    try:
        conn.request("GET", "/api/health")
        first = conn.getresponse()
        first_body = first.read()

        conn.request("GET", "/api/health")
        second = conn.getresponse()
        second_body = second.read()
    finally:
        conn.close()

    assert first.status == 200
    assert second.status == 200
    assert json.loads(first_body.decode("utf-8"))["ok"] is True
    assert json.loads(second_body.decode("utf-8"))["ok"] is True
    assert first.getheader("Connection") != "close"
    assert second.getheader("Connection") != "close"


def test_write_response_asks_the_client_to_close(api_port) -> None:
    conn = http.client.HTTPConnection("127.0.0.1", api_port, timeout=10)
    try:
        conn.request(
            "POST",
            "/api/unknown-write",
            body=json.dumps({}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        response = conn.getresponse()
        response.read()
        assert response.getheader("Connection") == "close"
    finally:
        conn.close()


def test_server_bind_does_not_reverse_dns(monkeypatch) -> None:
    calls: list[str] = []

    def fake_getfqdn(host: str) -> str:
        calls.append(host)
        return host

    monkeypatch.setattr(socket, "getfqdn", fake_getfqdn)

    server = main.ApiServer(("127.0.0.1", 0), ApiHandler)
    try:
        assert calls == []
    finally:
        server.server_close()
