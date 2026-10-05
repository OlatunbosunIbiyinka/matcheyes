# Personalization (Stage 6)

Stage 6 presents the verified insights of Stages 2–5 to three audiences (fan, broadcaster,
analyst), with optional preferences for a club, a player and a metric. It is a **presentation
layer**: it changes emphasis, ordering, depth and wording around the truth, never the truth.

Decision: [ADR-0012](decisions/0012-personalization-presentation-layer.md). Evaluation:
[stage6-evaluation.md](stage6-evaluation.md).

> No user study has been run. Nothing here shows that real fans, broadcasters or analysts
> prefer these views. The evaluation measures truthfulness, coverage and safety only.

## What may and may not change

| Personalization may change | Personalization may not change |
| --- | --- |
| which sections are shown, and in which order | verdict, claim strength, causal status |
| how much detail is shown | claim text, the Stage 3 fact |
| framing words around the verified text | evidence, evidence IDs, supporting / contradicting IDs |
| relevance score and feed order | integrity state, quarantined evidence |
| audience terminology (definitions, game state) | uncertainty and "not ruled out" lists |
| | any number |

## Flow

```
investigate_match ─► records (FinalInsight + verification)
                         │
                         ▼
   audit_insight (Stage 5 claim auditor) ── fails ──► withheld (listed by ID)
                         │ passes
                         ▼
   personalize(final, profile, ws) ─► PersonalizedInsight   (construction invariants)
                         │                     └─ invalid ──► withheld
                         ▼
   audit_view(view, ws, final) ── findings ──► withheld
                         │ clean
                         ▼
   placement + order ─► AudienceFeed(primary, secondary, withheld, notice)
```

`src/matcheyes/personalization/`:

| Module | Role |
| --- | --- |
| `contracts.py` | `Audience`, `Language` (EN), `PersonalizationProfile`, `ViewSection`, `PersonalizedInsight`, `AudienceFeed`; the fingerprint and the construction invariants (`truth_violations`) |
| `involvement.py` | favourite club / player / metric looked up in the match and the insight's cited events |
| `policy.py` | relevance weights, feed placement, order, the per-audience section table |
| `glossary.py` | plain metric definitions, from [metrics.md](metrics.md) |
| `render.py` | the three audience templates; `personalize`, `format_view` |
| `audit.py` | `audit_view`, the independent view auditor |
| `feed.py` | `build_feed`, `format_feed` |

Dependencies: personalization may import `domain`, `analytics`, `agents` and `orchestration`.
Nothing in those layers imports personalization (`tests/architecture`).

## Contracts

**`PersonalizationProfile`** holds only enums and identifiers:

* `audience`: fan, broadcaster or analyst;
* `favourite_club_id`, `favourite_player_id`: identifier pattern `^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$`;
* `favourite_metric`: one of the 12 metric names;
* `language`: `en` only.

Free text (spaces, newlines, markup, instructions) is rejected at the boundary. Profiles are
never stored.

**`PersonalizedInsight`** holds the verified `FinalInsight` itself as `source`; no truth field
is copied out. `source_fingerprint` is the SHA-256 of the source's canonical JSON. A view
also carries the profile, a relevance score with its basis, ordered `ViewSection`s, and the
optional content this audience leaves out (`omitted`).

**Construction invariants** (`truth_violations`, enforced by the model validator):

1. the fingerprint matches the source;
2. exactly one mandatory FACT section, the Stage 3 statement verbatim;
3. integrity: the compromised warning when compromised, the exclusion notice when evidence was
   quarantined otherwise; no warning on an intact insight;
4. an explained insight (explained, tentative, natural variation) has exactly one mandatory
   INTERPRETATION containing the verification label and the lead claim's text, citing exactly
   the claim's supporting evidence, and a mandatory CAVEAT equal to the claim's uncertainty;
5. an unexplained insight (insufficient evidence, unavailable) has a mandatory "No verified
   explanation" section and no interpretation, caveat, trigger or label;
6. evidence IDs come from the verified pool; quarantined IDs appear only in the QUARANTINE
   section, or in a DETAIL section as the verifier's record of the exclusion;
7. the canonical narrative, if shown, is unchanged.

Section kinds: FACT, CONTEXT, INTEGRITY, INTERPRETATION, CAVEAT, NO_INSIGHT (mandatory core:
FACT, INTEGRITY, INTERPRETATION, CAVEAT, NO_INSIGHT), and the optional TRIGGER, GLOSSARY,
INVOLVEMENT, ALTERNATIVES, EVIDENCE, QUARANTINE, DETAIL, NARRATIVE.

## Audience views

All views share the mandatory core. They differ in optional sections and framing.

| Audience | Optional sections | Framing |
| --- | --- | --- |
| Analyst | NARRATIVE (canonical, verbatim), ALTERNATIVES, a DETAIL per claim (type, strength, status, IDs), a DETAIL with integrity state, Stage 3 level and basis, downgrades and failure, an EVIDENCE section per verified item (tool, summary, facts), QUARANTINE, INVOLVEMENT | `[label] claim text (evidence …)` |
| Broadcaster | CONTEXT (`minute \| team`), TRIGGER, INVOLVEMENT | `claim text [label] (evidence …)` |
| Fan | CONTEXT (minute, team, game state), GLOSSARY, INVOLVEMENT | a plain verdict phrase, then claim text and label |

* **Trigger** (broadcaster): shown only when the verified explanation is a triggered one
  (explained or tentative) and its own supporting evidence names the cause. It states the
  type and minute ("Preceded by a goal at 50' (evidence ev-01).") or the earlier change's team
  and minute.
* **Fan verdict phrases**: "Explained by the evidence.", "Possible explanation, not
  confirmed.", "Ordinary variation." They add no claim.
* **Glossary**: what the metric measures, from [metrics.md](metrics.md). No definition says why
  a change mattered.
* **No player credit**: the canonical narrative names no player, and no view adds one, except
  the favourite player's factual involvement line (below).
* **No effects**: nothing upstream establishes an insight's effect on the match, so no view
  says a change "mattered", "cost" or was "decisive".

## Preferences

Preferences move views; they never change them.

| Preference | Effect |
| --- | --- |
| Favourite club | relevance boost when the club is this insight's team (or, smaller, its opponent); a club not in the match is neutral and never mentioned |
| Favourite player | relevance boost and an INVOLVEMENT line ("NAME appears in N of the events behind this insight.") only when the player acts in an event the insight cites; a squad player not in those events, or a player not in the match, is neutral |
| Favourite metric | relevance boost when it is this insight's metric; nothing else |

Relevance = verdict weight + Stage 3 level rank + preference boosts − compromised penalty. Each
term is recorded in `relevance_basis`. Broadcaster preference weights are kept small enough
that a preference never lifts a weaker verdict above a stronger one.

## Feeds

| Audience | Primary | Secondary |
| --- | --- | --- |
| Fan, broadcaster | explained, tentative and natural-variation insights, plus **every compromised insight**, ranked by relevance | insufficient-evidence and unavailable insights, as an explicit list |
| Analyst | every insight, on one timeline | none |

* When the primary feed is empty it says **"No verified insight available."**
* A compromised insight keeps its warning and ranks below an intact equivalent
  (`COMPROMISED_PENALTY`); it is never moved out of the primary feed.
* An insight that fails the claim audit, fails construction or fails the view audit is
  withheld and listed by investigation ID.
* Ties break on time, then candidate ID.

## View audit

`audit_view(view, ws, current)` checks a view against the **current** verified insight and the
observable match. It never calls the renderer; it reads sections as text and re-derives what
each may say.

| Check | What it verifies |
| --- | --- |
| source | fingerprint matches the wrapped insight, and the wrapped insight is the current one (staleness) |
| fact | one mandatory FACT, Stage 3's statement |
| verdict | an explanation is shown if and only if the verdict has one; framing does not claim another verdict |
| label | one label, the lead claim's strength, "verified on remaining evidence" exactly when compromised, within the integrity cap |
| claim | the lead claim's templated text at its strength; no other claim text |
| language | causal, certainty and intent wording calibrated to the claim's strength (OBSERVED outside the explanation) |
| caveat | the uncertainty verbatim; "Not ruled out" lists exactly the open alternatives |
| integrity | warning or exclusion notice mandatory, unaltered, before the explanation; never a false warning |
| evidence | pool IDs only; quarantined IDs only as excluded; evidence sections match their items |
| players | a player named only in the involvement line, only if involved, with the recounted number |
| clubs | another club only where its own evidence or the trigger names it; an unrelated favourite club never appears |
| numbers | every number comes from the insight, its evidence or the match |
| omitted | omitted list = the audience's optional content left out; nothing mandatory |
| audience | required sections present and complete (analyst: narrative, every evidence item, alternatives, quarantine, detail; broadcaster: context, verified trigger; fan: context with game state, definition) |
| relevance | score = sum of its basis; preference terms match a recount |

Known limits of the audit:

* it recognises only clubs in the match and the profile's favourite club, so an invented club
  name with no other signal would rely on the numbers and language checks;
* it reads English text, so a second language needs its own word lists.

## Real time

A view is a pure function of the current `FinalInsight`, the profile and the observable match.
No state is kept between calls. When an upstream insight changes, its fingerprint changes; a
view built from the earlier insight fails `audit_view` against the current one. There is no
persistence or versioning of insights or views (later "living insights" work).

## Security

* no network, process, dynamic-code or persistence code in the layer (scanned);
* no access to the generator or evaluator (scanned, and probed at runtime);
* profile inputs are enums and identifiers only; CLI errors do not echo rejected input;
* building feeds leaves investigations and traces unchanged.

## CLI

```bash
uv run python -m matcheyes investigate <match_dir>                       # unchanged output
uv run python -m matcheyes investigate <match_dir> --audience fan
uv run python -m matcheyes investigate <match_dir> --audience broadcaster --club <club_id>
uv run python -m matcheyes investigate <match_dir> --audience analyst --player <player_id> --metric shots
```

Without `--audience` the output is byte-identical to Stage 5. `--club`, `--player` and
`--metric` need `--audience`. `--json` always writes the unchanged investigation.

## Why no agent

Selection, ordering and templating are lookups, sorts and templates. They fail the agent
admission rule ([agent-design.md](agent-design.md)). A future model renderer may rephrase only
sections the deterministic policy has locked, must pass `audit_view`, and falls back to the
deterministic text.
