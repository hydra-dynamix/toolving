"""Install the `toolving-conceiver` agent skill that ships with the SDK.

    python -m toolving.skill install            # every agent host found, plus ~/.agents/skills
    python -m toolving.skill install --host claude-code --project
    python -m toolving.skill install --dir PATH # any other harness's skills folder
    python -m toolving.skill print              # the skill's text, for a harness to load
    python -m toolving.skill path               # where the packaged SKILL.md is

A skill is a folder holding SKILL.md. Each host reads skills from its own folder:

    claude-code  ~/.claude/skills            project: .claude/skills
    codex        ~/.codex/skills             project: .agents/skills
    pi           ~/.pi/agent/skills          project: .pi/skills
    agents       ~/.agents/skills            project: .agents/skills  (shared by Pi, Codex and others)

An existing SKILL.md that this command did not write is left alone unless --force is given.
"""

from __future__ import annotations

import argparse
import sys
from importlib import resources
from pathlib import Path

NAME = "toolving-conceiver"
MARKER = "<!-- toolving-conceiver skill, installed by `python -m toolving.skill install` -->"

HOSTS: dict[str, tuple[str, str, str | None]] = {
    # host: (user skills folder, project skills folder, folder whose presence means the host is installed)
    "claude-code": ("~/.claude/skills", ".claude/skills", "~/.claude"),
    "codex": ("~/.codex/skills", ".agents/skills", "~/.codex"),
    "pi": ("~/.pi/agent/skills", ".pi/skills", "~/.pi"),
    "agents": ("~/.agents/skills", ".agents/skills", None),
}


def skill_text() -> str:
    """The packaged SKILL.md."""
    return resources.files("toolving").joinpath("skills", NAME, "SKILL.md").read_text(encoding="utf-8")


def targets(hosts: list[str], project: bool, root: Path, home: Path) -> list[Path]:
    """The skill folders to write for these hosts (`all`: every host found, plus `agents`)."""
    chosen = hosts or ["all"]
    if "all" in chosen:
        chosen = [h for h, (_, _, marker) in HOSTS.items() if marker is None or project or _expand(marker, home).exists()]
    folders: list[Path] = []
    for host in chosen:
        if host not in HOSTS:
            raise SystemExit(f"unknown host {host!r}; one of: {', '.join(HOSTS)}, all")
        user, proj, _ = HOSTS[host]
        folder = (root / proj) if project else _expand(user, home)
        if folder not in folders:
            folders.append(folder)
    return folders


def _expand(path: str, home: Path) -> Path:
    return home / path[2:] if path.startswith("~/") else Path(path)


def install_into(folder: Path, text: str, force: bool) -> str:
    """Write NAME/SKILL.md under `folder`. Says what happened."""
    target = folder / NAME / "SKILL.md"
    if target.exists():
        current = target.read_text(encoding="utf-8", errors="replace")
        if current == text:
            return f"up to date: {target}"
        if MARKER not in current and not force:
            return f"skipped (not written by toolving; pass --force to replace): {target}"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    return f"installed: {target}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m toolving.skill", description="Install the toolving-conceiver agent skill.")
    sub = parser.add_subparsers(dest="command", required=True)
    inst = sub.add_parser("install", help="write the skill where agent hosts load skills")
    inst.add_argument("--host", action="append", default=[], help=f"one of {', '.join(HOSTS)}, all (repeatable; default all)")
    inst.add_argument("--project", action="store_true", help="install into this project's folders instead of the user's")
    inst.add_argument("--dir", action="append", default=[], type=Path, help="also install into this skills folder (repeatable)")
    inst.add_argument("--force", action="store_true", help="replace a SKILL.md this command did not write")
    sub.add_parser("print", help="print the skill's text")
    sub.add_parser("path", help="print the path of the packaged SKILL.md")
    args = parser.parse_args(argv)

    if args.command == "print":
        sys.stdout.write(skill_text())
        return 0
    if args.command == "path":
        print(resources.files("toolving").joinpath("skills", NAME, "SKILL.md"))
        return 0
    text = skill_text()
    folders = targets(args.host, args.project, Path.cwd(), Path.home()) if (args.host or not args.dir) else []
    folders += [d.expanduser() for d in args.dir if d.expanduser() not in folders]
    for folder in folders:
        print(install_into(folder, text, args.force))
    print(f"Restart the agent host (or start a new session) so it loads the {NAME} skill.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
