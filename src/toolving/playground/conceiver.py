"""The playground's link to a local Conceiver build (the Rust `conceiver` crate).

The crate has no server. A small driver program, conceiver_driver.rs next to
this file, is compiled against the crate and kept running; it holds one trie
and answers ask / teach / state / reset requests, one per line. Everything
the playground knows about Conceiver's answers comes through `ask` and
`teach` here, so a change to how Conceiver answers changes the driver only.

    toolving-playground --conceiver ~/repos/conceiver/conceiver   # or TOOLVING_CONCEIVER
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from importlib import resources
from pathlib import Path
from typing import Any, Iterable

EXE = ".exe" if sys.platform == "win32" else ""


class ConceiverError(Exception):
    """The Conceiver build or its driver failed. `code` is shown in the page."""

    def __init__(self, code: str, message: str, status: int = 502):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def _run(argv: list[str]) -> None:
    done = subprocess.run(argv, capture_output=True, text=True)
    if done.returncode != 0:
        raise ConceiverError("CONCEIVER_BUILD_FAILED", f"{' '.join(argv[:2])} failed:\n{done.stderr[-2000:]}", 503)


def build_driver(crate: Path, cache: Path) -> Path:
    """Build the conceiver library (release) and compile the driver against it.
    Returns the driver's path; recompiles only when the library or the driver
    source is newer than the driver."""
    if not (crate / "Cargo.toml").is_file():
        raise ConceiverError("CONCEIVER_NOT_FOUND", f"No Cargo.toml in {crate}; point --conceiver at the conceiver crate.", 503)
    target = cache / "target"
    _run(["cargo", "build", "--release", "--lib", "--manifest-path", str(crate / "Cargo.toml"),
          "--target-dir", str(target)])
    library = target / "release" / "libconceiver.rlib"
    driver = cache / f"conceiver-session{EXE}"
    with resources.as_file(resources.files(__package__).joinpath("conceiver_driver.rs")) as source:
        newest = max(library.stat().st_mtime, source.stat().st_mtime)
        if not driver.exists() or driver.stat().st_mtime < newest:
            _run(["rustc", "--edition", "2021", "-O", "--crate-name", "conceiver_session",
                  "--extern", f"conceiver={library}", "-o", str(driver), str(source)])
    return driver


def default_cache(crate: Path) -> Path:
    base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / ".cache")
    key = "".join(c if c.isalnum() else "-" for c in str(crate.resolve()))[-80:]
    return base / "toolving" / "conceiver" / key


class Conceiver:
    """One running driver, so one trie. Requests are serialized."""

    def __init__(self, driver: Path, crate: Path | None = None):
        self.crate = crate
        self._lock = threading.Lock()
        self._process = subprocess.Popen([str(driver)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                         text=True, encoding="utf-8", bufsize=1)
        self._read()  # {"ready": true}: the trie is loaded

    @classmethod
    def build(cls, crate: Path, cache: Path | None = None) -> "Conceiver":
        crate = crate.expanduser()
        return cls(build_driver(crate, cache or default_cache(crate)), crate)

    def _read(self) -> dict[str, Any]:
        assert self._process.stdout is not None
        line = self._process.stdout.readline()
        if not line:
            raise ConceiverError("CONCEIVER_STOPPED", f"The Conceiver driver stopped (exit {self._process.poll()}).")
        return json.loads(line)

    def _request(self, *fields: str) -> dict[str, Any]:
        for field in fields:
            if "\t" in field or "\n" in field or "\r" in field:
                raise ConceiverError("INVALID_REQUEST", "Lines sent to Conceiver cannot hold tabs or line breaks.", 400)
        with self._lock:
            assert self._process.stdin is not None
            try:
                self._process.stdin.write("\t".join(fields) + "\n")
                self._process.stdin.flush()
            except OSError as failure:
                raise ConceiverError("CONCEIVER_STOPPED", f"The Conceiver driver stopped: {failure}") from failure
            return self._read()

    def ask(self, line: str) -> dict[str, Any]:
        """The line's answer: kind "answer" (output, support), "abstain" or "fault",
        with the steps and evidence the walk showed."""
        return self._request("ask", line)

    def teach(self, line: str, answer: str) -> dict[str, Any]:
        return self._request("teach", line, answer)

    def state(self, newest: int = 50) -> dict[str, Any]:
        return self._request("state", str(newest))

    def reset(self) -> dict[str, Any]:
        return self._request("reset")

    def close(self) -> None:
        if self._process.stdin:
            self._process.stdin.close()
        self._process.wait(timeout=10)


def run_session(conceiver: Any, pairs: Iterable[tuple[str, str]]) -> dict[str, Any]:
    """Teach a sequence the way conceiver/tests/session.rs does: each line is asked
    first, then its answer is taught. Each row says what Conceiver answered before
    it was taught (the answer's text against the taught text, exactly), and the
    tally counts right / wrong / abstained / fault. Any other kind of answer the
    driver reports counts as abstained."""
    rows = []
    tally = {"right": 0, "wrong": 0, "abstained": 0, "fault": 0}
    for question, want in pairs:
        asked = conceiver.ask(question)
        kind = asked.get("kind")
        if kind == "answer":
            verdict = "right" if asked.get("output") == want else "wrong"
        else:
            verdict = "fault" if kind == "fault" else "abstained"
        tally[verdict] += 1
        taught = conceiver.teach(question, want)
        rows.append({"question": question, "answer": want, "asked": asked, "verdict": verdict,
                     "teach_fault": None if taught.get("taught") else taught.get("fault", "unknown")})
    return {"rows": rows, "tally": tally}
