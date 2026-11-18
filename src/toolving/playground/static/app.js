"use strict";
// The playground page: a sequence editor taught to Conceiver, probes, and the
// sequence assistant. Shared helpers are in common.js.

// The training-sequence format: a question line, its answer line, and one
// empty line between teachings.
function parseSequence(text) {
  const blocks = text.replace(/\r/g, "").split(/\n\s*\n/).map((block) => block.trim()).filter(Boolean);
  const pairs = [];
  for (const [index, block] of blocks.entries()) {
    const lines = block.split("\n").map((line) => line.trim());
    if (lines.length !== 2) {
      throw new Error(`Teaching ${index + 1} has ${lines.length} line${lines.length === 1 ? "" : "s"}; each teaching is a question line and an answer line, with an empty line between teachings.`);
    }
    pairs.push({ question: lines[0], answer: lines[1] });
  }
  return pairs;
}

function sessionCard(result) {
  const { right, wrong, abstained, fault } = result.tally;
  const card = el("div", { class: "msg card session" },
    el("span", { class: "label" }, `Taught ${result.rows.length} line${result.rows.length === 1 ? "" : "s"} · asked first: ${right} right, ${wrong} wrong, ${abstained} abstained${fault ? `, ${fault} faulted` : ""}`));
  for (const row of result.rows) {
    const asked = row.asked;
    const got = asked.kind === "answer" ? asked.output : asked.kind;
    const item = el("details", { class: "session-row " + row.verdict },
      el("summary", {}, el("span", { class: "verdict" }, row.verdict), " ", el("code", {}, row.question), " → ",
        el("code", {}, row.answer), el("span", { class: "muted" }, `  (before: ${got})`)),
      answerView(asked));
    if (row.teach_fault) item.append(el("div", { class: "result warn" }, `Teaching faulted: ${row.teach_fault}`));
    card.append(item);
  }
  return card;
}

async function teachSequence() {
  let pairs;
  try { pairs = parseSequence($("sequence-input").value); }
  catch (failure) { return showError(failure.message); }
  if (!pairs.length) return;
  youSaid(`teach ${pairs.length} line${pairs.length === 1 ? "" : "s"}`, pairs.map((p) => `${p.question}  →  ${p.answer}`).join("\n"));
  addMessage(sessionCard(await api("POST", "conceiver/session", { pairs })));
}

async function ask() {
  const line = $("ask-input").value.trim();
  if (!line) return;
  youSaid("ask", line);
  $("ask-input").value = "";
  addMessage(el("div", { class: "msg card" }, answerView(await api("POST", "conceiver/ask", { line }))));
}

async function resetTrie() {
  if (!confirm("Load a fresh trie? Everything taught since the playground started is dropped.")) return;
  const result = await api("POST", "conceiver/reset");
  addMessage(el("div", { class: "msg card" }, el("div", { class: "result ok" }, `Fresh trie loaded (${result.nodes} nodes).`)));
}

// Sequence assistant: an LLM chat that asks about the user's problem and proposes a
// training sequence. The page holds the conversation and sends it whole each turn;
// assistant turns go back as the JSON the model returned.
let conversation = [];
let assistantStatus = null;  // from /api/session
const ASSISTANT_INTRO = "Describe what you want Conceiver to answer. The assistant asks about your problem, then proposes a training sequence you can load into the editor.";

function assistantSays(node) {
  $("assistant-empty")?.remove();
  $("assistant-log").append(node);
  node.scrollIntoView({ block: "end", behavior: "smooth" });
}

// A proposed sequence, checked by the server against the training-sequence format.
function proposalCard(sequence) {
  const count = sequence.pairs.length;
  const load = el("button", { type: "button", class: "primary", onclick: () => {
    $("sequence-input").value = sequence.text;
    document.querySelector('.tabs button[data-tab="sequence"]').click();
    $("sequence-input").focus();
    load.textContent = "✓ Loaded into the editor";
  } }, "Load into the editor");
  return el("div", { class: "msg card proposal" },
    el("span", { class: "label" }, `Proposed training sequence · ${count} teaching${count === 1 ? "" : "s"}`),
    el("pre", { class: "evidence" }, sequence.text),
    el("div", { class: "actions" }, load));
}

async function askAssistant() {
  const text = $("assistant-input").value.trim();
  if (!text) return;
  assistantSays(el("div", { class: "msg you" }, text));
  $("assistant-input").value = "";
  $("assistant-send").disabled = true;
  const pending = el("div", { class: "muted" }, "Thinking… (a reply through agentctl takes a while)");
  assistantSays(pending);
  const turn = [...conversation, { role: "user", content: text }];
  try {
    const result = await api("POST", "agent/chat", { messages: turn });
    conversation = [...turn, { role: "assistant", content: result.raw }];
    pending.replaceWith(el("div", { class: "msg agent" }, result.reply));
    if (result.sequence) assistantSays(proposalCard(result.sequence));
    if (result.sequence_error) {
      assistantSays(el("div", { class: "msg card proposal" }, el("div", { class: "result bad" },
        `The proposed sequence is not a valid training sequence (${result.sequence_error}). Ask the assistant to fix it.`)));
    }
  } catch {
    pending.remove();
    $("assistant-input").value = text;  // nothing was added to the conversation; send it again
  } finally {
    $("assistant-send").disabled = !assistantStatus?.available;
  }
}

function resetAssistant() {
  conversation = [];
  const off = assistantStatus && !assistantStatus.available;
  const intro = el("div", { id: "assistant-empty" }, el("p", { class: "muted" }, off ? assistantStatus.reason : ASSISTANT_INTRO));
  if (off) intro.append(el("button", { type: "button", class: "primary", onclick: () => openSettings().catch(() => {}) }, "Open assistant settings"));
  $("assistant-log").replaceChildren(intro);
}

// Assistant settings: agentctl (found, version, how to install), Claude Code and
// its sign-in, and the settings the assistant runs with.
function commandBlock(step) {
  const copy = el("button", { type: "button", onclick: () => {
    navigator.clipboard?.writeText(step.command).then(() => (copy.textContent = "Copied"), () => {});
  } }, "Copy");
  return el("div", { class: "install-step" },
    el("div", { class: "meta" }, step.shell), el("pre", { class: "evidence" }, step.command),
    el("div", { class: "row" }, el("small", { class: "muted" }, step.note || ""), copy));
}

function showSettings(state) {
  const yes = (ok, text) => el("dd", { class: ok ? "ok" : "bad" }, (ok ? "✓ " : "✗ ") + text);
  const { agentctl, claude, config } = state;
  const rows = [
    el("dt", {}, "agentctl"), yes(agentctl.found, agentctl.found ? `${agentctl.path} (${agentctl.version || "version unknown"})` : "not found"),
    el("dt", {}, "agentctl config"), config.check ? yes(config.check.ok, config.check.message || config.path) : el("dd", { class: "muted" }, config.path),
    el("dt", {}, "Claude Code"), yes(claude.found, claude.found ? claude.path : "not found"),
  ];
  if (claude.found) {
    rows.push(el("dt", {}, "Claude sign-in"), yes(claude.signed_in, claude.signed_in
      ? `${claude.method || "signed in"}${claude.expires ? ` · expires ${new Date(claude.expires).toLocaleDateString()}` : ""}`
      : "not signed in"));
  }
  rows.push(el("dt", {}, "Assistant"), yes(state.available, state.available ? "ready" : state.reason));
  $("settings-status").replaceChildren(...rows);
  $("settings-install").hidden = agentctl.found;
  $("install-agentctl").replaceChildren(...state.install.agentctl.map(commandBlock));
  $("settings-claude").hidden = claude.found && claude.signed_in;
  $("install-claude").replaceChildren(...(claude.found
    ? [el("p", { class: "muted small" }, "Sign in to Claude Code in a terminal:"), commandBlock({ shell: "terminal", command: state.install.login })]
    : [el("p", { class: "muted small" }, "agentctl runs Claude Code (claude -p). Install it, then sign in with claude auth login:"),
      commandBlock({ shell: state.install.platform === "windows" ? "PowerShell" : "sh", command: state.install.claude })]));
  $("setting-agentctl").value = state.settings.agentctl;
  $("setting-model").value = state.settings.model;
  $("setting-timeout").value = state.settings.timeout;
  $("settings-config").textContent = config.text;
  showAssistantStatus(state.available ? { available: true, label: assistantLabel(state.settings) } : { available: false, reason: state.reason });
}

const assistantLabel = (settings) => `claude${settings.model ? " " + settings.model : ""} via agentctl`;

async function openSettings() {
  if (!$("settings").open) $("settings").showModal();
  showSettings(await api("GET", "assistant/settings"));
}

async function saveSettings() {
  $("settings-error").hidden = true;
  try {
    showSettings(await api("POST", "assistant/settings", {
      agentctl: $("setting-agentctl").value.trim(), model: $("setting-model").value.trim(), timeout: Number($("setting-timeout").value),
    }));
  } catch (failure) {
    $("settings-error").textContent = failure.message;
    $("settings-error").hidden = false;
  }
}

function toggleAssistant(open) {
  $("assistant").hidden = !open;
  document.body.classList.toggle("assistant-open", open);
  $("assistant-toggle").setAttribute("aria-expanded", String(open));
  if (open) $("assistant-input").focus();
}

function showAssistantStatus(assistant) {
  assistantStatus = assistant;
  $("assistant-model").textContent = assistant.available ? assistant.label : "";
  $("assistant-input").disabled = !assistant.available;
  $("assistant-send").disabled = !assistant.available;
  resetAssistant();
}

function wire() {
  wireCommon();
  resetAssistant();
  $("composer").addEventListener("submit", (event) => {
    event.preventDefault();
    ({ sequence: teachSequence, ask }[currentTab()])().catch(() => {});
  });
  $("ask-input").addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); ask().catch(() => {}); }
  });
  $("reset").addEventListener("click", () => resetTrie().catch(() => {}));
  $("assistant-toggle").addEventListener("click", () => toggleAssistant($("assistant").hidden));
  $("assistant-close").addEventListener("click", () => toggleAssistant(false));
  $("assistant-reset").addEventListener("click", resetAssistant);
  $("settings-open").addEventListener("click", () => openSettings().catch(() => {}));
  $("settings-check").addEventListener("click", () => openSettings().catch(() => {}));
  $("settings-form").addEventListener("submit", (event) => { event.preventDefault(); saveSettings().catch(() => {}); });
  $("assistant-form").addEventListener("submit", (event) => { event.preventDefault(); askAssistant(); });
  $("assistant-input").addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); askAssistant(); }
  });
}

(async () => {
  wire();
  const session = await startSession();
  if (session) showAssistantStatus(session.assistant);
})();
