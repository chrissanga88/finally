# Start FinAlly in Docker (Windows PowerShell). Idempotent: safe to run repeatedly.
# Usage: .\scripts\start_windows.ps1 [-Build] [-NoOpen]
param(
    [switch]$Build,
    [switch]$NoOpen
)
$ErrorActionPreference = "Stop"

$Image = "finally"
$Container = "finally"
$Volume = "finally-data"
$Port = if ($env:PORT) { $env:PORT } else { "8000" }
$Url = "http://localhost:$Port"

$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $Root

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Write-Error "docker is not installed or not on PATH."
    exit 1
}

if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Warning ".env not found; created it from .env.example."
    Write-Warning "The app runs without keys (simulated prices). AI chat returns 503 unless you set ANTHROPIC_API_KEY or LLM_MOCK=true in .env."
}

docker image inspect $Image *> $null
$imageExists = ($LASTEXITCODE -eq 0)
if ($Build -or -not $imageExists) {
    Write-Host "Building image '$Image'..."
    docker build -t $Image .
    if ($LASTEXITCODE -ne 0) { Write-Error "docker build failed"; exit 1 }
}

docker container inspect $Container *> $null
if ($LASTEXITCODE -eq 0) {
    Write-Host "Removing existing container '$Container'..."
    docker rm -f $Container | Out-Null
}

docker run -d --name $Container -v "${Volume}:/app/db" -p "${Port}:8000" --env-file .env $Image | Out-Null
if ($LASTEXITCODE -ne 0) { Write-Error "docker run failed"; exit 1 }

Write-Host "Waiting for FinAlly to become healthy..."
$healthy = $false
for ($i = 0; $i -lt 30; $i++) {
    try {
        Invoke-WebRequest -Uri "$Url/api/health" -UseBasicParsing -TimeoutSec 2 | Out-Null
        $healthy = $true
        break
    } catch {
        Start-Sleep -Seconds 1
    }
}

if ($healthy) {
    Write-Host "FinAlly is running at $Url"
} else {
    Write-Host "FinAlly container started but $Url/api/health is not responding yet."
    Write-Host "Check logs with: docker logs $Container"
}

if (-not $NoOpen) {
    Start-Process $Url
}
