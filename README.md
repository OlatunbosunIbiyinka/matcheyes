# MatchEyes

**See beyond the score.**

MatchEyes is an explainable football intelligence engine built for Microsoft's
*Inside the Game: Developer Hackathon* — challenge: **The Synthetic Match Insights Engine for
Premier League Studio**. A Microsoft Foundry model (`gpt-5-mini`) has been evaluated behind the
reasoning boundary, and the web surface replays a *recorded* run of it; the application runs
locally (a container image exists; it is not deployed to Azure).

It turns synthetic, football-realistic match events into explainable insights, key moments and
audience-personalized views that update minute by minute as a match is replayed (deterministic,
accelerated replay over Server-Sent Events; not production real-time ingestion) for fans,
studios, broadcasters and streaming platforms.

> Don't just tell me what happened. Explain why it matters.

## How it works (as built through Stage 9)

```
OBSERVABLE MATCH EVENTS
  -> event log and per-minute snapshots
  -> deterministic football intelligence   (FACTS + ANALYTICS, no LLM)
  -> contextual candidates                 (Stage 3)
  -> investigator + challenger             (AI REASONING; rule-based reference by default,
                                             or a recorded Foundry model run as Challenger)
  -> evidence verification and audit       (EVIDENCE)
  -> templated narrative                   (NARRATIVE)
  -> living insight lifecycle              (storylines, append-only revisions)
  -> audience personalization              (PRESENTATION)
  -> broadcast cue compiler
  -> read-only API + SSE -> web surface
```

Every insight is traceable: *insight -> hypothesis -> supporting metrics -> source events ->
confidence -> explanation*. The system can always answer **"What evidence supports this?"**

See [`docs/architecture.md`](docs/architecture.md).

## Status

Built in explicit, reviewed stages. **Current: Stage 9 (in review) — a real Microsoft Foundry
model behind the existing reasoning boundary: an untrusted proposal generator whose output passes
the same verifier and audits, evaluated on held-out data, recorded once and replayed on the web
surface with a read-only "show the work" view.** The public surface never calls the model: it
replays a pinned recording, and a missing entry shows as unavailable. This is a deterministic
replay of synthetic, fictional matches on one local process — not production real-time model
inference or broadcasting, and not cloud-hosted.

| Stage | Scope | Status |
| --- | --- | --- |
| 0 | Project foundation | Done |
| 1 | Synthetic data design: observable model, hidden truth, scenarios, invariants | Done |
| 1b | Seeded generator implementation + calibration | Done |
| 2 | Deterministic football intelligence: metrics, statistical shifts, evidence, candidate moments (frozen baseline) | Done |
| 3 | Deterministic contextual strengthening: match context, contextual baselines, multi-signal patterns, persistence, uncertainty, ranking | Done |
| 4 | Agentic investigation: competing explanations, typed tools, challenger, deterministic verifier and claim ladder; first claims above `associated` | Done |
| 5 | Evidence audit and verification hardening: lineage, provenance replay, entailment, independent claim auditor, three-layer red team, blinded LLM evaluation harness (live LLM not yet evaluated) | Done |
| 6 | Personalization (Fan / Broadcaster / Analyst): audience views and feeds over verified insights, preferences for relevance only, independent view audit, presentation red team (no user study yet) | Done |
| 7 | Snapshot-anchored insight lifecycle: event log with contiguous watermark, one canonical snapshot per closed minute, storylines with append-only revisions, lifecycle audit, replay evaluation (no corrections, no streaming infrastructure) | Done |
| 8 | Broadcast cue contract and live match surface: moment / insight / revision / retraction / status cues, deterministic selection, one server-owned replay per match, read-only stdlib HTTP + SSE, CSP static page (no Foundry, no Azure) | Done |
| 9 | Foundry model behind the reasoning boundary: strict wire schemas, Entra auth, held-out evaluation of the real model (model as Investigator rejected on measured evidence; model as Challenger adopted), recorded runs replayed publicly, read-only show-your-work, container image (Azure deployment not performed; open findings in [stage9-evaluation.md](docs/stage9-evaluation.md)) | In review |
| 10 | Product UX and hackathon polish | Not started |
| 11 | Final submission | Not started |

## Quick start

Requires Python 3.12+ (3.13 pinned) and [uv](https://docs.astral.sh/uv/).

```bash
uv sync                 # create .venv and install locked dependencies
uv run matcheyes        # smoke test the package
./scripts/check.sh      # format, lint, type-check, test (Windows: ./scripts/check.ps1)

# Deterministic analysis of any observable match directory
uv run python -m matcheyes analyse data/fixtures/minimal_match --json analysis.json

# Investigate each Stage 3 candidate (deterministic reference reasoner; --llm uses a model
# configured through MATCHEYES_LLM_* environment variables)
uv run python -m matcheyes investigate data/fixtures/minimal_match

# The same insights for one audience (presentation only; the verified truth is unchanged).
# --club / --player take IDs, --metric a metric name; all need --audience
uv run python -m matcheyes investigate data/fixtures/minimal_match --audience fan --club <club_id>

# Replay a match minute by minute: storylines, revisions and the lifecycle feed
uv run python -m matcheyes replay data/fixtures/minimal_match --json lifecycle.json

# The broadcast cue timeline of one surface (offline compile), and the live surface:
# a read-only server on http://127.0.0.1:8000 replaying observable matches at x20 over SSE
uv run python -m matcheyes cues data/fixtures/minimal_match --audience fan --json cues.json
uv run python -m matcheyes_synth generate --scenario S05_red_card_reorganisation --out demo
uv run python -m matcheyes serve demo/observable --speed 20

# The bundled demo: a recorded Foundry run (model as Challenger), replayed; no credentials needed
uv run python -m matcheyes serve deploy/matches --recordings deploy/recordings --speed 20
# Record a match once against a configured model (MATCHEYES_LLM_*; see .env.example)
uv run --extra azure python -m matcheyes record deploy/matches/<match_id> --out deploy/recordings --roles challenger

# Synthetic matches (hidden-world tooling; never shipped with the engine)
uv run python -m matcheyes_synth generate --scenario S02_press_surge   # default seed
uv run python -m matcheyes_synth realism --split held-out --count 20
uv run python -m matcheyes_synth effects --count 20

# Score analytics against planted truth (tune on development; held-out once)
uv run python -m matcheyes_eval stage2 --split development --seeds 20
uv run python -m matcheyes_eval stage4 --split development --seeds 20 --fault-seeds 5
uv run python -m matcheyes_eval redteam --split development --seeds 5
uv run python -m matcheyes_eval stage6 --split development --seeds 2
uv run python -m matcheyes_eval stage7 --split development --seeds 1
uv run python -m matcheyes_eval stage8 --split development --seeds 1
# LLM path: --profile live needs MATCHEYES_LLM_*; other profiles are SIMULATED, not a model
uv run python -m matcheyes_eval llm --profile faithful --split held-out --seeds 2 --matches 7
# Stage 9: re-score a recorded real-model run offline (--record makes live calls)
uv run python -m matcheyes_eval stage9 --transcript data/recordings/eval/stage9-heldout-bprime.transcript.json.gz
uv run python -m matcheyes_eval stage9-lifecycle --scenario S05_red_card_reorganisation --recordings deploy/recordings
```

Full workflow: [`docs/development.md`](docs/development.md).

## Repository layout

```
src/matcheyes/
  domain/          FACTS        typed football domain model
  ingestion/       FACTS        load + validate synthetic data -> domain
  analytics/       ANALYTICS    deterministic metrics, detection, evidence objects
  agents/          AI REASONING investigator + challenger roles, typed tools, verifier, narrative
  orchestration/   WORKFLOW     explicit, bounded investigation flow and traces
  personalization/ PRESENTATION audience views and feeds of verified insights, view audit
  lifecycle/       WORKFLOW     snapshots, storylines, append-only revisions, lifecycle audit
  broadcast/       PRESENTATION cue contract, moments, selection, cue timeline compiler
  api/             PRESENTATION read-only HTTP + SSE, server-owned live replay, static web surface
src/matcheyes_synth/  HIDDEN    fictional league, hidden state, scenarios, generator (never shipped)
src/matcheyes_eval/   HIDDEN    scores engine output against the answer key (never shipped)
tests/             unit, architecture-boundary, security and evaluation tests
data/              synthetic data and test fixtures
docs/              architecture, agent design, data model, evaluation, ADRs
scripts/           local quality gate
```

The web surface is three static files in `src/matcheyes/api/static/`. `Dockerfile` and `deploy/`
(an observable match, its pinned recording, hash-pinned requirements) build a credential-free
image of the replay server; it has not been deployed.

## Documentation

- [Hackathon brief](docs/hackathon-brief.md)
- [Competitive analysis](docs/competitive-analysis.md)
- [Architecture](docs/architecture.md)
- [Agent design](docs/agent-design.md)
- [Data model (observable)](docs/data-model.md)
- [Synthetic data: two worlds, one generator](docs/synthetic-data.md)
- [Causal claims ladder](docs/causal-claims.md)
- [Evaluation](docs/evaluation.md)
- [Deterministic metrics](docs/metrics.md) and [Stage 2 evaluation](docs/stage2-evaluation.md)
- [Contextual evidence](docs/contextual-evidence.md) and [Stage 3 evaluation](docs/stage3-evaluation.md)
- [Agentic investigation](docs/agentic-investigation.md) and [Stage 4 evaluation](docs/stage4-evaluation.md)
- [Evidence audit](docs/evidence-audit.md) and [Stage 5 evaluation](docs/stage5-evaluation.md)
- [Personalization](docs/personalization.md) and [Stage 6 evaluation](docs/stage6-evaluation.md)
- [Living insights](docs/living-insights.md) and [Stage 7 evaluation](docs/stage7-evaluation.md)
- [Broadcast cues and the live surface: Stage 8 evaluation](docs/stage8-evaluation.md)
- [A Foundry model as an untrusted reasoner: Stage 9 evaluation](docs/stage9-evaluation.md)
- [Development workflow](docs/development.md)
- [Architecture decision records](docs/decisions/README.md)
