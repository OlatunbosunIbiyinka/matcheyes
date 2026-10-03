# ADR-0003: Python toolchain: uv, ruff, mypy, pytest

- Status: Accepted
- Date: 2026-10-03
- Stage: 0

## Context

The backend needs first-class support for Microsoft Agent Framework and Azure SDKs, typed data
models, fast iteration and reproducible builds (local, CI, containers).

## Decision

- **Python 3.13** (`requires-python >=3.12`). Microsoft Agent Framework's Python packages
  (`agent-framework-core`, `agent-framework-foundry`) are GA and support Python 3.10+.
- **uv** for environments and a committed lockfile (`uv.lock`).
- **ruff** for formatting and linting, including bandit (`S`) security rules.
- **mypy --strict** for `src/`.
- **pytest** + **pytest-cov** for tests.
- `src/` layout, single installable package `matcheyes`, one package per architectural layer.
- No runtime dependencies are added until a stage needs them.

## Alternatives considered

| Option | Why not |
| --- | --- |
| .NET Agent Framework | Viable, but Python is faster for data analysis and prototyping here. |
| pip + requirements.txt | No first-class lockfile / dependency groups; slower. |
| Poetry | Slower; uv covers the same needs. |
| black + flake8 + isort | Three tools where ruff is one. |
| Multiple services from day one | Premature; layers are packages first, split only if needed. |

## Consequences

- Contributors need `uv` installed.
- Strict typing adds some friction but catches boundary errors early.
