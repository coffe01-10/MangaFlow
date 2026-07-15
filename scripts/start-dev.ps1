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

& ".\.venv\Scripts\python.exe" -m alembic -c "apps\api\alembic.ini" upgrade head
$env:ENVIRONMENT = "development"
$env:QUEUE_ENABLED = "true"

Write-Output "Starting MangaFlow: Web http://localhost:3000, API http://localhost:8000/api/docs"
Write-Output "Without Redis, the concurrency-limited local worker is used. Press Ctrl+C to stop."
npm run dev
