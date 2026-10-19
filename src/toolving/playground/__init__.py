"""toolving-playground: a local browser UI for the hosted learner API.

    toolving-playground                  # uses TOOLVING_API_URL / TOOLVING_API_KEY
    toolving-playground --port 8766 --no-browser

The page talks only to this local process. The process holds the API key and
forwards an allowlisted set of API calls, using the SDK client's auth,
idempotency keys, and retries. It listens on loopback only. Every call must
carry the per-launch token from the printed URL, and the Host header must be
the loopback address, so other websites open in the same browser cannot use
the key.
"""

from __future__ import annotations

import argparse
import json
import re
import secrets
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from typing import Any
from urllib.parse import urlsplit

from .. import __version__
from ..client import LearnerClient
from ..errors import LearnerAPIError

_NAME = r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}"
_ID = r"[A-Za-z0-9_-]{1,128}"
# (method, path under /v1/) that the page may call.
_ALLOWED = [
    ("GET", re.compile(r"learners")),
    ("GET", re.compile(r"meta/version")),
    ("GET", re.compile(rf"learners/{_NAME}")),
    ("POST", re.compile(r"learners")),
    ("POST", re.compile(rf"learners/{_NAME}/(predict|teach|remember|recall|forks|training|memory/maintain)")),
    ("POST", re.compile(rf"learners/{_NAME}/predictions/{_ID}/(teach|outcome)")),
    ("PUT", re.compile(rf"learners/{_NAME}/mode")),
]
_ASSETS = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
}


def _allowed(method: str, path: str) -> bool:
    return any(method == m and pattern.fullmatch(path) for m, pattern in _ALLOWED)


def build_handler(client: LearnerClient, token: str, port_holder: dict[str, int]):
    static = resources.files(__package__).joinpath("static")
    key_id = (client.api_key or "").split("_")[1] if (client.api_key or "").count("_") >= 2 else "?"

    class Handler(BaseHTTPRequestHandler):
        server_version = "toolving-playground"

        def log_message(self, *args):  # quiet; the page shows activity
            pass

        def send(self, status: int, body: bytes, content_type: str = "application/json") -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.end_headers()
            self.wfile.write(body)

        def send_json(self, status: int, value: Any) -> None:
            self.send(status, json.dumps(value, ensure_ascii=False).encode())

        def trusted(self) -> bool:
            port = port_holder["port"]
            host = self.headers.get("Host", "")
            if host not in {f"127.0.0.1:{port}", f"localhost:{port}"}:
                return False
            return secrets.compare_digest(self.headers.get("X-Playground-Token", ""), token)

        def do_GET(self):
            path = urlsplit(self.path).path
            if path.startswith("/api/"):
                return self.api("GET", path)
            asset = _ASSETS.get(path)
            if asset is None:
                return self.send(404, b"Not found", "text/plain")
            return self.send(200, static.joinpath(asset[0]).read_bytes(), asset[1])

        def do_POST(self):
            self.api("POST", urlsplit(self.path).path)

        def do_PUT(self):
            self.api("PUT", urlsplit(self.path).path)

        def api(self, method: str, path: str) -> None:
            if not self.trusted():
                return self.send_json(403, {"error": {"code": "FORBIDDEN", "message": "Missing or wrong playground token; reopen the URL printed by toolving-playground."}})
            if path == "/api/session":
                return self.send_json(200, {"base_url": client.base_url, "key_id": key_id, "sdk_version": __version__})
            target = path[len("/api/"):]
            if not _allowed(method, target):
                return self.send_json(404, {"error": {"code": "NOT_ALLOWED", "message": f"{method} /{target} is not available in the playground."}})
            body = None
            if method != "GET":
                length = int(self.headers.get("Content-Length") or 0)
                try:
                    body = json.loads(self.rfile.read(length) or b"{}")
                except json.JSONDecodeError:
                    return self.send_json(400, {"error": {"code": "INVALID_REQUEST", "message": "Body must be JSON."}})
            try:
                result = client._request(method, "/v1/" + target, body)
            except LearnerAPIError as failure:
                return self.send_json(
                    failure.status or 502,
                    {"error": {"code": failure.code, "message": failure.message, "reason": failure.reason,
                               "request_id": failure.request_id, "retryable": failure.retryable}},
                )
            except Exception as failure:  # network or unexpected; shown in the page
                return self.send_json(502, {"error": {"code": "UNREACHABLE", "message": f"{type(failure).__name__}: {failure}"}})
            return self.send_json(200, result)

    return Handler


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="toolving-playground", description="Local browser UI for the hosted learner API.")
    parser.add_argument("--url", help="API base URL (default: TOOLVING_API_URL)")
    parser.add_argument("--api-key", help="API key (default: TOOLVING_API_KEY)")
    parser.add_argument("--port", type=int, default=8766, help="local port (0 = any free port)")
    parser.add_argument("--no-browser", action="store_true", help="print the URL without opening a browser")
    args = parser.parse_args(argv)
    try:
        client = LearnerClient(base_url=args.url, api_key=args.api_key)
        client.version()
    except ValueError as failure:
        print(f"toolving-playground: {failure}", file=sys.stderr)
        return 2
    except LearnerAPIError as failure:
        print(f"toolving-playground: the API rejected the key or is unavailable: {failure}", file=sys.stderr)
        return 2
    except Exception as failure:
        print(f"toolving-playground: cannot reach {client.base_url if 'client' in locals() else args.url}: {failure}", file=sys.stderr)
        return 2
    token = secrets.token_urlsafe(24)
    port_holder: dict[str, int] = {}
    try:
        server = ThreadingHTTPServer(("127.0.0.1", args.port), build_handler(client, token, port_holder))
    except OSError as failure:
        print(f"toolving-playground: cannot listen on 127.0.0.1:{args.port} ({failure.strerror}); "
              "pick another with --port, or --port 0 for any free port", file=sys.stderr)
        return 2
    port_holder["port"] = server.server_address[1]
    url = f"http://127.0.0.1:{port_holder['port']}/#t={token}"
    print(f"Learner playground for {client.base_url}\n  {url}\nCtrl+C to stop.", flush=True)
    if not args.no_browser:
        threading.Timer(0.3, webbrowser.open, (url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
