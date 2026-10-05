# Evidence audit

Stage 5 adds three guarantees on top of the Stage 4 verifier ([agentic investigation](agentic-investigation.md)).
Results are in [stage5-evaluation.md](stage5-evaluation.md) and the decision record is
[ADR-0011](decisions/0011-evidence-audit-and-verification-hardening.md).

1. Every final claim can be traced, link by link, down to source event IDs, and a broken link
   is reported, not ignored (**lineage**).
2. Evidence that does not replay exactly is excluded before anything can cite it
   (**provenance**).
3. A second, separately written check re-derives what each final insight is allowed to say
   and reads the narrative as plain text (**claim auditor**).

None of the three adds state to the pipeline or a dependency. All three are deterministic and
live in the engine (`agents/` and `orchestration/`), so they can see only observable data.

## Lineage

`orchestration/lineage.py` builds an explicit graph for one investigation:

```
final claim -> verification verdict -> assertion checks -> evidence items
  -> tool request (replayed) -> Stage 3 candidate -> Stage 2 shift evidence
  -> metric series -> observed events
```

Every layer is linked by identifiers the pipeline already produces. `audit_lineage(ws, record)`
re-derives each link and records a `BrokenLink` when one does not resolve. The links are:

| Link | Checked |
| --- | --- |
| candidate → Stage 2 shift evidence | each `stage2_evidence_id` exists and has the candidate's metric and team |
| Stage 2 evidence → series | the shift's event IDs are among the events counted by the metric series |
| series / evidence → events | every event ID exists in the match |
| evidence → request | the request replays on this match and gives the recorded result; it belongs to the candidate |
| claim → evidence | every cited ID is in the final evidence pool |
| explanation claim → verdict | claim status equals the verified status; support IDs equal the verdict's support |
| support → check | each support ID has an accepted "true and material" supporting check |
| alternatives → verdicts | each alternative's displayed status equals its verified status |
| quarantine | no quarantined item is in the pool, and the insight discloses exactly what was quarantined |

A clean investigation has no broken links. An explanation must also reach at least one event.

## Provenance

The verifier now has a ninth gate, `provenance`. Before any check runs, each evidence item's
request is replayed through the same `ToolBox` the investigator used, and the result must be
identical. An item is **quarantined** when:

* its evidence ID is a duplicate;
* its request does not replay on this match (unknown candidate, period or arguments);
* the replayed result differs from the recorded one (facts, summary, event IDs, team, span).

A citation of a quarantined item is rejected ("evidence failed provenance"). Quarantined items
never reach the final insight.

### Evidence integrity

Each verification result and final insight carries `evidence_integrity`:

| State | When | Effect |
| --- | --- | --- |
| `intact` | nothing quarantined | none |
| `unaffected` | only quarantines that could not bear on the insight: an uncited duplicate whose original survived, or an uncited item whose request still replays from a tool in no considered explanation's `RELEVANT_TOOLS` | narrative: "some evidence failed provenance checks and was excluded; what follows rests on the remaining evidence only" |
| `compromised` | any other quarantine: a cited item, a request that no longer replays, an item from a relevant tool | eligible strength capped at hypothesised (`INTEGRITY_CAP`); narrative: "evidence integrity compromised: evidence relevant to this insight failed provenance checks and was excluded; the explanation may be incomplete"; label `[verified on remaining evidence, …]` |

Relevance is never judged from a quarantined item's facts, because those are exactly what
failed. The cap means compromised evidence can never produce a stronger claim. The auditor
checks that a compromised insight is disclosed, labelled and capped, and lineage checks that
the insight's state matches the verifier's.

Replay is exact because the tools are deterministic functions of observable data. It
*detects* every edit to an evidence item made after the tool produced it (Stage 4 trusted the
pool), and stops tampered evidence from being cited. It does not *recover* the true result.
The investigation concludes on the remaining evidence, so tampering can still change which
hedged explanation ranks first; this was observed once on held-out
([stage5-evaluation.md](stage5-evaluation.md#4-fault-results)). That insight is now flagged
`compromised`. The system detects evidence corruption, quarantines affected evidence, and
explicitly flags when the corruption may have affected the resulting explanation. It does not
restore the original explanation; evidence recovery is future hardening.

## Entailment-based materiality

Stage 4 accepted a supporting assertion as material when it was true and concerned a fact that
appears in a rule. That let a true but empty assertion count, for example `level_rank >= 0`
for a Strong change. An assertion is now material only if it is true **and** its comparator and
value *entail* the rule (`agents/hypotheses.entails`):

* `EQ v` entails a rule when `v` satisfies the rule;
* an ordered comparator entails a rule only in the same direction with an equal or tighter
  bound;
* `NE` entails only the identical `NE` rule;
* booleans never entail ordered rules.

## Hedged claim text

Below SUPPORTED, the explanation claim is typed `interpretive` (not `causal`) and uses hedged
text (`HYPOTHESIS_HEDGED`), for example "The change followed a goal and is consistent with
the ordinary response to it." Causal wording ("after going behind, the team…") is reserved for
SUPPORTED, where every plausible alternative was contradicted.

## Claim auditor

`orchestration/audit.py` is written independently of the verifier: it does not call it, and
it does not trust its accepted checks except to confirm them. It replays every cited item and
re-derives materiality, timing, contradictions and the claim ladder from the shared rule table.
It has twelve checks:

| # | Check | What fails it |
| --- | --- | --- |
| 1 | factual_correctness | the factual claim is not Stage 3's statement; a cited assertion is false |
| 2 | evidence_existence | a cited ID is missing or does not replay |
| 3 | relevance | support from an irrelevant tool or another candidate |
| 4 | materiality | support that satisfies no rule; a SUPPORTED claim missing a support group |
| 5 | temporal_validity | a trigger after the change; evidence outside the investigated period |
| 6 | team_correctness | the insight or its support concerns the other team |
| 7 | metric_correctness | the insight is not about the candidate's metric and time |
| 8 | comparator_correctness | an accepted assertion does not entail its rule |
| 9 | strength_eligibility | any claim above what the ladder allows |
| 10 | contradiction_handling | an explanation kept against a material contradiction in the pool |
| 11 | alternatives_handling | a plausible alternative missing, or marked contradicted without evidence |
| 12 | unsupported_content | narrative or claim text saying anything the case file does not |

The narrative check (12) works on the text alone, and also against the template:

* every line carries a label (FACT / ANALYSIS / AI INTERPRETATION);
* the FACT line is Stage 3's statement;
* the cited IDs are the lead claim's support;
* every number appears in the statement;
* the strength label matches the lead claim;
* the "weakened" and "not ruled out" lists match the verified statuses;
* no player names, player IDs or other-team names appear;
* calibration language matches the claim's strength (below).

With `template=True` the auditor also re-renders the narrative from the final insight and
requires an exact match. `template=False` measures what text-only checks catch.

### Claim categories

Each claim is classified as **fact**, **observation**, **association**, **hypothesis** or
**supported explanation**. The categories are never collapsed: wording allowed for one is
checked against the category the evidence earned.

### Calibration lexicon

| Lexicon | Examples | Allowed |
| --- | --- | --- |
| Causal | caused, because, led to, resulted in, due to, triggered | only at SUPPORTED |
| Certainty | clearly, definitely, proves, conclusively, always | never |
| Intent and condition | decided, instructed, manager, wanted to, tired, fatigued | never (not observable) |

The lexicon is deliberately short and literal. It catches the overclaims that a templated
narrative or a model rewording is likely to produce. It is not a general language-understanding
check, and a paraphrase outside the list would pass it. Template conformance is the stronger
guard.

## Where the evaluator stops and the engine starts

Lineage, provenance and the auditor are engine code and see only observable data. The red team
(`matcheyes_eval/redteam.py`) and the LLM evaluation (`matcheyes_eval/llm_eval.py`) are
evaluator code. They may know the scenario, the twin and the decoys. They use that knowledge
only to choose donors for tampering, to group results, and to score decoys after the
investigation has finished.
