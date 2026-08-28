param(
    [switch]$RunLive,
    [switch]$StartContainers,
    [switch]$StopContainers,
    [switch]$DryRun,
    [string]$PgUrl = $env:MANGAFLOW_ACCEPTANCE_PG_URL,
    [string]$RedisUrl = $env:MANGAFLOW_ACCEPTANCE_REDIS_URL
)

$ErrorActionPreference = "Stop"

if (-not $PgUrl) {
    $PgUrl = "postgresql+psycopg://mangaflow_test:mangaflow_acceptance_pass_55432@127.0.0.1:55432/mangaflow_acceptance"
}
if (-not $RedisUrl) {
    $RedisUrl = "redis://:mangaflow_acceptance_redis_pass_56379@127.0.0.1:56379/15"
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

# 1. DryRun verification branch (validates URLs using Python validator, ZERO side-effects)
if ($DryRun) {
    Write-Host "`n[DryRun] Validating configuration and URL formats..." -ForegroundColor Yellow
    $valCode = @"
import sys
sys.path.insert(0, 'apps/api')
from tests.integration.conftest import validate_safe_acceptance_pg_url, validate_safe_acceptance_redis_url, mask_url
pg = validate_safe_acceptance_pg_url(r'$PgUrl')
red = validate_safe_acceptance_redis_url(r'$RedisUrl')
print(f'  Target PG URL    : {mask_url(pg)}')
print(f'  Target Redis URL : {mask_url(red)}')
"@
    & $python -c "$valCode"
    if ($LASTEXITCODE -ne 0) {
        Write-Error "DryRun configuration validation failed."
        exit $LASTEXITCODE
    }

    $dockerFound = (Get-Command docker -ErrorAction SilentlyContinue) -ne $null
    Write-Host "  Docker Available : $dockerFound"
    Write-Host "`nDryRun completed successfully. No containers or tests were executed." -ForegroundColor Green
    exit 0
}

$startedContainersByScript = $false
$testExit = 0

try {
    Write-Host "`n[1/3] Environment Diagnostics:" -ForegroundColor Yellow
    $dockerCmd = Get-Command docker -ErrorAction SilentlyContinue
    $dockerComposeCmd = Get-Command docker-compose -ErrorAction SilentlyContinue
    $hasDocker = ($dockerCmd -ne $null) -or ($dockerComposeCmd -ne $null)
    Write-Host ("  Docker / Compose : " + $(if ($hasDocker) { "Available" } else { "Not Found (Missing)" }))

    if ($StartContainers) {
        if (-not $hasDocker) {
            Write-Error "Cannot start containers: docker command was not found on PATH."
            exit 1
        }
        Write-Host "`nStarting isolated acceptance containers via docker compose..." -ForegroundColor Yellow
        & docker compose -f docker-compose.acceptance.yml up -d
        if ($LASTEXITCODE -ne 0) {
            Write-Error "Failed to start acceptance containers."
            exit $LASTEXITCODE
        }
        $startedContainersByScript = $true

        Write-Host "Waiting for container healthchecks..." -ForegroundColor Yellow
        $waitCount = 0
        $servicesHealthy = $false
        while ($waitCount -lt 15) {
            Start-Sleep -Seconds 2
            $pgConn = Get-NetTCPConnection -LocalPort 55432 -State Listen -ErrorAction SilentlyContinue
            $redisConn = Get-NetTCPConnection -LocalPort 56379 -State Listen -ErrorAction SilentlyContinue
            if ($pgConn -and $redisConn) {
                $servicesHealthy = $true
                break
            }
            $waitCount++
        }
        if (-not $servicesHealthy) {
            Write-Warning "Timeout waiting for PostgreSQL 55432 and Redis 56379 to enter Listen state."
        }
    }

    $pgConn = Get-NetTCPConnection -LocalPort 55432 -State Listen -ErrorAction SilentlyContinue
    $redisConn = Get-NetTCPConnection -LocalPort 56379 -State Listen -ErrorAction SilentlyContinue
    Write-Host ("  PostgreSQL 55432 : " + $(if ($pgConn) { "Listening (Available)" } else { "Closed / Not Listening" }))
    Write-Host ("  Redis 56379      : " + $(if ($redisConn) { "Listening (Available)" } else { "Closed / Not Listening" }))

    Write-Host "`n[2/3] Executing Acceptance Suite:" -ForegroundColor Yellow

    if ($RunLive) {
        if (-not ($pgConn -and $redisConn)) {
            Write-Host "  [ERROR] -RunLive requested but required services are not listening on 55432 / 56379." -ForegroundColor Red
            & $python -m pytest tests/test_integration_harness.py tests/integration/ -v --run-live-integration --pg-url "$PgUrl" --redis-url "$RedisUrl"
            $testExit = $LASTEXITCODE
        } else {
            Write-Host "  Services ready. Running live PostgreSQL and Redis/RQ acceptance tests..." -ForegroundColor Green
            & $python -m pytest tests/test_integration_harness.py tests/integration/ -v --run-live-integration --pg-url "$PgUrl" --redis-url "$RedisUrl"
            $testExit = $LASTEXITCODE
        }
    } else {
        Write-Host "  [NOTICE] -RunLive flag omitted. Running offline harness and skipped live tests..." -ForegroundColor DarkYellow
        $env:MANGAFLOW_ENABLE_LIVE_INTEGRATION = "0"
        & $python -m pytest tests/test_integration_harness.py tests/integration/ -v
        $testExit = $LASTEXITCODE
    }

    Write-Host "`n[3/3] Execution Summary:" -ForegroundColor Yellow
    if ($testExit -eq 0) {
        Write-Host "  Harness and unit tests passed successfully." -ForegroundColor Green
    } else {
        Write-Host "  Test execution completed with non-zero exit code: $testExit" -ForegroundColor Red
    }

} finally {
    if ($StopContainers -and $startedContainersByScript) {
        Write-Host "`nCleaning up isolated acceptance containers..." -ForegroundColor Yellow
        & docker compose -f docker-compose.acceptance.yml down
    }
}

exit $testExit