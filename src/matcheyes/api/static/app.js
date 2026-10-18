"use strict";
// MatchEyes match replay page. It renders cue sections exactly as received and applies the cue
// display rules (show from, expire, supersede). It computes nothing about the match: scores,
// facts and insight text all come from the server's cues, and the chart places them only by
// their snapshot's clock tick. Text is set with textContent only.

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
const KEY_MOMENT = { goal: "Goal", red_card: "Red card" };
const SECTION_LABEL = {
  trigger: "What happened",
  interpretation: "Interpretation",
  no_insight: "Interpretation",
  caveat: "Limits",
  involvement: "Player involvement",
  glossary: "Definition",
  integrity: "Evidence warning",
};
// The server appends the verifier's label to an explained view as "[verified, <strength>]" or
// "[verified on remaining evidence, <strength>]", optionally followed by "(evidence <ids>)" on the
// broadcaster view. When the text ends in exactly that shape it is shown as the card's
// verification status instead of inside the prose; any other text is left as sent.
const VERIFIED_SUFFIX =
  /\s*\[(verified|verified on remaining evidence), ([a-z]+)\](?: \(evidence ([\w.:-]+(?:, [\w.:-]+)*)\))?$/;
const PERIOD_LABEL = { 1: "First half", 2: "Second half" };
const REPLAY_TEXT = {
  connecting: "Connecting",
  replaying: "Replaying",
  reconnecting: "Reconnecting",
  complete: "Replay complete",
  unavailable: "Unavailable",
  paused: "Paused",
  reloading: "Reloading",
};
const PAUSABLE = new Set(["replaying", "reconnecting", "complete"]);
const CATCH_UP_GAP_MS = 400;

let source = null;
let edition = null;
let run = null; // the server's replay identity; a different one is a different replay
let started = false;
let ended = false;
let replay = "connecting";
let replayDetail = "";
let catchingUp = true; // messages that arrive in one burst after (re)connecting are history
let lastArrival = 0;
let matches = [];
let current = null; // the selected match from /matches
let items = new Map(); // cue_id -> story item
let expiring = []; // {expires: [period, ms], node, item, id}
let now = [1, 0];
let minutes = new Map(); // snapshot_id -> minute label from the clock ticks
let eventsAt = new Map(); // snapshot_id -> event detail panels opened at that snapshot
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

function scoreText(score) {
  if (!current) return score.home + DASH + score.away;
  return current.home.short_name + " " + score.home + DASH + score.away + " " + current.away.short_name;
}

// --- replay and connection state --------------------------------------------------------------

function setReplay(next, detail) {
  replay = next;
  replayDetail = detail || "";
  renderReplay();
}

// Replay status is always visible in the hero pill. The connection banner carries detail for
// non-steady states (and when paused), using only existing replay/detail text.
const BANNER_STATES = new Set([
  "connecting", "reconnecting", "unavailable", "reloading", "complete", "paused",
]);

function renderReplay() {
  const shown = held && replay !== "reconnecting" && replay !== "unavailable" ? "paused" : replay;
  const node = $("replay-state");
  node.className = "replay-state r-" + shown;
  node.textContent = REPLAY_TEXT[shown];
  const banner = $("connection");
  const detail = replayDetail || (
    shown === "connecting" ? "Connecting to the replay." :
    shown === "replaying" ? "" :
    shown === "paused" ? (held ? holdText(held) : "Updates are paused.") :
    shown === "complete" ? "Replay complete." :
    shown === "reloading" ? "Reloading the replay." :
    shown === "reconnecting" ? "Connection lost. Reconnecting to the replay." :
    shown === "unavailable" ? "The replay is unavailable." :
    ""
  );
  const showBanner = BANNER_STATES.has(shown) || Boolean(detail);
  banner.hidden = !showBanner || (shown === "replaying" && !held && !detail);
  banner.className = "connection c-" + shown;
  $("connection-text").textContent = detail || REPLAY_TEXT[shown];
  $("retry").hidden = replay !== "unavailable";
  const button = $("hold");
  button.textContent = held ? "Resume updates" : "Pause updates";
  button.setAttribute("aria-disabled", held || canPause() ? "false" : "true");
  $("hold-status").textContent = held ? holdText(held) : "";
  $("hold-status").hidden = !held;
  refreshEmpty();
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
  node.querySelector(".crest-mono").textContent = team.short_name;
}

// The choice lasts for this page only: nothing is stored, so a reload returns to dark.
function setTheme() {
  document.documentElement.dataset.theme = document.querySelector("input[name=theme]:checked").value;
}

function teamShort(teamId) {
  if (!current || !teamId) return "";
  if (current.home.team_id === teamId) return current.home.short_name;
  if (current.away.team_id === teamId) return current.away.short_name;
  return "";
}

function heroEvent(kind, teamId, at) {
  if (!current) return;
  const list = current.home.team_id === teamId ? $("home-events")
    : current.away.team_id === teamId ? $("away-events") : null;
  if (!list) return;
  const li = el("li", "hero-event " + MOMENT_CLASS[kind]);
  const mark = el("span", "mark");
  mark.setAttribute("aria-hidden", "true");
  li.append(mark, el("span", "sr-only", KEY_MOMENT[kind] + " "), el("span", null, at));
  list.append(li);
  list.hidden = false;
}

// --- What the Match Reveals -------------------------------------------------------------------

function setFeedStatus(status, text) {
  const node = $("feed-status");
  node.className = "feed-status s-" + status;
  node.textContent = text;
}

function refreshEmpty() {
  const showing = [...items.values()].some((item) => !item.retracted);
  $("empty").hidden = showing;
  $("moments-empty").hidden = $("moments").children.length > 0;
  $("moments-note").hidden = !$("moments-empty").hidden;
  $("log-empty").hidden = $("log").children.length > 0;
  $("brief-moments-empty").hidden = $("brief-moments").children.length > 0;
  $("brief-moments-empty").textContent = started ? "No goals or red cards so far."
    : replay === "unavailable" ? "Not available until the replay can be reached."
      : "Waiting for the replay to start.";
  $("brief-analysis-empty").hidden = $("brief-analysis").children.length > 0;
}

function jumpTo(item) {
  if (!item.node.isConnected) return;
  item.head.focus();
  item.head.scrollIntoView({ block: "start" });
  arrive(item.node);
}

function jumpButton(item, text) {
  const button = el("button", "link-button", text);
  button.type = "button";
  button.addEventListener("click", () => jumpTo(item));
  item.links.push(button);
  return button;
}

function retireLinks(item) {
  for (const button of item.links) {
    keepFocus(button);
    if (button.classList.contains("analysis-go")) {
      button.remove();
    } else {
      button.disabled = true;
      button.textContent += " (no longer shown)";
    }
  }
  item.links = [];
}

function splitVerification(text) {
  const match = VERIFIED_SUFFIX.exec(text);
  if (!match) return { text, verified: "", strength: "", cited: "" };
  return {
    text: text.slice(0, match.index), verified: match[1], strength: match[2], cited: match[3] || "",
  };
}

function makeItem() {
  const id = "item-" + ++uid;
  const node = el("article", "item");
  node.setAttribute("aria-labelledby", id + "-head");
  const top = el("div", "item-top");
  const label = el("p", "verdict");
  const check = el("p", "check");
  check.hidden = true;
  top.append(label, check);
  const when = el("p", "item-when");
  const kicker = el("p", "item-kicker");
  const eyebrow = el("p", "item-eyebrow", "What the data shows");
  const head = el("h3", "item-head");
  head.id = id + "-head";
  head.tabIndex = -1;
  const body = el("dl", "item-body");
  const notice = el("div", "item-notice");
  notice.hidden = true;
  const noticeText = el("p");
  const toHistory = el("button", "link-button", "See the revision history");
  toHistory.type = "button";
  notice.append(noticeText, toHistory);

  const work = el("details", "disclosure work");
  const workPanel = el("div", "work-panel");
  const workNote = el("p", "work-note", "Trace the Evidence shows the server\u2019s work for this " +
    "revision. The verifier\u2019s decision is authoritative. Rows marked \u201cproposed\u201d are " +
    "what the reasoner suggested before verification; they are not conclusions.");
  const workStatus = el("p", "work-status");
  const stale = el("p", "work-stale");
  stale.hidden = true;
  const staleText = el("span");
  const latest = el("button", "text-button", "Show the latest revision");
  latest.type = "button";
  stale.append(staleText, latest);
  const workRows = el("div", "work-rows");
  workPanel.append(workNote, workStatus, stale, workRows);
  work.append(el("summary", null, "Trace the Evidence"), workPanel);

  const history = el("details", "disclosure history");
  const historySummary = el("summary", null, "Revision history");
  const historyList = el("ol", "history-list");
  history.append(historySummary, historyList);

  node.append(top, when, kicker, eyebrow, head, body, notice, work, history);
  const item = {
    node, label, check, when, kicker, head, body, notice, noticeText, work, workStatus, stale,
    staleText, workRows, history, historySummary, historyList, storyline: "", revision: 0,
    verdict: "", published: "", shown: 0, retracted: false, links: [], brief: null,
  };
  work.addEventListener("toggle", () => {
    if (work.open && item.shown !== item.revision) showWork(item);
  });
  latest.addEventListener("click", () => showWork(item));
  toHistory.addEventListener("click", () => {
    history.open = true;
    historySummary.focus();
  });
  return item;
}

function bodyRow(term, text, cls) {
  return [el("dt", cls, term), el("dd", cls, text)];
}

function fillItem(item, cue, at) {
  const src = cue.source;
  item.storyline = src.storyline_id;
  item.revision = src.revision;
  item.verdict = src.verdict;
  const compromised = src.evidence_integrity === "compromised";
  item.node.className = "item v-" + src.verdict + (compromised ? " compromised" : "") +
    (item.retracted ? " retracted" : "") +
    (item.node.classList.contains("arrive") ? " arrive" : "");
  if (!item.retracted) {
    item.label.textContent = (VERDICT_LABEL[src.verdict] || src.verdict) +
      (compromised ? " \u00b7 Integrity warning" : "");
  }
  item.when.textContent = item.published === at
    ? "Published " + at : "Published " + item.published + " \u00b7 updated " + at;
  let fact = "";
  let context = "";
  let verified = "";
  let strength = "";
  let cited = "";
  const body = [];
  for (const s of cue.sections) {
    if (s.kind === "notice") continue; // revision notices are kept in the revision history
    if (s.kind === "fact" && !fact) fact = s.text;
    else if (s.kind === "context" && !context) context = s.text;
    else if (s.kind === "interpretation") {
      const split = splitVerification(s.text);
      if (split.verified) {
        verified = split.verified;
        strength = split.strength;
        cited = split.cited;
      }
      body.push(...bodyRow(SECTION_LABEL.interpretation, split.text, "b-interpretation"));
    } else {
      body.push(...bodyRow(SECTION_LABEL[s.kind] || "Note", s.text, "b-" + s.kind));
    }
  }
  item.kicker.textContent = context;
  item.kicker.hidden = !context;
  item.head.textContent = fact || item.label.textContent;
  item.body.replaceChildren(...body);
  item.body.hidden = body.length === 0;
  item.check.hidden = !verified;
  item.check.className = "check" + (verified === "verified" ? "" : " partial");
  item.check.replaceChildren();
  if (verified) {
    const mark = el("span", "check-mark", "\u2713");
    mark.setAttribute("aria-hidden", "true");
    item.check.append(mark, el("span", null, verified[0].toUpperCase() + verified.slice(1)),
      el("span", "check-strength", "Strength: " + strength));
    if (cited) item.check.append(el("span", "check-strength", "Evidence cited: " + cited));
  }
  if (item.brief) {
    item.brief.firstChild.textContent = item.label.textContent;
    item.brief.lastChild.textContent = item.head.textContent;
  }
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
      const section = el("details", "work-section");
      section.open = out.length === 0;
      group = el("dl", "work-group");
      section.append(el("summary", null, heading), group);
      out.push(section);
    }
    group.append(el("dt", "tone-" + row.tone, row.label), el("dd", "tone-" + row.tone, row.text));
  }
  box.replaceChildren(...out);
}

async function showWork(item) {
  const storyline = item.storyline;
  const revision = item.revision;
  const requestedRun = run;
  item.workStatus.textContent = "Loading revision " + revision + ELLIPSIS;
  const workUrl = "/matches/" + encodeURIComponent($("match").value) + "/storylines/" +
    encodeURIComponent(storyline) + "/revisions/" + encodeURIComponent(String(revision));
  try {
    const response = await fetch(workUrl);
    if (!response.ok) throw new Error("unavailable");
    const body = await response.json();
    if (run !== requestedRun || body.run !== run) throw new Error("another replay");
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

function briefAdd(item) {
  const li = el("li");
  const button = jumpButton(item, item.head.textContent);
  li.append(el("span", "brief-verdict", item.label.textContent), button);
  item.brief = li;
  $("brief-analysis").prepend(li);
}

function briefRemove(item) {
  if (!item.brief) return;
  keepFocus(item.brief);
  item.brief.remove();
  item.brief = null;
}

// --- timeline ----------------------------------------------------------------------------------

function logEntry(type, cls, text, at, item) {
  const li = el("li", "analysis-entry " + cls);
  const body = el("div");
  body.append(el("span", "analysis-type", type), el("span", "analysis-text", text));
  if (item && item.node.isConnected) {
    const go = jumpButton(item, "View this insight");
    go.classList.add("analysis-go");
    body.append(go);
  }
  li.append(el("span", "event-minute", at), body);
  $("log").prepend(li);
}

function contextFor(panel, at) {
  const list = el("ul", "context-list");
  panel.append(el("p", "context-head", "Verified Analysis showing at " + at), list);
  const none = el("p", "quiet", "No verified insight was showing at this point.");
  panel.append(none, el("p", "context-note",
    "Shown for context only. MatchEyes does not link this analysis to the event."));
  return { list, none };
}

function addContext(context, item) {
  const li = el("li");
  li.append(jumpButton(item, item.head.textContent));
  context.list.append(li);
  context.none.hidden = true;
}

function onPeriod(cue, at, src) {
  const li = el("li", "period-mark");
  let headline = "";
  let statement = "";
  for (const s of cue.sections) {
    if (s.kind === "headline") headline = s.text;
    else statement += (statement ? " " : "") + s.text;
  }
  const label = el("p", "period-label");
  label.append(el("span", "period-name", headline));
  if (src.moment === "period_end") label.append(el("span", "period-score", scoreText(src.score)));
  if (statement) label.append(el("span", "sr-only", " " + statement));
  li.append(el("span", "event-minute", at), label);
  return li;
}

function onEvent(cue, at, src) {
  const li = el("li", "event " + (MOMENT_CLASS[src.moment] || "k-other") +
    (KEY_MOMENT[src.moment] ? " key" : "") + (cue.interrupt ? " current" : ""));
  const details = el("details", "event-details");
  const summary = el("summary", "event-summary");
  const mark = el("span", "event-mark");
  mark.setAttribute("aria-hidden", "true");
  const body = el("span", "event-body");
  for (const s of cue.sections) {
    if (s.kind === "headline") {
      const head = el("span", "event-head");
      head.append(el("span", null, s.text));
      const team = teamShort(src.team_id);
      if (team) head.append(el("span", "event-team", team));
      body.append(head);
    } else if (s.kind === "score") {
      body.append(el("span", "event-score", s.text));
    } else {
      body.append(el("span", "event-text", s.text));
    }
  }
  summary.append(el("span", "event-minute", at), mark, body);
  const panel = el("div", "event-more");
  panel.append(el("p", "event-fact", "Score at this point: " + scoreText(src.score)));
  const context = contextFor(panel, at);
  for (const item of items.values()) if (!item.retracted) addContext(context, item);
  const waiting = eventsAt.get(cue.snapshot_id) || [];
  waiting.push(context);
  eventsAt.set(cue.snapshot_id, waiting);
  details.append(summary, panel);
  li.append(details);
  return li;
}

function briefMoment(cue, at, src) {
  const li = el("li", "brief-moment " + MOMENT_CLASS[src.moment]);
  const mark = el("span", "mark");
  mark.setAttribute("aria-hidden", "true");
  const head = el("span", "brief-head");
  head.append(el("span", "brief-minute", at), el("span", null, KEY_MOMENT[src.moment]));
  const team = teamShort(src.team_id);
  if (team) head.append(el("span", "event-team", team));
  const text = cue.sections.find((s) => s.kind === "moment");
  li.append(mark, head);
  if (text) li.append(el("span", "brief-text", text.text));
  $("brief-moments").append(li);
}

function onMoment(cue, at, live) {
  const src = cue.source;
  const isPeriod = src.moment === "period_start" || src.moment === "period_end";
  const li = isPeriod ? onPeriod(cue, at, src) : onEvent(cue, at, src);
  $("moments").prepend(li);
  chartMoment(cue, at, src, li);
  if (KEY_MOMENT[src.moment]) {
    heroEvent(src.moment, src.team_id, at);
    briefMoment(cue, at, src);
  }
  setScore(src.score, live);
  if (src.moment === "period_end") phase = period >= 2 ? "Full time" : "Half-time";
  else if (src.moment === "period_start") phase = "";
  renderPhase();
  if (!isPeriod) expiring.push({ expires: key(cue.expires_at), node: li });
  if (live) {
    arrive(li);
    if (cue.interrupt) {
      const headline = cue.sections.find((s) => s.kind === "headline");
      const rest = cue.sections.filter((s) => s.kind !== "headline").map((s) => s.text).join(" ");
      announce(at + " " + (headline ? headline.text : "") + ". " + rest);
    }
  }
}

// --- match at a glance -------------------------------------------------------------------------
// An SVG overview of the replay so far, drawn around a top-down pitch. Every mark sits on a time
// rail at the clock tick of the snapshot that carried its cue (snapshot time, not event time):
// home events on the rail above the pitch, away events on the rail below. Each half fills its
// half of the pitch's length. The ball travels along the middle of the pitch to the timeline
// position (the replay clock or the selected event); it is never a tracked position. The score
// lines are the moment cues' scores.

const HALF_MS = 45 * 60000;
const CHART = {
  left: 68, right: 16, gap: 14, period: 32, railTop: 44, homeRow: 88, row: 16, pitchTop: 114,
  apron: 8, runoff: 10, awayRail: 62, noteRow: 11, stripes: 12,
};
// Pitch markings in metres, drawn to scale across a pitch 68 m wide.
const PITCH = {
  width: 68, box: 16.5, boxWidth: 40.32, six: 5.5, sixWidth: 18.32, spot: 11, arc: 9.15,
  goal: 7.32, goalDepth: 2.44, corner: 1, spotRadius: 0.3,
};
const BALL_TURN = 360 / (2 * Math.PI * 9); // degrees per unit rolled by the 9-unit ball
const HALF_CLOSE = { 1: "HT", 2: "FT" };
const NOTE_KIND = { insight: "published", revision: "revised", retraction: "retracted" };
const READOUT_EMPTY = "Select an event on the chart to see its details.";
const SVG_NS = $("chart-svg").namespaceURI;

let snapshotAt = new Map(); // snapshot_id -> [period, clock_ms] from the clock ticks
let halfEnd = { 1: HALF_MS, 2: HALF_MS }; // latest snapshot time seen in each half
let marks = []; // match-event marks, in cue order
let notes = []; // analysis-lane marks
let storylines = new Map(); // storyline_id -> {row, open, nodes}
let scores = []; // {at, score, goal} from every moment cue
let rove = null; // the mark that holds the chart's tab stop
let picked = null;
let gapSeen = false;
let chartWidth = 1000;
let G = {}; // vertical layout below the pitch, which grows with the chart's width
let pitchDrawn = ""; // the geometry the pitch was last drawn for

function svgEl(tag, cls, attrs) {
  const node = document.createElementNS(SVG_NS, tag);
  if (cls) node.setAttribute("class", cls);
  for (const name of Object.keys(attrs || {})) node.setAttribute(name, String(attrs[name]));
  return node;
}

function svgText(cls, x, y, text, anchor) {
  const node = svgEl("text", cls, { x: x.toFixed(1), y });
  if (anchor) node.setAttribute("text-anchor", anchor);
  node.textContent = text;
  return node;
}

function svgLine(cls, x1, y1, x2, y2) {
  return svgEl("line", cls, { x1: x1.toFixed(1), y1, x2: x2.toFixed(1), y2 });
}

function snapshotTime(cue) {
  return snapshotAt.get(cue.snapshot_id) || now.slice();
}

function chartX(at) {
  const half = (chartWidth - CHART.left - CHART.right - CHART.gap) / 2;
  const p = at[0] === 2 ? 2 : 1;
  return CHART.left + (p === 2 ? half + CHART.gap : 0) + at[1] * half / halfEnd[p];
}

function measure() {
  const length = chartWidth - CHART.left - CHART.right;
  const pitch = Math.round(Math.min(200, Math.max(84, length * 0.26)));
  const axis = CHART.pitchTop + pitch + CHART.apron + 18;
  const awayTop = axis + 30;
  const scoreTop = awayTop + CHART.awayRail + 22;
  const scoreBase = scoreTop + 30;
  const notes = scoreBase + 24;
  G = { pitch, axis, awayTop, awayRow: awayTop + 14, scoreTop, scoreBase, notes, end: notes + 28 };
  G.height = G.end + 4;
}

function teamName(teamId) {
  if (!current || !teamId) return "";
  if (current.home.team_id === teamId) return current.home.name;
  if (current.away.team_id === teamId) return current.away.name;
  return "";
}

function drawAxis() {
  const out = [];
  const left = CHART.left;
  const right = chartWidth - CHART.right;
  const rail = (y, height) => svgEl("rect", "c-rail", {
    x: left - CHART.runoff, y, width: (right - left + 2 * CHART.runoff).toFixed(1), height, rx: 6,
  });
  const homeBottom = CHART.pitchTop - CHART.apron - 6;
  out.push(rail(CHART.railTop, homeBottom - CHART.railTop), rail(G.awayTop, CHART.awayRail));
  out.push(svgLine("c-rail-line", left, CHART.homeRow, right, CHART.homeRow));
  out.push(svgLine("c-rail-line", left, G.awayRow, right, G.awayRow));
  for (const half of [1, 2]) {
    const start = chartX([half, 0]);
    const close = chartX([half, halfEnd[half]]);
    if (halfEnd[half] > HALF_MS) {
      const from = chartX([half, HALF_MS]);
      out.push(svgEl("rect", "c-stoppage", {
        x: from.toFixed(1), y: CHART.railTop, width: (close - from).toFixed(1),
        height: G.end - CHART.railTop,
      }));
      if (close - from > 56) out.push(svgText("c-small", from + 3, CHART.railTop - 4, "Stoppage"));
    }
    out.push(svgText("c-half", start, 12, PERIOD_LABEL[half]));
    for (let m = half === 1 ? 0 : 15; m <= 45; m += 15) {
      const x = chartX([half, m * 60000]);
      out.push(svgLine("c-grid", x, CHART.railTop, x, G.end));
    }
  }
  out.push(svgLine("c-band", left, G.scoreBase + 8, right, G.scoreBase + 8));
  if (snapshotAt.size) {
    const x = chartX(now);
    out.push(svgLine("c-now", x, CHART.railTop, x, G.end));
  }
  if (current) {
    const chip = (side, y) => svgEl("rect", "c-chip side-" + side,
      { x: 0, y: y - 6, width: 4, height: 12, rx: 1 });
    out.push(chip("home", CHART.homeRow), svgText("c-label", 9, CHART.homeRow + 4,
      current.home.short_name));
    out.push(chip("away", G.awayRow), svgText("c-label", 9, G.awayRow + 4, current.away.short_name));
    out.push(svgText("c-label", 0, G.scoreTop - 2, "Score"));
    out.push(svgText("c-small", 0, G.scoreTop + 14, current.home.short_name));
    out.push(svgLine("c-key c-step-home", 30, G.scoreTop + 10, 54, G.scoreTop + 10));
    out.push(svgText("c-small", 0, G.scoreTop + 28, current.away.short_name));
    out.push(svgLine("c-key c-step-away", 30, G.scoreTop + 24, 54, G.scoreTop + 24));
  }
  out.push(svgText("c-label", 0, G.notes + 8, "Verified Analysis"));
  $("chart-axis").replaceChildren(...out);
}

// The pitch is scenery: it depends only on the chart's size, so it is redrawn only on resize.
function drawPitch() {
  const left = CHART.left;
  const right = chartWidth - CHART.right;
  const top = CHART.pitchTop;
  const h = G.pitch;
  const drawn = left + " " + right + " " + h;
  if (drawn === pitchDrawn) return;
  pitchDrawn = drawn;
  const f = (v) => v.toFixed(1);
  const m = (metres) => metres * h / PITCH.width;
  const mid = (left + right) / 2;
  const cy = top + h / 2;
  const turf = {
    x: f(left - CHART.runoff), y: top - CHART.apron, width: f(right - left + 2 * CHART.runoff),
    height: h + 2 * CHART.apron, rx: 6,
  };
  const out = [svgEl("rect", "p-turf", turf)];
  const stripe = (right - left) / CHART.stripes;
  for (let i = 1; i < CHART.stripes; i += 2) {
    out.push(svgEl("rect", "p-stripe", {
      x: f(left + i * stripe), y: turf.y, width: f(stripe), height: turf.height,
    }));
  }
  out.push(svgEl("rect", "p-light", { ...turf, fill: "url(#pitch-light)" }));
  out.push(svgEl("rect", "p-light", { ...turf, fill: "url(#pitch-edge)" }));
  const lines = svgEl("g", "p-line");
  lines.append(svgEl("rect", null, { x: f(left), y: top, width: f(right - left), height: h }),
    svgLine(null, mid, top, mid, top + h),
    svgEl("circle", null, { cx: f(mid), cy: f(cy), r: f(m(PITCH.arc)) }));
  const spots = [mid];
  const rise = Math.sqrt(PITCH.arc ** 2 - (PITCH.box - PITCH.spot) ** 2);
  for (const [line, dir] of [[left, 1], [right, -1]]) {
    const box = (depth, width) => svgEl("rect", null, {
      x: f(dir > 0 ? line : line - m(depth)), y: f(cy - m(width) / 2), width: f(m(depth)),
      height: f(m(width)),
    });
    const edge = f(line + dir * m(PITCH.box));
    const r = f(m(PITCH.arc));
    lines.append(box(PITCH.box, PITCH.boxWidth), box(PITCH.six, PITCH.sixWidth),
      svgEl("path", null, {
        d: "M" + edge + " " + f(cy - m(rise)) + " A" + r + " " + r + " 0 0 " + (dir > 0 ? 1 : 0) +
          " " + edge + " " + f(cy + m(rise)),
      }));
    const corner = Math.max(2, m(PITCH.corner));
    for (const [y, dy] of [[top, 1], [top + h, -1]]) {
      lines.append(svgEl("path", null, {
        d: "M" + f(line) + " " + f(y + dy * corner) + " A" + f(corner) + " " + f(corner) + " 0 0 " +
          (dir * dy > 0 ? 0 : 1) + " " + f(line + dir * corner) + " " + f(y),
      }));
    }
    spots.push(line + dir * m(PITCH.spot));
    out.push(svgEl("rect", "p-goal", {
      x: f(dir > 0 ? line - m(PITCH.goalDepth) : line), y: f(cy - m(PITCH.goal) / 2),
      width: f(m(PITCH.goalDepth)), height: f(m(PITCH.goal)),
    }));
  }
  out.push(lines);
  for (const x of spots) {
    out.push(svgEl("circle", "p-spot", { cx: f(x), cy: f(cy), r: f(Math.max(1.3, m(PITCH.spotRadius))) }));
  }
  $("chart-pitch").replaceChildren(...out);
}

function drawTrack() {
  const out = [];
  const y = G.axis;
  for (const half of [1, 2]) {
    const start = chartX([half, 0]);
    const close = chartX([half, halfEnd[half]]);
    out.push(svgLine("c-track", start, y, close, y));
    if (snapshotAt.size && now[0] >= half) {
      const end = now[0] > half ? close : chartX(now);
      if (end > start) out.push(svgLine("c-track-played", start, y, end, y));
    }
    for (let m = half === 1 ? 0 : 15; m <= 45; m += 15) {
      const x = chartX([half, m * 60000]);
      out.push(svgLine("c-track-tick", x, y - 5, x, y + 5));
      out.push(svgText("c-tick", x, y + 23, (half === 1 ? m : 45 + m) + "'", "middle"));
    }
  }
  $("chart-track").replaceChildren(...out);
}

// The ball shows the timeline position: the selected event if there is one, else the replay clock.
function placeBall() {
  const ball = $("chart-ball");
  const position = $("chart-position");
  ball.setAttribute("class", picked ? "c-ball held" : "c-ball");
  if (!snapshotAt.size) {
    ball.setAttribute("display", "none");
    position.textContent = "The timeline position appears when the replay starts.";
    return;
  }
  const x = chartX(picked ? picked.at : now);
  ball.removeAttribute("display");
  ball.setAttribute("transform", "translate(" + x.toFixed(1) + " " +
    (CHART.pitchTop + G.pitch / 2) + ") rotate(" +
    (x * BALL_TURN).toFixed(1) + ")");
  position.textContent = picked
    ? "Timeline position: " + picked.minute + ", the selected event. Replay clock: " +
      (minute || DASH) + "."
    : "Timeline position: " + (minute || DASH) + ", the replay clock.";
}

function drawScore() {
  const out = [];
  if (scores.length) {
    let top = 1;
    for (const p of scores) top = Math.max(top, p.score.home, p.score.away);
    const y = (goals) => G.scoreBase - goals * (G.scoreBase - G.scoreTop) / top;
    const end = chartX(now);
    for (const side of ["home", "away"]) {
      const lift = side === "away" ? 2 : 0;
      let d = "";
      let last = 0;
      for (const p of scores) {
        last = chartX(p.at);
        const yy = (y(p.score[side]) + lift).toFixed(1);
        d += d ? " H" + last.toFixed(1) + " V" + yy : "M" + last.toFixed(1) + " " + yy;
      }
      d += " H" + Math.max(end, last).toFixed(1);
      out.push(svgEl("path", "c-step-" + side, { d }));
    }
    for (const p of scores) {
      if (!p.goal) continue;
      const text = p.score.home + DASH + p.score.away;
      out.push(svgText("c-step-label", chartX(p.at) + 3,
        y(Math.max(p.score.home, p.score.away)) - 4, text));
    }
  }
  $("chart-score").replaceChildren(...out);
}

function layoutChart() {
  chartWidth = Math.max(280, Math.round($("chart-frame").clientWidth || 1000));
  measure();
  $("chart-svg").setAttribute("viewBox", "0 0 " + chartWidth + " " + G.height);
  drawAxis();
  drawPitch();
  drawTrack();
  const rows = { "-1": [], "1": [] }; // per side, the x of the last mark in each row
  for (const m of marks) {
    const x = chartX(m.at);
    let y = CHART.period;
    if (m.side) {
      const taken = rows[m.side];
      let row = taken.findIndex((last) => x - last >= 14);
      if (row < 0 && taken.length < 3) row = taken.length;
      if (row < 0) row = taken.indexOf(Math.min(...taken));
      taken[row] = x;
      y = m.side < 0 ? CHART.homeRow - CHART.row * row : G.awayRow + CHART.row * row;
    }
    m.node.setAttribute("transform", "translate(" + x.toFixed(1) + " " + y + ")");
  }
  for (const n of notes) {
    const y = G.notes + n.row * CHART.noteRow;
    n.node.setAttribute("transform", "translate(" + chartX(n.at).toFixed(1) + " " + y + ")");
  }
  drawScore();
  placeBall();
}

function markShape(node, kind, half) {
  if (kind === "goal") {
    node.append(svgEl("circle", "mk-ball", { r: 6 }), svgEl("circle", "mk-dot", { r: 2.2 }));
  } else if (kind === "red_card") {
    node.append(svgEl("rect", "mk-card", { x: -3.5, y: -5, width: 7, height: 10, rx: 1 }));
  } else if (kind === "substitution") {
    node.append(svgEl("path", "mk-on", { d: "M-6 -1 L-3 -7 L0 -1 Z" }),
      svgEl("path", "mk-off", { d: "M0 1 L3 7 L6 1 Z" }));
  } else {
    node.append(svgEl("line", "mk-rule", { x1: 0, y1: 6, x2: 0,
      y2: CHART.pitchTop - CHART.apron - 6 - CHART.period }));
    node.append(svgEl("rect", "mk-diamond", { x: -4, y: -4, width: 8, height: 8,
      transform: "rotate(45)" }));
    if (kind === "period_end") node.append(svgText("mk-minute", 0, -9, HALF_CLOSE[half], "middle"));
  }
}

function setRove(mark) {
  if (rove && rove !== mark) rove.node.setAttribute("tabindex", "-1");
  rove = mark;
  mark.node.setAttribute("tabindex", "0");
}

function showReadout(mark) {
  const head = el("p", "readout-head");
  head.append(el("span", "readout-minute", mark.minute), el("span", null, mark.headline));
  const team = teamShort(mark.team);
  if (team) head.append(el("span", "event-team", team));
  const go = el("button", "link-button", "Show in the match timeline");
  go.type = "button";
  go.addEventListener("click", () => revealMoment(mark));
  const parts = [head];
  if (mark.statement) parts.push(el("p", "readout-text", mark.statement));
  parts.push(el("p", "event-fact", "Score at this point: " + scoreText(mark.score)), go);
  $("chart-readout").replaceChildren(...parts);
}

function unpick() {
  if (!picked) return;
  picked.node.classList.remove("picked");
  picked.node.removeAttribute("aria-current");
  picked = null;
  keepFocus($("chart-readout"));
  $("chart-readout").replaceChildren(el("p", "quiet", READOUT_EMPTY));
  placeBall();
  renderStats();
}

function pick(mark, fromChart) {
  if (picked !== mark) {
    unpick();
    picked = mark;
    mark.node.classList.add("picked");
    mark.node.setAttribute("aria-current", "true");
    showReadout(mark);
    placeBall();
    renderStats();
  }
  if (fromChart) {
    document.querySelector("input[name=lane][value=moments]").checked = true;
    showLane();
    if (mark.details) mark.details.open = true;
  }
}

function revealMoment(mark) {
  if (!mark.li.isConnected) return;
  document.querySelector("input[name=lane][value=moments]").checked = true;
  showLane();
  if (mark.details) {
    mark.details.open = true;
    mark.details.querySelector("summary").focus();
  } else {
    mark.li.tabIndex = -1;
    mark.li.focus();
  }
}

function chartKey(e) {
  const i = marks.findIndex((m) => m.node === e.target);
  if (i < 0) return;
  let next = -1;
  if (e.key === "ArrowRight" || e.key === "ArrowDown") next = Math.min(i + 1, marks.length - 1);
  else if (e.key === "ArrowLeft" || e.key === "ArrowUp") next = Math.max(i - 1, 0);
  else if (e.key === "Home") next = 0;
  else if (e.key === "End") next = marks.length - 1;
  else if (e.key === "Enter" || e.key === " ") {
    e.preventDefault();
    pick(marks[i], true);
    return;
  } else return;
  e.preventDefault();
  setRove(marks[next]);
  marks[next].node.focus();
}

function chartMoment(cue, at, src, li) {
  const home = current && current.home.team_id === src.team_id;
  const away = current && current.away.team_id === src.team_id;
  const headline = cue.sections.find((s) => s.kind === "headline");
  const statement = cue.sections.filter((s) => s.kind === "moment").map((s) => s.text).join(" ");
  const time = snapshotTime(cue);
  const mark = {
    at: time, snapshot: cue.snapshot_id, side: away ? 1 : home ? -1 : 0, minute: at,
    headline: headline ? headline.text : "",
    statement, team: src.team_id, score: src.score, li, details: li.querySelector("details"),
    node: svgEl("g", "mk mk-" + src.moment, { tabindex: -1, role: "button" }),
  };
  const team = teamName(src.team_id);
  mark.node.setAttribute("aria-label", at + " " + mark.headline + (team ? ", " + team : "") +
    ". " + statement + " Score at this point: " + scoreText(src.score) + ".");
  mark.node.append(svgEl("circle", "mk-hit", { r: 10 }));
  markShape(mark.node, src.moment, time[0]);
  if (KEY_MOMENT[src.moment]) {
    mark.node.append(svgText("mk-minute", 0, mark.side > 0 ? 19 : -10, at, "middle"));
  }
  mark.node.addEventListener("focus", () => setRove(mark));
  mark.node.addEventListener("click", () => {
    setRove(mark);
    mark.node.focus();
    pick(mark, true);
  });
  if (mark.details) {
    mark.details.addEventListener("toggle", () => {
      if (mark.details.open) pick(mark, false);
      else if (picked === mark) unpick();
    });
  }
  $("chart-events").append(mark.node);
  marks.push(mark);
  if (!rove) setRove(mark);
  scores.push({ at: time, score: src.score, goal: src.moment === "goal" });
  layoutChart();
}

function chartNote(cue, text) {
  const kind = NOTE_KIND[cue.kind];
  const id = cue.source.storyline_id;
  let line = storylines.get(id);
  if (!line || (kind === "published" && !line.open)) {
    const used = new Set();
    for (const other of storylines.values()) if (other.open) used.add(other.row);
    let row = 0;
    while (used.has(row) && row < 2) row++;
    line = { row, open: true, nodes: [] };
    storylines.set(id, line);
  }
  const verdict = kind === "published" ? " v-" + cue.source.verdict : "";
  const node = svgEl("g", "an an-" + kind + verdict);
  if (kind === "published") node.append(svgEl("circle", null, { r: 4 }));
  else if (kind === "revised") {
    node.append(svgEl("rect", null, { x: -3.5, y: -3.5, width: 7, height: 7, transform: "rotate(45)" }));
  } else node.append(svgEl("path", null, { d: "M-4 -4 L4 4 M4 -4 L-4 4" }));
  const title = svgEl("title");
  title.textContent = text;
  node.append(title);
  if (kind === "retracted") {
    line.open = false;
    for (const other of line.nodes) other.classList.add("gone");
  }
  line.nodes.push(node);
  notes.push({ node, at: snapshotTime(cue), row: line.row });
  $("chart-notes").append(node);
  layoutChart();
}

function chartStatus(status) {
  if (status === "data_incomplete" || status === "unavailable") gapSeen = true;
  const node = $("chart-gap");
  node.hidden = !gapSeen;
  node.textContent = status === "data_incomplete"
    ? "Data incomplete: events are missing, so the chart covers only the minutes received before " +
      "the gap."
    : status === "unavailable"
      ? "The latest data could not be verified; events from this part of the match may be " +
        "missing from the chart."
      : "Part of this replay arrived incomplete or could not be verified; events from that part " +
        "of the match may be missing from the chart.";
}

function chartClock(tick) {
  snapshotAt.set(tick.snapshot_id, [tick.period, tick.clock_ms]);
  if (tick.clock_ms > halfEnd[tick.period]) halfEnd[tick.period] = tick.clock_ms;
  layoutChart();
}

function chartReset() {
  if ($("chart").contains(document.activeElement)) $("chart-heading").focus();
  snapshotAt = new Map();
  halfEnd = { 1: HALF_MS, 2: HALF_MS };
  marks = [];
  notes = [];
  storylines = new Map();
  scores = [];
  rove = null;
  picked = null;
  gapSeen = false;
  for (const id of ["chart-events", "chart-notes", "chart-score"]) $(id).replaceChildren();
  $("chart-gap").hidden = true;
  $("chart-readout").replaceChildren(el("p", "quiet", READOUT_EMPTY));
  layoutChart();
}

// --- team statistics: one server-formatted message per snapshot; values only size the bars ----

const STATS_VERSION = "0.1.0";
// The measures shown, in order, and how each bar compares the two teams: "split" divides one bar
// between them, "mirror" grows each team's share out from the centre, "" draws no bar.
const STAT_BARS = {
  shots: "split", on_ball_share: "split", field_tilt: "split", pass_completion: "mirror",
  progressive_actions: "split", high_regains: "split", pressures: "split", ppda: "",
};
const STAT_KEYS = Object.keys(STAT_BARS);
const STAT_UNITS = { count: "Count", share: "Share", ratio: "Ratio" };
const STAT_STATES = new Set(["valid", "invalid", "unavailable"]);
const FIGURE_STATES = new Set(["ok", "insufficient_sample"]);
const STATS_NOT_CARRIED = "Values from earlier minutes are not carried forward.";
const STATS_LEVEL = {
  missing: "missing", stale: "stale", invalid: "unavailable", unavailable: "unavailable",
  unreadable: "unavailable",
};

let statsAt = new Map(); // snapshot_id -> statistics message of this edition
let statsLatest = null;
let statsTick = null; // snapshot_id of the latest clock tick
let statsBehind = false; // a clock tick passed without the statistics of its snapshot
let statsLastValid = ""; // minute of the latest snapshot with values
let statsAnnounced = "none"; // the state of the latest statistics last announced

function statsReadable(stats) {
  return stats.stats_version === STATS_VERSION && typeof stats.minute === "string" &&
    STAT_STATES.has(stats.status);
}

function figureOf(teamStats, key) {
  const values = teamStats.values;
  if (!values || typeof values !== "object" || !Object.hasOwn(values, key)) return null;
  const figure = values[key];
  if (!figure || typeof figure.display !== "string") return null;
  return FIGURE_STATES.has(figure.status) ? figure : null;
}

// The rows of a valid message, or null if it cannot be shown as it stands.
function statsRows(stats) {
  if (!current || !Array.isArray(stats.measures) || !Array.isArray(stats.teams)) return null;
  if (stats.teams.length !== 2) return null;
  const find = (team) =>
    stats.teams.find((teamStats) => teamStats && teamStats.team_id === team.team_id);
  const home = find(current.home);
  const away = find(current.away);
  if (!home || !away || home === away) return null;
  const rows = [];
  for (const key of STAT_KEYS) {
    const measure = stats.measures.find((measure) => measure && measure.key === key);
    if (!measure || typeof measure.label !== "string" || !STAT_UNITS[measure.unit]) return null;
    rows.push({ key, measure, home: figureOf(home, key), away: figureOf(away, key) });
  }
  return rows;
}

function barValue(figure) {
  if (!figure || figure.status !== "ok" || typeof figure.value !== "number") return null;
  return Number.isFinite(figure.value) && figure.value >= 0 ? figure.value : null;
}

function statsBar(row) {
  const kind = STAT_BARS[row.key];
  if (!kind) return null;
  const bar = svgEl("svg", "st-bar", {
    viewBox: "0 0 200 8", preserveAspectRatio: "none", "aria-hidden": "true", focusable: "false",
  });
  bar.append(svgEl("rect", "st-track", { x: 0, y: 0, width: 200, height: 8, rx: 4 }));
  const home = barValue(row.home);
  const away = barValue(row.away);
  let parts = null;
  let divide = 100;
  if (home !== null && away !== null) {
    if (kind === "split" && home + away > 0) {
      divide = (200 * home) / (home + away);
      parts = [[0, divide], [divide, 200 - divide]];
    } else if (kind === "mirror") {
      const h = 100 * Math.min(home, 1);
      parts = [[100 - h, h], [100, 100 * Math.min(away, 1)]];
    }
  }
  for (const [i, side] of (parts ? ["home", "away"] : []).entries()) {
    const [x, width] = parts[i];
    const attrs = { x: x.toFixed(2), y: 0, width: width.toFixed(2), height: 8 };
    bar.append(svgEl("rect", "st-fill-" + side, attrs));
  }
  if (parts || kind === "mirror") bar.append(svgLine("st-mid", divide, 0, divide, 8));
  return bar;
}

function statsCell(figure, measure, side) {
  const cell = el("td", "st-value st-" + side);
  cell.setAttribute("role", "cell");
  if (!figure) {
    cell.append(el("span", "st-figure st-short", "Not available"));
    return cell;
  }
  const ok = figure.status === "ok";
  cell.append(el("span", ok ? "st-figure" : "st-figure st-short", figure.display));
  if (typeof measure.sample_of === "string" && Number.isInteger(figure.sample)) {
    let text = "of " + figure.sample;
    if (!ok) {
      text = "Sample " + figure.sample;
      if (Number.isInteger(measure.min_sample)) text += ", needs " + measure.min_sample;
    }
    cell.append(el("span", "st-sample", text));
  }
  return cell;
}

function statsRow(row) {
  const measure = row.measure;
  const tr = el("tr", "st-row");
  tr.setAttribute("role", "row");
  const head = el("th", "st-label");
  head.scope = "row";
  head.setAttribute("role", "rowheader");
  const unit = STAT_UNITS[measure.unit];
  head.setAttribute("aria-label", measure.label + ", " + unit.toLowerCase());
  const name = el("span", "st-name");
  name.append(el("span", null, measure.label), el("span", "st-unit", unit));
  head.append(name);
  const bar = statsBar(row);
  if (bar) head.append(bar);
  const notes = [];
  if (typeof measure.note === "string") notes.push(measure.note);
  if (typeof measure.sample_of === "string") notes.push("Sample: " + measure.sample_of + ".");
  if (notes.length) {
    const note = el("span", "st-note", notes.join(" "));
    note.id = "st-note-" + row.key;
    head.setAttribute("aria-describedby", note.id);
    head.append(note);
  }
  tr.append(head, statsCell(row.home, measure, "home"), statsCell(row.away, measure, "away"));
  return tr;
}

function statsNotice(stats, latest) {
  const tail = latest && statsLastValid ? " The last ledger was for " + statsLastValid + "." : "";
  if (!statsReadable(stats)) return "The Stat Ledger for this minute could not be read." + tail;
  if (stats.status === "invalid") {
    return "No Stat Ledger for " + stats.minute + ": the match data for this minute did not " +
      "pass its consistency checks." + tail;
  }
  if (stats.status === "unavailable") {
    return "The Stat Ledger for " + stats.minute + " could not be calculated." + tail;
  }
  return "The Stat Ledger for " + stats.minute + " could not be read." + tail;
}

// The state of the statistics shown: "none" before any, "valid", "stale", "missing", "invalid",
// "unavailable" or "unreadable".
function statsState(stats, rows, latest, behind) {
  if (!stats) return latest && !behind ? "none" : "missing";
  if (rows) return latest && behind ? "stale" : "valid";
  if (!statsReadable(stats) || stats.status === "valid") return "unreadable";
  return stats.status;
}

// What a screen reader hears when the latest statistics change state: the visible notice, or
// nothing when the state is unchanged.
function statsAnnouncement(before, after, notice) {
  if (after === before || after === "none") return "";
  if (after !== "valid") return notice;
  if (before === "none") return "";
  return before === "stale" ? "The Stat Ledger is up to date again." :
    "The Stat Ledger is available again.";
}

function renderStats() {
  const latest = !picked;
  const stats = latest ? statsLatest : statsAt.get(picked.snapshot);
  const rows = stats && statsReadable(stats) && stats.status === "valid" ? statsRows(stats) : null;
  const state = statsState(stats, rows, latest, statsBehind);
  let anchor = "The Stat Ledger appears here from the first snapshot of the replay.";
  let notice = "";
  if (stats && statsReadable(stats)) {
    const half = PERIOD_LABEL[stats.period];
    anchor = "As of " + stats.minute + (half ? ", " + half.toLowerCase() : "") +
      (latest ? ", the latest snapshot." : ", the snapshot of the selected event.");
  } else if (!latest) {
    anchor = "The selected event is at " + picked.minute + ".";
  } else if (stats) {
    anchor = "The latest snapshot.";
  }
  if (state === "missing") {
    notice = latest ? "No Stat Ledger data has been received for this replay yet." :
      "No Stat Ledger data was received for the selected event's minute.";
  } else if (state === "stale") {
    notice = "This ledger has not been updated since " + stats.minute + ".";
  } else if (state !== "valid" && state !== "none") {
    notice = statsNotice(stats, latest) + " " + STATS_NOT_CARRIED;
  }
  $("stats-anchor").textContent = anchor;
  $("stats-latest").hidden = latest;
  const status = $("stats-status");
  const level = STATS_LEVEL[state];
  status.textContent = notice;
  status.className = "stats-status" + (level ? " s-" + level : "");
  status.hidden = !notice;
  $("stats-key").hidden = !rows;
  $("stats-table").hidden = !rows;
  $("stats-rows").replaceChildren(...(rows || []).map(statsRow));
  if (!latest) return;
  const heard = statsAnnouncement(statsAnnounced, state, notice);
  if (heard && !catchingUp) announce(heard);
  statsAnnounced = state;
}

function onStats(stats) {
  if (!stats || typeof stats !== "object" || stats.edition !== edition) return;
  if (typeof stats.snapshot_id !== "string") return;
  statsAt.set(stats.snapshot_id, stats);
  statsLatest = stats;
  if (stats.snapshot_id === statsTick) statsBehind = false;
  if (statsReadable(stats) && stats.status === "valid" && statsRows(stats)) {
    statsLastValid = stats.minute;
  }
  renderStats();
}

function statsClock(tick) {
  if (statsTick !== null && statsTick !== tick.snapshot_id && !statsAt.has(statsTick)) {
    statsBehind = true;
  }
  statsTick = tick.snapshot_id;
  renderStats();
}

function statsReset() {
  if ($("stats").contains(document.activeElement)) $("stats-heading").focus();
  statsAt = new Map();
  statsLatest = null;
  statsTick = null;
  statsBehind = false;
  statsLastValid = "";
  statsAnnounced = "none";
  renderStats();
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
    briefAdd(item);
    for (const context of eventsAt.get(cue.snapshot_id) || []) addContext(context, item);
    logEntry("Insight published", "a-published", item.head.textContent, at, item);
    chartNote(cue, "Insight published, " + at + ": " + item.head.textContent);
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
      logEntry("Insight revised", "a-revised", text, at, item);
      chartNote(cue, "Insight revised, " + at + ": " + text);
      item.noticeText.textContent = "Revised " + at + ".";
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
    if (live) announce("Insight retracted, " + at + ". " + reason);
    if (item) {
      item.retracted = true;
      item.node.classList.add("retracted");
      const why = RETRACTION_REASON[cue.source.reason];
      item.label.textContent = RETRACTION_LABEL + (why ? " \u00b7 " + why : "");
      item.noticeText.textContent = reason;
      item.notice.className = "item-notice retraction";
      item.notice.hidden = false;
      addHistory(item, at, reason);
      briefRemove(item);
      expiring.push({ expires: key(cue.expires_at), item, id: cue.supersedes[0] });
      if (live) arrive(item.node);
    }
    logEntry("Insight retracted", "a-retracted", reason + (fact ? " " + fact : ""), at, item);
    chartNote(cue, "Insight retracted, " + at + ". " + reason);
  } else if (cue.kind === "status") {
    const text = cue.sections.map((s) => s.text).join(" ");
    setFeedStatus(cue.source.status, text);
    chartStatus(cue.source.status);
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
      briefRemove(entry.item);
      retireLinks(entry.item);
      items.delete(entry.id);
    } else {
      entry.node.classList.remove("current");
    }
    return false;
  });
  refreshEmpty();
  chartClock(tick);
  statsClock(tick);
}

// --- pause and resume: received messages are held, then applied in order ----------------------

// A full edition measured at most 296 messages and 400,988 UTF-16 code units of data.
const HOLD_MAX_MESSAGES = 5000;
const HOLD_MAX_UNITS = 4000000;
const HELD_ANALYSIS = new Set(["insight", "revision", "retraction"]);

let held = null; // while paused: the hold of received messages
let reloading = false; // a new stream is reloading the edition after a hold overflowed
let frozenAt = ""; // the minute shown when the overflowed hold was paused

function newHold(editionNumber, isEnded, at, runId) {
  return {
    items: [], units: 0, edition: editionNumber, run: runId, ended: isEnded, at, moments: 0,
    analysis: 0, restarted: false, finished: false, overflow: false,
  };
}

// The field as the handler reads it, or null where the handler fails before reading it.
function parsedField(data, name) {
  try {
    const value = JSON.parse(data);
    return value === null ? null : { value: value[name] };
  } catch {
    return null;
  }
}

// Adds a received message to a hold. Dropped are only messages that applying could not change:
// everything before a new edition or run (whose arrival resets the page), an edition event for
// the edition and run already reached, and an end after an end of the same edition. An edition
// event the handler cannot read changes nothing when applied, so it is kept as it is.
function holdMessage(hold, type, data) {
  if (hold.overflow) return hold;
  if (type === "edition") {
    const number = parsedField(data, "edition");
    const runId = parsedField(data, "run");
    if (number && number.value === hold.edition && runId.value === hold.run) return hold;
    if (number) {
      Object.assign(hold, { items: [], units: 0, moments: 0, analysis: 0, finished: false });
      Object.assign(hold, { edition: number.value, run: runId.value, ended: false });
      hold.restarted = true;
    }
  } else if (type === "end") {
    if (hold.ended) return hold;
    Object.assign(hold, { ended: true, finished: true });
  } else if (type === "cue") {
    const kind = parsedField(data, "kind");
    if (kind && kind.value === "moment") hold.moments += 1;
    else if (kind && HELD_ANALYSIS.has(kind.value)) hold.analysis += 1;
  }
  hold.items.push([type, data]);
  hold.units += data.length;
  if (hold.items.length > HOLD_MAX_MESSAGES || hold.units > HOLD_MAX_UNITS) {
    Object.assign(hold, { items: [], units: 0, overflow: true });
  }
  return hold;
}

function counted(n, one, many) {
  return n + " " + (n === 1 ? one : many);
}

function heldParts(hold) {
  const parts = [];
  if (hold.moments) parts.push(counted(hold.moments, "match event", "match events"));
  if (hold.analysis) parts.push(counted(hold.analysis, "analysis update", "analysis updates"));
  return parts;
}

function holdText(hold) {
  const head = "Paused at " + hold.at + ".";
  if (hold.overflow) {
    return head + " Too many updates to hold; the replay will reload when you resume.";
  }
  const parts = heldParts(hold);
  let text = head + (parts.length ? " Waiting: " + parts.join(", ") + "." :
    " No new match events or analysis updates yet.");
  if (hold.restarted) text += " The replay has restarted.";
  if (hold.finished) text += " The replay has finished.";
  return text;
}

function resumeSummary(hold, at) {
  if (hold.restarted) return "Resumed. The replay restarted while paused; now at " + at + ".";
  const parts = heldParts(hold);
  return "Resumed." + (parts.length ? " " + parts.join(" and ") + " applied; now" : " Now") +
    " at " + at + ".";
}

function holdReceived(type, data) {
  holdMessage(held, type, data);
  if (held.overflow && source && source.readyState !== EventSource.CLOSED) source.close();
  renderReplay();
}

// The server closes a finished replay's stream after every reconnect; an end that is held counts,
// so a paused page treats those closures as quietly as an unpaused one.
function streamEnded() {
  return held ? held.ended : ended;
}

// Pausing needs a replay on screen and a stream that is neither loading nor lost for good.
function canPause() {
  return edition !== null && !reloading && PAUSABLE.has(replay);
}

function pause() {
  if (held || !canPause()) return;
  held = newHold(edition, ended, minute || DASH, run);
  renderReplay();
  announce("Paused at " + held.at + ". New updates are held.");
}

// The status after a resume comes from the connection as it is now, then the replayed data.
function syncReplay() {
  if (!source || source.readyState === EventSource.CLOSED) return;
  if (source.readyState === EventSource.CONNECTING && !ended) {
    setReplay("reconnecting", "Connection lost. Reconnecting automatically" + ELLIPSIS);
  } else {
    setReplay(ended ? "complete" : "replaying");
  }
}

function resume() {
  if (!held) return;
  const done = held;
  held = null;
  if (done.overflow) {
    frozenAt = done.at;
    reloading = true;
    if (source) source.close();
    openStream();
    announce("Resumed. The replay is reloading.");
    return;
  }
  const history = catchingUp;
  catchingUp = true;
  // A restarted hold begins with the new edition event, which must reset the page even when its
  // number matches the edition shown before the pause.
  if (done.restarted) edition = null;
  for (const [type, data] of done.items) apply(type, data);
  catchingUp = history;
  syncReplay();
  renderReplay();
  announce(resumeSummary(done, minute || DASH));
}

// --- connection ----------------------------------------------------------------------------

function reset() {
  keepFocus($("cards"));
  keepFocus($("briefing"));
  keepFocus($("moments"));
  keepFocus($("log"));
  items = new Map();
  expiring = [];
  now = [1, 0];
  minutes = new Map();
  eventsAt = new Map();
  minute = "";
  period = 0;
  phase = "";
  started = false;
  $("cards").replaceChildren();
  $("moments").replaceChildren();
  $("log").replaceChildren();
  $("brief-moments").replaceChildren();
  $("brief-analysis").replaceChildren();
  for (const id of ["home-events", "away-events"]) {
    $(id).replaceChildren();
    $(id).hidden = true;
  }
  clearScore();
  $("clock").textContent = DASH;
  renderPhase();
  setFeedStatus("awaiting_snapshot", "");
  refreshEmpty();
  chartReset();
  statsReset();
}

function audience() {
  return document.querySelector("input[name=audience]:checked").value;
}

function onEdition(message) {
  const number = message.edition;
  const otherRun = message.run !== run;
  if (reloading || otherRun || number !== edition) {
    const restart = edition !== null && !reloading;
    reset();
    ended = false;
    if (reloading) {
      reloading = false;
      setReplay("replaying");
      announce("The replay has been reloaded.");
    } else if (restart && otherRun && run !== null) {
      setReplay("replaying");
      announce("The replay restarted on the server. Showing it from kick-off.");
    } else if (restart) {
      setReplay("replaying");
      if (!catchingUp) announce("The replay is restarting from kick-off.");
    }
  }
  run = message.run;
  edition = number;
  started = true;
  renderReplay();
}

function onEnd() {
  ended = true;
  setReplay("complete");
  if (!catchingUp) announce("Replay complete.");
}

const STREAM_HANDLERS = { edition: onEdition, clock: onClock, stats: onStats, cue: onCue, end: onEnd };

// Every stream message, live or released from a hold, is applied here, one at a time: a message
// that fails is reported and does not stop the messages after it.
function apply(type, data) {
  try {
    STREAM_HANDLERS[type](type === "end" ? null : JSON.parse(data));
  } catch (error) {
    reportError(error);
  }
}

function receive(type, data) {
  arrival();
  if (held) holdReceived(type, data);
  else apply(type, data);
}

function connect() {
  held = null;
  reloading = false;
  if (source) source.close();
  edition = null;
  run = null;
  ended = false;
  reset();
  if (!$("match").value) {
    renderReplay();
    return;
  }
  openStream();
}

// A new stream sends no Last-Event-ID, so the server sends its current edition from the start.
// While reloading, the page keeps what it shows until that edition arrives, then rebuilds it.
function openStream() {
  catchingUp = true;
  lastArrival = 0;
  setReplay(reloading ? "reloading" : "connecting");
  const id = $("match").value;
  const params = new URLSearchParams({ audience: audience(), club: $("club").value });
  const stream = new EventSource("/matches/" + encodeURIComponent(id) + "/stream?" + params);
  source = stream;
  stream.addEventListener("open", () => {
    if (stream !== source) return;
    catchingUp = true;
    lastArrival = performance.now();
    if (replay === "reconnecting") announce("Reconnected.");
    setReplay(reloading ? "reloading" : streamEnded() ? "complete" : "replaying");
  });
  stream.addEventListener("edition", (e) => { if (stream === source) receive("edition", e.data); });
  stream.addEventListener("clock", (e) => { if (stream === source) receive("clock", e.data); });
  stream.addEventListener("stats", (e) => { if (stream === source) receive("stats", e.data); });
  stream.addEventListener("cue", (e) => { if (stream === source) receive("cue", e.data); });
  stream.addEventListener("end", (e) => { if (stream === source) receive("end", e.data); });
  stream.addEventListener("unavailable", () => {
    if (stream !== source) return;
    stream.close();
    held = null;
    reloading = false;
    const text = "The replay is unavailable on the server right now.";
    setReplay("unavailable", text);
    alertNow(text);
  });
  stream.addEventListener("error", () => {
    if (stream !== source) return;
    if (stream.readyState === EventSource.CLOSED) {
      held = null;
      let text = "This replay cannot be reached right now; the server may be busy.";
      if (reloading) {
        reloading = false;
        text = "The replay could not be reloaded; the server may be busy. The page still shows " +
          "the match as it was when updates were paused, at " + frozenAt + ".";
      }
      setReplay("unavailable", text);
      alertNow(text);
    } else if (!streamEnded()) {
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
    "nothing about the match. The verifier's label on each insight is shown as its verification " +
    "status rather than inside the text."));
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
  $("home-events").setAttribute("aria-label", m.home.name + " goals and red cards");
  $("away-events").setAttribute("aria-label", m.away.name + " goals and red cards");
  $("stats-col-home").textContent = m.home.name + ", home";
  $("stats-col-away").textContent = m.away.name + ", away";
  $("stats-key-home").textContent = m.home.short_name;
  $("stats-key-away").textContent = m.away.short_name;
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
  $("chart-svg").addEventListener("keydown", chartKey);
  $("hold").addEventListener("click", () => (held ? resume() : pause()));
  $("stats-latest").addEventListener("click", () => {
    unpick();
    $("stats-heading").focus();
  });
  window.addEventListener("resize", layoutChart);
  if (!matches.length) setReplay("unavailable", "No match is available to replay.");
  connect();
}

for (const radio of document.querySelectorAll("input[name=theme]")) {
  radio.addEventListener("change", setTheme);
}
setTheme();
clearScore();
refreshEmpty();
layoutChart();
init().catch(() => setReplay("unavailable", "Could not load matches."));
