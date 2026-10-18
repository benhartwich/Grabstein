"use strict";

// ---------------------------------------------------------------- Grundlagen
const $ = (sel, el = document) => el.querySelector(sel);
const $$ = (sel, el = document) => [...el.querySelectorAll(sel)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const icon = (name) => `<svg><use href="#i-${name}"/></svg>`;
const SPOTIFY_HINT = "Spotify findet das Gerät „Grabstein“ nicht. Nach jedem Neustart des Pi einmal in der Spotify-App unten aufs Gerätesymbol tippen und „Grabstein“ wählen – danach steuert die Web-App die Musik wieder selbst.";

const MODE = {
  off: { label: "Schläft", short: "Aus" },
  gentle: { label: "Lieb", short: "Lieb" },
  full: { label: "Gruselig", short: "Gruselig" },
  music: { label: "Nur Musik", short: "Musik" },
  auto: { label: "Automatik", short: "Automatik" },
};
const GROUP = {
  approach: { title: "Vorbeigehen", hint: "Kurz – wenn der Bewegungsmelder jemanden sieht." },
  lure: { title: "Anlocken", hint: "Wenn jemand stehen bleibt, aber weiter weg ist als der Gesprächsabstand." },
  stay: { title: "Stehenbleiben", hint: "Statt eines Gesprächs, z. B. ohne Internet oder wenn das Budget leer ist." },
  wake: { title: "Aufwachen", hint: "Kurz vor jedem Gespräch – überbrückt den Verbindungsaufbau." },
  leave: { title: "Abschied", hint: "Wenn Gustav sich nach Zeitlimit oder leerem Budget nicht selbst verabschiedet hat." },
  sleep: { title: "Einschlafen", hint: "Nach jedem Gespräch – damit man hört, dass Gustav wieder schläft." },
  silent: { title: "Keine Antwort", hint: "Wenn niemand mehr antwortet oder alle gegangen sind. Wer stehen bleibt, weckt Gustav nach 10 s noch einmal." },
  gentle: { title: "Lieb", hint: "Kinder-Modus – freundlich, nicht gruselig." },
};
const DAYS = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"];
const DURATIONS = [
  { key: "", label: "Unbefristet" },
  { key: "30", label: "30 Min" },
  { key: "60", label: "1 Std" },
  { key: "120", label: "2 Std" },
  { key: "night", label: "Bis 0 Uhr" },
];

const S = { tab: "start", status: null, settings: null, clips: null, gustav: null, draft: null, dirty: false, duration: "", lost: false };

async function api(method, path, body, raw) {
  const opt = { method, headers: {} };
  if (raw !== undefined) { opt.body = raw; }
  else if (body !== undefined) { opt.body = JSON.stringify(body); opt.headers["Content-Type"] = "application/json"; }
  let res;
  try { res = await fetch(path, opt); }
  catch { throw new Error("Keine Verbindung zum Grabstein"); }
  const data = await res.json().catch(() => ({}));
  if (res.status === 401 && data.login) { showLogin(); throw new Error(data.error || "PIN nötig"); }
  if (!res.ok) throw new Error(data.error || `Fehler ${res.status}`);
  return data;
}

function toast(msg, kind = "ok") {
  const el = document.createElement("div");
  el.className = `toast ${kind}`;
  el.textContent = msg;
  $("#toasts").append(el);
  setTimeout(() => el.remove(), kind === "err" ? 5000 : 2600);
}

async function run(fn, okMsg) {
  try { const r = await fn(); if (okMsg) toast(okMsg); return r; }
  catch (e) { toast(e.message, "err"); }
}

function debounce(fn, ms) {
  let t;
  return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); };
}

const ago = (s) => s == null ? "–" : s < 60 ? `vor ${s} s` : s < 3600 ? `vor ${Math.floor(s / 60)} min` : `vor ${Math.floor(s / 3600)} h`;
const fmtDate = (iso, opts) => {
  const d = new Date(iso + "T12:00:00");
  return opts ? d.toLocaleDateString("de-AT", opts) : `${DAYS[(d.getDay() + 6) % 7]} ${d.getDate()}.${d.getMonth() + 1}.`;
};
const todayIso = () => { const d = new Date(); d.setMinutes(d.getMinutes() - d.getTimezoneOffset()); return d.toISOString().slice(0, 10); };

// ---------------------------------------------------------------- Navigation
const VIEWS = {};
function show(tab) {
  if (!VIEWS[tab]) tab = "start";
  if (S.tab === "zeitplan" && tab !== "zeitplan" && S.dirty && !confirm("Zeitplan-Änderungen verwerfen?")) {
    location.hash = "#zeitplan"; return;
  }
  if (tab !== "zeitplan") { S.dirty = false; S.draft = null; }
  S.tab = tab;
  $$(".tabs a").forEach((a) => a.classList.toggle("active", a.dataset.tab === tab));
  $$(".view").forEach((v) => v.classList.toggle("active", v.id === `view-${tab}`));
  VIEWS[tab].open();
  window.scrollTo({ top: 0 });
}
window.addEventListener("hashchange", () => show(location.hash.slice(1)));

// ---------------------------------------------------------------- Status-Abfrage
async function poll() {
  try {
    S.status = await api("GET", "/api/status");
    setLost(false);
    $("#clock").textContent = S.status.time;
    if (S.tab === "start") VIEWS.start.update();
  } catch (e) {
    setLost(true);
  }
}
function setLost(lost) {
  if (lost === S.lost) return;
  S.lost = lost;
  $("#conn").classList.toggle("lost", lost);
  if (lost) toast("Verbindung zum Grabstein verloren", "err");
}
let pollTimer;
function startPolling() {
  clearInterval(pollTimer);
  poll();
  pollTimer = setInterval(() => { if (!document.hidden) poll(); }, 1000);
}
document.addEventListener("visibilitychange", () => { if (!document.hidden) poll(); });

async function loadSettings() { S.settings = await api("GET", "/api/settings"); return S.settings; }
async function saveSettings(patch, msg = "Gespeichert") {
  return run(async () => { S.settings = await api("PUT", "/api/settings", patch); return true; }, msg);
}

// ---------------------------------------------------------------- Start
VIEWS.start = {
  built: false,
  open() {
    if (!this.built) this.build();
    if (S.status) this.update();
  },
  build() {
    this.built = true;
    $("#view-start").innerHTML = `
      <div class="cols">
        <div class="stack">
          <div class="card hero" id="hero">
            <div class="eyebrow">Gustav ist gerade</div>
            <div class="state"><span class="pulse"></span><span id="hero-label">…</span></div>
            <div class="sub" id="hero-sub"></div>
            <div class="badges" id="hero-badges"></div>
          </div>
          <div class="card">
            <div class="card-head"><h2>Betriebsart</h2><span class="muted" id="op-hint"></span></div>
            <div class="seg" id="op-seg">
              ${["auto", "off", "music", "gentle", "full"].map((m) => `<button type="button" data-mode="${m}">${MODE[m].short}</button>`).join("")}
            </div>
            <div class="chips" id="op-dur">
              ${DURATIONS.map((d) => `<button type="button" class="chip" data-dur="${d.key}">${d.label}</button>`).join("")}
            </div>
          </div>
          <div class="card" id="live"></div>
        </div>
        <div class="stack">
          <div class="card">
            <h2>Auslösen</h2>
            <div class="actions">
              <button class="btn" data-clip="approach">${icon("play")}Vorbei</button>
              <button class="btn" data-clip="stay">${icon("play")}Bleiben</button>
              <button class="btn" data-clip="leave">${icon("play")}Abschied</button>
              <button class="btn" data-clip="gentle">${icon("play")}Lieb</button>
              <button class="btn primary half" id="act-talk">${icon("ghost")}Gespräch starten</button>
              <button class="btn danger half" id="act-stop">${icon("stop")}Alles stoppen</button>
            </div>
          </div>
          <div class="card">
            <div class="card-head"><h2>Gustav sagt …</h2><span class="muted opt">mit seiner Stimme</span></div>
            <form class="row" id="say-form">
              <input type="text" id="say-text" maxlength="300" placeholder="z. B. Hallo Lena … schönes Kostüm …" autocomplete="off">
              <button class="btn primary" type="submit" id="say-btn">${icon("mic")}<span>Sprechen</span></button>
            </form>
          </div>
          <div class="card">
            <div class="card-head"><h2>Heute Abend</h2></div>
            <div class="stats" id="stats"></div>
            <div class="meter-head"><span>Gesprächsbudget</span><b id="budget-txt"></b></div>
            <div class="progress warm"><i id="budget-bar" style="width:0"></i></div>
          </div>
          <div class="card">
            <div class="card-head"><h2>Letzte Ereignisse</h2><a href="#einstellungen" class="small">Ganzes Protokoll</a></div>
            <ul class="events" id="events"></ul>
          </div>
        </div>
      </div>`;

    $("#op-seg").addEventListener("click", (e) => {
      const b = e.target.closest("button"); if (!b) return;
      this.setOperation(b.dataset.mode);
    });
    $("#op-dur").addEventListener("click", (e) => {
      const b = e.target.closest("button"); if (!b || b.disabled) return;
      S.duration = b.dataset.dur;
      const cur = S.status?.operation?.mode;
      if (cur && cur !== "auto") this.setOperation(cur); else this.update();
    });
    $$("[data-clip]").forEach((b) => b.addEventListener("click", () =>
      run(() => api("POST", "/api/aktion", { type: "clip", group: b.dataset.clip }), "Clip läuft").then(poll)));
    $("#act-talk").addEventListener("click", () => run(() => api("POST", "/api/aktion", { type: "talk" }), "Gustav wacht auf …").then(poll));
    $("#act-stop").addEventListener("click", () => run(() => api("POST", "/api/aktion", { type: "stop" }), "Gestoppt").then(poll));
    $("#say-form").addEventListener("submit", async (e) => {
      e.preventDefault();
      const input = $("#say-text"), btn = $("#say-btn");
      const text = input.value.trim(); if (!text) return;
      btn.disabled = true; btn.querySelector("span").textContent = "…";
      const ok = await run(() => api("POST", "/api/aktion", { type: "say", text }), "Gustav spricht");
      btn.disabled = false; btn.querySelector("span").textContent = "Sprechen";
      if (ok) input.value = "";
    });
  },
  setOperation(mode) {
    let until = null;
    if (mode !== "auto" && S.duration) {
      const now = new Date();
      if (S.duration === "night") { const m = new Date(now); m.setHours(24, 0, 0, 0); until = m.getTime() / 1000; }
      else until = now.getTime() / 1000 + Number(S.duration) * 60;
    }
    const label = mode === "auto" ? "Zurück zum Zeitplan" : `${MODE[mode].label}${until ? "" : " – bis auf Weiteres"}`;
    saveSettings({ operation: { mode, until } }, label).then(poll);
  },
  update() {
    const st = S.status; if (!st || !this.built) return;
    const hero = $("#hero");
    hero.className = `card hero ${st.mode}`;
    $("#hero-label").textContent = MODE[st.mode].label;
    let sub;
    if (st.source === "manual") sub = `Von Hand · ${st.operation.until_hm ? `bis ${st.operation.until_hm}` : "bis auf Weiteres"}`;
    else if (st.source === "untrusted") sub = "Uhrzeit noch nicht synchronisiert – vorsichtshalber lieb";
    else if (st.next) {
      const day = st.next.day === todayIso() ? "" : ` (${fmtDate(st.next.day, { weekday: "long" })})`;
      sub = `Laut Zeitplan · ab ${st.next.at}${day} ${MODE[st.next.mode].label.toLowerCase()}`;
    } else sub = "Laut Zeitplan";
    $("#hero-sub").textContent = sub;
    const badges = [];
    if (!st.online) badges.push(`<span class="badge warn">Kein Internet – nur Clips</span>`);
    if (!st.trusted) badges.push(`<span class="badge warn">Uhr nicht synchron</span>`);
    if (!st.agent) badges.push(`<span class="badge warn">Kein Agent eingetragen</span>`);
    if (st.online && st.trusted && st.agent) badges.push(`<span class="badge ok">Alles bereit</span>`);
    if (st.cooldown.talk > 0 && !st.talk) badges.push(`<span class="badge">Gesprächspause ${st.cooldown.talk} s</span>`);
    $("#hero-badges").innerHTML = badges.join("");

    const op = st.operation.mode;
    $$("#op-seg button").forEach((b) => b.classList.toggle("active", b.dataset.mode === op));
    $$("#op-dur .chip").forEach((c) => c.classList.toggle("active", c.dataset.dur === S.duration));
    $("#op-hint").textContent = op === "auto" ? "folgt dem Zeitplan" : "überstimmt den Zeitplan";

    this.renderLive(st);

    const t = st.today;
    $("#stats").innerHTML = [
      [t.motion, "Bewegungen"], [t.stays, "Geblieben"], [t.clips, "Clips"], [t.talks, "Gespräche"],
    ].map(([n, l]) => `<div class="stat"><b>${n}</b><span>${l}</span></div>`).join("");
    $("#budget-txt").textContent = `${t.talk_min.toLocaleString("de-AT")} / ${t.budget_min ? `${t.budget_min} min` : "ohne Limit"}`;
    $("#budget-bar").style.width = t.budget_min ? `${Math.min(100, (t.talk_min / t.budget_min) * 100)}%` : "0";

    $("#events").innerHTML = st.log.slice().reverse().map((l) => `<li><time>${esc(l.hm)}</time><span>${esc(l.msg)}</span></li>`).join("")
      || `<li class="empty">Noch nichts passiert.</li>`;
    $("#act-talk").disabled = !!st.talk || !st.agent || !st.online;
    $("#say-btn").disabled = !st.tts;
  },
  distance(d, nearCm) {
    // Abstand auf einer Skala bis 6 m (Gesprächszone hervorgehoben), darunter die Signalstärke von bewegtem und ruhendem Ziel
    const pos = (cm) => Math.min(100, (cm / 600) * 100);
    const m = (cm) => (cm / 100).toLocaleString("de-AT", { maximumFractionDigits: 1 });
    const who = d.state === 0 ? "niemand" : d.state === 1 ? "bewegt sich" : d.state === 2 ? "steht still" : "bewegt sich und steht";
    const near = d.state && d.cm <= nearCm;
    return `<div class="radar">
      <div class="radar-head"><span>Abstand</span><b>${d.state ? `${(d.cm / 100).toLocaleString("de-AT", { maximumFractionDigits: 1 })} m` : "–"}</b><span class="muted">${who}</span></div>
      <div class="radar-scale">
        <i class="zone" style="width:${pos(nearCm)}%"></i>
        ${d.state ? `<i class="dot ${near ? "near" : ""}" style="left:${pos(d.cm)}%"></i>` : ""}
      </div>
      <div class="radar-ticks"><span>0</span><span style="left:${pos(nearCm)}%">${m(nearCm)} m</span>${[300, 450].filter((x) => Math.abs(x - nearCm) > 60).map((x) => `<span style="left:${pos(x)}%">${m(x)}</span>`).join("")}<span style="right:0">6 m</span></div>
      <div class="energy"><span>Bewegung</span><div class="progress"><i style="width:${d.move_energy}%"></i></div><b>${d.move_energy}</b></div>
      <div class="energy"><span>Ruhend</span><div class="progress"><i style="width:${d.still_energy}%"></i></div><b>${d.still_energy}</b></div>
    </div>`;
  },
  renderLive(st) {
    const live = $("#live");
    if (st.talk) {
      const tr = st.talk.transcript.map((x) => `<div class="bubble ${x.who === "Gustav" ? "gustav" : "besuch"}"><b>${esc(x.who)}</b>${esc(x.text)}</div>`).join("");
      live.className = "card talk";
      live.innerHTML = `
        <div class="card-head"><h2>Gespräch läuft</h2><span class="muted">${st.talk.elapsed} / ${st.talk.max} s</span></div>
        <div class="progress"><i style="width:${Math.min(100, (st.talk.elapsed / st.talk.max) * 100)}%"></i></div>
        ${tr ? `<div class="transcript">${tr}</div>` : `<p class="muted small">Mitschrift ist aus (Gustav → Mitschrift).</p>`}
        <button class="btn danger" id="talk-stop">${icon("stop")}Gespräch beenden</button>`;
      $("#talk-stop").onclick = () => run(() => api("POST", "/api/aktion", { type: "stop" }), "Gespräch beendet").then(poll);
      return;
    }
    const sen = st.sensors;
    const sensor = (on, label, val) => `<div class="sensor ${on === null ? "na" : on ? "on" : ""}"><div class="lbl"><span class="led"></span>${label}</div><div class="val">${val}</div></div>`;
    live.className = "card";
    live.innerHTML = `
      <div class="card-head"><h2>Live</h2><span class="muted">${st.cooldown.clip > 0 ? `Clip-Pause ${st.cooldown.clip} s` : "bereit"}</span></div>
      <div class="sensors">
        ${sensor(sen.pir, "Bewegung", sen.pir === null ? "nicht angeschlossen" : sen.pir ? "erkannt" : `zuletzt ${ago(sen.motion_ago)}`)}
        ${sensor(sen.radar, "Radar", sen.radar === null ? "nicht angeschlossen" : sen.radar ? `jemand da · ${Math.round(sen.radar_s)} s` : "frei")}
      </div>
      ${sen.distance ? this.distance(sen.distance, st.near_cm || 150) : ""}
      ${st.music && st.music.on && st.music.source === "spotify" && st.music.spotify_missing ? `<p class="notice">${SPOTIFY_HINT}</p>` : ""}
      ${st.playing ? `<div class="nowplaying"><span class="eq"><i></i><i></i><i></i></span>Spielt: ${esc(st.playing)}</div>` : ""}`;
  },
};

// ---------------------------------------------------------------- Zeitplan
VIEWS.zeitplan = {
  async open() {
    $("#view-zeitplan").innerHTML = `<div class="card"><p class="muted">Lade …</p></div>`;
    await run(loadSettings);
    if (!S.settings) return;
    S.draft = structuredClone(S.settings.schedule);
    S.dirty = false;
    this.render();
  },
  render() {
    const v = $("#view-zeitplan");
    v.innerHTML = `
      <div class="card">
        <div class="card-head"><h2>Nächste 7 Tage</h2>
          <div class="legend"><span><i style="background:var(--gentle)"></i>Lieb</span><span><i style="background:var(--full)"></i>Gruselig</span><span><i style="background:var(--accent)"></i>Nur Musik</span></div>
        </div>
        <div class="ticks"><div></div><div>${[12, 15, 18, 21, 0, 3, 6].map((h, i) => `<span style="left:${(i * 3 * 60 / 1080) * 100}%">${h}</span>`).join("")}</div></div>
        <div class="week" id="week"></div>
      </div>
      <p class="intro">„Musik“ heißt: nur Hintergrundmusik, Gustav reagiert nicht. Einträge für Wochentage gelten jede Woche. Ein Eintrag mit <b>Datum</b> ersetzt an diesem Tag alle Wochentags-Einträge – praktisch für Halloween. Überschneiden sich Einträge, gewinnt „Aus“, dann „Gruselig“. Über Mitternacht geht: einfach z. B. 20:00 bis 01:00.</p>
      <div class="stack" id="entries"></div>
      <div class="btn-row">
        <button class="btn" id="add-week">${icon("plus")}Wochentage</button>
        <button class="btn" id="add-date">${icon("plus")}Bestimmtes Datum</button>
      </div>
      <div class="savebar ${S.dirty ? "" : "hidden"}" id="savebar">
        <span>Ungespeicherte Änderungen</span>
        <div class="btn-row"><button class="btn ghost" id="sched-reset">Verwerfen</button><button class="btn primary" id="sched-save">Speichern</button></div>
      </div>`;
    this.renderEntries();
    this.preview();
    $("#add-week").onclick = () => this.add({ days: [0, 1, 2, 3, 4, 5, 6], date: null, from: "17:00", until: "21:00", mode: "gentle" });
    $("#add-date").onclick = () => this.add({ days: [], date: todayIso(), from: "17:00", until: "22:00", mode: "full" });
    $("#sched-reset").onclick = () => { S.draft = structuredClone(S.settings.schedule); this.touch(false); this.renderEntries(); };
    $("#sched-save").onclick = async () => {
      if (!await saveSettings({ schedule: S.draft }, "Zeitplan gespeichert")) return;
      S.draft = structuredClone(S.settings.schedule);
      this.touch(false); this.renderEntries();
    };
  },
  add(entry) {
    S.draft.push({ id: Math.random().toString(16).slice(2, 8), ...entry });
    this.touch(); this.renderEntries();
    $("#entries").lastElementChild?.scrollIntoView({ behavior: "smooth", block: "center" });
  },
  touch(dirty = true) {
    S.dirty = dirty;
    $("#savebar")?.classList.toggle("hidden", !dirty);
    this.preview();
  },
  renderEntries() {
    const box = $("#entries");
    if (!S.draft.length) {
      box.innerHTML = `<div class="card"><p class="empty">Kein Eintrag – Gustav bleibt im Automatik-Betrieb stumm.</p></div>`;
      return;
    }
    box.innerHTML = S.draft.map((e, i) => `
      <div class="entry ${e.mode}" data-i="${i}">
        <div class="entry-top">
          <div class="seg small" data-k="mode">
            ${["gentle", "full", "music", "off"].map((m) => `<button type="button" data-mode="${m}" class="${e.mode === m ? "active" : ""}">${MODE[m].short}</button>`).join("")}
          </div>
          <button class="icon-btn" data-k="del" aria-label="Eintrag löschen">${icon("trash")}</button>
        </div>
        <div class="times">
          <input type="time" step="60" value="${e.from}" data-k="from" aria-label="von">
          <span>bis</span>
          <input type="time" step="60" value="${e.until}" data-k="until" aria-label="bis">
        </div>
        <div class="seg small" data-k="kind">
          <button type="button" data-kind="week" class="${e.date ? "" : "active"}">Wochentage</button>
          <button type="button" data-kind="date" class="${e.date ? "active" : ""}">Datum</button>
        </div>
        ${e.date
          ? `<input type="date" value="${e.date}" data-k="date" aria-label="Datum">`
          : `<div class="chips days" data-k="days">${DAYS.map((d, n) => `<button type="button" class="chip ${e.days.includes(n) ? "active" : ""}" data-day="${n}">${d}</button>`).join("")}</div>`}
      </div>`).join("");

    box.oninput = (ev) => {
      const card = ev.target.closest(".entry"); if (!card) return;
      const e = S.draft[card.dataset.i], k = ev.target.dataset.k;
      if (k === "from" || k === "until" || k === "date") { e[k] = ev.target.value; this.touch(); }
    };
    box.onclick = (ev) => {
      const card = ev.target.closest(".entry"); if (!card) return;
      const i = Number(card.dataset.i), e = S.draft[i];
      const b = ev.target.closest("button"); if (!b) return;
      const k = b.closest("[data-k]")?.dataset.k || b.dataset.k;
      if (k === "del") { S.draft.splice(i, 1); }
      else if (k === "mode") { e.mode = b.dataset.mode; }
      else if (k === "kind") {
        if (b.dataset.kind === "date" && !e.date) { e.date = todayIso(); e.days = []; }
        if (b.dataset.kind === "week" && e.date) { e.date = null; e.days = [0, 1, 2, 3, 4, 5, 6]; }
      } else if (k === "days") {
        const n = Number(b.dataset.day);
        e.days = e.days.includes(n) ? e.days.filter((d) => d !== n) : [...e.days, n].sort();
      } else return;
      this.touch(); this.renderEntries();
    };
  },
  preview: debounce(async function () {
    const week = $("#week"); if (!week) return;
    const valid = S.draft.filter((e) => e.from && e.until && (e.date || e.days.length));
    let data;
    try { data = await api("POST", "/api/vorschau", { schedule: valid }); }
    catch (e) { week.innerHTML = `<p class="muted small">${esc(e.message)}</p>`; return; }
    const now = new Date(), nowMin = now.getHours() * 60 + now.getMinutes();
    // Die Zeile eines Tages reicht von 12:00 bis 06:00 am Folgetag.
    const rowDay = new Date(now); if (nowMin < 6 * 60) rowDay.setDate(rowDay.getDate() - 1);
    rowDay.setMinutes(rowDay.getMinutes() - rowDay.getTimezoneOffset());
    const rowIso = rowDay.toISOString().slice(0, 10);
    const nowPos = nowMin >= 12 * 60 ? nowMin - 12 * 60 : nowMin < 6 * 60 ? nowMin + 12 * 60 : null;
    week.innerHTML = data.days.map((d) => `
      <div class="week-row ${d.date === todayIso() ? "today" : ""}">
        <div class="day">${fmtDate(d.date)}</div>
        <div class="bar">
          ${d.segments.map((s) => `<i class="seg-${s.mode}" style="left:${(s.start / 1080) * 100}%;width:${((s.end - s.start) / 1080) * 100}%" title="${MODE[s.mode].label}"></i>`).join("")}
          ${d.date === rowIso && nowPos !== null ? `<i class="now" style="left:${(nowPos / 1080) * 100}%"></i>` : ""}
        </div>
      </div>`).join("");
  }, 250),
};

// ---------------------------------------------------------------- Clips
VIEWS.clips = {
  editing: null,
  timer: null,
  async open() {
    if (!S.clips) $("#view-clips").innerHTML = `<div class="card"><p class="muted">Lade …</p></div>`;
    await this.load();
  },
  async load(auto = false) {
    const data = await run(() => api("GET", "/api/clips"));
    if (!data) return;
    S.clips = data;
    // Automatisches Nachladen zeichnet nicht neu, während jemand tippt.
    if (!(auto && $("#view-clips textarea:focus"))) this.render();
    clearTimeout(this.timer);
    const busy = Object.values(data.groups).flat().some((c) => c.busy);
    if (busy && S.tab === "clips") this.timer = setTimeout(() => this.load(true), 1500);
  },
  render() {
    const { groups, tts } = S.clips;
    const v = $("#view-clips");
    v.innerHTML = `
      ${tts ? "" : `<div class="card"><p class="muted">Ohne ElevenLabs-Schlüssel und Stimme in der env-Datei lassen sich keine Clips erzeugen – eigene MP3s hochladen geht trotzdem.</p></div>`}
      <div class="cols">
        ${Object.keys(GROUP).map((gname) => this.group(gname, groups[gname] || [], tts)).join("")}
      </div>`;
    v.onclick = (e) => this.click(e);
    v.onchange = (e) => this.change(e);
  },
  group(gname, list, tts) {
    const active = list.filter((c) => c.active && c.file).length;
    return `
      <div class="card" data-group="${gname}">
        <div class="card-head"><h2>${GROUP[gname].title}</h2><span class="muted">${active} aktiv</span></div>
        <p class="intro small">${GROUP[gname].hint}</p>
        <div>${list.map((c) => this.clip(gname, c, tts)).join("") || `<p class="empty">Noch keine Clips.</p>`}</div>
        <div class="add">
          <textarea rows="2" maxlength="400" placeholder="Neuer Satz für Gustav …" data-new></textarea>
          <div class="row">
            <label class="btn ghost">${icon("upload")}Hochladen<input type="file" accept="audio/mpeg,audio/wav,.mp3,.wav" data-upload hidden></label>
            <button class="btn primary" data-act="add" ${tts ? "" : "disabled"}>${icon("plus")}Hinzufügen</button>
          </div>
        </div>
      </div>`;
  },
  clip(gname, c, tts) {
    const key = `${gname}/${c.id}`;
    const meta = [];
    if (c.busy) meta.push(`<span class="badge"><span class="spin"></span> wird erzeugt</span>`);
    else if (!c.file) meta.push(`<span class="badge warn">noch kein Ton</span>`);
    else if (c.stale) meta.push(`<span class="badge warn">Text geändert – neu erzeugen</span>`);
    if (c.own) meta.push(`<span class="badge">eigene Datei</span>`);
    const body = this.editing === key
      ? `<div class="clip-edit"><textarea rows="3" maxlength="400" data-edit>${esc(c.text)}</textarea>
           <div class="row"><button class="btn ghost" data-act="cancel">Abbrechen</button><button class="btn primary" data-act="save">Speichern</button></div></div>`
      : `<div class="text">${c.text ? esc(c.text) : `<span class="muted">${esc(c.id)}</span>`}</div>`;
    return `
      <div class="clip ${c.active ? "" : "inactive"}" data-id="${esc(c.id)}">
        <label class="switch" title="aktiv"><input type="checkbox" data-act="toggle" ${c.active ? "checked" : ""}><span></span></label>
        <div>
          ${body}
          ${meta.length ? `<div class="meta">${meta.join("")}</div>` : ""}
          <div class="tools">
            <button class="icon-btn" data-act="play" title="Am Grabstein abspielen" ${c.file ? "" : "disabled"}>${icon("play")}</button>
            <button class="icon-btn" data-act="listen" title="Am Handy anhören" ${c.file ? "" : "disabled"}>${icon("phone")}</button>
            ${c.own ? "" : `<button class="icon-btn" data-act="edit" title="Text bearbeiten">${icon("edit")}</button>
            <button class="icon-btn ${!c.file || c.stale ? "hot" : ""}" data-act="gen" title="Mit ElevenLabs erzeugen" ${tts && !c.busy ? "" : "disabled"}>${icon("redo")}</button>`}
            <button class="icon-btn" data-act="del" title="Löschen">${icon("trash")}</button>
          </div>
        </div>
      </div>`;
  },
  async click(e) {
    const b = e.target.closest("[data-act]"); if (!b || b.tagName === "INPUT") return;
    const card = b.closest("[data-group]"), gname = card.dataset.group;
    const row = b.closest(".clip"), id = row?.dataset.id;
    const base = `/api/clips/${gname}/${encodeURIComponent(id || "")}`;
    switch (b.dataset.act) {
      case "play": return run(() => api("POST", "/api/aktion", { type: "clip", group: gname, id }), "Spielt am Grabstein");
      case "listen": { this.audio?.pause(); this.audio = new Audio(`${base}/audio?${Date.now()}`); return this.audio.play().catch(() => toast("Abspielen am Handy nicht möglich", "err")); }
      case "edit": this.editing = `${gname}/${id}`; this.render(); return $("[data-edit]")?.focus();
      case "cancel": this.editing = null; return this.render();
      case "save": {
        const text = $("[data-edit]", row).value;
        this.editing = null;
        await run(() => api("PATCH", base, { text }), "Text gespeichert");
        return this.load();
      }
      case "gen": await run(() => api("POST", `${base}/erzeugen`), "Wird erzeugt …"); return this.load();
      case "del":
        if (!confirm("Diesen Clip löschen?")) return;
        await run(() => api("DELETE", base), "Gelöscht"); return this.load();
      case "add": {
        const ta = $("[data-new]", card), text = ta.value.trim();
        if (!text) return ta.focus();
        await run(() => api("POST", `/api/clips/${gname}`, { text }), "Hinzugefügt – wird erzeugt");
        return this.load();
      }
    }
  },
  async change(e) {
    const t = e.target, card = t.closest("[data-group]"); if (!card) return;
    const gname = card.dataset.group;
    if (t.dataset.act === "toggle") {
      const id = t.closest(".clip").dataset.id;
      await run(() => api("PATCH", `/api/clips/${gname}/${encodeURIComponent(id)}`, { active: t.checked }));
      return this.load();
    }
    if (t.hasAttribute("data-upload") && t.files[0]) {
      const f = t.files[0];
      await run(() => api("PUT", `/api/clips/${gname}/upload?name=${encodeURIComponent(f.name)}`, undefined, f), "Hochgeladen");
      return this.load();
    }
  },
};

// ---------------------------------------------------------------- Musik
VIEWS.musik = {
  timer: null,
  async open() {
    $("#view-musik").innerHTML = `<div class="card"><p class="muted">Lade …</p></div>`;
    await run(loadSettings);
    if (S.settings) await this.load();
  },
  async load() {
    clearTimeout(this.timer);
    const d = await run(() => api("GET", "/api/musik"));
    if (!d) return;
    this.data = d;
    // nicht neu zeichnen, während jemand tippt oder Suchergebnisse ansieht
    if (!$("#view-musik textarea:focus, #view-musik input:focus") && !$("#sp-results")?.childElementCount) this.render();
    if (S.tab === "musik") this.timer = setTimeout(() => this.load(), d.busy ? 3000 : 8000);
  },
  render() {
    const s = S.settings, d = this.data;
    const src = s.music_source;
    const state = !s.music_on ? "aus" : d.level > 0 ? `läuft · ${d.level} %` : "bereit (blendet ein, sobald aktiv)";
    $("#view-musik").innerHTML = `
      <div class="cols">
        <div class="stack">
          <div class="card">
            <div class="card-head"><h2>Hintergrundmusik</h2><span class="muted">${esc(state)}</span></div>
            <p class="intro small">Läuft, solange der Grabstein aktiv ist. Spielt ein Clip, wird sie leiser; spricht Gustav, blendet sie ganz aus und danach wieder ein.</p>
            <div class="fields">${fieldSwitch("music_on", "Musik an", "Nur wenn die Betriebsart nicht „Aus“ ist.", s)}</div>
            <div class="seg" id="mu-src">
              ${[["tracks", "Eigene Tracks"], ["stream", "Web-Stream"], ["spotify", "Spotify"]].map(([k, l]) => `<button type="button" data-src="${k}" class="${src === k ? "active" : ""}">${l}</button>`).join("")}
            </div>
            <div class="range"><input type="range" min="0" max="100" id="mu-vol" value="${s.music_volume}" aria-label="Musik-Lautstärke"><output id="mu-vol-out">${s.music_volume} %</output></div>
            <p class="small muted" style="margin-top:-6px">Lautstärke der Musik (Gustav bleibt bei ${s.volume} %)</p>
            <div class="range"><input type="range" min="0" max="100" id="mu-duck" value="${s.music_duck}" aria-label="Bei Clips"><output id="mu-duck-out">${s.music_duck} %</output></div>
            <p class="small muted" style="margin-top:-6px">So viel davon bleibt, während ein Clip läuft</p>
          </div>
          ${src === "stream" ? `<div class="card">
            <h2>Web-Stream</h2>
            <form class="row" id="mu-url-form"><input type="url" id="mu-url" placeholder="https://… (MP3-Stream oder .m3u/.pls)" value="${esc(s.music_stream_url)}"><button class="btn primary">Speichern</button></form>
            <p class="small muted">Die meisten Internetradios bieten einen MP3-Stream an. AAC/HLS-Streams (.m3u8) spielt der Grabstein nicht.</p>
          </div>` : ""}
          ${src === "spotify" ? `<div class="card" id="sp-card"><p class="muted small">Lade Spotify …</p></div>` : ""}
        </div>
        <div class="stack">
          <div class="card">
            <div class="card-head"><h2>Eigene Tracks</h2><span class="muted">${d.tracks.length} Stück${d.tracks.length === 1 ? "" : "e"}</span></div>
            <div>${d.tracks.map((t) => `<div class="track" data-name="${esc(t.name)}">
                <div><div class="name">${esc(t.prompt || t.name)}</div><div class="sub">${t.prompt ? "erzeugt · " : ""}${t.mb.toLocaleString("de-AT")} MB</div></div>
                <div class="row"><button class="icon-btn" data-act="listen" title="Am Handy anhören">${icon("phone")}</button><button class="icon-btn" data-act="del" title="Löschen">${icon("trash")}</button></div>
              </div>`).join("") || `<p class="empty">Noch keine Stücke – hochladen oder erzeugen.</p>`}</div>
            <label class="btn ghost">${icon("upload")}MP3 hochladen<input type="file" accept="audio/mpeg,audio/wav,.mp3,.wav" id="mu-upload" hidden></label>
          </div>
          <div class="card">
            <h2>Stück erzeugen</h2>
            <p class="intro small">ElevenLabs komponiert ein Instrumentalstück nach deiner Beschreibung. Dauert etwa eine halbe Minute und kostet Guthaben.</p>
            ${d.busy ? `<p class="small"><span class="spin"></span> Wird erzeugt: ${esc(d.busy)}</p>` : ""}
            <textarea id="mu-prompt" rows="3" maxlength="500" placeholder="z. B. düsteres Halloween-Ambient, Wind, ferne Spieluhr, langsam, geheimnisvoll, loopbar"></textarea>
            <div class="row"><select id="mu-len" aria-label="Länge">${[60, 90, 120, 180].map((n) => `<option value="${n}" ${n === 120 ? "selected" : ""}>${(n / 60).toLocaleString("de-AT")} min</option>`).join("")}</select>
              <button class="btn primary" id="mu-gen" ${d.busy || !d.tts ? "disabled" : ""}>Erzeugen</button></div>
          </div>
        </div>
      </div>`;
    const v = $("#view-musik");
    bindFieldsOnce(v);
    if (src === "spotify") this.spotify();
    $("#mu-src").onclick = (e) => { const b = e.target.closest("button"); if (b) saveSettings({ music_source: b.dataset.src }, "Quelle geändert").then(() => this.render()); };
    const slider = (id, key, label) => {
      const el = $(id), out = $(id + "-out");
      el.oninput = () => { out.textContent = `${el.value} %`; };
      el.onchange = () => saveSettings({ [key]: Number(el.value) }, `${label} ${el.value} %`);
    };
    slider("#mu-vol", "music_volume", "Musik");
    slider("#mu-duck", "music_duck", "Bei Clips");
    const form = $("#mu-url-form");
    if (form) form.onsubmit = (e) => { e.preventDefault(); saveSettings({ music_stream_url: $("#mu-url").value }, "Stream gespeichert"); };
    $("#mu-upload").onchange = async (e) => {
      const f = e.target.files[0]; if (!f) return;
      await run(() => api("PUT", `/api/musik/upload?name=${encodeURIComponent(f.name)}`, undefined, f), "Hochgeladen");
      this.load();
    };
    $("#mu-gen").onclick = async () => {
      const prompt = $("#mu-prompt").value.trim(); if (!prompt) return $("#mu-prompt").focus();
      $("#mu-prompt").value = "";
      await run(() => api("POST", "/api/musik/erzeugen", { prompt, seconds: Number($("#mu-len").value) }), "Wird erzeugt …");
      this.load();
    };
    v.querySelectorAll(".track [data-act]").forEach((b) => b.onclick = async () => {
      const name = b.closest(".track").dataset.name;
      if (b.dataset.act === "listen") { VIEWS.clips.audio?.pause(); VIEWS.clips.audio = new Audio(`/api/musik/${encodeURIComponent(name)}/audio`); return VIEWS.clips.audio.play(); }
      if (!confirm("Dieses Stück löschen?")) return;
      await run(() => api("DELETE", `/api/musik/${encodeURIComponent(name)}`), "Gelöscht");
      this.load();
    });
  },
};

VIEWS.musik.spotify = async function () {
  const card = $("#sp-card"); if (!card) return;
  const sp = await api("GET", "/api/spotify").catch((e) => ({ error: e.message }));
  if (!sp.configured) { card.innerHTML = `<h2>Spotify</h2><p class="muted small">SPOTIFY_CLIENT_ID fehlt in der env-Datei.</p>`; return; }
  if (!sp.connected) {
    card.innerHTML = `<h2>Spotify verbinden</h2>
      <ol class="steps">
        <li><button class="btn primary" id="sp-login">Mit Spotify anmelden</button></li>
        <li>Bei Spotify bestätigen. Danach zeigt das Handy eine <b>Fehlerseite</b> (127.0.0.1) – das ist richtig so.</li>
        <li>Die <b>Adresse dieser Fehlerseite</b> kopieren und hier einfügen:</li>
      </ol>
      <form class="row" id="sp-form"><input type="url" id="sp-url" placeholder="http://127.0.0.1:8888/callback?code=…"><button class="btn primary">Verbinden</button></form>`;
    $("#sp-login").onclick = async () => {
      const r = await run(() => api("POST", "/api/spotify/login"));
      if (r) window.open(r.url, "_blank");
    };
    $("#sp-form").onsubmit = async (e) => {
      e.preventDefault();
      if (await run(() => api("POST", "/api/spotify/verbinden", { url: $("#sp-url").value }), "Spotify verbunden")) VIEWS.musik.spotify();
    };
    return;
  }
  const pl = sp.playlist;
  card.innerHTML = `
    <div class="card-head"><h2>Spotify</h2><span class="muted">${sp.device ? "Gerät „Grabstein“ bereit" : "Gerät nicht gefunden"}</span></div>
    ${sp.device ? "" : `<p class="notice">${SPOTIFY_HINT}</p>`}
    ${sp.error ? `<p class="small" style="color:var(--danger)">${esc(sp.error)}</p>` : ""}
    <div class="track"><div><div class="sub">Gewählte Playlist</div><div class="name">${pl ? esc(pl.name) : "– noch keine –"}</div>
      ${sp.track ? `<div class="sub">♪ ${esc(sp.track)}</div>` : ""}</div>
      ${pl && pl.image ? `<img src="${esc(pl.image)}" alt="" width="56" height="56" style="border-radius:8px">` : ""}</div>
    <p class="small muted">Startet von selbst (zufällig, wiederholt), sobald der Grabstein aktiv ist und Musik an ist – und pausiert danach.</p>
    <form class="row" id="sp-search"><input type="text" id="sp-q" placeholder="Playlist suchen, z. B. Halloween" autocomplete="off"><button class="btn">Suchen</button></form>
    <button class="btn ghost" id="sp-mine">Meine Playlists</button>
    <div id="sp-results"></div>
    <button class="btn ghost" id="sp-off" style="justify-self:start">Spotify trennen</button>`;
  const show = async (q) => {
    const box = $("#sp-results");
    box.innerHTML = `<p class="small muted"><span class="spin"></span> Suche …</p>`;
    const r = await run(() => api("GET", `/api/spotify/suche?q=${encodeURIComponent(q)}`));
    if (!r) { box.innerHTML = ""; return; }
    box.innerHTML = r.playlists.map((p, i) => `<div class="track">
        <div class="row" style="gap:10px">${p.image ? `<img src="${esc(p.image)}" alt="" width="44" height="44" style="border-radius:6px;flex:none">` : ""}
          <div><div class="name">${esc(p.name)}</div><div class="sub">${esc(p.owner)}${p.tracks ? ` · ${p.tracks} Titel` : ""}</div></div></div>
        <button class="btn" data-i="${i}">Wählen</button></div>`).join("") || `<p class="empty">Nichts gefunden.</p>`;
    box.querySelectorAll("[data-i]").forEach((b) => b.onclick = async () => {
      const p = r.playlists[b.dataset.i];
      await saveSettings({ spotify_playlist: { uri: p.uri, name: p.name, image: p.image } }, `„${p.name}“ gewählt`);
      VIEWS.musik.spotify();
    });
  };
  $("#sp-search").onsubmit = (e) => { e.preventDefault(); const q = $("#sp-q").value.trim(); if (q) show(q); };
  $("#sp-mine").onclick = () => show("");
  $("#sp-off").onclick = async () => { if (confirm("Verbindung zu Spotify trennen?")) { await run(() => api("POST", "/api/spotify/trennen")); VIEWS.musik.spotify(); } };
};

// ---------------------------------------------------------------- Gustav
VIEWS.gustav = {
  async open() {
    $("#view-gustav").innerHTML = `<div class="card"><p class="muted">Lade …</p></div>`;
    const [g] = await Promise.all([run(() => api("GET", "/api/gustav")), run(loadSettings)]);
    if (!g || !S.settings) return;
    S.gustav = g;
    this.render();
  },
  render() {
    const g = S.gustav, s = S.settings;
    $("#view-gustav").innerHTML = `
      <div class="cols">
        <div class="stack">
          <div class="card">
            <h2>Gespräche</h2>
            <div class="fields">
              ${fieldSwitch("live_agent", "Live-Gespräche", "Aus = Gustav spielt nur Clips.", s)}
              ${fieldSwitch("talk_gentle", "Auch im lieben Modus", "Gustav redet dann freundlich statt gruselig.", s)}
              ${fieldNumber("talk_max_s", "Höchstdauer", "Danach verabschiedet er sich.", "s", s)}
              ${fieldNumber("talk_budget_min", "Budget pro Abend", "Kostenbremse für ElevenLabs. 0 = ohne Limit.", "min", s)}
              ${fieldSwitch("transcripts", "Mitschrift anzeigen", "Nur im Arbeitsspeicher, weg nach Neustart.", s)}
              ${fieldText("codeword", "Rätsel-Codewort", "Verrät Gustav nach einem gelösten Rätsel – wer es an der Tür sagt, bekommt ein Extra-Zuckerl.", s)}
            </div>
          </div>
          <div class="card">
            <h2>Verbindung</h2>
            <div class="tiles">
              <div class="tile"><span>Agent</span><b class="small">${g.agent_id ? esc(g.agent_id.slice(0, 14)) + "…" : "fehlt"}</b></div>
              <div class="tile"><span>Modell Gespräch</span><b class="small">${esc(g.model)}</b></div>
              <div class="tile"><span>Modell Clips</span><b class="small">${esc(g.clip_model)}</b></div>
              <div class="tile"><span>Zuletzt übertragen</span><b class="small">${g.pushed ? new Date(g.pushed * 1000).toLocaleString("de-AT", { dateStyle: "short", timeStyle: "short" }) : "–"}</b></div>
            </div>
          </div>
        </div>
        <div class="card">
          <h2>Persönlichkeit</h2>
          <label class="small muted" for="g-first">Erste Nachricht</label>
          <textarea id="g-first" rows="4" maxlength="1000">${esc(g.first_message)}</textarea>
          <label class="small muted" for="g-prompt">Systemprompt</label>
          <textarea id="g-prompt" class="code" maxlength="20000">${esc(g.prompt)}</textarea>
          <p class="small muted">{{modus}} ist „gentle“ oder „full“. Änderungen wirken erst nach „Zu ElevenLabs übertragen“.</p>
          <div class="btn-row">
            <button class="btn" id="g-save">Nur speichern</button>
            <button class="btn primary" id="g-push">Zu ElevenLabs übertragen</button>
          </div>
        </div>
      </div>`;
    bindFields($("#view-gustav"));
    const save = async (push) => {
      const body = { prompt: $("#g-prompt").value, first_message: $("#g-first").value, push };
      const r = await run(() => api("PUT", "/api/gustav", body), push ? "Gustav ist aktualisiert" : "Gespeichert");
      if (r) { S.gustav = { ...S.gustav, ...r }; if (r.hint) toast(r.hint); }
    };
    $("#g-save").onclick = () => save(false);
    $("#g-push").onclick = async (e) => { e.target.disabled = true; await save(true); e.target.disabled = false; };
  },
};

// ---------------------------------------------------------------- Einstellungen und System
function fieldNumber(key, label, hint, unit, s) {
  return `<div class="field"><label for="f-${key}">${label}<span class="hint">${hint}</span></label>
    <div class="unit"><input type="number" inputmode="numeric" id="f-${key}" data-key="${key}" value="${s[key]}">${unit}</div></div>`;
}
function fieldText(key, label, hint, s) {
  return `<div class="field"><label for="f-${key}">${label}<span class="hint">${hint}</span></label>
    <input type="text" class="short" id="f-${key}" data-key="${key}" value="${esc(s[key])}" maxlength="30"></div>`;
}
function fieldSwitch(key, label, hint, s) {
  return `<div class="field"><label for="f-${key}">${label}<span class="hint">${hint}</span></label>
    <label class="switch"><input type="checkbox" id="f-${key}" data-key="${key}" ${s[key] ? "checked" : ""}><span></span></label></div>`;
}
function bindFieldsOnce(root) {
  // Ansichten, die sich neu zeichnen, binden den Listener nur einmal
  if (root.dataset.bound) return;
  root.dataset.bound = "1";
  bindFields(root);
}
function bindFields(root) {
  root.addEventListener("change", (e) => {
    const t = e.target, key = t.dataset.key; if (!key) return;
    const value = t.type === "checkbox" ? t.checked : t.type === "text" ? t.value : Number(t.value);
    saveSettings({ [key]: value }).then(() => { if (S.settings) t.type === "checkbox" ? (t.checked = S.settings[key]) : (t.value = S.settings[key]); });
  });
}

VIEWS.einstellungen = {
  timer: null,
  async open() {
    $("#view-einstellungen").innerHTML = `<div class="card"><p class="muted">Lade …</p></div>`;
    await run(loadSettings);
    if (!S.settings) return;
    this.render();
    this.system();
    this.log();
  },
  render() {
    const s = S.settings;
    $("#view-einstellungen").innerHTML = `
      <div class="cols">
        <div class="stack">
          <div class="card">
            <h2>Erkennung</h2>
            <div class="fields">
              ${fieldSwitch("approach_clips", "Clips beim Vorbeigehen", "Bewegungsmelder löst kurze Clips aus.", s)}
              ${fieldNumber("stay_s", "Stehenbleiben ab", "So lange muss das Radar jemanden sehen.", "s", s)}
              ${fieldNumber("near_cm", "Gesprächsabstand", "Näher → Gustav redet. Weiter weg → er lockt.", "cm", s)}
              ${fieldNumber("leave_s", "Weg nach", "So lange frei = Gespräch endet.", "s", s)}
              ${fieldNumber("cooldown_clip_s", "Pause nach Clip", "Bis zum nächsten Vorbei-Clip.", "s", s)}
              ${fieldNumber("cooldown_talk_s", "Pause nach Gespräch", "Bis zum nächsten Gespräch.", "s", s)}
            </div>
          </div>
          <div class="card">
            <h2>Ton</h2>
            <div class="range">
              <input type="range" min="0" max="100" step="1" id="vol" value="${s.volume}" aria-label="Lautstärke">
              <output id="vol-out">${s.volume} %</output>
            </div>
            <div class="btn-row"><button class="btn" id="tone">${icon("play")}Testton</button></div>
            <div class="fields">${fieldSwitch("leds", "LED-Ring", "Aus im Ruhezustand, atmet bei Clips, pulsiert im Gespräch mit Gustavs Stimme.", s)}</div>
          </div>
          <div class="card">
            <div class="card-head"><h2>Verlauf</h2><span class="muted">pro Abend</span></div>
            <div class="table-wrap" id="history"></div>
          </div>
        </div>
        <div class="stack">
          <div class="card">
            <div class="card-head"><h2>System</h2><span class="muted" id="sys-host"></span></div>
            <div class="tiles" id="sys"></div>
            <div class="btn-row">
              <button class="btn" id="restart">Dienst neu starten</button>
              <button class="btn danger" id="poweroff">Pi herunterfahren</button>
            </div>
          </div>
          <div class="card">
            <div class="card-head"><h2>Protokoll</h2><button class="btn ghost" id="log-refresh">${icon("redo")}Aktualisieren</button></div>
            <div class="logbox" id="logbox">Lade …</div>
          </div>
        </div>
      </div>`;
    bindFields($("#view-einstellungen"));
    const vol = $("#vol");
    vol.oninput = () => { $("#vol-out").textContent = `${vol.value} %`; };
    vol.onchange = () => saveSettings({ volume: Number(vol.value) }, `Lautstärke ${vol.value} %`);
    $("#tone").onclick = () => run(() => api("POST", "/api/aktion", { type: "tone" }), "Testton läuft");
    $("#log-refresh").onclick = () => this.log();
    $("#restart").onclick = async () => {
      if (!confirm("Dienst neu starten? Dauert ca. 10 Sekunden.")) return;
      await run(() => api("POST", "/api/system/neustart"), "Startet neu …");
    };
    $("#poweroff").onclick = async () => {
      if (!confirm("Raspberry Pi wirklich herunterfahren? Danach nur per Strom aus/ein wieder starten.")) return;
      await run(() => api("POST", "/api/system/herunterfahren"), "Fährt herunter …");
    };
  },
  async system() {
    clearTimeout(this.timer);
    if (S.tab !== "einstellungen") return;
    const d = await api("GET", "/api/system").catch(() => null);
    if (d && $("#sys")) {
      const wifi = d.wifi ? `${d.wifi.dbm} dBm${d.wifi.dbm < -75 ? " (schwach)" : ""}` : "–";
      $("#sys-host").textContent = `${d.host} · ${d.ip}`;
      $("#sys").innerHTML = [
        ["Temperatur", d.temp_c != null ? `${d.temp_c.toLocaleString("de-AT")} °C` : "–"],
        ["WLAN", wifi], ["Netz", d.wifi?.ssid || "–"],
        ["Internet", d.online ? "ja" : "nein"], ["Uhr synchron", d.trusted ? "ja" : "nein"],
        ["Läuft seit", `${d.service_h.toLocaleString("de-AT")} h`], ["Pi an seit", `${(d.uptime_h ?? 0).toLocaleString("de-AT")} h`],
        ["Speicher frei", `${d.disk_free_gb.toLocaleString("de-AT")} GB`], ["Pins", `PIR ${d.pins.pir} · Radar ${d.pins.radar}`],
      ].map(([k, v]) => `<div class="tile"><span>${k}</span><b>${esc(v)}</b></div>`).join("");
      $("#history").innerHTML = d.history.length ? `<table>
        <tr><th>Abend</th><th>Beweg.</th><th>Stehen</th><th>Clips</th><th>Gespr.</th><th>Min</th></tr>
        ${d.history.map((h) => `<tr><td>${fmtDate(h.evening)}</td><td>${h.motion || 0}</td><td>${h.stays || 0}</td><td>${h.clips || 0}</td><td>${h.talks || 0}</td><td>${((h.talk_s || 0) / 60).toFixed(1)}</td></tr>`).join("")}
      </table>` : `<p class="empty">Noch keine Abende.</p>`;
    }
    this.timer = setTimeout(() => this.system(), 10000);
  },
  async log() {
    const d = await run(() => api("GET", "/api/log"));
    const box = $("#logbox"); if (!d || !box) return;
    box.innerHTML = d.lines.slice().reverse().map((l) => `<time>${esc(l.hm)}</time>  ${esc(l.msg)}`).join("\n") || "Leer.";
  },
};

// ---------------------------------------------------------------- Anmeldung
function showLogin() {
  const dlg = $("#login");
  if (dlg.open) return;
  dlg.showModal();
  $("#login-pin").focus();
}
$("#login-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const ok = await run(() => api("POST", "/api/login", { pin: $("#login-pin").value }));
  if (ok) { $("#login").close(); show(S.tab); startPolling(); }
});

// ---------------------------------------------------------------- Los
show(location.hash.slice(1) || "start");
startPolling();
