# Runs the same quality gate as CI. Usage: ./scripts/check.ps1
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

function Invoke-Step($Name, [scriptblock]$Block) {
    Write-Host "==> $Name" -ForegroundColor Cyan
    & $Block
    if ($LASTEXITCODE -ne 0) { throw "$Name failed" }
}

Invoke-Step "ruff format --check" { python -m uv run ruff format --check . }
Invoke-Step "ruff check"          { python -m uv run ruff check . }
Invoke-Step "mypy"                { python -m uv run mypy }
Invoke-Step "pytest"              { python -m uv run pytest --cov=matcheyes --cov-report=term-missing }
Write-Host "All checks passed." -ForegroundColor Green
