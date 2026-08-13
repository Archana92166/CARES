"""Local HTTP server for the CARES application backend."""

from __future__ import annotations

import argparse
import json
import os
import queue
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

from .api import CARESAPI
from .services import CARESBackend


FRONTEND_ROOT = Path(__file__).resolve().parent.parent / "frontend"


class CARESRequestHandler(BaseHTTPRequestHandler):
    server_version = "CARESBackend/1.0"

    @property
    def cares_server(self) -> "CARESThreadingServer":
        return self.server  # type: ignore[return-value]

    def do_GET(self) -> None:
        request_path = urlsplit(self.path).path
        if request_path == "/api/events/stream":
            self._stream_events()
            return
        if not request_path.startswith("/api/"):
            self._serve_frontend()
            return
        self._handle_json()

    def do_POST(self) -> None:
        self._handle_json()

    def do_PUT(self) -> None:
        self._handle_json()

    def do_PATCH(self) -> None:
        self._handle_json()

    def do_DELETE(self) -> None:
        self._handle_json()

    def _read_body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length == 0:
            return {}
        if length > 1_000_000:
            raise ValueError("Request body is too large.")
        raw = self.rfile.read(length).decode("utf-8")
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError("JSON request body must be an object.")
        return payload

    def _handle_json(self) -> None:
        try:
            request_body = self._read_body()
            response = self.cares_server.api.handle(self.command, self.path, request_body, self.headers)
        except (ValueError, json.JSONDecodeError) as exc:
            response = type("Response", (), {"status": 422, "body": {"error": str(exc)}, "headers": {}})()
        except Exception as exc:
            response = type("Response", (), {"status": 500, "body": {"error": "Internal server error."}, "headers": {}})()
            self.log_error("backend request failed: %s", exc)
        data = json.dumps(response.body, ensure_ascii=False).encode("utf-8")
        self.send_response(response.status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        for key, value in response.headers.items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(data)

    def _serve_frontend(self) -> None:
        """Serve the responsive SPA without exposing filesystem paths."""
        request_path = unquote(urlsplit(self.path).path)
        relative = request_path.lstrip("/") or "index.html"
        candidate = (FRONTEND_ROOT / relative).resolve()
        try:
            candidate.relative_to(FRONTEND_ROOT.resolve())
        except ValueError:
            self.send_error(HTTPStatus.NOT_FOUND, "Not found")
            return

        if not candidate.is_file():
            candidate = FRONTEND_ROOT / "index.html"
        if not candidate.is_file():
            self.send_error(HTTPStatus.NOT_FOUND, "Frontend is not installed")
            return

        content = candidate.read_bytes()
        content_type = {
            ".html": "text/html; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".js": "application/javascript; charset=utf-8",
            ".svg": "image/svg+xml",
        }.get(candidate.suffix, "application/octet-stream")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(content)

    def _stream_events(self) -> None:
        try:
            user_id = self.cares_server.api.authenticate_request(self.headers)
        except Exception:
            self.send_error(HTTPStatus.UNAUTHORIZED, "Authentication required")
            return
        subscriber = self.cares_server.backend.events.subscribe(user_id)
        try:
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            self.wfile.write(b"event: ready\ndata: {}\n\n")
            self.wfile.flush()
            for _ in range(120):
                try:
                    event = subscriber.get(timeout=15)
                    payload = json.dumps(event, ensure_ascii=False).encode("utf-8")
                    self.wfile.write(b"event: cares\ndata: " + payload + b"\n\n")
                except queue.Empty:
                    self.wfile.write(b": heartbeat\n\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            self.cares_server.backend.events.unsubscribe(user_id, subscriber)

    def log_message(self, format: str, *args: Any) -> None:
        # Do not log request bodies, cookies, passwords, or API keys.
        super().log_message(format, *args)


class CARESThreadingServer(ThreadingHTTPServer):
    def __init__(self, address: tuple[str, int], backend: CARESBackend) -> None:
        self.backend = backend
        self.api = CARESAPI(backend, secure_cookie=os.getenv("CARES_COOKIE_SECURE", "0") == "1")
        super().__init__(address, CARESRequestHandler)


def create_server(host: str = "127.0.0.1", port: int = 8000, db_path: str = "data/cares.sqlite3") -> CARESThreadingServer:
    backend = CARESBackend(db_path=db_path)
    return CARESThreadingServer((host, port), backend)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the CARES backend server.")
    parser.add_argument("--host", default=os.getenv("CARES_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("CARES_PORT", "8000")))
    parser.add_argument("--db", default=os.getenv("CARES_DB_PATH", "data/cares.sqlite3"))
    args = parser.parse_args()
    server = create_server(args.host, args.port, args.db)
    print(f"CARES backend listening on http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        server.backend.database.close()


if __name__ == "__main__":
    main()
