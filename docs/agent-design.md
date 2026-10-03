# Agent design

Status: **not started (Stage 4)**. This page defines the bar every agent must clear.

## Admission rule

An agent exists only if a deterministic function cannot do the job reliably and the agent
adds measurable value (interpretation, investigation, synthesis, specialization, or
contextual reasoning). Agents are not added to increase the count.

## Required specification for every agent

| Field | Description |
| --- | --- |
| Responsibility | One sentence. |
| Reason to exist | Why a function is insufficient. |
| Inputs | Typed schema (evidence objects, not raw event dumps). |
| Outputs | Typed, structured schema. |
| Tools | Deterministic analytics functions it may call. |
| Boundaries | What it must never do (e.g. compute statistics, invent events). |
| Failure behaviour | Timeout, invalid output, low confidence: what happens. |
| Evaluation | How its value is measured (see `evaluation.md`). |

## Candidate roles (to be validated against real data)

- Match Analyst: candidate insights from detected moments.
- Tactical Analyst: pressure, progression, transitions, possession patterns.
- Performance Analyst: player/team contribution.
- Evidence/Verification: checks claims against analytics + source events; can reject.
- Narrative: writes from verified findings only; labels fact vs interpretation.
- Personalization: audience adaptation of presentation, never facts.

Orchestration pattern (sequential / concurrent / handoff / manager) is chosen in Stage 4 and
recorded as an ADR.
