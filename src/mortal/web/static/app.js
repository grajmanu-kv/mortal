"use strict";
/* Mortal web UI: vanilla JS, no build step. All dynamic text goes through textContent;
   the only innerHTML is the ASCII scene, whose characters are escaped. */

const $ = (id) => document.getElementById(id);
const TRAITS = [
  ["openness", "Openness (curious)"], ["conscientiousness", "Conscientiousness (orderly)"],
  ["extraversion", "Extraversion (sociable)"], ["agreeableness", "Agreeableness (warm)"],
  ["neuroticism", "Neuroticism (anxious)"],
];
const NEED_ROWS = [
  ["energy", false], ["hunger", true], ["social", false], ["fun", false], ["health", false], ["mood", false],
];
const WEATHER_ICON = { sunny: "(o)", cloudy: "(~)", rain: "(//)", fog: "(==)", storm: "(!!)" };
const CUSTOM = "__custom__";

const app = { meta: null, lifeId: null, source: null, lastSeq: 0, lastScene: "", godKey: "", llmKey: "", running: false, alive: true };

/* ---------------------------------------------------------------- helpers */
async function api(method, path, body) {
  const res = await fetch(path, {
    method, headers: body ? { "Content-Type": "application/json" } : undefined, body: body ? JSON.stringify(body) : undefined,
  });
  if (res.status === 204) return null;
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const d = data.detail;
    const msg = Array.isArray(d) ? d.map((x) => `${(x.loc || []).slice(1).join(".")}: ${x.msg}`).join("; ") : d || res.statusText;
    throw new Error(msg);
  }
  return data;
}
const esc = (c) => (c === "<" ? "&lt;" : c === ">" ? "&gt;" : c === "&" ? "&amp;" : c);
function el(tag, cls, text) { const e = document.createElement(tag); if (cls) e.className = cls; if (text !== undefined) e.textContent = text; return e; }
function fillSelect(sel, options, value) {
  sel.replaceChildren(...options.map(([v, label]) => { const o = el("option", "", label); o.value = v; return o; }));
  if (value !== undefined) sel.value = value;
}
const lines = (text) => text.split("\n").map((s) => s.trim()).filter(Boolean);
const goodClass = (good) => (good >= 60 ? "good" : good >= 30 ? "mid" : "low");

/* ---------------------------------------------------------------- shared form pieces */
function moodOptions() { return [...app.meta.moods.map((m) => [m, m]), [CUSTOM, "custom..."]]; }
function wireMood(select, customWrap, customInput) {
  fillSelect(select, moodOptions(), app.meta.moods[0]);
  select.addEventListener("change", () => { customWrap.hidden = select.value !== CUSTOM; if (!customWrap.hidden) customInput.focus(); });
}
function readMood(select, customInput) {
  return select.value === CUSTOM ? customInput.value.trim() || "neutral" : select.value;
}
function setMood(select, customWrap, customInput, mood) {
  if (app.meta.moods.includes(mood)) { select.value = mood; customWrap.hidden = true; }
  else { select.value = CUSTOM; customWrap.hidden = false; customInput.value = mood; }
}
function providerOptions(withAuto) {
  const opts = app.meta.providers.map((p) => [p.id, p.label + (p.available ? "" : " (not configured)")]);
  return withAuto ? [["auto", "Auto (best available)"], ...opts] : [["auto", "Auto (best available)"], ...opts];
}

/* ---------------------------------------------------------------- routing */
function route() {
  const m = location.hash.match(/^#\/life\/([a-f0-9]{8})$/);
  if (m) showLife(m[1]); else showHome();
}
function setChrome({ life }) {
  ["bar-life", "bar-controls"].forEach((id) => ($(id).hidden = !life));
  $("conn").hidden = !life;
  $("home").hidden = life;
  $("life").hidden = !life;
}

/* ---------------------------------------------------------------- home */
async function showHome() {
  closeStream();
  setChrome({ life: false });
  document.body.dataset.phase = "morning";
  await loadLives();
}
async function loadLives() {
  const lives = await api("GET", "/api/lives");
  const ul = $("lives");
  ul.replaceChildren();
  $("lives-empty").hidden = lives.length > 0;
  for (const l of lives) {
    const li = el("li");
    const meta = el("div", "meta");
    meta.append(el("div", "", `${l.name}, ${l.age}`), el("small", "", `day ${l.day} · demi-god: ${l.mood}`));
    const badge = el("span", "badge " + (!l.alive ? "dead" : l.running ? "live" : ""), !l.alive ? "dead" : l.running ? "live" : "paused");
    const open = el("a", "btn", "open"); open.href = `#/life/${l.id}`;
    li.append(meta, badge, open);
    ul.append(li);
  }
}
function initCreateForm() {
  const sliders = $("trait-sliders");
  for (const [key, label] of TRAITS) {
    const row = el("div", "trait");
    const input = el("input"); input.type = "range"; input.min = 0; input.max = 10; input.value = 5; input.name = "trait-" + key;
    const out = el("span", "", "5");
    input.addEventListener("input", () => (out.textContent = input.value));
    row.append(el("span", "", label), input, out);
    sliders.append(row);
  }
  wireMood($("create-mood"), $("create-mood-custom-wrap"), $("create-mood-custom"));
  const prov = $("create-provider");
  fillSelect(prov, providerOptions(), "auto");
  const note = () => {
    const p = app.meta.providers.find((x) => x.id === prov.value);
    $("provider-note").textContent = p ? `${p.note}${p.model ? " Model: " + p.model + "." : ""}` : "Uses Gemini if GEMINI_API_KEY is set, else a configured local endpoint, else the offline demi-god.";
  };
  prov.addEventListener("change", note); note();

  $("create-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const f = ev.target, v = (n) => f.elements[n].value;
    $("create-error").textContent = "";
    const traits = {};
    for (const [key] of TRAITS) traits[key] = Number(f.elements["trait-" + key].value);
    const npcs = lines(v("npcs")).map((line) => {
      const [name, role, ...note] = line.split("|").map((s) => s.trim());
      return { name, role: role || "acquaintance", note: note.join(" | ") };
    }).filter((n) => n.name);
    const spec = {
      person: { name: v("name").trim(), age: Number(v("age")) || 0, occupation: v("occupation").trim(), description: v("description").trim(), traits, wage_per_hour: Number(v("wage")) || 0 },
      npcs, god: { mood: readMood($("create-mood"), $("create-mood-custom")), objectives: lines(v("objectives")) },
      llm: { provider: v("provider") }, seed: v("seed") === "" ? null : Number(v("seed")),
    };
    try {
      const life = await api("POST", "/api/lives", spec);
      location.hash = `#/life/${life.id}`;
    } catch (e) { $("create-error").textContent = e.message; }
  });
}

/* ---------------------------------------------------------------- life view */
async function showLife(id) {
  if (app.lifeId === id && app.source) return;
  closeStream();
  app.lifeId = id; app.lastSeq = 0; app.lastScene = ""; app.godKey = ""; app.llmKey = "";
  $("log").replaceChildren();
  setChrome({ life: true });
  $("export").href = `/api/lives/${id}/export`;
  setConn("connecting...", "");
  const src = new EventSource(`/api/lives/${id}/stream`);
  app.source = src;
  src.onopen = () => setConn("live", "ok");
  src.onerror = () => setConn("reconnecting...", "bad");
  src.onmessage = (ev) => render(JSON.parse(ev.data));
}
function closeStream() { if (app.source) { app.source.close(); app.source = null; } app.lifeId = null; }
function setConn(text, cls) { const c = $("conn"); c.textContent = text; c.className = "chip dim conn " + cls; }

function sceneClass(ch) {
  if (ch === "@") return "me"; if (ch === "X") return "dead";
  if (ch >= "a" && ch <= "z") return "npc"; if (ch >= "A" && ch <= "Z") return "label";
  switch (ch) {
    case "~": return "water"; case "*": return "tree"; case '"': return "grass"; case ",": return "grass2";
    case ".": return "road"; case "=": return "door"; case "+": case "-": case "|": return "wall"; default: return "";
  }
}
function sceneHtml(rows) {
  let out = "";
  for (const row of rows) {
    let cur = null, buf = "";
    const flush = () => { if (buf) out += cur ? `<span class="${cur}">${buf}</span>` : buf; buf = ""; };
    for (const ch of row) { const c = sceneClass(ch); if (c !== cur) { flush(); cur = c; } buf += esc(ch); }
    flush(); out += "\n";
  }
  return out;
}

function render(f) {
  app.alive = f.person.alive;
  document.body.dataset.phase = f.clock.phase;
  $("clock").textContent = `Day ${f.clock.day} · ${f.clock.weekday} ${f.clock.time}`;
  $("weather").textContent = `${WEATHER_ICON[f.clock.weather] || ""} ${f.clock.weather}`;
  const th = $("thinking");
  th.hidden = !f.runtime.thinking;
  th.textContent = f.runtime.thinking ? `the demi-god contemplates (${f.runtime.thinking})` : "";

  const key = f.scene.join("\n");
  if (key !== app.lastScene) { app.lastScene = key; $("scene").innerHTML = sceneHtml(f.scene); }
  $("where").textContent = `· ${f.person.location}`;
  renderLegend(f);
  renderPerson(f);
  renderPeople(f.people);
  renderControls(f);
  syncDemigod(f);
  appendLog(f.log);
}

function renderLegend(f) {
  const lg = $("legend");
  lg.replaceChildren();
  const add = (cls, glyph, text) => { const s = el("span", cls); s.append(el("b", "", glyph), " " + text); lg.append(s); };
  add("", "@", f.person.name);
  for (const p of f.people) add("npc", p.glyph, p.name);
  add("", "=", "door"); add("", "~", "river");
}

function renderPerson(f) {
  const p = f.person;
  const head = $("person-head");
  head.replaceChildren(document.createTextNode(p.name), el("small", "", `${p.age}${p.occupation ? " · " + p.occupation : ""}`));
  $("activity").textContent = p.alive ? (p.activity ? `${p.name} ${p.kind === "travel" ? "is " : ""}${p.activity}` : "") : `Dead ${p.cause_of_death || ""}.`;
  $("thought").textContent = p.thought ? `"${p.thought}"` : "";
  const box = $("needs");
  box.replaceChildren();
  for (const [name, inverse] of NEED_ROWS) {
    const v = p.needs[name], good = inverse ? 100 - v : v;
    const n = Math.max(0, Math.min(10, Math.round(v / 10)));
    box.append(el("span", "n-name", name), el("span", "bar-ascii " + goodClass(good), `[${"#".repeat(n)}${"-".repeat(10 - n)}]`), el("span", "n-val", String(v)));
  }
  const money = p.needs.money;
  box.append(el("span", "n-name", "money"), el("span", money < 0 ? "bar-ascii low" : "bar-ascii", `${money < 0 ? "-" : ""}$${Math.abs(money)}`), el("span"));
  $("summary-box").hidden = !p.summary;
  $("summary").textContent = p.summary || "";
}

function renderPeople(people) {
  const ul = $("people");
  ul.replaceChildren();
  if (!people.length) { ul.append(el("li", "dim", "nobody yet")); return; }
  for (const p of people) {
    const li = el("li");
    const aff = el("span", p.affinity >= 0 ? "aff-pos" : "aff-neg", `${p.affinity >= 0 ? "+" : ""}${p.affinity}`);
    const where = el("span", "where", p.where + " ");
    where.append(aff);
    const name = el("span", "", `${p.name} `); name.append(el("span", "dim", `(${p.role})`));
    li.append(el("span", "glyph", p.glyph), name, where);
    ul.append(li);
  }
}

function renderControls(f) {
  const r = f.runtime;
  app.running = r.running;
  const play = $("btn-play");
  play.textContent = r.running ? "pause" : "play";
  play.disabled = !f.person.alive;
  $("btn-step").disabled = r.running || !f.person.alive;
  const sp = $("speed");
  if (document.activeElement !== sp) sp.value = String(r.speed);
  $("god-send").disabled = !f.person.alive;
  $("god-text").disabled = !f.person.alive;
  $("dg-source").textContent = `${r.provider} · last decision: ${r.last_source === "llm" ? "LLM" : "rule-based"}`;
  const warn = $("dg-warn");
  warn.hidden = !r.breaker_open;
  warn.textContent = r.breaker_open ? "The LLM backend failed twice; the offline demi-god is covering for a few decisions, then will retry." : "";
}

function syncDemigod(f) {
  const editing = $("dg-apply").closest(".panel").contains(document.activeElement);
  const gk = JSON.stringify(f.god);
  if (gk !== app.godKey && !editing) {
    app.godKey = gk;
    setMood($("dg-mood"), $("dg-mood-custom-wrap"), $("dg-mood-custom"), f.god.mood);
    $("dg-objectives").value = f.god.objectives.join("\n");
  }
  const lk = JSON.stringify(f.llm);
  if (lk !== app.llmKey && !editing) {
    app.llmKey = lk;
    $("dg-provider").value = f.llm.provider;
    $("dg-model").value = f.llm.model || "";
  }
}

function appendLog(entries) {
  if (!entries.length) return;
  const box = $("log");
  const atBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 48;
  let added = false;
  for (const e of entries) {
    if (e.seq <= app.lastSeq) continue;
    app.lastSeq = e.seq; added = true;
    const row = el("div", "entry " + e.kind);
    const body = el("div");
    const tag = { divine: "GOD", demigod: "DEMI-GOD" }[e.kind];
    if (tag) body.append(el("span", "tag", tag));
    body.append(el("span", "txt", e.text));
    row.append(el("time", "", e.time), body);
    box.append(row);
  }
  if (!added) return;
  while (box.children.length > 500) box.removeChild(box.firstChild);
  if (atBottom) box.scrollTop = box.scrollHeight;
  $("jump").hidden = atBottom;
}

/* ---------------------------------------------------------------- life controls */
function initLifeControls() {
  fillSelect($("speed"), app.meta.speeds.map((s) => [String(s), `${s} tick/s`]), "2");
  fillSelect($("dg-provider"), providerOptions(), "auto");
  wireMood($("dg-mood"), $("dg-mood-custom-wrap"), $("dg-mood-custom"));

  const guard = (fn) => async (...a) => { try { await fn(...a); } catch (e) { toast(e.message); } };
  $("btn-play").addEventListener("click", guard(() => api("POST", `/api/lives/${app.lifeId}/${app.running ? "pause" : "play"}`)));
  $("btn-step").addEventListener("click", guard(() => api("POST", `/api/lives/${app.lifeId}/step`)));
  $("speed").addEventListener("change", guard((ev) => api("PUT", `/api/lives/${app.lifeId}/speed`, { speed: Number(ev.target.value) })));

  $("god-form").addEventListener("submit", guard(async (ev) => {
    ev.preventDefault();
    const text = $("god-text").value.trim();
    if (!text) return;
    const kind = ev.target.elements.kind.value;
    $("god-send").disabled = true;
    try { await api("POST", `/api/lives/${app.lifeId}/god`, { kind, text }); $("god-text").value = ""; }
    finally { $("god-send").disabled = !app.alive; }
  }));
  $("god-text").addEventListener("keydown", (ev) => { if (ev.key === "Enter" && (ev.ctrlKey || ev.metaKey)) $("god-form").requestSubmit(); });

  $("dg-apply").addEventListener("click", guard(async () => {
    await api("PUT", `/api/lives/${app.lifeId}/demigod`, { mood: readMood($("dg-mood"), $("dg-mood-custom")), objectives: lines($("dg-objectives").value) });
    app.godKey = "";
    toast("The demi-god has been told.");
  }));
  $("dg-llm").addEventListener("click", guard(async () => {
    await api("PUT", `/api/lives/${app.lifeId}/llm`, { provider: $("dg-provider").value, model: $("dg-model").value.trim() || null });
    app.llmKey = "";
  }));
  $("delete").addEventListener("click", guard(async () => {
    if (!confirm("Delete this life permanently?")) return;
    await api("DELETE", `/api/lives/${app.lifeId}`);
    location.hash = "#/";
  }));
  const log = $("log");
  log.addEventListener("scroll", () => { $("jump").hidden = log.scrollHeight - log.scrollTop - log.clientHeight < 48; });
  $("jump").addEventListener("click", () => { log.scrollTop = log.scrollHeight; });
}

let toastTimer = 0;
function toast(msg) {
  const c = $("conn");
  const prev = c.textContent, cls = c.className;
  c.textContent = msg; c.className = "chip conn bad"; c.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { c.textContent = prev; c.className = cls; }, 3500);
}

/* ---------------------------------------------------------------- boot */
(async function boot() {
  app.meta = await api("GET", "/api/meta");
  initCreateForm();
  initLifeControls();
  window.addEventListener("hashchange", route);
  route();
})().catch((e) => { document.body.prepend(el("pre", "error", "Could not start: " + e.message)); });
