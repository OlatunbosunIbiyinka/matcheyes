# Data

MatchEyes runs only on **synthetic, football-realistic** match data, as required by the hackathon.

| Folder | Purpose | Committed? |
| --- | --- | --- |
| `data/raw/` | Official hackathon synthetic data, unmodified, if its licence allows redistribution | Decided in Stage 1 |
| `data/fixtures/` | Small, representative slices used by tests | Yes |
| `data/local/` | Scratch space for local experiments | No (git-ignored) |

The schema is documented in [`docs/data-model.md`](../docs/data-model.md). Nothing in this
repository assumes a field exists until it has been seen in the real data.
