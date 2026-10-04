# ADR-0004: Generate our own synthetic data with planted ground truth

- Status: Accepted (2026-10-03, with Stage 0 approval)
- Date: 2026-10-03
- Stage: 1

## Context

The official rules require synthetic, football-realistic data, but **no data is provided** by
the organisers. Judges explicitly score "creativity and optimized synthetic data creation". The
video may not show third-party trademarks. Competitor review (`docs/competitive-analysis.md`)
found generators with unrealistic event volumes and real club names. It also found no entry
that measures whether its explanations are correct.

## Decision (proposed)

Build a seeded, deterministic synthetic match generator in which hidden tactical states
(e.g. pressing intensity, tempo, fatigue, tactical changes) cause the event stream. Each match
produces two separate outputs:

1. **Event stream:** the only input the MatchEyes engine ever sees.
2. **Answer key:** the planted causes and when they happen. Only the evaluation suite may read it.

The separation is enforced by an architecture test. The generator uses a fictional league,
clubs and players, and is checked for realism against published football benchmarks (event
counts, pass completion, shot volumes, possession distributions).

## Alternatives considered

| Option | Why not |
| --- | --- |
| Reshape a real open dataset (e.g. StatsBomb open data) | Not synthetic; licence and trademark issues; no ground truth for causes. |
| Simple random/Markov generator | Unrealistic. Momentum and tactical shifts have no real cause to explain. |
| LLM-generated events | Not reproducible, statistically unreliable, expensive. |

## Consequences

- Enables a **measured explanation accuracy**, the core differentiator.
- Scenarios (comeback, pressing surge, red card, late collapse) are reproducible for demos and
  tests.
- Risk of circularity: detection logic might simply mirror the generator. Mitigations: the
  answer key is kept separate, detectors are written independently of generator parameters,
  and evaluation includes noise and control scenarios with nothing planted.
- Stage 1 takes more effort than "inspect supplied data", but it produces a scored capability.
