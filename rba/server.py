"""Loopback HTTP adapter for the local review workflow."""
from __future__ import annotations

import json
import os
import secrets
import socketserver
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from .engine import PermissionDenied, ReviewEngine, ReviewError, StaleApproval

MAX_BODY = 100_000
LOOPBACK = {"127.0.0.1", "localhost", "::1"}


def make_handler(runtime: Path, token: str):
    engine = ReviewEngine(runtime)

    class Handler(BaseHTTPRequestHandler):
        server_version = "RBA/1.0"

        def setup(self):
            super().setup()
            self.connection.settimeout(5)

        def log_message(self, fmt, *args):
            print(f"rba {self.client_address[0]} {self.command}")

        def _reply(self, status: int, data: object):
            payload = json.dumps(data, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(payload)

        def _secure(self, state_change: bool = False) -> bool:
            expected_host = f"127.0.0.1:{self.server.server_port}"
            host = self.headers.get("Host", "")
            if host != expected_host:
                self._reply(403, {"error": "Host rejected"})
                return False
            origin = self.headers.get("Origin")
            if origin:
                try:
                    parsed_origin = urlparse(origin)
                except ValueError:
                    self._reply(403, {"error": "Origin rejected"})
                    return False
                if parsed_origin.scheme != "http" or parsed_origin.hostname != "127.0.0.1" or parsed_origin.netloc != expected_host or parsed_origin.username is not None or parsed_origin.password is not None:
                    self._reply(403, {"error": "Origin rejected"})
                    return False
            if state_change and self.headers.get("X-RBA-Token") != token:
                self._reply(403, {"error": "Request token rejected"})
                return False
            return True

        def _body(self):
            size = int(self.headers.get("Content-Length", "0"))
            if size < 0 or size > MAX_BODY:
                raise ReviewError("Request exceeds the 100 KB limit.")
            payload = json.loads(self.rfile.read(size) or b"{}")
            if not isinstance(payload, dict):
                raise ReviewError("Request body must be a JSON object.")
            return payload

        def do_GET(self):
            if not self._secure():
                return
            path = urlparse(self.path).path
            if path == "/api/session":
                return self._reply(200, {"token": token})
            if path == "/api/state":
                return self._reply(200, engine.state())
            if path in ("/", "/index.html", "/app.js", "/style.css", "/desktop.css"):
                name = "index.html" if path in ("", "/", "/index.html") else path.lstrip("/")
                data = (Path(__file__).parent.parent / "web" / name).read_bytes()
                mime = "text/html" if name.endswith("html") else "text/javascript" if name.endswith("js") else "text/css"
                self.send_response(200)
                self.send_header("Content-Type", f"{mime}; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self'; script-src 'self'; connect-src 'self'; img-src 'self' data:")
                self.end_headers()
                self.wfile.write(data)
                return
            self._reply(404, {"error": "Not found"})

        def do_POST(self):
            if not self._secure(state_change=True):
                return
            try:
                data = self._body()
                path = urlparse(self.path).path
                if path == "/api/plan":
                    plan = engine.prepare(data.get("target_id"), data.get("payload"))
                    return self._reply(201, {"plan": plan.__dict__})
                if path == "/api/plan/revise":
                    plan = engine.revise(data["plan_id"], int(data["version"]), data["target_id"], data["payload"])
                    return self._reply(200, {"plan": plan.__dict__})
                if path == "/api/approve":
                    receipt = engine.approve(data["plan_id"], int(data["version"]), data["fingerprint"])
                    return self._reply(201, {"approval": receipt.__dict__})
                if path == "/api/execute":
                    readback = data.get("readback", True)
                    if not isinstance(readback, bool):
                        raise ReviewError("Readback choice must be a boolean.")
                    receipt = engine.execute(data["approval_id"], readback)
                    status = 409 if receipt.status == "rejected" else 200
                    return self._reply(status, {"receipt": receipt.__dict__, "error": receipt.detail if status == 409 else None})
                if path == "/api/verify":
                    receipt = engine.verify(data["receipt_id"])
                    return self._reply(200, {"receipt": receipt.__dict__})
                if path == "/api/settings":
                    return self._reply(200, engine.update_settings(data))
                self._reply(404, {"error": "Not found"})
            except StaleApproval as exc:
                self._reply(409, {"error": str(exc), "receipt": exc.receipt})
            except PermissionDenied as exc:
                self._reply(403, {"error": str(exc)})
            except (ReviewError, KeyError, TypeError, ValueError, OSError) as exc:
                self._reply(400, {"error": str(exc)[:300]})

    return Handler


class LoopbackHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def server_bind(self):
        socketserver.TCPServer.server_bind(self)
        self.server_name = "127.0.0.1"
        self.server_port = self.socket.getsockname()[1]


def make_server(runtime: str | Path | None = None, port: int = 0):
    root = Path(runtime or os.environ.get("RBA_RUNTIME", Path.home() / ".local/share/review-before-action"))
    root = root.expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(root, 0o700)
    return LoopbackHTTPServer(("127.0.0.1", port), make_handler(root, secrets.token_urlsafe(32)))


def serve(host: str = "127.0.0.1", port: int = 8335, runtime: str | Path | None = None):
    if host != "127.0.0.1":
        raise ValueError("Server must bind to 127.0.0.1.")
    httpd = make_server(runtime, port)
    print(f"Review Before Action listening at http://127.0.0.1:{httpd.server_port}")
    try:
        httpd.serve_forever()
    finally:
        httpd.server_close()
