#!/usr/bin/env python3
"""
WSGI adapter that lets the Scrapling Tool web UI run on PythonAnywhere
(and any other WSGI-only host). It bridges the WSGI interface to the
`http.server.BaseHTTPRequestHandler`-based handler used by `scrape web`.

Configure PythonAnywhere's "WSGI configuration file" to point here, e.g.:

    import sys
    sys.path.insert(0, "/home/<username>/scrapling-tool")
    from pythonanywhere_wsgi import application  # noqa: E402

That path must also contain this repo (index.html, ultra_scraper.py, src/).
"""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path
from urllib.parse import urlsplit

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
_src = _HERE / "src"
if str(_src) not in sys.path:
    sys.path.insert(0, str(_src))

from ultra_scraper import _build_web_handler  # noqa: E402

Handler = _build_web_handler()


# ---------------------------------------------------------------------------
# Fake socket/connection so BaseHTTPRequestHandler works without a real socket
# ---------------------------------------------------------------------------
class _FakeConnection:
    """Emulates the socket interface BaseHTTPRequestHandler expects."""

    def __init__(self, request_bytes: bytes, client_addr: tuple):
        self._request = io.BytesIO(request_bytes)
        self._response = io.BytesIO()
        self._client_addr = client_addr
        self._timeout = None

    # -- http.client / http.server socket API ---------------------------------
    def makefile(self, mode: str, bufsize: int = -1):
        if "r" in mode:
            return self._request
        return self._response

    def sendall(self, data: bytes):
        self._response.write(data)

    def send(self, data: bytes) -> int:
        return self._response.write(data)

    def settimeout(self, timeout):
        self._timeout = timeout

    def gettimeout(self):
        return self._timeout

    def setsockopt(self, *args):
        pass

    def getsockname(self):
        return ("127.0.0.1", 0)

    def shutdown(self, *args):
        pass

    def close(self):
        pass

    # BaseHTTPRequestHandler reads the response through `wfile`; since the
    # handler uses `wbufsize == 0` it writes via `_SocketWriter` -> `sendall`.
    # We also expose `wfile` as a real file-like for robustness.
    @property
    def wfile(self):
        return self._response


def _build_request(environ: dict) -> bytes:
    """Reconstruct a raw HTTP request from a WSGI environ dict."""
    method = environ.get("REQUEST_METHOD", "GET")
    path = environ.get("PATH_INFO", "/") or "/"
    query = environ.get("QUERY_STRING", "")
    if query:
        path = f"{path}?{query}"
    proto = environ.get("SERVER_PROTOCOL", "HTTP/1.1")

    headers = []
    for key, value in environ.items():
        if key.startswith("HTTP_"):
            header_name = key[5:].replace("_", "-")
            headers.append((header_name, value))
    # CONTENT_TYPE / CONTENT_LENGTH are not prefixed with HTTP_ in WSGI
    if environ.get("CONTENT_TYPE"):
        headers.append(("Content-Type", environ["CONTENT_TYPE"]))
    if environ.get("CONTENT_LENGTH"):
        headers.append(("Content-Length", environ["CONTENT_LENGTH"]))
    if not any(k.lower() == "host" for k, _ in headers):
        host = environ.get("HTTP_HOST") or environ.get("SERVER_NAME", "")
        if host:
            headers.append(("Host", host))

    body = environ.get("wsgi.input")
    raw_body = b""
    if body is not None and environ.get("CONTENT_LENGTH"):
        try:
            raw_body = body.read(int(environ["CONTENT_LENGTH"]))
        except (TypeError, ValueError):
            raw_body = b""

    lines = [f"{method} {path} {proto}"]
    lines += [f"{k}: {v}" for k, v in headers]
    raw = ("\r\n".join(lines) + "\r\n\r\n").encode("latin-1", "replace") + raw_body
    return raw


def _parse_response(raw: bytes):
    """Split a raw HTTP response into (status, headers, body)."""
    head, sep, body = raw.partition(b"\r\n\r\n")
    if not sep:
        return "500", "Internal Server Error", [("Content-Type", "text/plain")], b"Bad gateway response", ""
    head_lines = head.decode("latin-1", "replace").split("\r\n")
    status_line = head_lines[0]
    parts = status_line.split(" ", 2)
    status_code = parts[1] if len(parts) > 1 else "500"
    reason = parts[2] if len(parts) > 2 else ""
    headers = []
    for line in head_lines[1:]:
        if ":" in line:
            k, _, v = line.partition(":")
            headers.append((k.strip(), v.strip()))
    return status_code, reason, headers, body, status_line


def application(environ, start_response):
    """WSGI entry point: translates environ <-> the http.server handler."""
    client_addr = (
        environ.get("REMOTE_ADDR", "127.0.0.1"),
        int(environ.get("REMOTE_PORT", 0) or 0),
    )
    raw_request = _build_request(environ)
    conn = _FakeConnection(raw_request, client_addr)

    try:
        Handler(conn, client_addr, server=None)
    except Exception as exc:  # noqa: BLE001 - surface as 500 JSON like the native handler
        body = json.dumps({"error": f"Handler error: {exc}"}).encode("utf-8")
        start_response("500 Internal Server Error", [
            ("Content-Type", "application/json; charset=utf-8"),
            ("Content-Length", str(len(body))),
        ])
        return [body]

    raw_response = conn._response.getvalue()
    status_code, reason, headers, body, _status_line = _parse_response(raw_response)

    if not any(k.lower() == "content-length" for k, _ in headers):
        headers.append(("Content-Length", str(len(body))))

    status_text = f"{status_code} {reason}".strip()
    start_response(status_text, headers)
    return [body]
