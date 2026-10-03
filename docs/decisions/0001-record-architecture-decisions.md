# ADR-0001: Record architecture decisions

- Status: Accepted
- Date: 2026-10-03
- Stage: 0

## Context

MatchEyes is built in reviewed stages and judged partly on technical implementation and
agentic design. Reviewers (and future us) need to know *why* choices were made, not just what
the code does.

## Decision

Record significant decisions as short ADRs in `docs/decisions/`, numbered sequentially, using
`0000-template.md`. A decision is "significant" if it affects architecture, data, security,
cost, or is hard to reverse.

## Alternatives considered

| Option | Why not |
| --- | --- |
| Decisions only in commit messages / chat | Not discoverable; lost context. |
| One large design document | Hard to see when and why each decision changed. |

## Consequences

Small overhead per decision. Superseded ADRs are kept and linked, preserving history.
