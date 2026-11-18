"use strict";
// Shared by the playground and the inspector. Both talk only to the local
// toolving-playground process. Every value from it is rendered with
// textContent, never as HTML.

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
    const message = [e.code, e.message].filter(Boolean).join(" — ");
    showError(message || `HTTP ${response.status}`);
    throw new Error(message);
  }
  clearError();
  return data;
}

function showError(message) { $("error").textContent = message; $("error").hidden = false; }
function clearError() { $("error").hidden = true; }

function addMessage(node) {
  $("empty")?.remove();
  $("messages").append(node);
  node.scrollIntoView({ block: "end", behavior: "smooth" });
}

function youSaid(label, text) {
  addMessage(el("div", { class: "msg you" }, el("span", { class: "label" }, label), text));
}

const tally = (item) => `+${item.support} -${item.against}`;

// What Conceiver answered a line, as the driver reports it: kind "answer"
// (output and support), "abstain" or "fault", the steps and evidence the walk
// showed, and where the line's walk ended. Kinds the driver adds later are
// shown by name.
function answerView(asked) {
  const view = el("div", { class: "answer-view" });
  const kind = asked.kind || "unknown";
  view.append(el("span", { class: "badge" }, kind.toUpperCase()));
  if (kind === "answer") {
    view.append(el("div", { class: "output" }, asked.output),
      el("div", { class: "muted" }, asked.support > 0 ? `supported (${asked.support} for)` : "hypothesis: no evidence for it yet"));
  } else if (kind === "abstain") {
    view.append(el("div", { class: "muted" }, asked.candidates > 2
      ? `${asked.candidates} taught answers and no pick between them.`
      : "Nothing taught answers this line."));
  } else if (kind === "fault") {
    view.append(el("div", { class: "muted" }, `The walk faulted: ${asked.fault}.`));
  }
  if (asked.steps?.length) {
    const list = el("ol", { class: "steps" });
    for (const step of asked.steps) {
      list.append(el("li", {}, el("span", { class: "step-kind" }, step.kind), " ",
        el("code", {}, step.text || "—"), " ", el("span", { class: "tally" }, tally(step))));
    }
    view.append(el("details", { open: "" }, el("summary", {}, `Steps and evidence (${asked.steps.length})`), list));
  }
  if (asked.input) {
    const i = asked.input;
    view.append(el("div", { class: "meta" },
      `line ${i.seen_before ? "seen before" : "first time"} · at its place ${tally(i)} · recount ${i.recount_agrees ? "agrees" : "DISAGREES"} · ${i.opened} opened`));
  }
  return view;
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

function wireCommon() {
  applyTheme(currentTheme());
  $("theme").addEventListener("click", () => applyTheme(THEMES[(THEMES.indexOf(currentTheme()) + 1) % THEMES.length]));
  document.querySelectorAll(".tabs button").forEach((button) => button.addEventListener("click", () => {
    document.querySelectorAll(".tabs button").forEach((b) => b.classList.toggle("on", b === button));
    document.querySelectorAll("[data-pane]").forEach((pane) => (pane.hidden = pane.dataset.pane !== button.dataset.tab));
  }));
}

function currentTab() {
  return document.querySelector(".tabs button.on")?.dataset.tab;
}

// Loads the session and says whether Conceiver is on; returns the session or null.
async function startSession() {
  if (!token) { showError("Open the URL printed by toolving-playground (it carries the session token)."); return null; }
  try {
    const session = await api("GET", "session");
    $("session").textContent = session.conceiver.available ? `Conceiver ${session.conceiver.crate}` : "Conceiver off";
    if (!session.conceiver.available) showError(session.conceiver.reason);
    return session;
  } catch { return null; }
}
