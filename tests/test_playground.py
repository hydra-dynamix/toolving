"""The playground's local server: token and Host gates, the Conceiver endpoints,
the session runner, and the sequence assistant (agent and agentctl mocked).

A test against a real Conceiver build runs when TOOLVING_CONCEIVER names the
conceiver crate folder (it needs cargo and rustc)."""

from __future__ import annotations

import json
import os
import subprocess
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest

from toolving.playground import build_handler
from toolving.playground.agent import (
    REPLY_SCHEMA,
    AgentError,
    SequenceAgent,
    SequenceError,
    _stdout_of,
    agentctl_complete,
    compose_prompt,
    load_instructions,
    validate_sequence,
)
from toolving.playground.assistant_config import AGENT, AssistantConfig, Settings
from toolving.playground.conceiver import Conceiver, run_session

TOKEN = "t" * 32


class FakeConceiver:
    """Stands in for the driver: answers what it was taught, abstains otherwise."""

    crate = "/conceiver"

    def __init__(self):
        self.taught: dict[str, str] = {}
        self.calls: list[tuple] = []

    def ask(self, line):
        self.calls.append(("ask", line))
        if line == "boom":
            return {"kind": "fault", "fault": "Limit"}
        if line in self.taught:
            return {"kind": "answer", "output": self.taught[line], "support": 1, "candidates": 1,
                    "steps": [{"kind": "answer", "text": self.taught[line], "support": 1, "against": 0}]}
        return {"kind": "abstain", "candidates": 0, "steps": []}

    def teach(self, line, answer):
        self.calls.append(("teach", line, answer))
        self.taught[line] = answer
        return {"taught": True, "known": False}

    def state(self, newest=50):
        self.calls.append(("state", newest))
        return {"nodes": 10, "log_entries": 2, "opened": 5, "taught": [], "log": []}

    def reset(self):
        self.calls.append(("reset",))
        self.taught.clear()
        return {"reset": True, "nodes": 10}


def model_says(reply, sequence=None):
    return json.dumps({"reply": reply, "sequence": sequence})


@pytest.fixture
def serve():
    servers = []

    def start(**options):
        port = {}
        server = ThreadingHTTPServer(("127.0.0.1", 0), build_handler(TOKEN, port, **options))
        port["port"] = server.server_address[1]
        threading.Thread(target=server.serve_forever, daemon=True).start()
        servers.append(server)
        return f"http://127.0.0.1:{port['port']}"

    yield start
    for server in servers:
        server.shutdown()
        server.server_close()


@pytest.fixture
def no_install(monkeypatch, tmp_path):
    """No agentctl or Claude Code where the installers would put them."""
    for name in ("HOME", "USERPROFILE", "LOCALAPPDATA"):
        monkeypatch.setenv(name, str(tmp_path / "home"))
    return tmp_path / "home"


def offline_config(home, *, which=lambda name: None, run=None):
    def no_run(argv, **options):
        raise AssertionError(f"ran {argv}")
    return AssistantConfig(home, which=which, run=run or no_run)


@pytest.fixture
def playground(serve, no_install, tmp_path):
    """A playground with a stand-in Conceiver and a stand-in model."""
    conceiver, prompts, replies = FakeConceiver(), [], []

    def complete(prompt):
        prompts.append(prompt)
        return replies.pop(0)

    assistant = offline_config(tmp_path / "settings")
    assistant.agent = SequenceAgent(complete, label="stand-in", instructions="INSTRUCTIONS")
    base = serve(conceiver=conceiver, assistant=assistant)
    return SimpleNamespace(base=base, conceiver=conceiver, prompts=prompts, replies=replies)


def call(base, method, path, body=None, *, token=TOKEN, host=None):
    request = urllib.request.Request(base + path, method=method, data=None if body is None else json.dumps(body).encode())
    request.add_header("Content-Type", "application/json")
    if token:
        request.add_header("X-Playground-Token", token)
    if host:
        request.add_header("Host", host)
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as failure:
        return failure.code, json.loads(failure.read())


# --- The server ----------------------------------------------------------------------

def test_both_pages_and_their_assets_are_served(playground):
    for path in ("/", "/inspect", "/common.js", "/app.js", "/inspect.js", "/style.css"):
        with urllib.request.urlopen(playground.base + path, timeout=5) as response:
            assert response.status == 200
    with urllib.request.urlopen(playground.base + "/", timeout=5) as response:
        page = response.read().decode()
    assert 'id="assistant"' in page and 'id="sequence-input"' in page
    with urllib.request.urlopen(playground.base + "/inspect", timeout=5) as response:
        assert 'id="log"' in response.read().decode()


def test_calls_need_the_token_and_a_loopback_host(playground):
    base = playground.base
    assert call(base, "POST", "/api/conceiver/ask", {"line": "x"}, token="")[0] == 403
    assert call(base, "POST", "/api/conceiver/ask", {"line": "x"}, token="wrong")[0] == 403
    assert call(base, "POST", "/api/conceiver/ask", {"line": "x"}, host="evil.example")[0] == 403
    assert call(base, "POST", "/api/agent/chat", {"messages": [{"role": "user", "content": "hi"}]}, token="")[0] == 403
    assert playground.conceiver.calls == [] and playground.prompts == []


def test_session_says_what_is_available(playground, serve, tmp_path):
    status, session = call(playground.base, "GET", "/api/session")
    assert status == 200
    assert session["conceiver"] == {"available": True, "crate": "/conceiver"}
    assert session["assistant"] == {"available": True, "label": "stand-in"}

    bare = serve(conceiver_unavailable="no crate", assistant=offline_config(tmp_path / "bare"))
    session = call(bare, "GET", "/api/session")[1]
    assert session["conceiver"] == {"available": False, "reason": "no crate"}
    assert session["assistant"]["available"] is False and "assistant settings" in session["assistant"]["reason"]
    status, error = call(bare, "POST", "/api/conceiver/ask", {"line": "x"})
    assert status == 503 and error["error"] == {"code": "CONCEIVER_UNAVAILABLE", "message": "no crate"}
    status, error = call(bare, "POST", "/api/agent/chat", {"messages": [{"role": "user", "content": "hi"}]})
    assert status == 503 and error["error"]["code"] == "AGENT_UNAVAILABLE"


def test_conceiver_calls_go_to_the_driver(playground):
    base, conceiver = playground.base, playground.conceiver
    assert call(base, "POST", "/api/conceiver/ask", {"line": " file parks "})[1]["kind"] == "abstain"
    assert call(base, "POST", "/api/conceiver/teach", {"question": "file parks", "answer": "records/parks"})[1]["taught"]
    assert call(base, "POST", "/api/conceiver/ask", {"line": "file parks"})[1]["output"] == "records/parks"
    assert call(base, "GET", "/api/conceiver/state?newest=7")[1]["nodes"] == 10
    assert call(base, "POST", "/api/conceiver/reset")[1]["reset"]
    assert conceiver.calls == [("ask", "file parks"), ("teach", "file parks", "records/parks"), ("ask", "file parks"),
                               ("state", 7), ("reset",)]


@pytest.mark.parametrize("method, path, body, status", [
    ("POST", "/api/conceiver/ask", {}, 400),
    ("POST", "/api/conceiver/ask", {"line": "  "}, 400),
    ("POST", "/api/conceiver/teach", {"question": "q"}, 400),
    ("POST", "/api/conceiver/session", {"pairs": []}, 400),
    ("POST", "/api/conceiver/session", {"pairs": [{"question": "q\nq", "answer": "a"}]}, 400),
    ("POST", "/api/conceiver/bootstrap", {}, 404),
    ("GET", "/api/learners", None, 404),
])
def test_bad_conceiver_calls_are_refused_before_the_driver(playground, method, path, body, status):
    assert call(playground.base, method, path, body)[0] == status
    assert playground.conceiver.calls == []


def test_a_sequence_is_asked_line_by_line_before_each_line_is_taught(playground):
    pairs = [{"question": "file council minutes 2025", "answer": "records/council/2025"},
             {"question": "file council minutes 2025", "answer": "records/council/2025"},
             {"question": "file council minutes 2025", "answer": "elsewhere"}]
    status, result = call(playground.base, "POST", "/api/conceiver/session", {"pairs": pairs})
    assert status == 200
    assert [row["verdict"] for row in result["rows"]] == ["abstained", "right", "wrong"]
    assert result["tally"] == {"right": 1, "wrong": 1, "abstained": 1, "fault": 0}
    assert playground.conceiver.calls[:2] == [("ask", "file council minutes 2025"),
                                              ("teach", "file council minutes 2025", "records/council/2025")]


def test_the_session_runner_counts_faults_and_unknown_kinds_apart_from_answers():
    conceiver = FakeConceiver()
    conceiver.ask = lambda line: {"kind": "fault", "fault": "Limit"} if line == "boom" else {"kind": "copy-guess", "output": "x"}
    result = run_session(conceiver, [("boom", "a"), ("new", "x")])
    assert [row["verdict"] for row in result["rows"]] == ["fault", "abstained"]
    assert result["rows"][0]["teach_fault"] is None


# --- The assistant -------------------------------------------------------------------

def test_instructions_are_the_packaged_file_and_cover_format_and_learning():
    text = load_instructions()
    assert "question line followed by one answer line" in text
    assert "same number of words" in text and "**Unsure" in text and "`sequence`" in text
    assert SequenceAgent(lambda prompt: "", label="x").instructions == text


def test_validation_accepts_the_training_sequence_format():
    sequence = validate_sequence([
        {"question": "Where do Acme invoices go?", "answer": "Finance / vendors / Acme"},
        {"question": " Where do Globex invoices go? ", "answer": "Finance / vendors / Globex"},
    ])
    assert sequence.text == ("Where do Acme invoices go?\nFinance / vendors / Acme\n\n"
                             "Where do Globex invoices go?\nFinance / vendors / Globex\n")
    assert sequence.to_json()["pairs"][1] == {"question": "Where do Globex invoices go?", "answer": "Finance / vendors / Globex"}


@pytest.mark.parametrize("items, message", [
    ([], "at least one teaching"),
    (None, "at least one teaching"),
    (["Where?"], "Teaching 1 is not a question and answer"),
    ([{"question": "Where?", "answer": " "}], "Teaching 1 has no answer"),
    ([{"question": "q", "answer": "a"}, {"answer": "a"}], "Teaching 2 has no question"),
    ([{"question": "Where?\nAnd why?", "answer": "a"}], "question must be a single line"),
    ([{"question": "Where?", "answer": "line\r\nbreak"}], "answer must be a single line"),
    ([{"question": "Where?", "answer": "a\tb"}], "answer cannot hold a tab"),
])
def test_validation_rejects_what_the_format_cannot_hold(items, message):
    with pytest.raises(SequenceError, match=message):
        validate_sequence(items)


def test_the_prompt_holds_the_instructions_the_turns_and_the_reply_format():
    prompt = compose_prompt("INSTRUCTIONS", [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "{}"},
                                             {"role": "user", "content": "files"}])
    assert prompt.startswith("INSTRUCTIONS")
    assert "<user>\nhi\n</user>\n\n<assistant>\n{}\n</assistant>\n\n<user>\nfiles\n</user>" in prompt
    assert json.dumps(REPLY_SCHEMA) in prompt


def test_conversation_asks_then_proposes_a_valid_sequence(playground):
    base = playground.base
    playground.replies.append(model_says("What lines will Conceiver be given?"))
    first = [{"role": "user", "content": "I want Conceiver to file minutes."}]
    status, turn = call(base, "POST", "/api/agent/chat", {"messages": first})
    assert status == 200 and turn["reply"] == "What lines will Conceiver be given?"
    assert turn["sequence"] is None and turn["sequence_error"] is None

    pairs = [{"question": "file council minutes 2025", "answer": "records/council/2025"},
             {"question": "file finance minutes 2026", "answer": "records/finance/2026"}]
    playground.replies.append("```json\n" + model_says("Here is a sequence.", pairs) + "\n```")
    second = first + [{"role": "assistant", "content": turn["raw"]},
                      {"role": "user", "content": "council minutes go to records/council/<year>."}]
    status, turn = call(base, "POST", "/api/agent/chat", {"messages": second})
    assert status == 200 and turn["sequence"]["pairs"] == pairs
    assert turn["sequence"]["text"].startswith("file council minutes 2025\nrecords/council/2025\n\n")
    assert playground.prompts[-1] == compose_prompt("INSTRUCTIONS", second)


def test_an_invalid_proposal_is_reported_not_offered(playground):
    playground.replies.append(model_says("Here.", [{"question": "Two\nlines", "answer": "a"}]))
    status, turn = call(playground.base, "POST", "/api/agent/chat", {"messages": [{"role": "user", "content": "go"}]})
    assert status == 200 and turn["sequence"] is None and "single line" in turn["sequence_error"]


@pytest.mark.parametrize("messages", [
    None,
    [],
    [{"role": "assistant", "content": "hi"}],
    [{"role": "user", "content": "a"}, {"role": "user", "content": "b"}],
    [{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"}],
    [{"role": "user", "content": "  "}],
])
def test_malformed_conversations_are_refused_before_the_model_is_called(playground, messages):
    status, error = call(playground.base, "POST", "/api/agent/chat", {"messages": messages})
    assert status == 400 and error["error"]["code"] == "INVALID_REQUEST"
    assert playground.prompts == []


@pytest.mark.parametrize("raw", ["not json", '{"sequence": null}', '{"reply": 3, "sequence": null}'])
def test_a_reply_out_of_form_comes_back_as_an_error(playground, raw):
    playground.replies.append(raw)
    status, error = call(playground.base, "POST", "/api/agent/chat", {"messages": [{"role": "user", "content": "go"}]})
    assert status == 502 and error["error"]["code"] == "AGENT_BAD_OUTPUT"


# --- agentctl ------------------------------------------------------------------------

def test_agentctls_raw_log_gives_the_reply():
    assert _stdout_of('stdout:\n{"reply": "hi"}\n\nstderr:\nWarning: sign-in ends soon\n') == '{"reply": "hi"}'
    assert _stdout_of("stdout:\nonly\n") == "only"


def fake_agentctl(monkeypatch, tmp_path, *, exit_code=0, stdout="{}"):
    seen = []
    log = tmp_path / "output.log"
    log.write_text(f"stdout:\n{stdout}\n\nstderr:\nwarning\n", encoding="utf-8")

    def run(argv, **options):
        prompt_file = Path(argv[argv.index("--prompt-file") + 1])
        seen.append((argv, options, prompt_file.read_text(encoding="utf-8")))
        summary = {"command": "claude -p ...", "exit_code": exit_code, "preview_lines": ["it broke"],
                   "raw_log_path": str(log)}
        return subprocess.CompletedProcess(argv, 0, json.dumps([{"iteration": 1, "summary": summary}]), "")

    monkeypatch.setattr(subprocess, "run", run)
    return seen


def test_agentctl_runs_the_prompt_and_returns_what_the_agent_printed(monkeypatch, tmp_path):
    seen = fake_agentctl(monkeypatch, tmp_path, stdout=model_says("hello"))
    assert json.loads(agentctl_complete("agentctl", "claude", config_dir=tmp_path)("PROMPT")) == {"reply": "hello", "sequence": None}
    argv, options, prompt = seen[0]
    assert argv[:3] == ["agentctl", "run", "claude"] and "--json" in argv and prompt == "PROMPT"
    assert options["cwd"] == tmp_path


def test_agentctl_failures_come_back_as_errors(monkeypatch, tmp_path):
    fake_agentctl(monkeypatch, tmp_path, exit_code=1)
    with pytest.raises(AgentError, match="exited with 1: it broke"):
        agentctl_complete("agentctl", "claude")("PROMPT")


# --- Assistant settings ---------------------------------------------------------------

@pytest.mark.parametrize("value, message", [
    ({"model": "claude opus"}, "model"),
    ({"model": "x;rm"}, "model"),
    ({"timeout": "soon"}, "whole number"),
    ({"timeout": 5}, "between"),
    ([], "object"),
])
def test_settings_refuse_what_they_cannot_hold(value, message):
    with pytest.raises(ValueError, match=message):
        Settings.parse(value)


def test_without_agentctl_the_assistant_is_off_and_the_settings_say_how_to_install_it(no_install, tmp_path):
    config = offline_config(tmp_path / "settings")
    assert config.agent is None and "not installed" in config.reason
    state = config.status()
    assert state["available"] is False and state["agentctl"] == {"found": False, "path": None, "version": None}
    install = state["install"]
    commands = [step["command"] for step in install["agentctl"]]
    assert "hydra-dynamix/ldgr-core/main/scripts/install." in commands[0]
    assert commands[1] == "cargo install --git https://github.com/hydra-dynamix/agentctl agentctl"
    assert install["login"] == "claude auth login" and "claude.ai/install" in install["claude"]
    assert not config.config_file.exists()  # nothing written while there is nothing to run


def test_an_agentctl_where_the_installer_puts_it_is_found_without_path(no_install, tmp_path):
    installed = no_install / ".local" / "bin" / ("agentctl.exe" if os.name == "nt" else "agentctl")
    installed.parent.mkdir(parents=True)
    installed.write_text("")
    assert offline_config(tmp_path / "settings").agentctl_path() == str(installed)


def fake_tools(tmp_path):
    """agentctl and claude stand-ins: the paths exist, and their answers are canned."""
    agentctl = tmp_path / "bin" / "agentctl"
    agentctl.parent.mkdir(parents=True, exist_ok=True)
    agentctl.write_text("")
    ran = []

    def run(argv, **options):
        ran.append((argv, options))
        out = {("--version",): "agentctl 0.1.3", ("config", "check"): "ok agentctl.toml",
               ("auth", "status"): json.dumps({"loggedIn": True, "authMethod": "claude.ai",
                                              "loginExpiresAt": "2026-11-28T07:22:13Z", "email": "someone@example.com"})}
        return subprocess.CompletedProcess(argv, 0, out[tuple(argv[1:])], "")

    which = {"agentctl": str(agentctl), "claude": str(tmp_path / "bin" / "claude")}.get
    return agentctl, run, which, ran


def test_with_agentctl_the_settings_report_versions_config_and_sign_in(no_install, tmp_path):
    agentctl, run, which, ran = fake_tools(tmp_path)
    config = offline_config(tmp_path / "settings", which=which, run=run)
    assert config.agent is not None and config.agent.label == "claude via agentctl"
    state = config.status()
    assert state["agentctl"] == {"found": True, "path": str(agentctl), "version": "0.1.3"}
    assert state["config"]["check"] == {"ok": True, "message": "ok agentctl.toml"}
    assert state["claude"] == {"found": True, "path": which("claude"), "signed_in": True, "method": "claude.ai",
                               "expires": "2026-11-28T07:22:13Z"}
    assert "email" not in json.dumps(state)
    assert ([str(agentctl), "config", "check"], tmp_path / "settings") in [(argv, options["cwd"]) for argv, options in ran]


def test_saving_settings_rewrites_the_agentctl_config_and_the_agent(no_install, tmp_path, monkeypatch):
    agentctl, run, which, _ = fake_tools(tmp_path)
    home = tmp_path / "settings"
    config = offline_config(home, which=which, run=run)
    config.apply(Settings.parse({"model": "claude-sonnet-5-5", "timeout": 120}))
    assert json.loads(config.settings_file.read_text())["model"] == "claude-sonnet-5-5"
    toml = config.config_file.read_text()
    assert f"[agents.{AGENT}]" in toml and '["claude", "-p", "--model", "claude-sonnet-5-5"]' in toml
    assert "prompt_stdin = true" in toml
    assert config.agent.label == "claude claude-sonnet-5-5 via agentctl"
    assert offline_config(home, which=which, run=run).settings.model == "claude-sonnet-5-5"  # kept

    seen = fake_agentctl(monkeypatch, tmp_path, stdout=model_says("hi"))
    assert config.agent.reply([{"role": "user", "content": "hello"}])["reply"] == "hi"
    argv, options, _ = seen[0]
    assert argv[:3] == [str(agentctl), "run", AGENT] and options["cwd"] == home and options["timeout"] == 120


def test_an_agentctl_path_that_is_not_there_turns_the_assistant_off(no_install, tmp_path):
    _, run, which, _ = fake_tools(tmp_path)
    config = offline_config(tmp_path / "settings", which=which, run=run)
    config.apply(Settings.parse({"agentctl": str(tmp_path / "missing" / "agentctl")}))
    assert config.agent is None and "not found at" in config.reason


def test_the_settings_endpoint_reads_and_saves(serve, no_install, tmp_path):
    agentctl, run, which, _ = fake_tools(tmp_path)
    base = serve(assistant=offline_config(tmp_path / "settings", which=which, run=run))
    status, state = call(base, "GET", "/api/assistant/settings")
    assert status == 200 and state["available"] and state["settings"] == {"agentctl": "", "model": "", "timeout": 600}
    status, state = call(base, "POST", "/api/assistant/settings", {"agentctl": str(agentctl), "model": "claude-opus-5-5", "timeout": 300})
    assert status == 200 and state["settings"]["model"] == "claude-opus-5-5" and "--model" in state["config"]["text"]
    assert call(base, "GET", "/api/session")[1]["assistant"]["label"] == "claude claude-opus-5-5 via agentctl"
    status, error = call(base, "POST", "/api/assistant/settings", {"model": "two words"})
    assert status == 400 and error["error"]["code"] == "INVALID_SETTINGS"
    assert call(base, "GET", "/api/assistant/settings", token="")[0] == 403


def test_the_settings_widget_is_on_the_page(playground):
    with urllib.request.urlopen(playground.base + "/", timeout=5) as response:
        page = response.read().decode()
    assert 'id="settings"' in page and 'id="setting-model"' in page and 'id="settings-install"' in page


# --- A real Conceiver build ----------------------------------------------------------

@pytest.mark.skipif(not os.environ.get("TOOLVING_CONCEIVER"), reason="set TOOLVING_CONCEIVER to the conceiver crate")
def test_a_real_build_learns_the_filing_pattern(tmp_path):
    conceiver = Conceiver.build(Path(os.environ["TOOLVING_CONCEIVER"]), tmp_path)
    try:
        result = run_session(conceiver, [("file council minutes 2025", "records/council/2025"),
                                         ("file finance minutes 2026", "records/finance/2026")])
        assert result["tally"]["abstained"] == 2
        asked = conceiver.ask("file parks minutes 2027")
        assert (asked["kind"], asked["output"]) == ("answer", "records/parks/2027")
        assert asked["steps"][0]["kind"] == "answer"
        state = conceiver.state(5)
        assert [pair["question"] for pair in state["taught"]] == ["file council minutes 2025", "file finance minutes 2026"]
        assert len(state["log"]) == 5
    finally:
        conceiver.close()
