"use strict";
// MatchEyes match replay page. It renders cue sections exactly as received and applies the cue
// display rules (show from, expire, supersede). It computes nothing about the match: scores,
// facts and insight text all come from the server's cues. Text is set with textContent only.

const $ = (id) => document.getElementById(id);
const DASH = "\u2013";
const ELLIPSIS = "\u2026";
const VERDICT_LABEL = {
  explained: "Verified explanation",
  tentative: "Tentative explanation",
  natural_variation: "Natural variation",
  insufficient_evidence: "No verified explanation",
  unavailable: "Investigation unavailable",
};
const RETRACTION_LABEL = "Retracted";
const RETRACTION_REASON = {
  withdrawn: "No longer detected",
  replaced: "Replaced",
  no_verified_explanation: "No verified explanation",
  withheld: "Withheld after audit",
  unavailable: "Verification unavailable",
};
const MOMENT_CLASS = {
  goal: "k-goal",
  red_card: "k-red-card",
  substitution: "k-substitution",
  period_start: "k-period",
  period_end: "k-period",
};
const SECTION_LABEL = { caveat: "Caveat", glossary: "Definition", integrity: "Integrity" };
const PERIOD_LABEL = { 1: "First half", 2: "Second half" };
const REPLAY_TEXT = {
  connecting: "Connecting",
  replaying: "Replaying",
  reconnecting: "Reconnecting",
  complete: "Replay complete",
  unavailable: "Unavailable",
};
const CATCH_UP_GAP_MS = 400;

let source = null;
let edition = null;
let ended = false;
let replay = "connecting";
let catchingUp = true; // messages that arrive in one burst after (re)connecting are history
let lastArrival = 0;
let matches = [];
let current = null; // the selected match from /matches
let items = new Map(); // cue_id -> story item
let expiring = []; // {expires: [period, ms], node, item, id}
let now = [1, 0];
let minutes = new Map(); // snapshot_id -> minute label from the clock ticks
let minute = "";
let period = 0;
let phase = "";
let uid = 0;

function el(tag, cls, text) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined) node.textContent = text;
  return node;
}

function key(instant) {
  return [instant.period, instant.clock_ms];
}

function before(a, b) {
  return a[0] < b[0] || (a[0] === b[0] && a[1] <= b[1]);
}

function minuteOf(snapshot) {
  return (snapshot && minutes.get(snapshot)) || minute || DASH;
}

function announce(text) {
  const node = $("announcer");
  node.textContent = node.textContent === text ? text + "\u00a0" : text;
}

function alertNow(text) {
  $("alerts").textContent = text;
}

function arrive(node) {
  node.classList.remove("arrive");
  void node.offsetWidth; // restart the arrival highlight
  node.classList.add("arrive");
}

function keepFocus(container) {
  if (container.contains(document.activeElement)) $("story-heading").focus();
}

// --- replay and connection state --------------------------------------------------------------

function setReplay(next, detail) {
  replay = next;
  const node = $("replay-state");
  node.className = "replay-state r-" + next;
  node.textContent = REPLAY_TEXT[next];
  const banner = $("connection");
  banner.hidden = !(next === "reconnecting" || next === "unavailable");
  banner.className = "connection c-" + next;
  $("connection-text").textContent = detail || "";
  $("retry").hidden = next !== "unavailable";
}

function arrival() {
  const at = performance.now();
  if (catchingUp && lastArrival && at - lastArrival > CATCH_UP_GAP_MS) catchingUp = false;
  lastArrival = at;
}

// --- hero ----------------------------------------------------------------------------------

function clearScore() {
  $("home-score").textContent = DASH;
  $("away-score").textContent = DASH;
  $("score").classList.add("pending");
  $("score-text").textContent = "Score not yet available.";
}

function setScore(score, live) {
  const home = String(score.home);
  const away = String(score.away);
  const changed = $("home-score").textContent !== home || $("away-score").textContent !== away;
  $("home-score").textContent = home;
  $("away-score").textContent = away;
  $("score").classList.remove("pending");
  if (current) {
    $("score-text").textContent =
      "Score: " + current.home.name + " " + home + ", " + current.away.name + " " + away + ".";
  }
  if (changed && live) arrive($("score"));
}

function renderPhase() {
  $("phase").textContent = phase || PERIOD_LABEL[period] || (period ? "Period " + period : "");
}

function crest(node, team) {
  node.textContent = team.short_name;
}

function teamShort(teamId) {
  if (!current || !teamId) return "";
  if (current.home.team_id === teamId) return current.home.short_name;
  if (current.away.team_id === teamId) return current.away.short_name;
  return "";
}

// --- Match Story ------------------------------------------------------------------------------

function setFeedStatus(status, text) {
  const node = $("feed-status");
  node.className = "feed-status s-" + status;
  node.textContent = text;
}

function refreshEmpty() {
  const showing = [...items.values()].some((item) => !item.retracted);
  $("empty").hidden = showing;
  $("moments-empty").hidden = $("moments").children.length > 0;
  $("log-empty").hidden = $("log").children.length > 0;
}

function sectionNode(s) {
  const cls = s.kind === "interpretation" ? "standfirst" : "section s-" + s.kind;
  const p = el("p", cls);
  if (SECTION_LABEL[s.kind]) p.append(el("span", "section-label", SECTION_LABEL[s.kind]));
  p.append(document.createTextNode(s.text));
  return p;
}

function makeItem() {
  const id = "item-" + ++uid;
  const node = el("article", "item");
  node.setAttribute("aria-labelledby", id + "-head");
  const meta = el("p", "item-meta");
  const label = el("span", "verdict");
  const when = el("span", "item-when");
  meta.append(label, when);
  const kicker = el("p", "item-kicker");
  const head = el("h3", "item-head");
  head.id = id + "-head";
  const body = el("div", "item-body");
  const notice = el("p", "item-notice");
  notice.hidden = true;

  const work = el("details", "disclosure work");
  const workPanel = el("div", "work-panel");
  const workNote = el("p", "work-note", "The verifier's decision is authoritative. Rows marked " +
    "\u201cproposed\u201d are what the reasoner suggested before verification; they are not " +
    "conclusions.");
  const workStatus = el("p", "work-status");
  const stale = el("p", "work-stale");
  stale.hidden = true;
  const staleText = el("span");
  const latest = el("button", "text-button", "Show the latest revision");
  latest.type = "button";
  stale.append(staleText, latest);
  const workRows = el("div", "work-rows");
  workPanel.append(workNote, workStatus, stale, workRows);
  work.append(el("summary", null, "How this insight was checked"), workPanel);

  const history = el("details", "disclosure history");
  const historySummary = el("summary", null, "Revision history");
  const historyList = el("ol", "history-list");
  history.append(historySummary, historyList);

  node.append(meta, kicker, head, body, notice, work, history);
  const item = {
    node, label, when, kicker, head, body, notice, work, workStatus, stale, staleText, workRows,
    historySummary, historyList, storyline: "", revision: 0, verdict: "", published: "",
    shown: 0, retracted: false,
  };
  work.addEventListener("toggle", () => {
    if (work.open && item.shown !== item.revision) showWork(item);
  });
  latest.addEventListener("click", () => showWork(item));
  return item;
}

function fillItem(item, cue, at) {
  const src = cue.source;
  item.storyline = src.storyline_id;
  item.revision = src.revision;
  item.verdict = src.verdict;
  const compromised = src.evidence_integrity === "compromised";
  item.node.className = "item v-" + src.verdict + (compromised ? " compromised" : "") +
    (item.node.classList.contains("arrive") ? " arrive" : "");
  item.label.textContent = (VERDICT_LABEL[src.verdict] || src.verdict) +
    (compromised ? " \u00b7 Integrity warning" : "");
  item.when.textContent = item.published === at
    ? "Published " + at : "Published " + item.published + " \u00b7 updated " + at;
  let fact = "";
  let context = "";
  const body = [];
  for (const s of cue.sections) {
    if (s.kind === "notice") continue; // revision notices are kept in the revision history
    if (s.kind === "fact" && !fact) fact = s.text;
    else if (s.kind === "context" && !context) context = s.text;
    else body.push(sectionNode(s));
  }
  item.kicker.textContent = context;
  item.kicker.hidden = !context;
  item.head.textContent = fact || item.label.textContent;
  item.body.replaceChildren(...body);
  refreshStale(item);
}

function addHistory(item, at, text) {
  const li = el("li");
  li.append(el("span", "history-minute", at), el("span", null, text));
  item.historyList.prepend(li);
  const count = item.historyList.children.length;
  item.historySummary.textContent = "Revision history (" + count + ")";
}

function refreshStale(item) {
  const isStale = item.shown > 0 && item.shown !== item.revision;
  item.stale.hidden = !isStale;
  item.staleText.textContent = isStale
    ? "Showing revision " + item.shown + "; the insight is now at revision " + item.revision + ". "
    : "";
}

function renderRows(box, rows) {
  const out = [];
  let group = null;
  let heading = null;
  for (const row of rows) {
    if (row.section !== heading) {
      heading = row.section;
      out.push(el("h4", null, heading));
      group = el("dl", "work-group");
      out.push(group);
    }
    group.append(el("dt", "tone-" + row.tone, row.label), el("dd", "tone-" + row.tone, row.text));
  }
  box.replaceChildren(...out);
}

async function showWork(item) {
  const storyline = item.storyline;
  const revision = item.revision;
  item.workStatus.textContent = "Loading revision " + revision + ELLIPSIS;
  const workUrl = "/matches/" + encodeURIComponent($("match").value) + "/storylines/" +
    encodeURIComponent(storyline) + "/revisions/" + encodeURIComponent(String(revision));
  try {
    const response = await fetch(workUrl);
    if (!response.ok) throw new Error("unavailable");
    const body = await response.json();
    item.workStatus.textContent = "Revision " + revision + " of this insight.";
    renderRows(item.workRows, body.rows);
    item.shown = revision;
  } catch (error) {
    item.workStatus.textContent = "The work behind this revision is not available.";
    item.workRows.replaceChildren();
    item.shown = 0;
  }
  refreshStale(item);
}

// --- timeline ----------------------------------------------------------------------------------

function logEntry(type, cls, text, at) {
  const li = el("li", "analysis-entry " + cls);
  const body = el("div");
  body.append(el("span", "analysis-type", type), el("span", "analysis-text", text));
  li.append(el("span", "event-minute", at), body);
  $("log").prepend(li);
}

function onMoment(cue, at, live) {
  const src = cue.source;
  const li = el("li", "event " + (MOMENT_CLASS[src.moment] || "k-other") +
    (cue.interrupt ? " key current" : ""));
  const mark = el("span", "event-mark");
  mark.setAttribute("aria-hidden", "true");
  const body = el("div", "event-body");
  let headline = "";
  let statement = "";
  for (const s of cue.sections) {
    if (s.kind === "headline") {
      headline = s.text;
      const head = el("p", "event-head");
      head.append(el("span", null, s.text));
      const team = teamShort(src.team_id);
      if (team) head.append(el("span", "event-team", team));
      body.append(head);
    } else if (s.kind === "score") {
      body.append(el("p", "event-score", s.text));
      statement += " " + s.text;
    } else {
      body.append(el("p", "event-text", s.text));
      statement += " " + s.text;
    }
  }
  li.append(el("span", "event-minute", at), mark, body);
  $("moments").prepend(li);
  setScore(src.score, live);
  if (src.moment === "period_end") phase = period >= 2 ? "Full time" : "Half-time";
  else if (src.moment === "period_start") phase = "";
  renderPhase();
  expiring.push({ expires: key(cue.expires_at), node: li });
  if (live) {
    arrive(li);
    if (cue.interrupt) announce(at + " " + headline + "." + statement);
  }
}

// --- cues ----------------------------------------------------------------------------------

function onCue(cue) {
  const at = minuteOf(cue.snapshot_id);
  const live = !catchingUp;
  if (cue.kind === "moment") {
    onMoment(cue, at, live);
  } else if (cue.kind === "insight") {
    const item = makeItem();
    item.published = at;
    fillItem(item, cue, at);
    addHistory(item, at, "Published.");
    items.set(cue.cue_id, item);
    $("cards").prepend(item.node);
    logEntry("Insight published", "a-published", item.head.textContent, at);
    if (live) {
      arrive(item.node);
      announce("New insight, " + at + ": " + item.head.textContent);
    }
  } else if (cue.kind === "revision") {
    const item = items.get(cue.supersedes[0]);
    if (!item) return;
    items.delete(cue.supersedes[0]);
    const previous = item.verdict;
    fillItem(item, cue, at);
    items.set(cue.cue_id, item);
    if (cue.interrupt) {
      const notice = cue.sections.find((s) => s.kind === "notice");
      const text = notice ? notice.text : cue.sections[0].text;
      addHistory(item, at, text);
      logEntry("Insight revised", "a-revised", text, at);
      item.notice.textContent = "Revised " + at + ". See the revision history.";
      item.notice.className = "item-notice";
      item.notice.hidden = false;
      if (live && previous !== item.verdict) {
        arrive(item.node);
        announce("Insight revised, " + at + ": now " + item.label.textContent + ".");
      }
    }
  } else if (cue.kind === "retraction") {
    const item = items.get(cue.supersedes[0]);
    const reason = cue.sections[0].text;
    const fact = cue.sections[1] ? cue.sections[1].text : "";
    logEntry("Insight retracted", "a-retracted", reason + (fact ? " " + fact : ""), at);
    if (live) announce("Insight retracted, " + at + ". " + reason);
    if (!item) return;
    item.retracted = true;
    item.node.classList.add("retracted");
    const why = RETRACTION_REASON[cue.source.reason];
    item.label.textContent = RETRACTION_LABEL + (why ? " \u00b7 " + why : "");
    item.notice.textContent = reason;
    item.notice.className = "item-notice retraction";
    item.notice.hidden = false;
    addHistory(item, at, reason);
    expiring.push({ expires: key(cue.expires_at), item, id: cue.supersedes[0] });
    if (live) arrive(item.node);
  } else if (cue.kind === "status") {
    const text = cue.sections.map((s) => s.text).join(" ");
    setFeedStatus(cue.source.status, text);
    if (live && cue.source.status === "unavailable") alertNow(text);
  }
  refreshEmpty();
}

function onClock(tick) {
  now = [tick.period, tick.clock_ms];
  minutes.set(tick.snapshot_id, tick.minute);
  minute = tick.minute;
  if (tick.period !== period) {
    if (period && phase !== "Full time") phase = "";
    period = tick.period;
  }
  $("clock").textContent = tick.minute;
  renderPhase();
  expiring = expiring.filter((entry) => {
    if (!before(entry.expires, now)) return true;
    if (entry.item) {
      keepFocus(entry.item.node);
      entry.item.node.remove();
      items.delete(entry.id);
    } else {
      entry.node.classList.remove("current");
    }
    return false;
  });
  refreshEmpty();
}

// --- connection ----------------------------------------------------------------------------

function reset() {
  keepFocus($("cards"));
  items = new Map();
  expiring = [];
  now = [1, 0];
  minutes = new Map();
  minute = "";
  period = 0;
  phase = "";
  $("cards").replaceChildren();
  $("moments").replaceChildren();
  $("log").replaceChildren();
  clearScore();
  $("clock").textContent = DASH;
  renderPhase();
  setFeedStatus("awaiting_snapshot", "");
  refreshEmpty();
}

function audience() {
  return document.querySelector("input[name=audience]:checked").value;
}

function connect() {
  if (source) source.close();
  edition = null;
  ended = false;
  reset();
  const id = $("match").value;
  if (!id) return;
  catchingUp = true;
  lastArrival = 0;
  setReplay("connecting");
  const params = new URLSearchParams({ audience: audience(), club: $("club").value });
  const stream = new EventSource("/matches/" + encodeURIComponent(id) + "/stream?" + params);
  source = stream;
  stream.addEventListener("open", () => {
    catchingUp = true;
    lastArrival = performance.now();
    if (replay === "reconnecting") announce("Reconnected.");
    setReplay(ended ? "complete" : "replaying");
  });
  stream.addEventListener("edition", (e) => {
    arrival();
    const number = JSON.parse(e.data).edition;
    if (number !== edition) {
      const restart = edition !== null;
      reset();
      ended = false;
      if (restart) {
        setReplay("replaying");
        if (!catchingUp) announce("The replay is restarting from kick-off.");
      }
    }
    edition = number;
  });
  stream.addEventListener("clock", (e) => {
    arrival();
    onClock(JSON.parse(e.data));
  });
  stream.addEventListener("cue", (e) => {
    arrival();
    onCue(JSON.parse(e.data));
  });
  stream.addEventListener("end", () => {
    arrival();
    ended = true;
    setReplay("complete");
    if (!catchingUp) announce("Replay complete.");
  });
  stream.addEventListener("error", () => {
    if (stream !== source) return;
    if (stream.readyState === EventSource.CLOSED) {
      const text = "This replay cannot be reached right now; the server may be busy.";
      setReplay("unavailable", text);
      alertNow(text);
    } else if (!ended) {
      if (replay !== "reconnecting") announce("Connection lost. Reconnecting.");
      setReplay("reconnecting", "Connection lost. Reconnecting automatically" + ELLIPSIS);
    }
  });
}

// --- match selection and provenance ----------------------------------------------------------

function fact(dl, term, value) {
  if (!value && value !== 0) return;
  dl.append(el("dt", null, term), el("dd", null, String(value)));
}

function renderAbout(m) {
  const r = m.reasoner || {};
  const parts = [];
  if (m.synthetic) {
    parts.push(el("p", null, "This is a fictional match. The league, clubs, players and events " +
      "are synthetic and were created for this demonstration."));
  }
  parts.push(el("p", null, "The server replays the match from its recorded event data, so every " +
    "viewer sees the same replay. Every insight is checked by the deterministic verifier, whose " +
    "decision is authoritative."));
  if (r.kind === "recorded-model") {
    parts.push(el("p", null, "The reasoning in this replay comes from a recorded model run, " +
      "replayed from its transcript. No live model calls are made."));
  } else if (r.kind) {
    parts.push(el("p", null, "The reasoning in this replay comes from the deterministic " +
      "reference reasoner."));
  }
  const dl = el("dl", "facts");
  fact(dl, "Competition", m.competition);
  fact(dl, "Matchday", m.matchday);
  fact(dl, "Venue", m.venue);
  fact(dl, "Reasoner", r.kind === "recorded-model" ? "Recorded model run" : r.kind && "Reference");
  fact(dl, "Reasoner name", r.name);
  fact(dl, "Deployment", r.deployment);
  fact(dl, "Served model", r.served_models);
  fact(dl, "Transcript SHA-256", r.transcript_sha256);
  parts.push(dl);
  parts.push(el("p", null, "All text is shown exactly as the server sent it; this page computes " +
    "nothing about the match."));
  $("about-body").replaceChildren(...parts);
}

function selectMatch() {
  current = matches.find((x) => x.match_id === $("match").value) || null;
  const club = $("club");
  club.replaceChildren(new Option("None", ""));
  if (!current) return;
  const m = current;
  for (const team of [m.home, m.away]) club.append(new Option(team.name, team.team_id));
  $("home").textContent = m.home.name;
  $("away").textContent = m.away.name;
  $("home-short").textContent = m.home.short_name;
  $("away-short").textContent = m.away.short_name;
  crest($("home-crest"), m.home);
  crest($("away-crest"), m.away);
  $("meta").textContent = m.competition + " \u00b7 Matchday " + m.matchday + " \u00b7 " + m.venue;
  $("match-title").textContent = m.home.name + " v " + m.away.name + ", replay";
  document.title = m.home.name + " v " + m.away.name + " \u00b7 MatchEyes";
  $("provenance").textContent = (m.synthetic ? "Fictional match \u00b7 " : "") + "Replay";
  renderAbout(m);
}

function showLane() {
  const lane = document.querySelector("input[name=lane]:checked").value;
  $("lane-moments").hidden = lane !== "moments";
  $("lane-log").hidden = lane !== "log";
}

async function init() {
  const response = await fetch("/matches");
  if (!response.ok) throw new Error("unavailable");
  matches = (await response.json()).matches;
  const select = $("match");
  for (const m of matches) {
    select.append(new Option(m.home.name + " v " + m.away.name + " (" + m.venue + ")", m.match_id));
  }
  selectMatch();
  select.addEventListener("change", () => { selectMatch(); connect(); });
  for (const radio of document.querySelectorAll("input[name=audience]")) {
    radio.addEventListener("change", connect);
  }
  for (const radio of document.querySelectorAll("input[name=lane]")) {
    radio.addEventListener("change", showLane);
  }
  $("club").addEventListener("change", connect);
  $("retry").addEventListener("click", connect);
  $("controls").addEventListener("submit", (e) => e.preventDefault());
  $("about-link").addEventListener("click", () => { $("about-details").open = true; });
  if (!matches.length) setReplay("unavailable", "No match is available to replay.");
  connect();
}

clearScore();
refreshEmpty();
init().catch(() => setReplay("unavailable", "Could not load matches."));
