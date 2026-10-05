# Stage 6 evaluation: personalization

Stage 6 asks one question first: **does any audience view, under any preference, say
something the verified insight does not?** It then reports coverage, length and depth per
audience, what preferences change, feed behaviour, and a presentation red team.

Design: [personalization.md](personalization.md). Decision:
[ADR-0012](decisions/0012-personalization-presentation-layer.md).

> **No user study was run.** These results show that views are truthful to the verified
> insights and complete against the audience checklists below. They say nothing about whether
> real fans, broadcasters or analysts find the views useful or prefer them.

No numeric targets were set in advance, apart from the invariants that must be zero. The
results below are as measured.

## Protocol

```bash
uv run python -m matcheyes_eval stage6 --split development --seeds 2
uv run python -m matcheyes_eval stage6 --split held-out --seeds 2
```

* **Datasets.** The Stage 4/5 synthetic catalogue: every scenario, 2 seeds, planted, twin and
  control variants (34 matches per split). Held-out seeds were not used during development of
  Stage 6.
* **Insights.** Every Stage 3 candidate at weak or above, investigated by the reference
  reasoner and verified by the Stage 4/5 pipeline.
* **Compromised insights**, produced by the pipeline (not written by hand), using Stage 5
  evidence tampering (`altered_metric_value`) run through the real verifier:
  * *tampered-all*: every evidence item altered after the tool ran;
  * *tampered-one*: only `ev-03` altered, so some explanations survive on the remaining
    evidence and keep a "verified on remaining evidence" label.
* **Profiles.** Each insight × each audience (fan, broadcaster, analyst) × each preference
  shape: no preference; favourite club = the insight's team, its opponent, a club not in the
  match; favourite metric = the insight's metric; favourite player who acts in a cited event,
  a squad player who does not, a player not in the match. A cited player existed for every
  insight in both splits.
* **Hidden truth** is not used. Personalization's truth is the verified insight; every measure
  compares a view with the insight it wraps.

| | Development | Held-out |
| --- | --- | --- |
| Matches | 34 | 34 |
| Insights (clean + tampered-all + tampered-one) | 1,449 (483 + 483 + 483) | 1,686 (562 + 562 + 562) |
| Verdicts: insufficient / natural variation / tentative | 1,222 / 82 / 145 | 1,413 / 127 / 146 |
| Integrity: intact / compromised | 483 / 966 | 562 / 1,124 |
| Compromised insights that keep a label | 91 | 106 |
| Views (insights × 3 audiences × 8 profiles) | 34,776 | 40,464 |

No insight was *explained* (the strongest verdict) in either split; this is a property of the
Stage 4/5 reference pipeline on this catalogue, not of Stage 6. Explained insights are covered
by the unit fixtures.

## 1. Truth invariants (expected 0)

| Measure | Development | Held-out |
| --- | --- | --- |
| Views that fail construction (contract invariants) | 0 of 34,776 | 0 of 40,464 |
| Fingerprint mismatches | 0 | 0 |
| Views flagged by the independent view audit | 0 | 0 |
| Insights changed by personalizing | 0 | 0 |
| Insights whose mandatory core differs across the three audiences | 0 of 1,449 | 0 of 1,686 |

The mandatory core is the Stage 3 fact, the integrity disclosure, the verified label and claim
text with its evidence IDs, the uncertainty, or the "no verified explanation" statement.

## 2. Audience results (no preference)

| Audience | Checklist coverage | Length vs canonical narrative | Sections per view | Evidence IDs shown per view |
| --- | --- | --- | --- | --- |
| Fan | 100.0% / 100.0% | 1.28× / 1.28× | 4.8 / 4.8 | 0.2 / 0.3 |
| Broadcaster | 100.0% / 100.0% | 0.95× / 0.95× | 3.9 / 3.9 | 0.2 / 0.3 |
| Analyst | 100.0% / 100.0% | 6.42× / 6.43× | 10.2 / 10.2 | 4.4 / 4.4 |

Development / held-out. Checklist items:

* every audience: the Stage 3 fact; an explanation or an explicit "no verified explanation";
  the integrity disclosure exactly when integrity is flagged;
* analyst: canonical narrative; every verified evidence item; alternatives when present;
  quarantined evidence when present; Stage 3 level and basis; every downgrade;
* broadcaster: minute and team; the trigger exactly when the verified explanation cites one;
* fan: the plain verdict phrase for explained insights; the metric definition; the game state.

Reading the numbers:

* The **fan** view is longer than the canonical narrative (1.28×): it adds the game state and
  a metric definition, and keeps every mandatory caveat. It is not a compressed summary.
* The **broadcaster** view is about the narrative's length (0.95×), most of it the mandatory
  core.
* The **analyst** view is about 6.4× the narrative: every evidence item with its facts, every
  claim's evidence and the verification detail.
* Fan and broadcaster views show few evidence IDs because most insights have no verified
  explanation to cite.

## 3. Preference safety

For every preference shape and audience, both splits:

| Measure | Development | Held-out |
| --- | --- | --- |
| Views whose sections (other than the involvement line) differ from the no-preference view | 0 | 0 |
| Views whose feed placement differs from the no-preference view | 0 | 0 |
| Views flagged by the view audit | 0 | 0 |
| Involvement lines for a player not in the cited events | 0 | 0 |

What preferences did change (each shape, each audience, both splits):

| Shape | Relevance raised | Involvement line |
| --- | --- | --- |
| Club = insight's team | every insight | none |
| Club = opponent | every insight (smaller boost) | none |
| Club not in the match | none | none |
| Metric = insight's metric | every insight | none |
| Player in the cited events | every insight | every insight |
| Squad player not in the cited events | none | none |
| Player not in the match | none | none |

## 4. Feeds

Per match feed, summed over 34 matches; development / held-out.

| Feed | Primary | Secondary | Withheld | Empty primary ("No verified insight available.") |
| --- | --- | --- | --- | --- |
| Clean, fan | 136 / 167 | 347 / 395 | 0 / 0 | 2 / 0 matches |
| Clean, broadcaster | 136 / 167 | 347 / 395 | 0 / 0 | 2 / 0 matches |
| Clean, analyst | 483 / 562 | 0 / 0 | 0 / 0 | 0 / 0 |

* About 70% of clean insights move to the secondary list for fans and broadcasters (71.8%
  development, 70.3% held-out), consistent with the Stage 4 insufficient-evidence rate.
* **Compromised insights**: in every tampered feed (all, one; every audience; both splits),
  all compromised insights were in the primary feed with the mandatory warning (966 of 966
  per audience development, 1,124 of 1,124 held-out). None was moved to the secondary list or
  hidden.
* No insight was withheld: every clean and tampered insight passed the claim audit, built a
  valid view and passed the view audit.

## 5. Presentation red team

Known faults injected into genuine views with `model_copy`, which skips the construction
validators, so only `audit_view` stands in the way. Each fault is injected into every view it
applies to: one view per audience per insight.

| Result | Development | Held-out |
| --- | --- | --- |
| Faults injected | 76,065 | 88,565 |
| Caught by `audit_view` | 76,065 | 88,565 |
| Caught by the expected check | 76,065 | 88,565 |
| Fault types injected | 27 of 27 | 27 of 27 |

| Fault | Expected check | Development | Held-out |
| --- | --- | --- | --- |
| dropped warning | integrity | 2,898 | 3,372 |
| softened warning | integrity | 2,898 | 3,372 |
| warning made optional | integrity | 2,898 | 3,372 |
| raised strength label | label | 681 | 819 |
| compromised claims full verification | label | 273 | 318 |
| added causal wording | language | 4,347 | 5,058 |
| certainty wording | language | 4,347 | 5,058 |
| intent wording | language | 4,347 | 5,058 |
| invented player | players | 4,347 | 5,058 |
| uninvolved favourite credited | players | 4,347 | 5,058 |
| player agency ("X drove it") | players, language | 4,347 | 5,058 |
| invented club (opponent named) | clubs | 4,347 | 5,058 |
| unrelated favourite club mentioned | clubs | 4,347 | 5,058 |
| dropped "Not ruled out" | caveat | 420 | 414 |
| quarantined evidence shown as valid | evidence | 2,898 | 3,372 |
| invented number | numbers | 4,347 | 5,058 |
| changed claim text | claim | 681 | 819 |
| changed evidence ID | claim, evidence | 681 | 819 |
| changed verdict | source | 4,347 | 5,058 |
| verdict framing ("Explained…" on a non-explained insight) | verdict | 681 | 819 |
| changed integrity state | source | 4,347 | 5,058 |
| preference alters truth (strengthened source, re-fingerprinted) | source | 681 | 819 |
| stale fingerprint | source | 4,347 | 5,058 |
| altered canonical narrative | evidence, audience | 1,449 | 1,686 |
| hidden alternatives | audience | 1,449 | 1,686 |
| hidden evidence item | audience | 966 | 1,124 |
| inflated relevance | relevance | 4,347 | 5,058 |

Each fault also applies to at least one real fixture in
`tests/personalization/test_view_audit.py`, where it must be caught.

## 6. Security

* Personalization has no network, process, dynamic-code or persistence code (static scans).
* Personalizing a match loaded from disk, in a clean interpreter, loads neither the generator
  nor the evaluator (runtime probe).
* Profiles accept only enums and identifiers; injection strings are rejected and not echoed
  by the CLI.
* Building feeds leaves investigations and traces unchanged.
* Without `--audience`, the CLI output is byte-identical to Stage 5 (SHA-256 recorded before
  the change, on the fixture and on two matches with insights).

## Limitations

* **No user study.** Usefulness, preference and comprehension are unmeasured.
* **Checklists are ours.** Coverage is measured against checklists derived from the Stage 6
  discovery, not against an external standard; 100% means the views contain what we decided
  they should.
* **No explained insights** occurred in the evaluation datasets; that verdict is covered by unit
  fixtures only.
* **Compromised insights come from one tampering type** (`altered_metric_value`) in two
  modes. Other Stage 5 tampering types produce the same integrity states through the same
  verifier, but were not run here.
* **The red team is ours.** The faults are the ones we thought of; the audit is shown to catch
  them, not every possible presentation error. In particular, the club check recognises only
  the two clubs in the match and the profile's favourite club.
* **English only**, and the language checks read English word lists.
* **Sparse feeds.** About 70% of insights have no verified explanation, so fan and broadcaster
  primary feeds are short, and 2 development matches had none.
* **No live LLM** renders anything; the views are deterministic templates.
