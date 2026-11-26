from pathlib import Path

from toolving import skill


def test_the_packaged_skill_has_its_frontmatter_and_marker():
    text = skill.skill_text()
    assert text.startswith("---\nname: toolving-conceiver\ndescription: ")
    assert skill.MARKER in text


def test_install_writes_every_host_found_and_the_shared_folder(tmp_path: Path):
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    folders = skill.targets([], False, tmp_path, home)
    assert folders == [home / ".claude/skills", home / ".agents/skills"]
    for folder in folders:
        assert skill.install_into(folder, skill.skill_text(), False).startswith("installed")
        assert (folder / "toolving-conceiver" / "SKILL.md").read_text(encoding="utf-8") == skill.skill_text()
    assert skill.install_into(folders[0], skill.skill_text(), False).startswith("up to date")


def test_project_install_uses_the_project_folders(tmp_path: Path):
    folders = skill.targets(["claude-code", "codex", "pi"], True, tmp_path, tmp_path / "home")
    assert folders == [tmp_path / ".claude/skills", tmp_path / ".agents/skills", tmp_path / ".pi/skills"]


def test_a_skill_it_did_not_write_is_left_alone_unless_forced(tmp_path: Path):
    target = tmp_path / "toolving-conceiver" / "SKILL.md"
    target.parent.mkdir()
    target.write_text("someone else's skill", encoding="utf-8")
    assert skill.install_into(tmp_path, skill.skill_text(), False).startswith("skipped")
    assert target.read_text(encoding="utf-8") == "someone else's skill"
    assert skill.install_into(tmp_path, skill.skill_text(), True).startswith("installed")


def test_main_installs_into_a_given_folder(tmp_path: Path, capsys):
    assert skill.main(["install", "--dir", str(tmp_path)]) == 0
    assert (tmp_path / "toolving-conceiver" / "SKILL.md").exists()
    assert skill.main(["print"]) == 0
    assert "toolving-conceiver" in capsys.readouterr().out
