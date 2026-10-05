# ADR-0012: Personalization is a derived presentation layer over verified insights

- Status: Accepted
- Date: 2026-10-05
- Stage: 6

## Context

Stages 2–5 produce a verified, audited `FinalInsight` per Stage 3 candidate. Its strength,
verdict, claims, evidence, integrity state and uncertainty have been decided by the
deterministic verifier and checked by the independent auditor and lineage graph
([ADR-0011](0011-evidence-audit-and-verification-hardening.md)).

The hackathon brief asks for genuinely different experiences for a fan, a broadcaster and an
analyst, with favourite club, favourite player, a single metric the viewer cares about and a
language preference. The risk is that presentation becomes a second place where truth is
decided: a favourite club makes a weak insight sound strong, a short fan view drops a caveat
or an integrity warning, or a model asked to "make it engaging" adds causal language.

Two constraints already exist in the code:

* the canonical narrative may not name any player, or any club other than the candidate's
  team (`orchestration/audit.py`);
* in Stage 4 most insights end at insufficient evidence (71.5% held-out), so honest audience
  feeds will often be sparse.

## Decision

1. **A new layer, `matcheyes.personalization`, between orchestration and api.** It may import
   `domain`, `analytics`, `agents` and `orchestration`. `api` may import it; `orchestration`
   and everything below may not. It is covered by the import-boundary, no-network,
   no-process, no-dynamic-code and no-scenario-name scans.
2. **The verified insight is held, not copied.** `PersonalizedInsight.source` is the frozen
   `FinalInsight` itself. No truth field is copied out, and none is added. A
   `source_fingerprint` (SHA-256 of the source's canonical JSON) is checked at construction
   and by the view auditor against the current upstream insight, so a mutated or stale view is
   detected.
3. **Truth invariants are enforced at construction.** A `PersonalizedInsight` cannot be built
   unless it carries:
   * the Stage 3 fact verbatim;
   * the integrity warning when compromised, and the exclusion notice when evidence was
     quarantined;
   * for an explanation, exactly the verified label and claim text, with the claim's
     supporting evidence IDs, and the claim's uncertainty verbatim;
   * for insufficient evidence or an unavailable explanation, a "no verified explanation"
     section and no interpretation.
   Evidence IDs must come from the verified pool; quarantined IDs only appear in a section
   that says they were excluded.
4. **Personalization is deterministic.** A fixed policy table per audience selects sections,
   depth and order; templates render them. There is no agent and no model call. This follows
   the agent admission rule ([agent-design.md](../agent-design.md)): every step is a lookup,
   a template or a sort.
5. **Personalization runs only after audit.** The feed builder runs `audit_insight` on each
   record first. An insight that fails is withheld, and listed as withheld, never shown.
6. **An independent view auditor (`audit_view`) checks the result as text.** It does not call
   the renderer. It re-derives what the view may say from the source insight, the Stage 3
   candidate and the observable match.
7. **Preferences affect relevance only.** Favourite club, favourite player and favourite
   metric change the relevance score and order, with the reason recorded in
   `relevance_basis`. They never reach strength, verdict, integrity, evidence or claim text.
   * Club relevance needs the club to be in the match.
   * Player relevance needs the player to act in an event the insight cites. Only then may
     the view name the player, in a factual count ("appears in N of the events behind this
     insight"), never with a verb of agency.
   * The favourite metric is relevance-only.
   * Preferences are identifiers and enums, never free text.
8. **Feed filtering cannot hide integrity problems.**
   * For fans and broadcasters, insufficient-evidence and unavailable insights leave the
     primary feed for an explicit secondary list, with a "No verified insight available."
     notice when the primary feed is empty.
   * A compromised insight is never moved out of the primary feed. It keeps its warning and
     ranks lower than an intact equivalent.
   * Analysts see every insight.
9. **The LLM is excluded from relevance and truth.** It does not decide what is relevant, what
   is valid, what is supported, whether causation exists or whether integrity is intact. A
   future LLM renderer may only rephrase sections a deterministic policy has already locked,
   and its output must pass `audit_view`, with fallback to the deterministic text.

## Alternatives considered

| Option | Why not |
| --- | --- |
| Rewrite `FinalInsight.narrative` per audience | Overwrites the audited reference text; the canonical auditor would reject player names the brief asks for |
| A presentation model with copied truth fields | Every copy is a place truth can drift; holding the source is simpler and checkable |
| A personalization agent | Fails the admission rule: selection, ordering and templating are deterministic |
| Deterministic policy, LLM rendering (now) | The live LLM path has never been evaluated; deferred until it can be measured, behind this policy and audit |
| LLM decides relevance | Relevance decides what a reader sees and can suppress caveats; injection surface; not reproducible |
| Hide insufficient and compromised insights from casual audiences | Hiding a compromised insight hides an integrity problem; insufficient insights stay reachable instead |

## Consequences

* The same verified truth reaches every audience and profile, and this is testable: the
  claim set extracted from fan, broadcaster and analyst views is identical.
* Personalization has no truth state of its own. It is a pure function of the current
  insights, the profile and the observable match, so it reflects upstream changes on the
  next call. Persistence and versioning of insights remain later "living insights" work.
* Fan and broadcaster feeds will often be empty or short, because most candidates are not
  explained. This is the intended, honest behaviour.
* "Why it mattered" is limited to a metric definition and the verified explanation. Nothing
  upstream establishes an insight's effect on the match, so no view claims one.
* Only English templates exist. A second language needs a reviewed template catalogue and its
  own calibration word list.
