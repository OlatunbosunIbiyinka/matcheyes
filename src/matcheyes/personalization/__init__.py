"""PRESENTATION: audience views derived from verified insights (Stage 6, ADR-0012).

    FinalInsight -> audit_insight -> profile -> deterministic policy -> PersonalizedInsight
    -> audit_view -> audience feed

A view holds the verified `FinalInsight` it presents and copies none of its truth fields.
Personalization changes emphasis, order, depth and wording; it never changes verdict,
strength, claims, evidence, integrity or uncertainty (docs/personalization.md).
"""
