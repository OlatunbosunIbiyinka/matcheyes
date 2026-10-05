# Development workflow

## Toolchain

| Tool | Purpose |
| --- | --- |
| Python 3.13 (`>=3.12`) | Runtime. Microsoft Agent Framework supports 3.10+. |
| uv | Environments, dependency locking (`uv.lock`), running tools. |
| ruff | Formatting and linting (incl. bandit security rules). |
| mypy (strict) | Static typing of `src/`. |
| pytest + pytest-cov | Tests and coverage. |
| GitHub Actions | CI: same gate as `scripts/check.*`. |

See [ADR-0003](decisions/0003-python-toolchain.md).

## Setup

```bash
pip install uv          # or: https://docs.astral.sh/uv/getting-started/installation/
uv sync
```

On Windows, if `uv` is not on `PATH` after a `--user` install, use `python -m uv ...`.

## Daily loop

```bash
uv run ruff format .    # format
uv run ruff check --fix .
uv run mypy
uv run pytest -m "not slow"   # fast loop (~30 s)
uv run pytest                 # everything, incl. multi-seed generator statistics (~5 min)
./scripts/check.sh      # full gate (Windows: ./scripts/check.ps1)
```

Tests marked `slow` are the generator's multi-seed realism and effect-size checks. CI always
runs them.

Analytics evaluation is a separate, manual step. It is not part of the gate, because its numbers
are results to report, not pass/fail thresholds:

```bash
uv run python -m matcheyes_eval tune --seeds 20                       # development seeds only
uv run python -m matcheyes_eval stage2 --split development --seeds 20
uv run python -m matcheyes_eval stage2 --split held-out --seeds 20    # only with a frozen config
uv run python -m matcheyes_eval stage3 --split development --seeds 20 # Stage 2 vs Stage 3
uv run python -m matcheyes_eval stage3 --split held-out --seeds 20    # only with frozen code
```

`python -m matcheyes analyse <match_dir> --contextual` prints Stage 3 contextual candidates
after the Stage 2 analysis.

## Coding standards

- Typed everywhere (`mypy --strict` on `src/`); typed models at every boundary.
- Respect layer boundaries (see `docs/architecture.md`); CI enforces them.
- Deterministic logic is never delegated to an LLM.
- Small modules with one responsibility; no hidden global state.
- Configuration from environment variables; no credentials in code or files. Azure access via
  `DefaultAzureCredential` / Managed Identity.
- Every new dependency is justified in the PR or an ADR.
- Comments explain constraints and intent, not what the code does.

## Branching and commits

- `main` is always green and runnable.
- Short-lived feature branches per stage, e.g. `stage-1-data-discovery`.
- Conventional commit prefixes: `feat:`, `fix:`, `docs:`, `test:`, `chore:`, `refactor:`.

## Architecture decisions

Significant decisions get an ADR in `docs/decisions/` using the template there.
