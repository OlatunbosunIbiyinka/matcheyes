"use strict";
// MatchEyes broadcast surface. It renders cue sections exactly as received and applies the cue
// display rules (show from, expire, supersede). It computes nothing about the match: scores,
// facts and insight text all come from the server's cues. Text is set with textContent only.

const $ = (id) => document.getElementById(id);
const VERDICT_LABEL = {
  explained: "Verified explanation",
  tentative: "Tentative explanation",
  natural_variation: "Natural variation",
  insufficient_evidence: "No verified explanation",
  unavailable: "Investigation unavailable",
};
const RETRACTION_LABEL = "Retracted";

let source = null;
let edition = null;
let matches = [];
let cards = new Map(); // cue_id -> card element
let expiring = []; // {expires: [period, ms], node, remove}
let now = [1, 0];

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

function reset() {
  cards = new Map();
  expiring = [];
  now = [1, 0];
  $("cards").replaceChildren();
  $("moments").replaceChildren();
  $("log").replaceChildren();
  $("score").textContent = "0-0";
  $("clock").textContent = "-";
  setStatus("awaiting_snapshot", "Connecting...");
  refreshEmpty();
}

function refreshEmpty() {
  const live = [...cards.values()].some((c) => !c.classList.contains("retracted"));
  $("empty").hidden = live;
}

function setStatus(status, text) {
  const node = $("status");
  node.className = "status " + status;
  node.textContent = text;
}

function sectionNodes(sections) {
  const box = el("div", "sections");
  for (const s of sections) box.append(el("p", "section " + s.kind, s.text));
  return box;
}

function fillCard(card, cue) {
  const src = cue.source;
  const badges = el("div", "badges");
  badges.append(el("span", "badge", "Current"));
  badges.append(el("span", "badge", VERDICT_LABEL[src.verdict] || src.verdict));
  if (src.evidence_integrity === "compromised") badges.append(el("span", "badge", "Integrity warning"));
  badges.append(el("span", "when", "as of " + cue.snapshot_id));
  card.className = "card " + src.verdict + (src.evidence_integrity === "compromised" ? " compromised" : "");
  card.replaceChildren(badges, sectionNodes(cue.sections));
  card.dataset.cue = cue.cue_id;
}

function logEntry(text, minute) {
  const li = el("li");
  li.append(el("span", "when", minute), document.createTextNode(text));
  $("log").prepend(li);
}

function onCue(cue, minute) {
  if (cue.kind === "moment") {
    const li = el("li", cue.interrupt ? "live" : "");
    li.append(el("span", "when", minute));
    for (const s of cue.sections) {
      if (s.kind === "headline") li.append(el("span", "head", s.text));
      else li.append(el("span", "section " + s.kind, s.text + " "));
    }
    $("moments").prepend(li);
    const score = cue.source.score;
    $("score").textContent = score.home + "-" + score.away;
    expiring.push({ expires: key(cue.expires_at), node: li, remove: false });
  } else if (cue.kind === "insight") {
    const card = el("article", "card");
    fillCard(card, cue);
    card.classList.add("flash");
    cards.set(cue.cue_id, card);
    $("cards").prepend(card);
  } else if (cue.kind === "revision") {
    const card = cards.get(cue.supersedes[0]);
    if (!card) return;
    cards.delete(cue.supersedes[0]);
    fillCard(card, cue);
    if (cue.interrupt) {
      card.classList.add("flash");
      logEntry(cue.sections[0].text, minute);
    }
    cards.set(cue.cue_id, card);
  } else if (cue.kind === "retraction") {
    const card = cards.get(cue.supersedes[0]);
    const reason = cue.sections[0].text;
    logEntry(reason + " " + (cue.sections[1] ? cue.sections[1].text : ""), minute);
    if (!card) return;
    card.classList.add("retracted");
    const badges = card.querySelector(".badges");
    badges.replaceChildren(el("span", "badge retracted", RETRACTION_LABEL));
    card.append(el("p", "section retraction", reason));
    expiring.push({ expires: key(cue.expires_at), node: card, remove: true, id: cue.supersedes[0] });
  } else if (cue.kind === "status") {
    setStatus(cue.source.status, cue.sections.map((s) => s.text).join(" "));
  }
  refreshEmpty();
}

function onClock(tick) {
  now = [tick.period, tick.clock_ms];
  $("clock").textContent = "as of " + tick.minute;
  for (const card of cards.values()) card.classList.remove("flash");
  expiring = expiring.filter((item) => {
    if (!before(item.expires, now)) return true;
    if (item.remove) {
      item.node.remove();
      cards.delete(item.id);
    } else {
      item.node.classList.remove("live");
    }
    return false;
  });
  refreshEmpty();
}

function connect() {
  if (source) source.close();
  edition = null;
  reset();
  const match = $("match").value;
  if (!match) return;
  const params = new URLSearchParams({ audience: $("audience").value, club: $("club").value });
  source = new EventSource("/matches/" + encodeURIComponent(match) + "/stream?" + params);
  source.addEventListener("edition", (e) => {
    const number = JSON.parse(e.data).edition;
    if (number !== edition) reset();
    edition = number;
  });
  source.addEventListener("clock", (e) => onClock(JSON.parse(e.data)));
  source.addEventListener("cue", (e) => onCue(JSON.parse(e.data), $("clock").textContent));
  source.addEventListener("end", () => setStatus("current", "Replay finished."));
  source.onerror = () => setStatus("unavailable", "Connection lost - retrying...");
}

function fillClubs() {
  const m = matches.find((x) => x.match_id === $("match").value);
  const club = $("club");
  club.replaceChildren(new Option("None", ""));
  if (!m) return;
  for (const team of [m.home, m.away]) club.append(new Option(team.name, team.team_id));
  $("home").textContent = m.home.name;
  $("away").textContent = m.away.name;
}

async function init() {
  const response = await fetch("/matches");
  matches = (await response.json()).matches;
  const select = $("match");
  for (const m of matches) {
    select.append(new Option(m.home.short_name + " v " + m.away.short_name + " (" + m.venue + ")", m.match_id));
  }
  fillClubs();
  select.addEventListener("change", () => { fillClubs(); connect(); });
  $("audience").addEventListener("change", connect);
  $("club").addEventListener("change", connect);
  connect();
}

init().catch(() => setStatus("unavailable", "Could not load matches."));
