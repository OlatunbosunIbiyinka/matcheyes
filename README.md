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

Built in explicit, reviewed stages. **Current: Stage 0 — Project foundation.**

| Stage | Scope | Status |
| --- | --- | --- |
| 0 | Project foundation | In review |
| 1 | Data discovery | Not started |
| 2 | Deterministic football intelligence | Not started |
| 3 | First end-to-end insight | Not started |
| 4 | Agentic architecture (Microsoft Agent Framework) | Not started |
| 5 | Evidence + verification | Not started |
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
```

Full workflow: [`docs/development.md`](docs/development.md).

## Repository layout

```
src/matcheyes/
  domain/          FACTS        typed football domain model
  ingestion/       FACTS        load + validate synthetic data -> domain
  analytics/       ANALYTICS    deterministic metrics, detection, evidence objects
  agents/          AI REASONING specialist agents (structured in, structured out)
  orchestration/   WORKFLOW     explicit EVENT->DETECT->INVESTIGATE->VERIFY->EXPLAIN->PERSONALIZE
  api/             PRESENTATION HTTP boundary for the web app
tests/             unit, architecture-boundary and (later) evaluation tests
data/              synthetic data and test fixtures
docs/              architecture, agent design, data model, evaluation, ADRs
scripts/           local quality gate
```

The web app (`web/`) and infrastructure (`infra/`) are added in Stages 7 and 8.

## Documentation

- [Architecture](docs/architecture.md)
- [Agent design](docs/agent-design.md)
- [Data model](docs/data-model.md)
- [Evaluation](docs/evaluation.md)
- [Development workflow](docs/development.md)
- [Architecture decision records](docs/decisions/README.md)
