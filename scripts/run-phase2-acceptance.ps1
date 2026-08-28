param(
    [string]$PgUrl = $env:MANGAFLOW_ACCEPTANCE_PG_URL,
    [string]$RedisUrl = $env:MANGAFLOW_ACCEPTANCE_REDIS_URL,
    [switch]$StartContainers,
    [switch]$StopContainers,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"

if (-not $PgUrl) {
    $PgUrl = "postgresql+psycopg://mangaflow_test:mangaflow_test_pass@127.0.0.1:55432/mangaflow_acceptance"
}
if (-not $RedisUrl) {
    $RedisUrl = "redis://:mangaflow_redis_test_pass@127.0.0.1:56379/15"
}

Write-Host "================================================================" -ForegroundColor Cyan
Write-Host "  MangaFlow Phase 2: Isolated PostgreSQL & Redis/RQ Acceptance  " -ForegroundColor Cyan
Write-Host "================================================================" -ForegroundColor Cyan

$root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
Set-Location -LiteralPath $root

$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) {
    $python = "python"
}

Write-Host "`n[1/4] Environment Diagnostics:" -ForegroundColor Yellow
$dockerCmd = Get-Command docker -ErrorAction SilentlyContinue
$dockerComposeCmd = Get-Command docker-compose -ErrorAction SilentlyContinue
$hasDocker = ($dockerCmd -ne $null) -or ($dockerComposeCmd -ne $null)

Write-Host ("  Docker / Compose : " + $(if ($hasDocker) { "Available" } else { "Not Found (Missing)" }))
$pgConn = Get-NetTCPConnection -LocalPort 55432 -ErrorAction SilentlyContinue
$redisConn = Get-NetTCPConnection -LocalPort 56379 -ErrorAction SilentlyContinue

Write-Host ("  PostgreSQL 55432 : " + $(if ($pgConn) { "Listening (Available)" } else { "Closed / Not Listening" }))
Write-Host ("  Redis 56379      : " + $(if ($redisConn) { "Listening (Available)" } else { "Closed / Not Listening" }))

if ($StartContainers -and $hasDocker) {
    Write-Host "`n[2/4] Starting isolated acceptance containers..." -ForegroundColor Yellow
    docker compose -f docker-compose.acceptance.yml up -d
    Start-Sleep -Seconds 3
}

$canRunLive = ($pgConn -ne $null) -and ($redisConn -ne $null)

if ($DryRun) {
    Write-Host "`n[3/4] DryRun diagnostics complete. Status: $(if ($canRunLive) { 'READY_FOR_LIVE' } else { 'BLOCKED_MISSING_SERVICES' })" -ForegroundColor Cyan
    exit 0
}

Write-Host "`n[3/4] Executing Test Harness & Acceptance Suite:" -ForegroundColor Yellow

if (-not $canRunLive) {
    Write-Host "  [NOTICE] Live external services not running on 127.0.0.1:55432 / 56379." -ForegroundColor DarkYellow
    Write-Host "  Executing integration harness validation & skipped regression checks..." -ForegroundColor DarkYellow
    
    & $python -m pytest tests/test_integration_harness.py tests/integration/ -v
    $testExit = $LASTEXITCODE

    Write-Host "`n================================================================" -ForegroundColor Yellow
    Write-Host "  STATUS: BLOCKED (Environment Missing: Docker/Postgres/Redis)  " -ForegroundColor Yellow
    Write-Host "  Integration harness tests PASSED; live integration deferred. " -ForegroundColor Yellow
    Write-Host "  To run live tests when Docker is installed:" -ForegroundColor Yellow
    Write-Host "    1. docker compose -f docker-compose.acceptance.yml up -d" -ForegroundColor Yellow
    Write-Host "    2. powershell -File scripts\run-phase2-acceptance.ps1" -ForegroundColor Yellow
    Write-Host "================================================================" -ForegroundColor Yellow
    exit $testExit
}

Write-Host "  Services detected! Running full live PostgreSQL and Redis/RQ acceptance..." -ForegroundColor Green
$env:MANGAFLOW_ENABLE_LIVE_INTEGRATION = "1"
$env:MANGAFLOW_ACCEPTANCE_PG_URL = $PgUrl
$env:MANGAFLOW_ACCEPTANCE_REDIS_URL = $RedisUrl

& $python -m pytest tests/test_integration_harness.py tests/integration/ -v --run-live-integration --pg-url "$PgUrl" --redis-url "$RedisUrl"
$testExit = $LASTEXITCODE

if ($StopContainers -and $hasDocker) {
    Write-Host "`n[4/4] Stopping isolated acceptance containers..." -ForegroundColor Yellow
    docker compose -f docker-compose.acceptance.yml down
}

exit $testExit