# ADR-0005: Separate observable match data from hidden ground truth

- Status: Accepted
- Date: 2026-10-03
- Stage: 1

## Context

We generate our own synthetic matches with planted causes (ADR-0004), so that MatchEyes's
explanations can be scored objectively. That is only meaningful if MatchEyes can never see the
causes it is meant to infer. A leak, even an accidental one such as a scenario name in a match
ID, would make every evaluation result worthless.

## Decision

Model two worlds in separate top-level packages:

- `matcheyes`: the engine. Consumes only observable data (`matcheyes.domain`).
- `matcheyes_synth`: generator, league styles, hidden state, scenarios, answer keys.
- `matcheyes_eval`: the only code allowed to join engine output with ground truth.

Allowed dependencies: `matcheyes_synth → matcheyes.{domain, ingestion}`;
`matcheyes_eval → matcheyes, matcheyes_synth`. Nothing points back into the engine's direction.

Enforcement at seven layers: source scan, `importlib` lint ban, runtime `sys.modules` probe,
wheel contents (CI), schema field-name overlap, closed ingestion schemas, and opaque
identifiers. Deployment will add a storage boundary: the engine mounts only the observable
root. Details: [synthetic-data.md](../synthetic-data.md#two-logically-separate-worlds).

## Alternatives considered

| Option | Why not |
| --- | --- |
| Truth stored next to events in the same files, ignored by the engine | One careless field access leaks it. Not enforceable. |
| One package with a convention | Not testable; erodes under time pressure. |
| Separate repositories | Overhead without extra safety beyond what the wheel boundary gives. |

## Consequences

- Evaluation results are credible: the engine provably cannot read the answer key.
- The generator reuses the engine's own domain models, so observable output always matches the
  ingestion schema exactly.
- Small friction: shared structural field names must be reviewed and allow-listed in the
  schema test.
