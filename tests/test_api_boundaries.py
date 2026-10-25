from __future__ import annotations

import json
import socket
import threading
from http.server import ThreadingHTTPServer
from urllib import error, request

from services.api.main import ApiHandler


def _start_api() -> tuple[ThreadingHTTPServer, str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), ApiHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, f"http://127.0.0.1:{server.server_port}"


def _request_status(req: request.Request) -> tuple[int, dict[str, str]]:
    try:
        with request.urlopen(req) as response:
            return response.status, dict(response.headers.items())
    except error.HTTPError as exc:
        return exc.code, dict(exc.headers.items())


def test_cors_preflight_rejects_untrusted_origins_and_does_not_emit_wildcard() -> None:
    server, base_url = _start_api()
    try:
        status, headers = _request_status(request.Request(
            f"{base_url}/api/store-templates",
            method="OPTIONS",
            headers={
                "Origin": "https://attacker.example",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type",
            },
        ))
    finally:
        server.shutdown()
        server.server_close()

    assert status == 403
    assert headers.get("Access-Control-Allow-Origin") is None


def test_cors_allows_mabang_extension_only_for_import_completion_capability() -> None:
    server, base_url = _start_api()
    try:
        allowed_status, allowed_headers = _request_status(request.Request(
            f"{base_url}/api/mabang-page-imports/9c4cf079-7b90-4a48-b0e3-94e0d840884f/complete",
            method="OPTIONS",
            headers={
                "Origin": "https://www.mabangerp.com",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type",
            },
        ))
        denied_status, _ = _request_status(request.Request(
            f"{base_url}/api/store-templates",
            method="OPTIONS",
            headers={
                "Origin": "https://www.mabangerp.com",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type",
            },
        ))
    finally:
        server.shutdown()
        server.server_close()

    assert allowed_status == 204
    assert allowed_headers["Access-Control-Allow-Origin"] == "https://www.mabangerp.com"
    assert denied_status == 403


def test_actual_cross_origin_write_is_rejected_before_route_dispatch() -> None:
    server, base_url = _start_api()
    try:
        status, headers = _request_status(request.Request(
            f"{base_url}/api/unknown-write-route",
            method="POST",
            data=b"{}",
            headers={"Content-Type": "application/json", "Origin": "https://attacker.example"},
        ))
    finally:
        server.shutdown()
        server.server_close()

    assert status == 403
    assert headers.get("Access-Control-Allow-Origin") is None


def test_file_download_does_not_emit_wildcard_cors(tmp_path, monkeypatch) -> None:
    template_path = tmp_path / "template.xlsx"
    template_path.write_bytes(b"xlsx")
    monkeypatch.setattr("services.api.main._resolve_template_path", lambda: template_path)
    server, base_url = _start_api()
    try:
        status, headers = _request_status(request.Request(
            f"{base_url}/api/mabang/template",
            headers={"Origin": "https://attacker.example"},
        ))
    finally:
        server.shutdown()
        server.server_close()

    assert status == 200
    assert headers.get("Access-Control-Allow-Origin") is None


def test_api_errors_do_not_expose_tracebacks(monkeypatch) -> None:
    def broken_list_items(collection: str):
        raise RuntimeError("simulated internal failure")

    monkeypatch.setattr("services.api.storage.list_items", broken_list_items)
    server, base_url = _start_api()
    try:
        with request.urlopen(f"{base_url}/api/assets", timeout=10) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
            status = resp.status
    except error.HTTPError as exc:
        status = exc.code
        payload = json.loads(exc.read().decode("utf-8"))
    finally:
        server.shutdown()
        server.server_close()

    assert status == 500
    assert payload == {"error": "Internal server error"}


def test_static_files_cannot_escape_web_root() -> None:
    server, base_url = _start_api()
    try:
        try:
            request.urlopen(f"{base_url}/../README.md", timeout=10)
        except error.HTTPError as exc:
            status = exc.code
        else:  # pragma: no cover - defensive clarity
            status = 200
    finally:
        server.shutdown()
        server.server_close()

    assert status in {403, 404}


def test_invalid_json_returns_client_error() -> None:
    server, base_url = _start_api()
    req = request.Request(
        f"{base_url}/api/products",
        data=b"{",
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        try:
            request.urlopen(req, timeout=10)
        except error.HTTPError as exc:
            status = exc.code
            payload = json.loads(exc.read().decode("utf-8"))
        else:  # pragma: no cover - defensive clarity
            status = 200
            payload = {}
    finally:
        server.shutdown()
        server.server_close()

    assert status == 400
    assert payload == {"error": "Invalid JSON body"}


def test_oversized_json_returns_payload_too_large() -> None:
    server, base_url = _start_api()
    try:
        host, port = server.server_address
        raw_request = (
            "POST /api/products HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            "Content-Type: application/json\r\n"
            "Content-Length: 1048577\r\n"
            "Connection: close\r\n"
            "\r\n"
        ).encode("ascii")
        with socket.create_connection((host, port), timeout=10) as sock:
            sock.sendall(raw_request)
            sock.shutdown(socket.SHUT_WR)
            chunks = []
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                chunks.append(chunk)
    finally:
        server.shutdown()
        server.server_close()

    raw_response = b"".join(chunks)
    header_bytes, body = raw_response.split(b"\r\n\r\n", 1)
    status_line = header_bytes.splitlines()[0].decode("ascii")
    status = int(status_line.split()[1])
    payload = json.loads(body.decode("utf-8"))
    assert status == 413
    assert payload == {"error": "Request body too large"}
