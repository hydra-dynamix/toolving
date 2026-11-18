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
  $("assistant-log").replaceChildren(el("p", { class: "muted", id: "assistant-empty" },
    assistantStatus && !assistantStatus.available ? assistantStatus.reason : ASSISTANT_INTRO));
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
