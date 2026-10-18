<#
.SYNOPSIS
  Deploy the MatchEyes credential-free demo to Azure Container Apps.

.DESCRIPTION
  Tailored to the existing architecture (single-process static + SSE, ADR-0015/0017):
    - maxReplicas forced to 1 in Bicep
    - minReplicas 0 (cost) or 1 (warm judge session)
    - 30-minute ingress idle timeout for SSE heartbeats (15s in-app)
    - no SWA split; no Foundry on the public path
    - builds the same Dockerfile used for local acceptance

  Requires: Azure CLI (logged in), Docker, Contributor on the target subscription/RG.

  Does NOT: commit secrets, enable ACR admin user, or deploy Config B.

.PARAMETER ResourceGroup
  Resource group name (created if missing).

.PARAMETER Location
  Azure region (default westeurope - change if needed).

.PARAMETER NamePrefix
  Short prefix for resource names.

.PARAMETER ImageTag
  Tag pushed to ACR (default: demo).

.PARAMETER MinReplicas
  0 = scale to zero; 1 = keep warm.

.PARAMETER SkipBuild
  Reuse an image already in ACR with ImageTag.

.PARAMETER WarmForJudging
  Shorthand for -MinReplicas 1.
#>
[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)]
  [string] $ResourceGroup,

  [string] $Location = "swedencentral",

  [string] $NamePrefix = "matcheyes",

  [string] $ImageTag = "demo",

  [ValidateSet(0, 1)]
  [int] $MinReplicas = 0,

  [switch] $SkipBuild,

  [switch] $WarmForJudging
)

$ErrorActionPreference = "Stop"
if ($WarmForJudging) { $MinReplicas = 1 }

$here = $PSScriptRoot
$repoRoot = (Resolve-Path (Join-Path $here "..\..")).Path
$dockerfile = Join-Path $repoRoot "Dockerfile"

function Require-Command([string] $Name) {
  if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
    throw "Required command not found: $Name"
  }
}

Require-Command az
Require-Command docker

Write-Host "== MatchEyes ACA deploy ==" -ForegroundColor Cyan
Write-Host "RG=$ResourceGroup Location=$Location Prefix=$NamePrefix Tag=$ImageTag MinReplicas=$MinReplicas"

$account = az account show --output json 2>$null | ConvertFrom-Json
if (-not $account) { throw "Not logged in. Run: az login" }
Write-Host "Subscription: $($account.name) ($($account.id))"

az group create --name $ResourceGroup --location $Location --output none

Write-Host "`n[1/4] Deploy infra (ACR + Log Analytics + Container Apps Environment)..." -ForegroundColor Cyan
$infraOutFile = Join-Path $env:TEMP "matcheyes-aca-infra-out.json"
az deployment group create `
  --resource-group $ResourceGroup `
  --template-file (Join-Path $here "infra.bicep") `
  --parameters namePrefix=$NamePrefix location=$Location `
  --query properties.outputs `
  --output json | Tee-Object -FilePath $infraOutFile
if ($LASTEXITCODE -ne 0) {
  throw "Infra deployment failed (exit $LASTEXITCODE). If the error mentions locationineligible, retry with another -Location (e.g. swedencentral, uksouth, eastus)."
}
if (-not (Test-Path -LiteralPath $infraOutFile)) {
  throw "Infra deployment produced no outputs file: $infraOutFile"
}

$infra = Get-Content $infraOutFile -Raw | ConvertFrom-Json
$acrName = $infra.acrName.value
$acrLogin = $infra.acrLoginServer.value
$envId = $infra.managedEnvironmentId.value
Write-Host "ACR=$acrLogin"
Write-Host "Env=$envId"

$localImage = "matcheyes-demo:$ImageTag"
$remoteImage = "${acrLogin}/matcheyes-demo:$ImageTag"

if (-not $SkipBuild) {
  Write-Host "`n[2/4] Build image from repo Dockerfile (same artifact family as acceptance)..." -ForegroundColor Cyan
  Push-Location $repoRoot
  try {
    docker build -t $localImage -f $dockerfile .
    if ($LASTEXITCODE -ne 0) { throw "docker build failed" }
  } finally {
    Pop-Location
  }

  Write-Host "`n[3/4] Push to ACR (AAD login; admin user stays disabled)..." -ForegroundColor Cyan
  az acr login --name $acrName | Out-Null
  docker tag $localImage $remoteImage
  docker push $remoteImage
  if ($LASTEXITCODE -ne 0) { throw "docker push failed" }
} else {
  Write-Host "`n[2-3/4] SkipBuild set - expecting $remoteImage already in ACR." -ForegroundColor Yellow
}

Write-Host "`n[4/4] Deploy Container App (maxReplicas=1, HTTP/1.1 ingress, /health probes)..." -ForegroundColor Cyan
$appOutFile = Join-Path $env:TEMP "matcheyes-aca-app-out.json"
az deployment group create `
  --resource-group $ResourceGroup `
  --template-file (Join-Path $here "app.bicep") `
  --parameters `
    namePrefix=$NamePrefix `
    location=$Location `
    acrName=$acrName `
    managedEnvironmentId=$envId `
    imageTag=$ImageTag `
    minReplicas=$MinReplicas `
    httpConcurrentRequests=1 `
  --query properties.outputs `
  --output json | Tee-Object -FilePath $appOutFile
if ($LASTEXITCODE -ne 0) {
  throw "App deployment failed (exit $LASTEXITCODE). Prefer deleting a Failed container app and redeploying; app.bicep uses UAMI+AcrPull-first to avoid pull races."
}
if (-not (Test-Path -LiteralPath $appOutFile)) {
  throw "App deployment produced no outputs file: $appOutFile"
}

$app = Get-Content $appOutFile -Raw | ConvertFrom-Json
$url = $app.publicUrl.value
Write-Host "`nPublic URL: $url" -ForegroundColor Green
Write-Host "Image:      $($app.image.value)"
Write-Host "Replicas:   min=$($app.minReplicas.value) max=$($app.maxReplicas.value)"
Write-Host "SSE note:   app heartbeats every 15s; ACA default idle timeout is minutes (safe margin)"
Write-Host "Identity:   $($app.identityName.value) (user-assigned, AcrPull before app create)"

Write-Host "`nWaiting for /health (cold start may take ~15-60s)..." -ForegroundColor Cyan
$deadline = [DateTime]::UtcNow.AddMinutes(5)
$ok = $false
while ([DateTime]::UtcNow -lt $deadline) {
  try {
    $r = Invoke-WebRequest -Uri "$url/health" -UseBasicParsing -TimeoutSec 20
    if ($r.StatusCode -eq 200) {
      Write-Host "HEALTH 200 $($r.Content)" -ForegroundColor Green
      $ok = $true
      break
    }
    Write-Host "HEALTH $($r.StatusCode) - retrying..."
  } catch {
    Write-Host "HEALTH not ready: $($_.Exception.Message)"
  }
  Start-Sleep -Seconds 5
}

if (-not $ok) {
  Write-Warning "Timed out waiting for /health. Check: az containerapp logs show -g $ResourceGroup -n $($app.containerAppName.value) --follow"
  exit 2
}

Write-Host "`nDone. Open: $url" -ForegroundColor Green
Write-Host "Judge tip: first hit after idle may cold-start; UI reconnects SSE automatically."
Write-Host "Warm session:  .\deploy\azure\deploy.ps1 -ResourceGroup $ResourceGroup -WarmForJudging -SkipBuild"
