"""Synthetic match world: fictional league, hidden state, scenario catalogue and (later) the
seeded generator.

This package owns HIDDEN GROUND TRUTH. It may import `matcheyes.domain` and
`matcheyes.ingestion` so that its observable output conforms exactly to what the engine
accepts, but nothing in `matcheyes` may import this package, and it is excluded from the
deployable wheel (ADR-0005).
"""
