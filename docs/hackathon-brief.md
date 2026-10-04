# Hackathon brief (source of truth)

Summarised from the official rules: [microsoft/insidethegamehackathon — OFFICIAL RULES.md](https://github.com/microsoft/insidethegamehackathon)
(published 2026-09-28, reviewed 2026-10-03). If this page and the rules disagree, the rules win.

## Dates (Pacific Time)

| Milestone | Date |
| --- | --- |
| Registration | 2026-09-29 09:00 – 2026-10-20 12:00 |
| Submission / hackathon period | 2026-10-06 09:00 – **2026-10-27 23:59** |
| Judging | 2026-10-27 – 2026-11-10 |
| Winners announced | by 2026-11-20 |

## The challenge: one pipeline, five stages

1. **Ingest** synthetic match events as they happen (passes, shots, tackles, possession changes,
   pressure events, match metadata).
2. **Interpret** them into meaningful statistics, patterns and context.
3. **Explain** why a moment matters, not only that it happened.
4. **Render** the insight so it can sit on screen alongside the match: timed and machine-readable
   enough to drive synchronised graphical overlays.
5. **Personalize** so an analyst and a casual fan get genuinely different experiences: modes,
   favourite club(s), favourite player(s), player-focus mode, language preference, a single
   metric the viewer cares about.

Features the rules suggest considering: player identification tags with speed/distance
thresholds; pass quality (distance, accuracy, difficulty rating); ball and shot speed;
auto-eventing from video; narratives, milestones and recaps; explainability (control vs chaos,
tactical pressure changes, game rhythm); **creating or extending synthetic football-realistic
datasets**; multi-language storytelling.

## Constraints that shape MatchEyes

| Rule | Consequence |
| --- | --- |
| Projects must use synthetic, football-realistic data; **no data is provided** by the organisers | We must source or generate our own data (Stage 1 scope changes). |
| Judging rewards "creativity and optimized synthetic data creation" | Data generation is a scored capability, not a chore. |
| No third-party trademarks or copyrighted material in the video without permission | Fictional league, clubs and players. No real club names, crests or player names. |
| Project created after 2026-09-29 | Satisfied (repo created 2026-10-03). |
| Working project must be accessible to judges (site, demo or test build), free, until judging ends | A hosted, publicly reachable deployment is required. |
| Public GitHub repo; video < 2 minutes on YouTube/Vimeo/etc.; English | Stage 11 checklist. |
| Open source allowed if licences are respected and we build on it | Track dependency licences. |
| Projects are visible to all entrants | Competitors will see our repo; differentiation must be in execution, not secrecy. |

## Judging

Stage one is a pass/fail viability gate (fits the theme, uses the required technologies). Stage
two uses five equally weighted criteria:

| Criterion | What judges look for (paraphrased) |
| --- | --- |
| Technological implementation | Creative, optimised synthetic data; quality engineering; effective use of hero technologies (Microsoft Foundry, Agent Framework, Azure MCP, GitHub Copilot agent mode, Fabric, Azure apps/AI services/databases); maintainable code. |
| Agentic design & innovation | Creative agentic patterns; orchestration, MCP or multi-agent collaboration; novel or meaningfully improved AI. |
| Real-world impact | Significance; production deployability; impact on users and businesses. |
| UX & presentation | Intuitive UX; demo clearly communicates value; balance of frontend and backend. |
| Category adherence | Fit to the chosen category. |

Ties are broken by criterion order, so **technological implementation is the first tie-breaker**.

## Prizes and categories

A project can win one Grand Prize plus one Category Prize.

- Grand Prize (1st, 2nd): most complete, compelling, innovative use of the Microsoft AI stack.
- Best Use of Microsoft Foundry.
- Best Enterprise Solution: reliability, transparency, controls, path to adoption.
- Best Multi-Agent System: distinct roles, shared state, effective handoffs, **failure
  recovery**, an outcome a single agent could not achieve as well.
- Best Azure Cloud Native Integration: AKS / Container Apps / Functions / Logic Apps,
  event-driven architecture, operational excellence.

Category target: to be chosen after Stage 4 (see `competitive-analysis.md`).
