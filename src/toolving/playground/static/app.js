"use strict";
// Talks only to the local toolving-playground process. Every value from the API
// is rendered with textContent, never as HTML.

const token = (() => {
  const match = location.hash.match(/t=([A-Za-z0-9_-]+)/);
  if (match) {
    sessionStorage.setItem("playground-token", match[1]);
    history.replaceState(null, "", location.pathname);
  }
  return sessionStorage.getItem("playground-token") || "";
})();

const $ = (id) => document.getElementById(id);
const el = (tag, attrs = {}, ...children) => {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (key === "class") node.className = value;
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else node.setAttribute(key, value);
  }
  for (const child of children) node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  return node;
};
const show = (value) => (typeof value === "string" ? value : JSON.stringify(value));
let learner = null;

async function api(method, path, body) {
  const started = performance.now();
  const response = await fetch("/api/" + path, {
    method,
    headers: { "Content-Type": "application/json", "X-Playground-Token": token },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await response.json().catch(() => ({ error: { code: "BAD_RESPONSE", message: "Non-JSON response" } }));
  const ms = Math.round(performance.now() - started);
  const entry = el("li", {}, `${method} ${path} · ${response.status} · ${ms} ms`);
  if (!response.ok) {
    entry.className = "bad";
    entry.append(` · ${data.error?.code || ""}`);
  }
  $("activity").prepend(entry);
  if (!response.ok) {
    const e = data.error || {};
    const message = [e.code, e.message, e.reason && `(${e.reason})`, e.request_id && `request ${e.request_id}`].filter(Boolean).join(" — ");
    showError(message || `HTTP ${response.status}`);
    throw new Error(message);
  }
  clearError();
  return data;
}

function showError(message) { $("error").textContent = message; $("error").hidden = false; }
function clearError() { $("error").hidden = true; }

function parseValue(text, format) {
  if (format !== "json") return { value: text, format: "text" };
  try { return { value: JSON.parse(text), format: "json" }; }
  catch { showError("That input is not valid JSON."); return null; }
}

function addMessage(node) {
  $("empty")?.remove();
  $("messages").append(node);
  node.scrollIntoView({ block: "end", behavior: "smooth" });
}

function youSaid(label, text) {
  addMessage(el("div", { class: "msg you" }, el("span", { class: "label" }, label), text));
}

// Corrections report LEARNED or RECORDED; a plain teach reports TAUGHT.
function teachResult(container, result) {
  const text = {
    LEARNED: "✓ Learned.",
    TAUGHT: "✓ Taught.",
    RECORDED: "Recorded, but nothing reusable could be derived yet — try another example that varies what should generalize.",
  }[result.status] || `Status: ${result.status}`;
  container.replaceChildren(el("div", { class: "result " + (result.status === "RECORDED" ? "warn" : "ok") }, text));
}

function predictionCard(input, p) {
  const card = el("div", { class: "msg card " + p.status });
  card.append(el("span", { class: "badge" }, p.status));
  if (p.status === "PREDICTED" || p.status === "CHOSEN") card.append(el("div", { class: "output" }, show(p.output)));
  if (p.status === "ABSTAINED") card.append(el("div", { class: "muted" }, "Nothing learned applies to this input (" + (p.reason || "no reason") + "). Teach it the answer below."));
  if (p.status === "CHOSEN") {
    card.append(el("div", { class: "muted" }, "Memories gave different answers; the learner picked one. Click the right answer to confirm or refute its pick:"));
    const alts = el("div", { class: "alts" });
    for (const alt of p.alternatives) {
      alts.append(el("button", { type: "button", class: JSON.stringify(alt) === JSON.stringify(p.output) ? "pick" : "", onclick: () => correct(alt) }, show(alt)));
    }
    card.append(alts);
  }
  const outcome = el("div");
  const answer = el("input", { placeholder: "The right answer…", "aria-label": "Correct output" });
  const actions = el("div", { class: "actions" });
  if (p.status !== "ABSTAINED") actions.append(el("button", { type: "button", title: "Teach this output as correct", onclick: () => correct(p.output) }, "✓ Right"));
  actions.append(answer, el("button", { type: "button", onclick: () => answer.value && correct(parseAnswer(answer.value)) }, p.status === "ABSTAINED" ? "Teach" : "Correct"));
  card.append(actions, outcome);
  if (p.evidence?.markdown) {
    card.append(el("details", {}, el("summary", {}, `Related memories (${p.evidence.memory_ids?.length || 0})`), el("pre", { class: "evidence" }, p.evidence.markdown)));
  }
  card.append(el("div", { class: "meta" }, `prediction ${p.prediction_id} · state v${p.state_version}`));
  answer.addEventListener("keydown", (event) => { if (event.key === "Enter" && answer.value) correct(parseAnswer(answer.value)); });

  function parseAnswer(text) {
    if (typeof input === "string") return text;
    try { return JSON.parse(text); } catch { return text; }
  }
  async function correct(output) {
    const result = await api("POST", `learners/${learner}/predictions/${p.prediction_id}/teach`, { output });
    actions.querySelectorAll("button,input").forEach((node) => (node.disabled = true));
    card.querySelectorAll(".alts button").forEach((node) => (node.disabled = true));
    teachResult(outcome, result);
    outcome.prepend(el("div", { class: "meta" }, `taught → ${show(output)}`));
  }
  return card;
}

async function predict() {
  if (!learner) return showError("Create or pick a learner first.");
  const raw = $("ask-input").value.trim();
  if (!raw) return;
  const parsed = parseValue(raw, $("ask-format").value);
  if (!parsed) return;
  youSaid("predict", raw);
  $("ask-input").value = "";
  const p = await api("POST", `learners/${learner}/predict`, { input: parsed.value, format: parsed.format, evidence: $("ask-evidence").value });
  addMessage(predictionCard(parsed.value, p));
}

async function teach() {
  if (!learner) return showError("Create or pick a learner first.");
  const input = $("teach-input").value.trim();
  const output = $("teach-output").value.trim();
  if (!input || !output) return;
  youSaid("teach", `${input}  →  ${output}`);
  const result = await api("POST", `learners/${learner}/teach`, { input, output, format: "text" });
  const box = el("div", { class: "msg card" });
  teachResult(box, result);
  addMessage(box);
  $("teach-input").value = "";
  $("teach-output").value = "";
  $("teach-input").focus();
}

async function batch() {
  if (!learner) return showError("Create or pick a learner first.");
  const lines = $("batch-input").value.split("\n").map((line) => line.trim()).filter(Boolean);
  if (!lines.length) return;
  if (lines.length % 2) return showError("Batch needs an even number of lines: input, output, input, output…");
  const examples = [];
  for (let i = 0; i < lines.length; i += 2) examples.push({ input: lines[i], output: lines[i + 1], role: "target" });
  youSaid(`train ${examples.length} examples`, examples.map((e) => `${e.input}  →  ${e.output}`).join("\n"));
  const result = await api("POST", `learners/${learner}/training`, { examples });
  addMessage(el("div", { class: "msg card" }, el("div", { class: "result ok" }, `✓ Trained (${result.status}).`)));
  $("batch-input").value = "";
}

async function loadLearners(select) {
  const { learners } = await api("GET", "learners");
  const picker = $("learner");
  picker.replaceChildren(...learners.map((item) => el("option", { value: item.learner }, item.learner)));
  const wanted = select || localStorage.getItem("playground-learner");
  const chosen = learners.find((item) => item.learner === wanted) || learners[0];
  if (chosen) {
    picker.value = chosen.learner;
    learner = chosen.learner;
    $("mode").value = chosen.mode;
    localStorage.setItem("playground-learner", learner);
  } else {
    learner = null;
  }
}

async function newLearner() {
  const name = prompt("Name for the new learner (letters, digits, . _ -):");
  if (!name) return;
  await api("POST", "learners", { name });
  await loadLearners(name);
  addMessage(el("div", { class: "msg card" }, el("div", { class: "result ok" }, `Created learner ${name}. Teach it a couple of examples to begin.`)));
}

async function forkLearner() {
  if (!learner) return;
  const name = prompt(`Fork "${learner}" as:`, `${learner}-copy`);
  if (!name) return;
  await api("POST", `learners/${learner}/forks`, { name });
  await loadLearners(name);
  addMessage(el("div", { class: "msg card" }, el("div", { class: "result ok" }, `Forked into ${name}; the original is unchanged.`)));
}

async function setMode() {
  if (!learner) return;
  await api("PUT", `learners/${learner}/mode`, { mode: $("mode").value });
}

async function remember() {
  const raw = $("remember-input").value.trim();
  if (!learner || !raw) return;
  let event = raw;
  try { event = JSON.parse(raw); } catch { /* plain text */ }
  const result = await api("POST", `learners/${learner}/remember`, { event });
  $("remember-input").value = "";
  $("recall-output").textContent = result.evidence?.markdown || `Remembered ${result.memory_id}`;
  $("recall-output").hidden = false;
}

async function recall() {
  const raw = $("recall-input").value.trim();
  if (!learner || !raw) return;
  let cue = raw;
  try { cue = JSON.parse(raw); } catch { /* plain text */ }
  const result = await api("POST", `learners/${learner}/recall`, { cue, mode: $("recall-mode").value });
  $("recall-output").textContent = result.evidence?.markdown || "(nothing)";
  $("recall-output").hidden = false;
}

async function maintain() {
  if (!learner) return;
  const result = await api("POST", `learners/${learner}/memory/maintain`, {});
  $("recall-output").textContent = `Memory maintenance done.`;
  $("recall-output").hidden = false;
}

// Theme: System (follows the OS) → Dark → Light, remembered per browser.
const THEMES = ["system", "dark", "light"];
const THEME_LABELS = { system: "◐ System", dark: "● Dark", light: "○ Light" };
function applyTheme(theme) {
  if (theme === "system") delete document.documentElement.dataset.theme;
  else document.documentElement.dataset.theme = theme;
  $("theme").textContent = THEME_LABELS[theme];
  try { localStorage.setItem("playground-theme", theme); } catch { /* storage blocked: still applies now */ }
}
function currentTheme() {
  try { return THEMES.includes(localStorage.getItem("playground-theme")) ? localStorage.getItem("playground-theme") : "system"; }
  catch { return "system"; }
}

function wire() {
  applyTheme(currentTheme());
  $("theme").addEventListener("click", () => applyTheme(THEMES[(THEMES.indexOf(currentTheme()) + 1) % THEMES.length]));
  let tab = "ask";
  document.querySelectorAll(".tabs button").forEach((button) => button.addEventListener("click", () => {
    tab = button.dataset.tab;
    document.querySelectorAll(".tabs button").forEach((b) => b.classList.toggle("on", b === button));
    document.querySelectorAll("[data-pane]").forEach((pane) => (pane.hidden = pane.dataset.pane !== tab));
  }));
  $("composer").addEventListener("submit", (event) => {
    event.preventDefault();
    const run = { ask: predict, teach, batch }[tab];
    run().catch(() => {});
  });
  $("ask-input").addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); predict().catch(() => {}); }
  });
  $("teach-output").addEventListener("keydown", (event) => { if (event.key === "Enter") { event.preventDefault(); teach().catch(() => {}); } });
  $("learner").addEventListener("change", () => loadLearners($("learner").value).catch(() => {}));
  $("new-learner").addEventListener("click", () => newLearner().catch(() => {}));
  $("fork-learner").addEventListener("click", () => forkLearner().catch(() => {}));
  $("mode").addEventListener("change", () => setMode().catch(() => {}));
  $("remember").addEventListener("click", () => remember().catch(() => {}));
  $("recall").addEventListener("click", () => recall().catch(() => {}));
  $("maintain").addEventListener("click", () => maintain().catch(() => {}));
}

(async () => {
  wire();
  if (!token) return showError("Open the URL printed by toolving-playground (it carries the session token).");
  try {
    const session = await api("GET", "session");
    $("session").textContent = `${session.base_url} · key ${session.key_id}`;
    await loadLearners();
  } catch { /* shown */ }
})();
