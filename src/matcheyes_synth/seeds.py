"""Development vs held-out seeds.

- Development seeds (and each scenario's `default_seed`) may be used for anything: generator
  calibration, demos, and tuning Stage 2+ detection thresholds.
- Held-out seeds are reserved for reported evaluation metrics. Nothing may be tuned on them.
  They are disjoint from development seeds by construction.
"""

DEVELOPMENT_SEED_BASE = 10_000
HELD_OUT_SEED_BASE = 900_000
MAX_SEEDS_PER_SPLIT = 1_000


def development_seeds(count: int = 20) -> tuple[int, ...]:
    _check(count)
    return tuple(range(DEVELOPMENT_SEED_BASE, DEVELOPMENT_SEED_BASE + count))


def held_out_seeds(count: int = 20) -> tuple[int, ...]:
    _check(count)
    return tuple(range(HELD_OUT_SEED_BASE, HELD_OUT_SEED_BASE + count))


def is_held_out(seed: int) -> bool:
    return HELD_OUT_SEED_BASE <= seed < HELD_OUT_SEED_BASE + MAX_SEEDS_PER_SPLIT


def _check(count: int) -> None:
    if not 1 <= count <= MAX_SEEDS_PER_SPLIT:
        raise ValueError(f"count must be within 1-{MAX_SEEDS_PER_SPLIT}")
