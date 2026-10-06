"""LIFECYCLE: living insights over canonical match snapshots (Stage 7, ADR-0013).

    event log -> contiguous watermark -> canonical snapshot -> existing Stage 2-5 pipeline
    -> storyline reconciliation -> append-only revisions -> lifecycle feed -> personalization

The lifecycle owns temporal identity, revision history, withdrawal and reinstatement, and the
split between current and historical truth. It establishes no truth of its own: every revision
is a FinalInsight the existing pipeline verified and audited on that revision's own snapshot
(docs/living-insights.md).
"""
