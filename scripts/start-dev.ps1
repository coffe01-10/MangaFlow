param()

$ErrorActionPreference = "Stop"
$root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
Set-Location -LiteralPath $root

if (-not (Test-Path -LiteralPath ".venv\Scripts\python.exe")) {
    throw "The environment is not initialized. Run scripts\setup-codex.ps1 first."
}
if (-not (Test-Path -LiteralPath ".env")) {
    throw ".env is missing. Copy .env.example and configure Vertex AI."
}

function Get-DotEnvValue([string]$Name) {
    $escapedName = [Regex]::Escape($Name)
    foreach ($line in Get-Content -LiteralPath ".env") {
        $trimmed = $line.Trim()
        if (-not $trimmed -or $trimmed.StartsWith("#")) {
            continue
        }
        if ($trimmed -match "^$escapedName\s*=\s*(.*)$") {
            return $Matches[1].Trim().Trim('"').Trim("'")
        }
    }
    return $null
}

$redisUrl = Get-DotEnvValue "REDIS_URL"
if ($redisUrl) {
    $env:REDIS_URL = $redisUrl
}

$proxyUrl = Get-DotEnvValue "MANGAFLOW_PROXY_URL"
if ($proxyUrl) {
    $env:HTTP_PROXY = $proxyUrl
    $env:HTTPS_PROXY = $proxyUrl
    $localBypass = @("localhost", "127.0.0.1")
    if ($env:NO_PROXY) {
        $localBypass += $env:NO_PROXY.Split(",", [StringSplitOptions]::RemoveEmptyEntries)
    }
    $env:NO_PROXY = ($localBypass | Select-Object -Unique) -join ","
    Write-Output "Local HTTP/Mixed proxy enabled for API and Worker processes."
}

& ".\.venv\Scripts\python.exe" -m alembic -c "apps\api\alembic.ini" upgrade head
if ($LASTEXITCODE -ne 0) {
    throw "Database migration failed with exit code $LASTEXITCODE. Services were not started."
}
$env:ENVIRONMENT = "development"
$env:QUEUE_ENABLED = "true"

Write-Output "Starting MangaFlow: Web http://127.0.0.1:3000, API http://127.0.0.1:8000/api/docs"
Write-Output "Without Redis, the concurrency-limited local worker is used. Press Ctrl+C to stop."
npm run dev
