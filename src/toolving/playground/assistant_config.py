"""Settings for the playground's assistant, and the state of what it runs on.

The assistant runs `agentctl run toolving-assistant`. The playground keeps its
own agentctl.toml for that agent in its settings folder and runs agentctl from
there (agentctl reads ./agentctl.toml before ~/.agentctl/config.toml), so the
user's own agentctl configuration is never edited. The agent is Claude Code
(`claude -p`, with --model when one is set), reading the prompt on stdin.

Settings are kept in assistant.json in the same folder:
    ~/.toolving/playground/   (or TOOLVING_PLAYGROUND_HOME)
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable

from .agent import SequenceAgent, agentctl_complete

AGENT = "toolving-assistant"
_MODEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:\[\]-]{0,99}")

# How agentctl is installed. It ships paired with ldgr from the public
# hydra-dynamix/ldgr-core releases (its README's install commands), or builds
# alone from source with cargo (hydra-dynamix/agentctl README).
INSTALL = {
    "windows": {
        "shell": "PowerShell",
        "command": "irm https://raw.githubusercontent.com/hydra-dynamix/ldgr-core/main/scripts/install.ps1 | iex",
        "note": r"Installs ldgr and agentctl into %LOCALAPPDATA%\Programs\ldgr\bin and adds it to your user PATH.",
    },
    "unix": {
        "shell": "sh",
        "command": "curl -fsSL https://raw.githubusercontent.com/hydra-dynamix/ldgr-core/main/scripts/install.sh | sh",
        "note": "Needs Python 3. Installs ldgr and agentctl into ~/.local/bin (LDGR_INSTALL_DIR to change it).",
    },
    "cargo": {
        "shell": "sh",
        "command": "cargo install --git https://github.com/hydra-dynamix/agentctl agentctl",
        "note": "agentctl alone, built from source; needs Rust. Installs into ~/.cargo/bin.",
    },
}
# Claude Code, the agent agentctl runs, with its sign-in.
CLAUDE_INSTALL = {
    "windows": "irm https://claude.ai/install.ps1 | iex",
    "unix": "curl -fsSL https://claude.ai/install.sh | bash",
    "login": "claude auth login",
}


def settings_home() -> Path:
    return Path(os.environ.get("TOOLVING_PLAYGROUND_HOME") or Path.home() / ".toolving" / "playground")


def platform() -> str:
    return "windows" if sys.platform == "win32" else "unix"


@dataclass(frozen=True)
class Settings:
    agentctl: str = ""  # empty: found on PATH or where the installers put it
    model: str = ""     # empty: Claude Code's default model
    timeout: int = 600  # seconds for one reply

    @classmethod
    def parse(cls, value: Any) -> "Settings":
        """Settings from user input; raises ValueError with a message for the page."""
        if not isinstance(value, dict):
            raise ValueError("Settings must be an object.")
        agentctl = str(value.get("agentctl") or "").strip()
        model = str(value.get("model") or "").strip()
        if model and not _MODEL.fullmatch(model):
            raise ValueError("The model must be a model name such as claude-opus-5-5, with no spaces.")
        try:
            timeout = int(value.get("timeout") or 600)
        except (TypeError, ValueError) as failure:
            raise ValueError("The timeout must be a whole number of seconds.") from failure
        if not 10 <= timeout <= 3600:
            raise ValueError("The timeout must be between 10 and 3600 seconds.")
        return cls(agentctl, model, timeout)


def _installed_locations(name: str) -> list[Path]:
    exe = name + (".exe" if sys.platform == "win32" else "")
    places = [Path.home() / ".local" / "bin", Path.home() / ".cargo" / "bin"]
    if os.environ.get("LOCALAPPDATA"):
        places.insert(0, Path(os.environ["LOCALAPPDATA"]) / "Programs" / "ldgr" / "bin")
    return [place / exe for place in places]


class AssistantConfig:
    """The assistant's settings, its agentctl config, and the agent built from them.
    `run` and `which` are subprocess.run and shutil.which, or stand-ins in tests."""

    def __init__(self, home: Path | None = None, *, run: Callable[..., Any] = subprocess.run,
                 which: Callable[[str], str | None] = shutil.which):
        self.home = home or settings_home()
        self._run = run
        self._which = which
        self.settings = self._load()
        self.agent: SequenceAgent | None = None
        self.reason = ""
        self.apply(self.settings, save=False)

    @property
    def settings_file(self) -> Path:
        return self.home / "assistant.json"

    @property
    def config_file(self) -> Path:
        return self.home / "agentctl.toml"

    def _load(self) -> Settings:
        try:
            return Settings.parse(json.loads(self.settings_file.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            return Settings()

    def agentctl_path(self, settings: Settings | None = None) -> str | None:
        chosen = (settings or self.settings).agentctl
        if chosen:
            return chosen if Path(chosen).is_file() else None
        found = self._which("agentctl")
        if found:
            return found
        return next((str(path) for path in _installed_locations("agentctl") if path.is_file()), None)

    def config_text(self, settings: Settings | None = None) -> str:
        settings = settings or self.settings
        command = ["claude", "-p"] + (["--model", settings.model] if settings.model else [])
        return ("# Written by toolving-playground for its assistant; edit it in the playground's settings.\n"
                f"[agents.{AGENT}]\ncommand = {json.dumps(command)}\nprompt_stdin = true\n")

    def apply(self, settings: Settings, *, save: bool = True) -> None:
        """Use these settings: write the agentctl config, save them, and rebuild the agent."""
        if save:
            self.home.mkdir(parents=True, exist_ok=True)
            self.settings_file.write_text(json.dumps(asdict(settings), indent=2) + "\n", encoding="utf-8")
        self.settings = settings
        executable = self.agentctl_path(settings)
        if executable is None:
            self.agent, self.reason = None, (
                f"agentctl was not found at {settings.agentctl}." if settings.agentctl
                else "agentctl is not installed. Open the assistant settings to install it.")
            return
        self.home.mkdir(parents=True, exist_ok=True)
        self.config_file.write_text(self.config_text(settings), encoding="utf-8")
        label = f"claude{' ' + settings.model if settings.model else ''} via agentctl"
        self.agent = SequenceAgent(agentctl_complete(executable, AGENT, settings.timeout, config_dir=self.home), label=label)
        self.reason = ""

    def _output(self, argv: list[str], cwd: Path | None = None) -> tuple[int, str]:
        try:
            done = self._run(argv, capture_output=True, text=True, encoding="utf-8", timeout=30, cwd=cwd)
        except (OSError, subprocess.SubprocessError) as failure:
            return -1, str(failure)
        return done.returncode, (done.stdout or "").strip() or (done.stderr or "").strip()

    def status(self) -> dict[str, Any]:
        """What the assistant runs on, checked now: agentctl, its config, Claude Code and its sign-in."""
        if self.agent is None:  # agentctl may have been installed since
            self.apply(self.settings, save=False)
        executable = self.agentctl_path()
        agentctl: dict[str, Any] = {"found": executable is not None, "path": executable, "version": None}
        config: dict[str, Any] = {"path": str(self.config_file), "text": self.config_text(), "check": None}
        if executable:
            code, out = self._output([executable, "--version"])
            agentctl["version"] = out.removeprefix("agentctl ").strip() if code == 0 else None
            if self.config_file.is_file():
                code, out = self._output([executable, "config", "check"], cwd=self.home)
                config["check"] = {"ok": code == 0, "message": out[:500]}
        claude_path = self._which("claude") or next(
            (str(path) for path in _installed_locations("claude") if path.is_file()), None)
        claude: dict[str, Any] = {"found": claude_path is not None, "path": claude_path, "signed_in": None,
                                  "method": None, "expires": None}
        if claude_path:
            code, out = self._output([claude_path, "auth", "status"])
            try:
                auth = json.loads(out) if code == 0 else {}
            except ValueError:
                auth = {}
            claude.update(signed_in=bool(auth.get("loggedIn")), method=auth.get("authMethod"),
                          expires=auth.get("loginExpiresAt"))
        system = platform()
        return {
            "settings": asdict(self.settings),
            "available": self.agent is not None,
            "reason": self.reason,
            "agentctl": agentctl,
            "config": config,
            "claude": claude,
            "install": {
                "platform": system,
                "agentctl": [INSTALL[system], INSTALL["cargo"]],
                "claude": CLAUDE_INSTALL[system],
                "login": CLAUDE_INSTALL["login"],
                "default_paths": [str(path) for path in _installed_locations("agentctl")],
            },
        }
