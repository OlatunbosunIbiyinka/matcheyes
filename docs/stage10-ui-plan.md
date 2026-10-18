# Stage 10 UI plan: replay presentation polish

Status: **plan only** (not yet implemented). Source of truth: Stage 10 UI audit of
`main` at documentation time, plus the Stage 10 differentiation review (read-only). The
public surface already serves Stage 8/9 cues plus Stage 10.3 statistics. This document does
**not** change ADRs, cue contracts, API schemas, or acceptance thresholds.

Related decisions (unchanged):

- [ADR-0014](decisions/0014-broadcast-cue-contract-and-live-presentation-surface.md) —
  cue contract and live presentation surface
- [ADR-0015](decisions/0015-foundry-model-as-untrusted-reasoner.md) — Foundry offline on
  the public path; recorded Challenger only
- [ADR-0016](decisions/0016-snapshot-anchored-team-statistics.md) — snapshot-anchored stats
- [ADR-0017](decisions/0017-replay-lifecycle-identity-and-serving-bounds.md) — run identity,
  readiness, serving bounds

When polish is measured, record results in a future `docs/stage10-evaluation.md` (not this
file).

**Naming caveat:** Product chrome labels below are presentation choices only. Trademark
availability, legal clearance, and uniqueness versus the broader hackathon gallery or other
products have **not** been established. This plan does **not** claim they are cleared or
globally unique. Do not use **“Intelligence Changes”** as a preferred label.

---

## Product thesis

**MatchEyes — See beyond the score.**

MatchEyes shows what the score cannot: evidence-backed analysis, its verification status
(proposed vs verified, and when conclusions are revised or retracted), and how those
conclusions change over the match. The primary demo journey is **Match Story → Trace the
Evidence**. The chart and **The Stat Ledger** are supporting context, not the product centre.

Presentation labels rename chrome only. They must not overclaim verification: proposed
findings stay distinguishable from verified findings; revisions and retractions keep their
existing cue semantics.

---

## Product language glossary (chrome only)

Internal identifiers (`moment`, `insight`, `revision`, `retraction`, `status`, `stats`,
`run`, `tone`, cue IDs) stay as today. Only user-visible strings change under Gate B.

| Presentation label | UI surface (today → target) | Underlying concept | Must not imply |
| --- | --- | --- | --- |
| **MatchEyes — See beyond the score** | Title / topbar tagline; hero-level brand | Product identity | That the page invents tactics or overrides the score |
| **Match Story** | `#story-heading`, briefing, insight cards | Verified (and living) Stage 6/7/8 insight cues | That every card is causal proof without opening Trace |
| **On-Pitch Record** | Aside lane “Match events”; chart event rail copy | `cue.kind === "moment"` (goals, cards, subs, periods) | Official league record, or planted hidden-world truth |
| **Verified Analysis** | Aside lane “Analysis updates”; analysis chart lane | Insight / revision / retraction cues | That every row is verified before the verifier ran; erase proposed / revised / retracted |
| **The Stat Ledger** | `#stats-heading` “Team statistics” | ADR-0016 `stats` SSE; snapshot-anchored measures | That ledger figures are automatically verified insights or cue strength |
| **Trace the Evidence** | Card disclosure “Evidence and verification” / planned “Show the work” | `GET .../storylines/.../revisions/{n}` → `work_rows` | That proposed reasoner rows equal verified conclusions |

**Rejected label:** “Intelligence Changes” — too ambiguous for a first-time judge; do not use.

**Helper copy required for The Stat Ledger:** statistics are **context only** and are **not**
automatically verified insights (retain and strengthen today’s “context only / not linked to
match events or insights” meaning).

**Status distinctions retained (not renamed away):** proposed vs verified tones in Trace rows;
published / revised / retracted insight lifecycle; existing verdict and integrity labels on
cards.

---

## Objective

Make the existing replay page judge-ready through **presentation-only** changes so that a
visitor can:

1. Recognise **MatchEyes — See beyond the score** and the **fixture** in the first viewport.
2. Follow the developing **Match Story** (verified insights and how they change) in match time.
3. Tell **On-Pitch Record** apart from **Verified Analysis**.
4. Open **Trace the Evidence** and inspect verifier-authoritative vs proposed rows from the
   server — the defining product gesture.
5. Always see what the **replay system** is doing—including connect, pause, reconnect,
   complete, restart, and unavailable—without the page inventing truth.

Success criterion: a judge understands the match, the story, the evidence trail, and the
connection state even when the stream drops; the browser never becomes an authority on
football facts; the surface does not read as a generic stats dashboard.

---

## Scope

### In scope

Presentation layer only:

| Artifact | Role |
| --- | --- |
| `src/matcheyes/api/static/index.html` | Structure, labels, landmarks, information hierarchy |
| `src/matcheyes/api/static/app.css` | Hierarchy, visibility, layout |
| `src/matcheyes/api/static/app.js` | Wiring of existing server fields only; chrome string updates |
| Architecture / static tests that **pin** presentation strings and allowlists | Regression |
| This plan; later `stage10-evaluation.md` | Documentation |

### Out of scope

- Backend behaviour, cue kinds, `BROADCAST_VERSION`, stats schema, verifier gates
- Live Foundry / `MATCHEYES_LLM_*` on the public path
- Azure resource changes, image rebuild, or deploy (see [UI vs deployment](#ui-implementation-vs-deployment-approval))
- Inventing match facts, metrics, confidence values, timestamps, evidence, or fixture details
- CDN fonts/scripts or any CSP relaxation
- Changing SSE event types, run-identity rules, or Trace-the-Evidence authority order
- Renaming internal API fields or cue `kind` / `tone` values

---

## Five UX goals

### 1. Editorial MatchEyes hero and fixture identity

**Intent:** Brand-first first viewport carrying **MatchEyes — See beyond the score**; clear
competition / teams / score / minute from server data only. The score remains visible context;
the thesis is what lies beyond it.

### 2. Match Story / chronological insight flow

**Intent:** **Match Story** (briefing + insight cards) is the primary narrative spine —
verified insights and how they change in match time—not a disconnected feed. Chart and Stat
Ledger stay secondary.

### 3. Separate On-Pitch Record and Verified Analysis

**Intent:** Moments stay factual on-pitch events (**On-Pitch Record**); insight / revision /
retraction stay **Verified Analysis** (with proposed / verified / revised / retracted
distinctions intact). Chart and aside must not blur those roles.

### 4. Expandable Trace the Evidence (primary demo)

**Intent:** Disclosure summary **Trace the Evidence**; clear loading / unavailable / stale
states; rows from `GET .../storylines/.../revisions/{n}` only; **verified tones above
proposed**; revisions and retractions keep existing semantics. This journey is the defining
product experience, not an optional footnote.

### 5. Accessible loading, reconnect, completion, restart, and error states

**Intent:** Visible (not only screen-reader) system status for connecting, replaying,
paused, reconnecting, reloading, complete, unavailable, and server restart (new `run`).

---

## Current implementation

Single-page surface served under a strict CSP
(`default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self'`
in `api/server.py`). The page renders cue sections with `textContent` only and applies
show / expire / supersede rules; it computes nothing about the match.

| Area | Location | Behaviour today |
| --- | --- | --- |
| Brand | `index.html` topbar | Wordmark + tagline “See beyond the score” at nav scale |
| Hero / fixture | `.hero`, `selectMatch` in `app.js` | Teams, crests, meta (`competition` · matchday · venue), score from moment cues, clock from `clock` events, `#replay-state` |
| Match Story | `#story-heading`, `#briefing`, `#cards` | Briefing (key moments / analysis now) + newest-first insight cards; empty state explains the verifier gate |
| Event vs analysis | Aside lanes + chart | Toggle “Match events” / “Analysis updates”; chart note states snapshot-close placement vs analysis publication |
| Evidence disclosure | Card `<details class="disclosure work">` | Summary “Evidence and verification”; `showWork` fetches revision rows; run guard; stale revision notice; proposed vs verified tones |
| Stats | `#stats` | “Team statistics”; context-only note already present |
| Replay states | `REPLAY_TEXT`, `#connection`, pause/hold | `connecting`, `replaying`, `reconnecting`, `complete`, `unavailable`, `paused`, `reloading`; `#announcer` / `#alerts` live regions |
| Stream | `EventSource` | `edition` (with `run`), `clock`, `stats`, `cue`, `end`, `unavailable`; no `Last-Event-ID` on new connect (full rebuild) |
| Fixture API | `GET /matches` | `match_id`, `competition`, `matchday`, `venue`, `synthetic`, `home`/`away` `{team_id,name,short_name}`, `reasoner` |
| Work API | `GET /matches/{id}/storylines/{sid}/revisions/{n}` | `edition`, `run`, `rows` (`section`, `label`, `text`, `tone`) from `lifecycle/explain.work_rows` |

**Event time vs analysis publication time (already exposed):**

- On-pitch marks use each cue’s `snapshot_id` mapped through clock ticks to a
  **minute label at snapshot close**, not an invented exact event clock. The chart note in
  `index.html` already states this.
- Insight / revision / retraction cards use the same minute map for “Published …” /
  “updated …”. Do not invent a second timestamp the API does not send.

**Existing Stage 10-related tests to preserve:**

- `tests/architecture/test_stage10_serving.py` — stream listeners, stats field allowlist
- `tests/architecture/test_stage10_stats.py` — stats contract digests
- `tests/architecture/test_replay_controls.py` — single receive path, hold/resume, run reset,
  show-work run check, pause control a11y
- `tests/architecture/test_stage8_security.py` — no `innerHTML`, CSP-compatible surface

Much of the structural Stage 10 checklist is **already built**. Remaining work is hierarchy,
product language, and making Trace the Evidence the obvious demo path—not a greenfield UI.

---

## Identified gaps

| Goal | Gap |
| --- | --- |
| 1 Hero / brand | Tagline exists but is nav-scale; first viewport reads as scoreboard + controls + chart more than “See beyond the score.” |
| 2 Match Story | Cards are newest-first; briefing summarises but does not clearly read as a chronological spine; Story is not clearly primary over stats/chart. |
| 3 Timelines | Dual surfaces use generic “Match events” / “Analysis updates”; first-time judges can blur roles. |
| 4 Trace the Evidence | Summary is “Evidence and verification”; verification journey is easy to miss beside dashboard chrome; loading / authority note can be clearer. |
| 5 Replay states | Connection banner is mainly for reconnecting / unavailable; connecting, complete, and restart can be easy to miss beside the score. |
| Differentiation | Surface can read as a generic football analytics dashboard; evidence-verification is not staged as the defining experience. |

No missing **server fields** are required for these gaps. Product choices (e.g. chronological
display order vs keep newest-first + stronger briefing) must still use only cue order and
existing minute labels.

---

## Ordered implementation plan (incremental)

Execute only after explicit UI implementation authorization (**Gate B** — not granted by this
document). Each step should leave tests green and preserve SSE / run / CSP behaviour.

1. **Land this plan** in the repo (`docs/stage10-ui-plan.md`) — documentation-only (Gate A
   when committed on request).
2. **Product language in chrome** — Apply glossary labels (On-Pitch Record, Verified Analysis,
   The Stat Ledger + context dek, Trace the Evidence, brand thesis); never “Intelligence
   Changes.”
3. **Match Story + Trace hierarchy** — Make Story the primary column/journey; Trace the
   Evidence the defining disclosure; demote chart and Stat Ledger to supporting context
   (layout/copy/focus order only).
4. **Hero / brand hierarchy** — HTML/CSS so **MatchEyes — See beyond the score** + fixture
   identity dominate the first viewport; no new fixture strings beyond `/matches` and
   cue-driven score/minute.
5. **Replay-state prominence** — Make `#replay-state` and `#connection` the obvious “what
   the system is doing” region for all listed states; keep ADR-0017 semantics.
6. **Timeline / chart copy pass** — Reinforce snapshot-close (On-Pitch) vs analysis
   publication (Verified Analysis); lane headings and focus order only.
7. **Regression pins** — Extend static architecture tests for new labels/structure; do not
   weaken field allowlists, hold/run assertions, or proposed-below-verified authority.
8. **Manual verification** — Local `serve`, then ACA (see below). Image rebuild / deploy
   only under separate Gate C approval.

Do not combine deploy with UI commits by default.

---

## Acceptance criteria

A judge who never opens the repository can:

1. See **MatchEyes — See beyond the score** and **which fixture** (competition, teams,
   venue/matchday as already provided) in the first viewport, and state that the product is
   about what the score cannot show.
2. Follow **Match Story** as verified insights change in match time (published / updated
   minutes from existing clock mapping).
3. Distinguish **On-Pitch Record** from **Verified Analysis** in the aside (and understand
   the chart’s dual lanes via disclaimer language).
4. Open **Trace the Evidence** as the primary demo gesture and see server `rows` with
   **verifier authority over proposed** content; proposed findings remain visually and
   verbally distinct from verified findings; revised and retracted insights retain existing
   meanings; a run change leaves stale work ignored or refreshed per existing guards.
5. Treat **The Stat Ledger** as context only (helper copy), not as verified insight strength.
6. Always know whether the replay is **connecting**, **replaying**, **paused**,
   **reconnecting**, **reloading**, **complete**, **unavailable**, or **restarted** after a
   new server `run`—including after a dropped connection.

Non-criteria (must remain false):

- Page invents scores, events, metrics, evidence IDs, or confidence.
- Page calls a live model or embeds Foundry credentials.
- Cue kinds, stats schema, tones, or CSP change.
- Labels imply proposed == verified, or that Stat Ledger figures are verified insights.
- Claims of trademark clearance or global uniqueness for chrome names.

---

## Regression and accessibility tests

### Automated (required before calling UI done)

| Suite | Protects |
| --- | --- |
| `tests/architecture/test_replay_controls.py` | Single dispatch path; hold/resume; run reset; show-work run check; pause button a11y; no silent break of `unavailable` |
| `tests/architecture/test_stage10_serving.py` | Stream event set including `stats`; stats field allowlist; display-only figures |
| `tests/architecture/test_stage10_stats.py` | Stats contract pins |
| `tests/architecture/test_stage8_security.py` | No `innerHTML` / unsafe sinks; presentation stays server-text |

Add or adjust **string/structure pins** when labels change (e.g. “Trace the Evidence”,
“On-Pitch Record”, “Verified Analysis”, “The Stat Ledger”, hero/tagline). Prefer pins over
behaviour changes. Pin that Stat Ledger helper copy still denies automatic insight
verification.

### Accessibility expectations (manual + existing live regions)

- Keep skip link (target Match Story), landmarks (`aria-labelledby`), focus management on
  story/timeline resets.
- Keep `#announcer` (`role="status"`) and `#alerts` (`role="alert"`).
- Visible focus (`:focus-visible`); do not remove `aria-describedby` on pause.
- Theme switch and lane toggles remain operable by keyboard.
- Do not rely on colour alone for verdict / retraction / reconnect / proposed vs verified
  meaning (retain text labels already present).

---

## Risks

| Risk | Mitigation |
| --- | --- |
| Polish invents narrative or facts | Diff review: no new free text except fixed chrome labels and required deks; cue/about/work rows only |
| Labels overclaim verification (“Verified Analysis”, “Ledger”, “Trace”) | Glossary “must not imply”; Stat Ledger dek; keep proposed below verified; retain revised/retracted copy |
| Surface still reads as generic dashboard | Story + Trace hierarchy before scoreboard/stats polish; acceptance #4–5 |
| Break pause / run / Trace fetch | Keep `test_replay_controls` green; no EventSource semantics change |
| Temptation to load fonts/CDNs | CSP forbids it; keep system font stacks in `app.css` |
| Chart + lanes still confuse | Copy and focus-order; On-Pitch vs Verified Analysis headings |
| ACA cold start misread as “broken” | Honest connecting / reconnecting copy only; no fake progress percentages |
| Scope creep into deploy | Hard gate below |
| Assumed name uniqueness | Naming caveat at top; no legal/uniqueness claims in UI or docs |

---

## Architectural constraints (explicit)

1. **Presentation sits downstream of verified truth** (architecture dependency rules;
   ADR-0012 / ADR-0014). The static page must not feed facts back into analytics or agents.
2. **Cue contract frozen** for this workstream: `moment`, `insight`, `revision`,
   `retraction`, `status` only; `stats` remains a separate SSE event (ADR-0016), not a cue
   kind.
3. **Run identity** (ADR-0017): edition messages carry `run`; Trace-the-Evidence responses
   must match current `run`; a new run resets the page; do not splice replays via
   `Last-Event-ID` from another run.
4. **CSP unchanged**: no inline script/style hosts beyond `'self'`; `connect-src 'self'` only.
5. **Authority order in Trace the Evidence**: verifier / verified tones above proposed
   (`lifecycle/explain.py`); the page must not reorder tones to elevate proposals.
6. **No live Foundry** on `serve` / ACA image (ADR-0015).
7. **Timestamps**: only map existing `snapshot_id` → clock `minute`; do not invent clocks.
8. **Labels are chrome only**: do not rename server fields, cue kinds, or tones; do not use
   “Intelligence Changes.”
9. **No overclaim**: proposed ≠ verified; revisions and retractions keep existing meanings;
   Stat Ledger ≠ verified insight.

---

## Manual verification plan

### Local

```text
uv run python -m matcheyes serve deploy/matches --recordings deploy/recordings --speed 20
```

Open `http://127.0.0.1:8000/` and check:

1. First viewport: **MatchEyes — See beyond the score** + fixture identity + score/minute/
   replay state (score as context, not the whole product).
2. Match Story: insight appears/updates with published minutes; empty state before any;
   Story reads as primary vs chart/Stat Ledger.
3. Aside: switch **On-Pitch Record** vs **Verified Analysis**; contents stay in role.
4. Chart: readout and legend still describe snapshot (on-pitch) vs analysis publication.
5. **Trace the Evidence**: open disclosure; rows load; proposed clearly secondary to
   verified; revised/retracted cards still clear; close and reopen after a forced restart if
   testing run change locally.
6. **The Stat Ledger**: helper copy states context only / not automatic verified insights.
7. Pause / resume: hold summary; resume applies held messages; overflow path if exercised.
8. Disconnect (DevTools offline): reconnecting banner + live announcement; restore:
   replaying or complete as appropriate.
9. Keyboard: skip link, lane radios, chart event buttons, Trace summaries.
10. Theme: dark / light / system without losing status or proposed/verified distinction.

### Azure Container Apps

After a **separately approved** deploy of an image that includes the UI changes:

1. Hit public URL; allow cold start (connecting may last tens of seconds).
2. Repeat the local checklist on the hosted origin (same-origin SSE; no SWA split).
3. Confirm `/health` is ready before judging demos when possible.
4. Confirm About / provenance still describe fictional match + recorded reasoner as served.
5. Do **not** expect mid-match resume across scale-to-zero; new process ⇒ new `run` ⇒
   restart from kick-off (already announced by the page).

---

## UI implementation vs deployment approval

| Gate | What it covers | Who must approve |
| --- | --- | --- |
| **A — Documentation** | This file | Commit on request (amendments do not auto-commit) |
| **B — UI implementation** | Static HTML/CSS/JS + test pins only | Explicit “implement Stage 10 UI” authorization — **not** granted by this amendment |
| **C — Deploy** | Docker build, ACR push, ACA revision, `minReplicas` | Explicit deploy authorization (separate from B) |

Rules:

- Completing gate B does **not** imply gate C.
- Gate C must not change cue contracts, Foundry wiring, or CSP.
- Prefer verifying gate B locally before any ACA rollout.
- Cost/scale (`minReplicas` 0 vs 1) remains an operator choice under `deploy/azure/`; not part
  of presentation acceptance.
- This differentiation amendment does **not** authorize Gate B.

---

## Boundary reminder

Stage 10 presentation polish **preserves** existing ADRs and contracts. It rearranges and
labels what the server already sends—centering **Match Story** and **Trace the Evidence**
under **See beyond the score**. It does not generate football reasoning, modify verified
findings, change cue semantics, elevate proposed over verified, treat The Stat Ledger as
verified insight, or treat the browser as an authority on truth.
