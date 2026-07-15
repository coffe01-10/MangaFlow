param()

$ErrorActionPreference = "Stop"
$root = if ($env:CODEX_WORKTREE_PATH) {
    [IO.Path]::GetFullPath($env:CODEX_WORKTREE_PATH)
} else {
    [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
}
Set-Location -LiteralPath $root

if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
    throw "Node.js was not found. Install Node.js 22 or newer."
}
if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    throw "Python was not found. Install Python 3.12 or newer."
}

if (-not (Test-Path -LiteralPath ".venv\Scripts\python.exe")) {
    python -m venv .venv
}

npm install
& ".\.venv\Scripts\python.exe" -m pip install -r "apps\api\requirements-dev.txt"

if (-not (Test-Path -LiteralPath ".env")) {
    Copy-Item -LiteralPath ".env.example" -Destination ".env"
    Write-Warning ".env was created. Configure the Google Cloud project and credential path."
}

New-Item -ItemType Directory -Force -Path "storage", "uploads" | Out-Null
& ".\.venv\Scripts\python.exe" -m alembic -c "apps\api\alembic.ini" upgrade head

Write-Output "MangaFlow setup is complete. Run scripts\start-dev.ps1 to start."
