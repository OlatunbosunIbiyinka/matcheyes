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

Built in explicit, reviewed stages. **Current: Stage 5 — Evidence audit, model evaluation and
verification hardening.**

| Stage | Scope | Status |
| --- | --- | --- |
| 0 | Project foundation | Done |
| 1 | Synthetic data design: observable model, hidden truth, scenarios, invariants | Done |
| 1b | Seeded generator implementation + calibration | Done |
| 2 | Deterministic football intelligence: metrics, statistical shifts, evidence, candidate moments (frozen baseline) | Done |
| 3 | Deterministic contextual strengthening: match context, contextual baselines, multi-signal patterns, persistence, uncertainty, ranking | Done |
| 4 | Agentic investigation: competing explanations, typed tools, challenger, deterministic verifier and claim ladder; first claims above `associated` | Done |
| 5 | Evidence audit and verification hardening: lineage, provenance replay, entailment, independent claim auditor, three-layer red team, blinded LLM evaluation harness (live LLM not yet evaluated) | In review |
| 6 | Personalization (Fan / Broadcaster / Analyst) | Not started |
| 7 | Product UX | Not started |
| 8 | Azure / cloud-native | Not started |
| 9 | Production readiness | Not started |
| 10 | Hackathon polish | Not started |
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

# Synthetic matches (hidden-world tooling; never shipped with the engine)
uv run python -m matcheyes_synth generate --scenario S02_press_surge   # default seed
uv run python -m matcheyes_synth realism --split held-out --count 20
uv run python -m matcheyes_synth effects --count 20

# Score analytics against planted truth (tune on development; held-out once)
uv run python -m matcheyes_eval stage2 --split development --seeds 20
uv run python -m matcheyes_eval stage4 --split development --seeds 20 --fault-seeds 5
uv run python -m matcheyes_eval redteam --split development --seeds 5
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
  api/             PRESENTATION HTTP boundary for the web app
src/matcheyes_synth/  HIDDEN    fictional league, hidden state, scenarios, generator (never shipped)
src/matcheyes_eval/   HIDDEN    scores engine output against the answer key (never shipped)
tests/             unit, architecture-boundary and (later) evaluation tests
data/              synthetic data and test fixtures
docs/              architecture, agent design, data model, evaluation, ADRs
scripts/           local quality gate
```

The web app (`web/`) and infrastructure (`infra/`) are added in Stages 7 and 8.

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
- [Development workflow](docs/development.md)
- [Architecture decision records](docs/decisions/README.md)
