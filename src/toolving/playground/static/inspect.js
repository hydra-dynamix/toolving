"use strict";
// The inspector: ask a line and read Conceiver's answer with its steps and
// evidence, teach a correction, and read what the loaded trie holds. Shared
// helpers are in common.js.

function teachResult(container, result, answer) {
  container.replaceChildren(
    el("div", { class: "meta" }, `taught → ${answer}`),
    el("div", { class: "result " + (result.taught ? "ok" : "warn") },
      result.taught ? `✓ Taught (${result.known ? "a known outcome" : "a new outcome"}).` : `Teaching faulted: ${result.fault}`));
}

function answerCard(line, asked) {
  const card = el("div", { class: "msg card " + (asked.kind || "") });
  card.append(answerView(asked));
  const outcome = el("div");
  const typed = el("input", { placeholder: "The right answer…", "aria-label": "Correct answer" });
  const actions = el("div", { class: "actions" });
  if (asked.kind === "answer") actions.append(el("button", { type: "button", title: "Teach this answer as right", onclick: () => correct(asked.output) }, "✓ Right"));
  actions.append(typed, el("button", { type: "button", onclick: () => typed.value.trim() && correct(typed.value.trim()) }, asked.kind === "answer" ? "Correct" : "Teach"));
  typed.addEventListener("keydown", (event) => { if (event.key === "Enter" && typed.value.trim()) correct(typed.value.trim()); });
  card.append(actions, outcome);

  async function correct(answer) {
    const result = await api("POST", "conceiver/teach", { question: line, answer });
    actions.querySelectorAll("button,input").forEach((node) => (node.disabled = true));
    teachResult(outcome, result, answer);
    refresh().catch(() => {});
  }
  return card;
}

async function ask() {
  const line = $("ask-input").value.trim();
  if (!line) return;
  youSaid("ask", line);
  $("ask-input").value = "";
  addMessage(answerCard(line, await api("POST", "conceiver/ask", { line })));
  refresh().catch(() => {});
}

async function teach() {
  const question = $("teach-input").value.trim();
  const answer = $("teach-output").value.trim();
  if (!question || !answer) return;
  youSaid("teach", `${question}  →  ${answer}`);
  const box = el("div", { class: "msg card" });
  teachResult(box, await api("POST", "conceiver/teach", { question, answer }), answer);
  addMessage(box);
  $("teach-input").value = "";
  $("teach-output").value = "";
  $("teach-input").focus();
  refresh().catch(() => {});
}

async function refresh() {
  const state = await api("GET", `conceiver/state?newest=${$("newest").value}`);
  $("trie-counts").replaceChildren(
    el("dt", {}, "nodes"), el("dd", {}, state.nodes.toLocaleString()),
    el("dt", {}, "log entries"), el("dd", {}, state.log_entries.toLocaleString()),
    el("dt", {}, "places opened"), el("dd", {}, state.opened.toLocaleString()));
  $("taught").replaceChildren(...(state.taught.length
    ? state.taught.map((pair) => el("li", {}, el("code", {}, pair.question), " → ", el("code", {}, pair.answer)))
    : [el("li", { class: "muted" }, "nothing yet")]));
  $("log").tBodies[0].replaceChildren(...state.log.map((entry) => el("tr", { class: entry.side },
    el("td", {}, entry.place), el("td", {}, entry.side), el("td", {}, entry.episode), el("td", {}, el("code", {}, entry.at)))));
}

async function resetTrie() {
  if (!confirm("Load a fresh trie? Everything taught since the playground started is dropped, in the playground too.")) return;
  const result = await api("POST", "conceiver/reset");
  addMessage(el("div", { class: "msg card" }, el("div", { class: "result ok" }, `Fresh trie loaded (${result.nodes} nodes).`)));
  await refresh();
}

(async () => {
  wireCommon();
  $("composer").addEventListener("submit", (event) => {
    event.preventDefault();
    ({ ask, teach }[currentTab()])().catch(() => {});
  });
  $("ask-input").addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); ask().catch(() => {}); }
  });
  $("teach-output").addEventListener("keydown", (event) => { if (event.key === "Enter") { event.preventDefault(); teach().catch(() => {}); } });
  $("refresh").addEventListener("click", () => refresh().catch(() => {}));
  $("newest").addEventListener("change", () => refresh().catch(() => {}));
  $("reset").addEventListener("click", () => resetTrie().catch(() => {}));
  const session = await startSession();
  if (session?.conceiver.available) await refresh().catch(() => {});
})();
