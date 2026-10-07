# MatchEyes

**See beyond the score.**

MatchEyes is an explainable football intelligence engine built on Azure for Microsoft's
*Inside the Game: Developer Hackathon* — challenge: **The Synthetic Match Insights Engine for
Premier League Studio**.

It turns synthetic, football-realistic match events into real-time insights, key narratives,
recaps and audience-personalized experiences for fans, studios, broadcasters and streaming
platforms.

> Don't just tell me what happened. Explain why it matters.

## How it works (target architecture)

```
RAW MATCH EVENTS
  -> deterministic football intelligence   (FACTS + ANALYTICS, no LLM)
  -> moment / momentum detection
  -> targeted agentic investigation        (AI REASONING)
  -> evidence verification                 (EVIDENCE)
  -> narrative generation                  (NARRATIVE)
  -> audience personalization              (PRESENTATION)
```

Every insight is traceable: *insight -> hypothesis -> supporting metrics -> source events ->
confidence -> explanation*. The system can always answer **"What evidence supports this?"**

See [`docs/architecture.md`](docs/architecture.md).

## Status

Built in explicit, reviewed stages. **Current: Stage 8 — Broadcast cue contract and live match
surface: the living insights of Stage 7, plus factual key moments, compiled into a timed,
content-addressed cue stream and replayed to a minimal web surface over Server-Sent Events.**
This is a deterministic replay of synthetic, fictional matches on one local process — not
production real-time broadcasting, and no cloud hosting.

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
| 8 | Broadcast cue contract and live match surface: moment / insight / revision / retraction / status cues, deterministic selection, one server-owned replay per match, read-only stdlib HTTP + SSE, CSP static page (no Foundry, no Azure) | In review |
| 9 | Production readiness (Azure / cloud-native hosting deferred from Stage 8) | Not started |
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
tests/             unit, architecture-boundary and (later) evaluation tests
data/              synthetic data and test fixtures
docs/              architecture, agent design, data model, evaluation, ADRs
scripts/           local quality gate
```

The Stage 8 web surface is three static files in `src/matcheyes/api/static/`. Infrastructure
(`infra/`) and a fuller web app come in later stages.

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
- [Development workflow](docs/development.md)
- [Architecture decision records](docs/decisions/README.md)
