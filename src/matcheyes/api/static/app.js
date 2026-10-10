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
};
const CATCH_UP_GAP_MS = 400;

let source = null;
let edition = null;
let started = false;
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
  const node = $("replay-state");
  node.className = "replay-state r-" + next;
  node.textContent = REPLAY_TEXT[next];
  const banner = $("connection");
  banner.hidden = !(next === "reconnecting" || next === "unavailable");
  banner.className = "connection c-" + next;
  $("connection-text").textContent = detail || "";
  $("retry").hidden = next !== "unavailable";
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
  node.textContent = team.short_name;
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
  work.append(el("summary", null, "Evidence and verification"), workPanel);

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
  panel.append(el("p", "context-head", "Analysis showing at " + at), list);
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
      briefRemove(entry.item);
      retireLinks(entry.item);
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
    started = true;
    refreshEmpty();
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
