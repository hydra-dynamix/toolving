"""toolving-playground: a local browser UI for writing training sequences and
teaching them to a local Conceiver build.

    toolving-playground --conceiver ~/repos/conceiver/conceiver   # or TOOLVING_CONCEIVER
    toolving-playground --port 8766 --no-browser
    toolving-inspector --conceiver ...                            # opens the inspector page

Two pages share one process and so one Conceiver trie:
- `/`, the playground: a sequence editor whose sequence is taught to Conceiver
  line by line (each line asked first, then taught), and the assistant panel,
  an LLM chat that helps write the sequence (agent.py; it runs through agentctl);
- `/inspect`, the inspector: ask a line and read its answer, steps and +/-
  evidence, teach a correction, and read what the loaded trie holds.

Conceiver is the Rust `conceiver` crate; conceiver.py builds a driver for it
and keeps it running. The process listens on loopback only. Every API call
must carry the per-launch token from the printed URL, and the Host header must
be the loopback address, so other websites open in the same browser cannot
drive it.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from .. import __version__
from .agent import AgentError, SequenceAgent, SequenceError, agent_from_env, validate_sequence
from .conceiver import Conceiver, ConceiverError, run_session

_ASSETS = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/inspect": ("inspect.html", "text/html; charset=utf-8"),
    "/common.js": ("common.js", "text/javascript; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/inspect.js": ("inspect.js", "text/javascript; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
}


class _Refused(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


def _text(body: dict[str, Any], field: str) -> str:
    value = body.get(field)
    if not isinstance(value, str) or not value.strip():
        raise _Refused(400, "INVALID_REQUEST", f"{field} must be non-empty text.")
    return value.strip()


def build_handler(token: str, port_holder: dict[str, int], *,
                  conceiver: Any = None, conceiver_unavailable: str = "No Conceiver build is configured.",
                  agent: SequenceAgent | None = None, agent_unavailable: str = "The assistant is not configured."):
    """`conceiver` is a conceiver.Conceiver, or anything with its ask / teach / state / reset."""
    static = resources.files(__package__).joinpath("static")

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
            url = urlsplit(self.path)
            if url.path.startswith("/api/"):
                return self.api("GET", url.path, parse_qs(url.query))
            asset = _ASSETS.get(url.path)
            if asset is None:
                return self.send(404, b"Not found", "text/plain")
            return self.send(200, static.joinpath(asset[0]).read_bytes(), asset[1])

        def do_POST(self):
            self.api("POST", urlsplit(self.path).path, {})

        def read_json(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length") or 0)
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
            except json.JSONDecodeError:
                body = None
            if not isinstance(body, dict):
                raise _Refused(400, "INVALID_REQUEST", "Body must be a JSON object.")
            return body

        def api(self, method: str, path: str, query: dict[str, list[str]]) -> None:
            if not self.trusted():
                return self.send_json(403, {"error": {"code": "FORBIDDEN", "message": "Missing or wrong playground token; reopen the URL printed by toolving-playground."}})
            try:
                return self.send_json(200, self.route(method, path, query))
            except _Refused as failure:
                status, code, message = failure.status, failure.code, failure.message
            except (AgentError, ConceiverError) as failure:
                status, code, message = failure.status, failure.code, failure.message
            except SequenceError as failure:
                status, code, message = 400, "INVALID_SEQUENCE", str(failure)
            return self.send_json(status, {"error": {"code": code, "message": message}})

        def route(self, method: str, path: str, query: dict[str, list[str]]) -> Any:
            if (method, path) == ("GET", "/api/session"):
                return {
                    "sdk_version": __version__,
                    "conceiver": {"available": True, "crate": str(getattr(conceiver, "crate", "") or "")}
                    if conceiver else {"available": False, "reason": conceiver_unavailable},
                    "assistant": {"available": True, "label": agent.label}
                    if agent else {"available": False, "reason": agent_unavailable},
                }
            if (method, path) == ("POST", "/api/agent/chat"):
                if agent is None:
                    raise _Refused(503, "AGENT_UNAVAILABLE", agent_unavailable)
                return agent.reply(self.read_json().get("messages"))
            if path.startswith("/api/conceiver/"):
                if conceiver is None:
                    raise _Refused(503, "CONCEIVER_UNAVAILABLE", conceiver_unavailable)
                action = (method, path[len("/api/conceiver/"):])
                if action == ("GET", "state"):
                    newest = (query.get("newest") or ["50"])[0]
                    return conceiver.state(int(newest) if newest.isdigit() else 50)
                if action == ("POST", "ask"):
                    return conceiver.ask(_text(self.read_json(), "line"))
                if action == ("POST", "teach"):
                    body = self.read_json()
                    return conceiver.teach(_text(body, "question"), _text(body, "answer"))
                if action == ("POST", "session"):
                    sequence = validate_sequence(self.read_json().get("pairs"))
                    return run_session(conceiver, sequence.pairs)
                if action == ("POST", "reset"):
                    return conceiver.reset()
            raise _Refused(404, "NOT_FOUND", f"{method} {path} is not available.")

    return Handler


def main(argv: list[str] | None = None, *, page: str = "/") -> int:
    parser = argparse.ArgumentParser(prog="toolving-playground", description="Local browser UI for teaching a local Conceiver build.")
    parser.add_argument("--conceiver", default=os.environ.get("TOOLVING_CONCEIVER"),
                        help="the conceiver crate folder, holding its Cargo.toml (default: TOOLVING_CONCEIVER)")
    parser.add_argument("--agentctl", help="agentctl executable for the assistant (default: agentctl on PATH)")
    parser.add_argument("--agent", default="claude", help="the agentctl agent the assistant runs (default: claude)")
    parser.add_argument("--port", type=int, default=8766, help="local port (0 = any free port)")
    parser.add_argument("--no-browser", action="store_true", help="print the URL without opening a browser")
    args = parser.parse_args(argv)

    conceiver: Conceiver | None = None
    conceiver_unavailable = "Start with --conceiver (or TOOLVING_CONCEIVER) set to the conceiver crate folder."
    if args.conceiver:
        print(f"Building the Conceiver driver against {args.conceiver} …", flush=True)
        try:
            conceiver = Conceiver.build(Path(args.conceiver))
        except (ConceiverError, OSError) as failure:
            conceiver_unavailable = getattr(failure, "message", str(failure))
            print(f"toolving-playground: Conceiver is off: {conceiver_unavailable}", file=sys.stderr)
    agent, agent_unavailable = agent_from_env(args.agentctl, args.agent)

    token = secrets.token_urlsafe(24)
    port_holder: dict[str, int] = {}
    handler = build_handler(token, port_holder, conceiver=conceiver, conceiver_unavailable=conceiver_unavailable,
                            agent=agent, agent_unavailable=agent_unavailable)
    try:
        server = ThreadingHTTPServer(("127.0.0.1", args.port), handler)
    except OSError as failure:
        print(f"toolving-playground: cannot listen on 127.0.0.1:{args.port} ({failure.strerror}); "
              "pick another with --port, or --port 0 for any free port", file=sys.stderr)
        return 2
    port_holder["port"] = server.server_address[1]
    base = f"http://127.0.0.1:{port_holder['port']}"
    print(f"Playground  {base}/#t={token}\nInspector   {base}/inspect#t={token}\n"
          f"Conceiver: {conceiver.crate if conceiver else 'off'}\n"
          f"Assistant: {agent.label if agent else 'off: ' + agent_unavailable}\nCtrl+C to stop.", flush=True)
    if not args.no_browser:
        threading.Timer(0.3, webbrowser.open, (f"{base}{page}#t={token}",)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        if conceiver:
            conceiver.close()
    return 0


def inspector_main(argv: list[str] | None = None) -> int:
    """toolving-inspector: the same local server, opening the inspector page."""
    return main(argv, page="/inspect")


if __name__ == "__main__":
    raise SystemExit(main())
