# Competitive analysis

Purpose: understand what others have shown, so MatchEyes is stronger rather than a copy.
Nothing here is reused: no code, prompts, diagrams, UI or wording. Only publicly visible
material was reviewed.

Labels used:

- **FACT**: what the public source shows.
- **INFERENCE**: our reading of it. It may be wrong.
- **MATCHEYES DECISION**: what we choose to do, independently.

Classifications: **BASELINE** (we must meet it), **DIFFERENTIATION** (a different experience or
approach), **ADVANCEMENT** (we take an existing idea materially further), **NOT WORTH COPYING**.

## Review log

| Round | Date | Scope | Next review |
| --- | --- | --- | --- |
| 1 | 2026-10-03 | Official rules; GitHub repos created after 2026-09-28; closely related football-AI projects | Before Stage 4 (agent design), and before Stage 10 using the hackathon project gallery (submissions open 2026-10-06) |

Context: submissions open on 2026-10-06, so most participant projects were not public at round
1. The three participant repos found were between zero and three days old. Their state will
change.

---

## Part A: Hackathon participants

### A1. Touchline (`subair99/touchline`)

- **Source:** https://github.com/subair99/touchline (created 2026-10-03; README only at review time)
- **What it does (FACT):** the description says synthetic events from its own generator flow
  through Event Hubs into a deterministic state engine. Five Foundry agents explain each moment.
  Two verification gates check every claim and every translation. Verified insights become
  overlays, recaps and personalised fan views.
- **Notable technical approach (FACT, stated only):** Event Hubs, a deterministic state engine,
  Foundry agents, verification of claims and of translations.
- **Notable product approach (FACT, stated only):** overlays, recaps, personalised views.
- **Strengths (INFERENCE):** the stated design is close to what the brief asks for: real-time
  transport, deterministic facts and verification. It will probably be a strong entry in the
  multi-agent and cloud-native categories.
- **Limitations/gaps (INFERENCE):** nothing is implemented yet, so quality is unknown. The
  description does not mention how explanation accuracy is measured, how insights change over
  time, or how agent disagreement is handled.
- **What MatchEyes learns:** "deterministic engine + agents + verification" is likely to be
  common among strong entries. It is the **baseline**, not our differentiator.
- **MATCHEYES DECISION:** compete on things this description does not claim: measured
  explanation accuracy against planted ground truth (D1), insights that change over time (D2)
  and causal-claim tests (D3). Re-review before Stage 4.

### A2. MatchMind (`abhiiiesh/MatchMind`)

- **Source:** https://github.com/abhiiiesh/MatchMind (created 2026-10-03; Python + React)
- **What it does (FACT):** a pipeline of seven named "agents": ingestion, metrics, context,
  narrative, persona, translator and fact-checker. Output goes out over WebSocket/REST, an OBS
  overlay page and Azure AI Speech neural voices in six languages. There are heatmaps, pass
  networks, pressing zones, a timeline scrubber, Bicep for Container Apps, and Cosmos DB for
  retrieval over historical data.
- **Notable technical approach (FACT):** the fact-checker uses regular expressions to compare
  scorelines in the text with the true score, and to stop non-goals being celebrated as goals.
  The synthetic generator defaults to 120 events per match, uses real club names, and maps onto
  the StatsBomb schema. The match catalogue includes real fixtures and real player names from
  StatsBomb open data.
- **Notable product approach (FACT):** persona and language switching, a player focus card,
  a broadcast overlay, spoken commentary.
- **Strengths (INFERENCE):** wide feature coverage, a polished-looking dashboard and a clear
  demo story. Overlay and multilingual audio map directly onto the "render" and "personalize"
  stages.
- **Limitations/gaps (INFERENCE):**
  - Several "agents" are deterministic pipeline stages. Calling them agents inflates the count
    without adding agentic behaviour.
  - Verification works at string level, not claim level. Causal or tactical claims ("pressure
    caused...") are not checked.
  - 120 events per match is about an order of magnitude below a real match, which weakens the
    "football-realistic" claim.
  - Real club and player names and real StatsBomb matches sit uneasily with the rules: the
    data must be synthetic, and the video may not use third-party trademarks without permission.
  - Breadth over depth: many features, but no visible way to measure how good an explanation is.
- **What MatchEyes learns:** breadth demos well. A broadcast overlay and multilingual output
  are expected. Labelling functions as agents is a weakness a judge can spot.
- **MATCHEYES DECISION:** meet the overlay and multilingual baseline (B4, B5). Be strict about
  what counts as an agent (N1). Use a fictional league (B6). Make verification claim-level and
  partly deterministic (A1). Do not compete on feature count.

### A3. PitchLens (`enniob/pitchlens`)

- **Source:** https://github.com/enniob/pitchlens (created 2026-09-30; TypeScript / Next.js)
- **What it does (FACT):** a browser match viewer with 3D players, ball physics, rules,
  formations and a seeded synthetic simulator with fictional clubs. It has playback,
  seeking and statistics. Its "explain this moment" feature is at the contract stage, and the
  README says live AI is not implemented yet.
- **Notable technical approach (FACT):** the explanation contract builds a bounded,
  deterministic evidence package for a chosen moment. It includes no information from after
  that moment. A response validator requires every citation to resolve to an event or snapshot,
  forbids "facts" that assert intent or cause, and allows an insufficient-evidence status. It
  has extensive unit tests.
- **Notable product approach (FACT):** visual-first. Fictional clubs, explicitly labelled
  synthetic, with no network calls at runtime.
- **Strengths (INFERENCE):** very disciplined engineering. The no-future-leakage rule and
  citation-resolving validation are exactly the right grounding primitives. Using fictional
  clubs avoids trademark risk.
- **Limitations/gaps (INFERENCE):** there's no AI, Azure or agentic layer yet, and most effort
  has gone into rendering and physics. Category adherence and agentic design depend on work not
  yet visible.
- **What MatchEyes learns:** citation resolution, "no information from the future" and an
  explicit insufficient-evidence outcome are the **minimum** credible grounding design.
- **MATCHEYES DECISION:** adopt these as baseline requirements of our own evidence model (B2,
  B3). Go further with claim types, causal tests, confidence that changes over time, and
  measured accuracy (A1, D1–D3). Do not compete on 3D rendering (N3).

---

## Part B: Related football-AI systems (outside the hackathon)

| Project | Source | FACT (what it shows) | Lesson for MatchEyes |
| --- | --- | --- | --- |
| ITF Match Insights (Microsoft customer story) | [microsoft.com](https://www.microsoft.com/en/customers/story/26191-itf-licensing-uk-ltd-azure) | Tennis. Unified telemetry model on Azure (Databricks, Data Lake, Azure SQL), with an interpretation layer on Microsoft Foundry and Azure OpenAI that answers coaches' questions mid-match. Stresses one consistent view for all users. | A real production example of "deterministic facts + Foundry interpretation". Useful for the real-world impact story. |
| Euro 2024 momentum + AI commentary | [ItayAvioz/Euro.2024.momentum.prediction](https://github.com/ItayAvioz/Euro.2024.momentum.prediction) | A data-led momentum model using 3-minute windows, with a forecasting model. An LLM agent decides whether momentum insight is interesting enough to include, using a confidence threshold. Flags "tension" when the score leader and momentum leader differ. | Choosing *when* to speak is valuable: insight selection beats insight volume. Score leader versus momentum leader is a strong narrative trigger. |
| Multi-agent football analyzer (video) | [predaaaasaaaaaaa/mult-agents-football-match-analyzer](https://github.com/predaaaasaaaaaaa/mult-agents-football-match-analyzer) | The first stages are deterministic Python. An LLM is used only for reporting. The LLM sees summaries and pulls specific data through tools. | Confirms our ADR-0002 approach and the cost discipline of tools-on-demand. |
| Multi-agent soccer orchestration | [MaximPyanin/multi-agent-soccer-orchestration](https://github.com/MaximPyanin/multi-agent-soccer-orchestration) | A supervisor routes chat questions to data and web-search agents (LangGraph, Azure OpenAI). | A generic football chatbot. Exactly what we avoid as the main experience. |
| PitchSideAI | [s23deepak/PitchSideAI](https://github.com/s23deepak/PitchSideAI) | A seven-agent pre-match research pipeline, live Q&A, WebSocket commentary, Celery/Redis/Postgres. | Heavy infrastructure for research notes. Durable jobs are useful later; the agent count is not a value in itself. |

---

## Part C: Classified ideas and MatchEyes response

### BASELINE: we must meet these

| ID | Idea | Seen in | MatchEyes response |
| --- | --- | --- | --- |
| B1 | Deterministic analytics; the LLM only interprets | Touchline (stated), video analyzer, ITF | Already ADR-0002, enforced in CI. |
| B2 | Every cited fact resolves to a real event or metric | PitchLens | Evidence objects carry event IDs. The validator rejects unresolved citations. |
| B3 | No information from the future: an insight at time *t* uses only events at or before *t* | PitchLens | Built into the analytics API (as-of queries) and tested. |
| B4 | Overlay-ready, timed, machine-readable output | MatchMind, Touchline (stated), rules | A versioned overlay contract with display window and expiry. |
| B5 | Multilingual output | MatchMind, Touchline (stated), rules | Supported in Stage 6; numbers and claim IDs must survive translation. |
| B6 | Fictional clubs and players, labelled synthetic | PitchLens, rules | Fictional league. No real names or crests anywhere, including the video. |
| B7 | Audience modes (fan / analyst / broadcaster) | MatchMind, rules | Stage 6, plus favourite club / player focus as the rules ask. |
| B8 | Momentum timeline and key-moment pins | MatchMind, Euro 2024 | Part of Stage 7 UX. |

### DIFFERENTIATION: a different experience or approach

| ID | Idea | Why it matters | Feasibility |
| --- | --- | --- | --- |
| D1 | **Planted-truth synthetic data.** Our generator is driven by hidden tactical states (pressing intensity, tempo, fatigue, tactical changes) that cause events. The causes it plants are written to a separate answer key the engine never sees. The evaluation suite then scores whether MatchEyes recovers the planted cause, when, and with what confidence. | Turns "the explanation looks right" into a **measured explanation accuracy**. Directly scores on "optimized synthetic data creation" and on evaluation. No reviewed competitor claims this. | High. We control the generator. The risk is circularity, which we handle by keeping the answer key in a separate module (enforced by an architecture test) and keeping detection logic independent of the generator's parameters. |
| D2 | **Living insights.** Insights are versioned and can gain confidence, lose it, or be superseded ("At 34' we said X; by 61' the evidence says Y"), with the history visible. | The brief's real-time requirement made visible. A chatbot or a static recap cannot do this. Strong demo moment. | Medium. Needs an insight store with versions and re-evaluation as new events arrive. Design in Stage 3, harden in Stage 9. |
| D3 | **Causal-claim tests.** A causal claim ("pressure caused the momentum shift") must pass deterministic checks: the cause comes first in time, the change is large compared with the team's own baseline, and the cause and effect happen in the same area or phase. Claims that fail are downgraded to "associated with" or rejected. | Goes beyond checking that numbers are right, to checking that the *reasoning* is supported. Addresses "control vs chaos / pressure changes" from the brief honestly. | Medium. Built on Stage 2 analytics. Thresholds are calibrated using D1's planted causes. |
| D4 | **Competing hypotheses, adjudicated.** Specialists propose alternative explanations. The verifier scores each against the evidence and shows why one won, one was secondary, or one was rejected. | Visible, purposeful multi-agent value (an outcome one agent would not reach as well). Matches the Best Multi-Agent criteria. | Medium. Only kept if Stage 4 evaluation shows it improves accuracy on D1 scenarios. Otherwise simplified. |
| D5 | **Selective speech.** Insights are only promoted when they cross a significance threshold. "Silence" is a valid output, and suppressed candidates are logged. | Broadcast-realistic: fewer, better insights. Avoids the commentary spam common in LLM demos. | High. Deterministic scoring plus a threshold. |

### ADVANCEMENT: taking an existing idea further

| ID | Existing idea | How MatchEyes goes further |
| --- | --- | --- |
| A1 | Fact-checking by regex (MatchMind), citation validation (PitchLens) | Claim-level verification: each claim is typed (FACT / ANALYSIS / INTERPRETATION). Numeric claims are checked deterministically against analytics. Causal claims go through D3. Only unclear interpretations go to an LLM verifier. The outcome is accept / downgrade / reject, with a reason. |
| A2 | Persona switching | A **same-facts guarantee**: every audience and language variant must cite the identical set of verified claims. An automated consistency test enforces this. |
| A3 | Overlay output | Overlay events carry as-of time, display window, expiry and a link to what they supersede, so a broadcast graphic can be retracted when the insight changes (ties to D2). |
| A4 | Agent tracing | A per-insight provenance view in the UI ("show your work"): events, metrics, hypotheses, verdicts, agent runs, latency and cost. Built on Foundry tracing / OpenTelemetry where verified. |
| A5 | Score leader vs momentum leader (Euro 2024) | Generalised into narrative triggers from deterministic match-state tension (against the run of play, sustained pressure without reward, late-game control loss), each with evidence. |

### NOT WORTH COPYING (for MatchEyes)

| ID | Approach | Reason |
| --- | --- | --- |
| N1 | Naming deterministic pipeline stages as "agents" | Inflates agent count. Judges look for distinct, purposeful roles. |
| N2 | A general football chatbot as the main experience | Commodity. Fails "why not just ask a chatbot?". A scoped "ask about this moment, grounded in its evidence" may come later. |
| N3 | 3D rendering, ball physics, neural speech/SSML | High effort, little help with explainability. Outside our strengths and the rubric's core. |
| N4 | Retrieval over real historical club records | Trademark/real-data risk and not needed for synthetic matches. |
| N5 | Auto-eventing from video | The rules list it, but it needs footage we may not use and is a separate computer-vision project. Documented as out of scope. |
| N6 | Many infrastructure components from day one (Celery, Redis, Cosmos, Event Hubs, Speech) | Each must answer "what problem does this solve?". Decided in Stage 8 based on the real workload. |

---

## Part D: Positioning

**Why would someone use MatchEyes instead of the score, a stats panel, or a chatbot?**

| Alternative | What it can't do that MatchEyes does |
| --- | --- |
| Scoreline | Says nothing about control, pressure or whether the result reflects the game. |
| Stats panel | Shows numbers without saying which ones matter *now* and why. |
| Generic chatbot | Can't see live events, can't prove its claims, doesn't update when the match changes, and may invent facts. |
| Typical hackathon "agents + LLM" entry | Explanations are plausible but unmeasured. MatchEyes shows a measured explanation accuracy against planted truth, shows its evidence for every claim, and revises itself as the match evolves. |

**Draft one-liner (ours):** *MatchEyes explains why the match is turning, proves it with
evidence, and changes its mind when the game does.*

**Category hypothesis:** Grand Prize plus **Best Multi-Agent System** or **Best Enterprise
Solution**. Decide after Stage 4, based on which of D4 (multi-agent) or A1/A4 (trust and
controls) proves strongest. Touchline's stated design suggests the multi-agent category will be
contested, which favours leading with trust and evaluation.

## Part E: Impact on the plan

1. **Stage 1 changes scope.** No organiser data exists. Stage 1 becomes: design the synthetic
   data model, build a seeded generator with hidden tactical states and a separate answer key
   (D1), validate it for realism against published football benchmarks, and produce fixtures.
   Proposed as ADR-0004.
2. **Stage 2:** analytics expose as-of queries (B3) and evidence objects with event IDs (B2).
3. **Stage 3:** the first end-to-end insight is scored against the planted truth from day one.
4. **Stages 4–5:** D3, D4 and A1 are built only if evaluation shows they add value.
5. **Every review round:** update this document before major decisions.
