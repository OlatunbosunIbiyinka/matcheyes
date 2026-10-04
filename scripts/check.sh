#!/usr/bin/env bash
# Runs the same quality gate as CI. Usage: ./scripts/check.sh
set -euo pipefail
cd "$(dirname "$0")/.."

uv run ruff format --check .
uv run ruff check .
uv run mypy
uv run pytest --cov=matcheyes --cov=matcheyes_synth --cov-report=term-missing
echo "All checks passed."
