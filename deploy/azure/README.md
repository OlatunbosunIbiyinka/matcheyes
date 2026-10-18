# Azure Container Apps — MatchEyes public demo

Deploys the **same credential-free image** as `Dockerfile` / local acceptance: one process
serves the static page **and** SSE (`/matches/{id}/stream`). No Static Web App split (keeps
same-origin CSP and server-owned replay).

Governing decisions: [ADR-0014](../../docs/decisions/0014-broadcast-cue-contract-and-live-presentation-surface.md),
[ADR-0015](../../docs/decisions/0015-foundry-model-as-untrusted-reasoner.md),
[ADR-0017](../../docs/decisions/0017-replay-lifecycle-identity-and-serving-bounds.md).

## What this stack is (and is not)

| Include | Exclude (cost / ADR) |
| --- | --- |
| ACA consumption, **maxReplicas = 1** | AKS, APIM, Front Door, SignalR |
| ACR Basic (no admin user; AcrPull via MI) | Cosmos, Event Hubs, Redis, Key Vault for the demo |
| Log Analytics (ACA logs) | Live Foundry on the public path |
| `minReplicas` 0 (idle) or 1 (judge warm) | Multi-replica / sticky sessions |

Foundry remains **recording/eval only** (Entra offline). The public app never gets
`MATCHEYES_LLM_*`.

## Architecture constraints encoded in Bicep

| Constraint | Setting | Why |
| --- | --- | --- |
| Single in-memory replay | `maxReplicas: 1` | New replica = new `run`; two replicas = split-brain |
| Cost when idle | `minReplicas: 0` (default) | Scale to zero; cold start ≈ new replay from kick-off |
| SSE keep-alive | ACA default ingress idle (minutes) | App heartbeats every **15 s** — default idle ≫ heartbeat |
| Transport | `http` (HTTP/1.1) | SSE |
| Readiness | Startup + Readiness on `/health` | ADR-0017 readiness; tolerate cold start |
| No secrets in app | empty env | Credential-free image |

**Cold start / restart (expected):** process restart ⇒ new `run` identity; browser shows
reconnecting / replay restarted (already in `api/static/app.js`). Not mid-match resume across
scale-to-zero.

**Timeouts:** request-head 10 s and stream caps (64 conns / 32 streams) are in-app
(ADR-0017). Platform risk is only if ingress idle &lt; heartbeat margin — we set 30 minutes.

## Prerequisites

- Azure CLI logged in (`az login`) with rights to create RG resources  
- Docker Desktop running  
- Deploy-artifact acceptance complete (or equivalent) before first production URL  

## Lessons applied (best practice)

- Prefer **Sweden Central** (or another eligible region): some subscriptions reject new resources in West Europe (`locationineligible`).  
- **User-assigned identity + AcrPull before app create** — system-assigned AcrPull races cause `Operation expired` / Failed apps with a 404 on the main FQDN.  
- First live verify with **`minReplicas: 1`**, then optionally scale to 0 for cost.  

## Deploy

From the repo root (`matcheyes/`):

```powershell
# Cost-saving default (scale to zero)
.\deploy\azure\deploy.ps1 -ResourceGroup matcheyes-demo-rg -Location swedencentral

# Keep one warm replica during a live judging call
.\deploy\azure\deploy.ps1 -ResourceGroup matcheyes-demo-rg -WarmForJudging -SkipBuild
```

Default region is **Sweden Central** (aligns with the Stage 9 Foundry region; some subscriptions reject new customers in West Europe).

The script:

1. Creates the resource group if needed  
2. Deploys `infra.bicep` (ACR + Log Analytics + ACA environment)  
3. Builds `Dockerfile` → pushes `matcheyes-demo:<tag>` to ACR  
4. Deploys `app.bicep` and waits for `GET /health` → 200  

## After deploy

- Public URL is printed (`https://<fqdn>/`).  
- First open after idle may take **15–60 s** (cold start).  
- Judges: open the URL, wait for health, watch SSE reconnect if the tab slept.  
- Optional warm: `-WarmForJudging` then return to `minReplicas: 0` after the session.

## Files

| File | Role |
| --- | --- |
| `infra.bicep` | ACR, logs, managed environment |
| `app.bicep` | Container App + AcrPull + scale/ingress/probes |
| `deploy.ps1` | Operator runbook automation |
| `README.md` | This document |

## Hackathon note

Submission needs a **reachable** demo until judging ends. Prefer `minReplicas: 1` for the
demo window in the video / live review, then scale back to 0 to save cost.
