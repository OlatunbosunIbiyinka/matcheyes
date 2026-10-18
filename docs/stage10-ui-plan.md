# Stage 10 UI plan: replay presentation polish

Status: **active product plan** (Gate A landed; Gate B chrome landed on `main` at
`9c73d82`; this revision records **Match Timeline → The Stat Ledger → What the Match Reveals**
before a separate commit). Source of truth: Stage 10 UI audit, differentiation review, Gate B,
plus the **What the Match Reveals** / ledger-after-graph product decisions. This document does
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
globally unique. Do not use **“Intelligence Changes”** or **“Match Story”** as preferred
user-visible labels.

---

## Product thesis

**MatchEyes — See beyond the score.**

MatchEyes helps viewers understand **what happened**, **what the available evidence
reveals**, and **why an analytical conclusion deserves its verification status** (proposed
vs verified, and when conclusions are revised or retracted).

The primary visual journey is **Match Timeline → The Stat Ledger → What the Match Reveals →
Trace the Evidence**, beneath brand and fixture. **The Stat Ledger** is immediate quantitative
match context after the graph—not proof of an insight and not the product centre.

**Product decision (hierarchy):** visualisation first, then statistics beside/after it for
context, then the analytical narrative. This is presentation only. It does **not** change the
truth pipeline, cue authority, verifier gates, or server behaviour. Statistics after the graph
must not be read as causal proof of findings below.

Presentation labels rename chrome only. They must not overclaim verification: proposed
findings stay distinguishable from verified findings; revisions and retractions keep their
existing cue semantics.

---

## Product language glossary (chrome only)

Internal identifiers (`moment`, `insight`, `revision`, `retraction`, `status`, `stats`,
`run`, `tone`, `#story-heading`, cue IDs) stay as today unless a rename is technically
required. Prefer updating user-visible strings alone.

| Presentation label | UI surface | Underlying concept | Must not imply |
| --- | --- | --- | --- |
| **MatchEyes — See beyond the score** | Title / topbar tagline; hero-level brand | Product identity | That the page invents tactics or overrides the score |
| **Match Timeline** | Primary visualisation (`#chart-heading`; existing SVG graph) | Snapshot-anchored on-pitch marks + analysis publication marks from cues/clock | Ball tracking, player positions, or event clocks more precise than snapshot close |
| **What the Match Reveals** | `#story-heading`, briefing, insight cards | Verified (and living) Stage 6/7/8 insight cues | That every card is causal proof without opening Trace |
| **On-Pitch Record** | Aside lane; chart event rail copy | `cue.kind === "moment"` (goals, cards, subs, periods) | Official league record, or planted hidden-world truth |
| **Verified Analysis** | Aside lane; analysis chart lane | Insight / revision / retraction cues | That every row is verified before the verifier ran; erase proposed / revised / retracted |
| **The Stat Ledger** | `#stats-heading` | ADR-0016 `stats` SSE; snapshot-anchored measures | That ledger figures are automatically verified insights or cue strength |
| **Trace the Evidence** | Card disclosure summary | `GET .../storylines/.../revisions/{n}` → `work_rows` | That proposed reasoner rows equal verified conclusions |

**Rejected / retired preferred labels:**

- **“Intelligence Changes”** — too ambiguous for a first-time judge; do not use.
- **“Match Story”** — retired as a preferred user-visible product label; replaced by
  **What the Match Reveals**. Internal ids such as `#story-heading` may remain.

**Helper copy required for The Stat Ledger:** statistics are **context only** and are **not**
automatically verified insights.

**Status distinctions retained (not renamed away):** proposed vs verified tones in Trace rows;
published / revised / retracted insight lifecycle; existing verdict and integrity labels on
cards.

---

## Revised information hierarchy

Product decision (presentation only):

1. **MatchEyes identity and fixture context** (hero: brand, competition, teams, score,
   minute, replay state).
2. **Match Timeline** — the existing match visualisation and replay position (primary visual
   anchor, not a decorative inset chart).
3. **The Stat Ledger** — existing team statistics immediately after the graph as supporting
   quantitative context (not automatically verified insights; not proof of an insight).
4. **What the Match Reveals** — evolving analytical narrative (briefing + insight cards).
5. **Trace the Evidence** — evidence inspection on each finding (not relocated away from the
   card).

**Layout:** Match Timeline is always full content width; The Stat Ledger always stacks
**directly below** it (no side-by-side at any breakpoint). DOM / reading order remains
**chart → stats → narrative** in all breakpoints.

Keep On-Pitch Record and Verified Analysis markers distinguishable on the timeline. Make the
connection between timeline developments and analytical findings clear **using existing data
only** (shared minute labels / snapshot mapping already on the page)—do not invent causal
links the server does not establish, and do not imply that ledger figures prove a finding.

Narrative and evidence must remain easy to reach (nearby in the page flow; skip link and
focus targets preserved or retargeted thoughtfully). Preserve responsive layout and
accessibility.

**Visualisation honesty:** the current graph is a **snapshot-anchored timeline** (event marks
at snapshot close; analysis marks at publication mapping). It is **not** pitch tracking. Do
not simulate ball coordinates, player positions, or finer clocks. If a richer pitch animation
is desired later, it requires data the server does not provide today—retain this supported
visualisation.

---

## Objective

Make the existing replay page judge-ready through **presentation-only** changes so that a
visitor can:

1. Recognise **MatchEyes — See beyond the score** and the **fixture** in the first viewport.
2. Read the **Match Timeline** as the main visual of what happened in match time.
3. Consult **The Stat Ledger** next as context only (not verified insight strength).
4. Follow **What the Match Reveals**, then open **Trace the Evidence**.
5. Tell **On-Pitch Record** apart from **Verified Analysis**.
6. Always see what the **replay system** is doing—including connect, pause, reconnect,
   complete, restart, and unavailable—without the page inventing truth.

Success criterion: a judge understands the match visualisation, the analytical narrative, the
evidence trail, and the connection state even when the stream drops; the browser never
becomes an authority on football facts; the surface does not read as a generic stats
dashboard or AI chat.

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
| `README.md` stage table chrome wording (optional, keep in sync) | Docs |

### Out of scope

- Backend behaviour, cue kinds, `BROADCAST_VERSION`, stats schema, verifier gates
- Live Foundry / `MATCHEYES_LLM_*` on the public path
- Azure resource changes, image rebuild, or deploy (see [UI vs deployment](#ui-implementation-vs-deployment-approval))
- Inventing match facts, metrics, confidence values, timestamps, evidence, fixture details,
  ball/player tracking, or event–insight causal links the API does not send
- New graphing libraries, CDN fonts/scripts, or any CSP relaxation
- Changing SSE event types, run-identity rules, or Trace-the-Evidence authority order
- Renaming internal API fields or cue `kind` / `tone` values
- Amending or rewriting the Gate B commit; this revision is a **separate** change

---

## Five UX goals

### 1. Editorial MatchEyes hero and fixture identity

**Intent:** Brand-first first viewport carrying **MatchEyes — See beyond the score**; clear
competition / teams / score / minute from server data only. The score remains visible context;
the thesis is what lies beyond it.

### 2. Match Timeline as primary visual anchor

**Intent:** The existing SVG timeline is the main visual of the match—replay position,
On-Pitch Record marks, and Verified Analysis publication marks—honest about snapshot-close
vs analysis timing. Not a demoted decorative chart.

### 3. What the Match Reveals (analytical narrative)

**Intent:** Briefing + insight cards explain what the evidence reveals as the match develops.
Newest-first card order may remain if it preserves cue integrity; do not invent chronology.
Trace the Evidence stays the defining disclosure on each finding.

### 4. Separate On-Pitch Record and Verified Analysis

**Intent:** Moments stay factual on-pitch events; insight / revision / retraction stay
Verified Analysis (with proposed / verified / revised / retracted distinctions intact).
Chart and aside must not blur those roles.

### 5. Accessible loading, reconnect, completion, restart, and error states

**Intent:** Visible (not only screen-reader) system status for connecting, replaying,
paused, reconnecting, reloading, complete, unavailable, and server restart (new `run`).

---

## Current implementation (post–Gate B baseline)

Single-page surface served under a strict CSP
(`default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self'`
in `api/server.py`). The page renders cue sections with `textContent` only and applies
show / expire / supersede rules; it computes nothing about the match.

| Area | Location | Behaviour (Gate B) |
| --- | --- | --- |
| Brand | `index.html` topbar + hero | Wordmark + tagline “See beyond the score” |
| Hero / fixture | `.hero`, `selectMatch` in `app.js` | Teams, crests, meta, score, clock, `#replay-state` |
| Narrative | `#story-heading`, `#briefing`, `#cards` | Was labelled “Match Story”; briefing + newest-first cards |
| Visualisation | `#chart` SVG | Snapshot-close on-pitch marks; analysis publication lane; was placed after `<main>` and marked supporting |
| Event vs analysis | Aside lanes + chart | On-Pitch Record / Verified Analysis |
| Evidence disclosure | Card `<details class="disclosure work">` | Trace the Evidence; run guard; proposed vs verified tones |
| Stats | `#stats` | The Stat Ledger; context-only / not automatically verified insights |
| Replay states | `REPLAY_TEXT`, `#connection`, pause/hold | Listed states; live regions |
| Stream | `EventSource` | `edition` (with `run`), `clock`, `stats`, `cue`, `end`, `unavailable` |
| Work API | `GET .../revisions/{n}` | Server `rows` only |

**Event time vs analysis publication time (already exposed):**

- On-pitch marks use each cue’s `snapshot_id` mapped through clock ticks to a
  **minute label at snapshot close**, not an invented exact event clock.
- Insight / revision / retraction cards use the same minute map for “Published …” /
  “updated …”. Do not invent a second timestamp the API does not send.

**Existing Stage 10-related tests to preserve:**

- `tests/architecture/test_stage10_serving.py`
- `tests/architecture/test_stage10_stats.py`
- `tests/architecture/test_replay_controls.py`
- `tests/architecture/test_stage8_security.py`

---

## Identified gaps (this revision)

| Goal | Gap |
| --- | --- |
| Hierarchy | Need DOM order **Match Timeline → The Stat Ledger → What the Match Reveals**; Gate B had narrative before chart; interim work may still leave Ledger after narrative |
| Narrative label | Prefer **What the Match Reveals**; no user-visible **Match Story** |
| Stats placement | Ledger must sit immediately after the graph for quantitative context without implying proof |
| Visual distinctiveness | Timeline as full-width main visual anchor; Ledger stacked below (never beside) |
| Tracking temptation | Do not “upgrade” the graph into fake pitch tracking |

No missing **server fields** are required. Product choices must still use only cue order and
existing minute labels.

---

## Ordered implementation plan (this revision)

Execute only under explicit authorization for this design revision (separate from Gate B
commit `9c73d82`). Each step should leave tests green and preserve SSE / run / CSP behaviour.

1. **Amend this plan** — record glossary, thesis, and hierarchy (this document).
2. **DOM hierarchy** — **chart → stats → main** (`What the Match Reveals`); `.match-centre`
   stacks Timeline full-width above Ledger at every viewport.
3. **Product language** — **What the Match Reveals**, **Match Timeline**, Stat Ledger dek;
   reject “Match Story” / “Intelligence Changes” in HTML chrome.
4. **Visual clarity** — timeline as anchor; event vs analysis distinction; Trace on cards.
5. **Regression pins** — pin `chart < stats < main`, labels, skip target, Stat Ledger dek.
6. **Manual verification** — local `serve` checklist below. Deploy only under separate Gate C.

Do not amend Gate B; do not combine deploy with this UI revision by default.

---

## Acceptance criteria

A judge who never opens the repository can:

1. See **MatchEyes — See beyond the score** and **which fixture** in the first viewport.
2. See the **Match Timeline** as the main visual of match developments (honest about data
   limits).
3. See **The Stat Ledger** immediately below the full-width graph as context only—not as
   automatically verified insights or proof of findings below.
4. Follow **What the Match Reveals** and open **Trace the Evidence** with verifier authority
   over proposed content; revised/retracted meanings intact; run guards intact.
5. Distinguish **On-Pitch Record** from **Verified Analysis**.
6. Always know replay state (connecting, replaying, paused, reconnecting, reloading,
   complete, unavailable, or restarted after a new `run`).

Non-criteria (must remain false):

- Page invents scores, events, metrics, evidence IDs, confidence, ball/player tracking, or
  causal event–insight or statistic–insight links.
- Page calls a live model or embeds Foundry credentials.
- Cue kinds, stats schema, tones, or CSP change.
- Labels imply proposed == verified, or that Stat Ledger figures are verified insights.
- “Match Story” remains a preferred user-visible product label.
- Claims of trademark clearance or global uniqueness for chrome names.

---

## Regression and accessibility tests

### Automated (required before calling this revision done)

| Suite | Protects |
| --- | --- |
| `tests/architecture/test_replay_controls.py` | Hold/resume; run reset; show-work run check; pause a11y |
| `tests/architecture/test_stage10_serving.py` | Stream/stats allowlists; chrome labels; DOM hierarchy |
| `tests/architecture/test_stage10_stats.py` | Stats contract pins |
| `tests/architecture/test_stage8_security.py` | No `innerHTML` / unsafe sinks; CSP-compatible surface |

Pin **What the Match Reveals**, **Match Timeline**, absence of user-visible **Match Story**
in HTML chrome, skip-link target, Stat Ledger context / not automatically verified insights,
and DOM order **chart → stats → main** (Match Timeline → Stat Ledger → What the Match Reveals).

### Accessibility expectations

- Skip link targets primary content thoughtfully (Match Timeline and/or narrative heading);
  landmarks and focus management on story/timeline resets remain.
- Keep `#announcer` and `#alerts` live regions; visible `:focus-visible`; pause
  `aria-describedby`.
- Theme switch and lane toggles remain keyboard operable.
- Do not rely on colour alone for verdict / retraction / reconnect / proposed vs verified.

---

## Risks

| Risk | Mitigation |
| --- | --- |
| Polish invents narrative or facts | Diff review: chrome labels and deks only; cue/about/work rows only |
| Fake tracking UI | Retain snapshot timeline; document honesty limit |
| Labels overclaim verification | Glossary; Stat Ledger dek; proposed below verified |
| Ledger read as proof of insights | Placement reason + dek; no invented statistic–insight links |
| Narrative hard to reach under viz | Keep narrative immediately below Timeline+Ledger; Trace stays on cards |
| Break pause / run / Trace fetch | Keep `test_replay_controls` green |
| CDN / CSP temptation | Forbidden |
| Scope creep into deploy | Hard gate below |
| Rewriting Gate B history | Separate commit only; no amend of `9c73d82` |

---

## Architectural constraints (explicit)

1. **Presentation sits downstream of verified truth** (ADR-0012 / ADR-0014).
2. **Cue contract frozen:** `moment`, `insight`, `revision`, `retraction`, `status`; `stats`
   is a separate SSE event (ADR-0016).
3. **Run identity** (ADR-0017): Trace responses must match current `run`; no cross-run
   `Last-Event-ID` splice.
4. **CSP unchanged**; `connect-src 'self'` only.
5. **Authority order in Trace the Evidence:** verified above proposed; page must not reorder.
6. **No live Foundry** on `serve` / ACA image (ADR-0015).
7. **Timestamps:** only `snapshot_id` → clock `minute`; no invented clocks or tracking.
8. **Labels are chrome only**; do not use “Intelligence Changes” or preferred “Match Story”.
9. **No overclaim:** proposed ≠ verified; Stat Ledger ≠ verified insight.
10. **Hierarchy change is product/presentation only** — not a truth-pipeline change.

---

## Manual verification plan

### Local

```text
uv run python -m matcheyes serve deploy/matches --recordings deploy/recordings --speed 20
```

Open `http://127.0.0.1:8000/` and check:

1. First viewport: brand + fixture + score/minute/replay state.
2. **Match Timeline** is full-width; **The Stat Ledger** stacks directly below; then
   **What the Match Reveals**.
3. Narrative label is **What the Match Reveals** (no preferred “Match Story” chrome).
4. Aside: On-Pitch Record vs Verified Analysis.
5. Timeline copy: snapshot close vs analysis publication; no fake tracking claims.
6. **Trace the Evidence**: load rows; proposed secondary; run/stale guards; stays on the card.
7. **The Stat Ledger**: context only / not automatically verified insights; not proof of findings.
8. Pause / resume; disconnect/reconnect; keyboard; theme.

### Azure Container Apps

After a **separately approved** deploy only. Same checklist on the hosted origin. Cold start
and scale-to-zero behaviour unchanged (new process ⇒ new `run`).

---

## UI implementation vs deployment approval

| Gate | What it covers | Who must approve |
| --- | --- | --- |
| **A — Documentation** | This file | Commit on request |
| **B — UI implementation** | Static HTML/CSS/JS + test pins (Gate B landed as `9c73d82`) | Was authorized separately |
| **B2 — Hierarchy / Reveals revision** | Plan amend + presentation/tests for this product decision | Explicit authorization for this revision |
| **C — Deploy** | Docker build, ACR push, ACA revision, `minReplicas` | Explicit deploy authorization |

Rules:

- Completing B2 does **not** imply Gate C.
- Do not amend Gate B; land B2 as a new commit when approved.
- Prefer verifying B2 locally before any ACA rollout.

---

## Boundary reminder

Stage 10 presentation polish **preserves** existing ADRs and contracts. It rearranges and
labels what the server already sends—centering **Match Timeline → The Stat Ledger → What
the Match Reveals → Trace the Evidence** under **See beyond the score**. It does not generate
football reasoning, modify verified findings, change cue semantics, elevate proposed over
verified, invent tracking, treat The Stat Ledger as verified insight or proof, or treat the
browser as an authority on truth.
