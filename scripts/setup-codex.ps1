param()

$ErrorActionPreference = "Stop"
$root = if ($env:CODEX_WORKTREE_PATH) {
    [IO.Path]::GetFullPath($env:CODEX_WORKTREE_PATH)
} else {
    [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
}
Set-Location -LiteralPath $root

# Windows PowerShell 5.1: $ErrorActionPreference does not apply to native
# executables, so check $LASTEXITCODE after each one (same pattern as
# start-dev.ps1). Otherwise a partial install is reported as a success.
function Invoke-Native {
    param([Parameter(Mandatory = $true)][scriptblock]$Command)
    & $Command
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed with exit code ${LASTEXITCODE}: $Command"
    }
}

if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
    throw "Node.js was not found. Install Node.js 22 or newer."
}
if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    throw "Python was not found. Install Python 3.12 or newer."
}

if (-not (Test-Path -LiteralPath ".venv\Scripts\python.exe")) {
    Invoke-Native { python -m venv .venv }
}

Invoke-Native { npm install }
Invoke-Native { & ".\.venv\Scripts\python.exe" -m pip install -r "apps\api\requirements-dev.txt" }

if (-not (Test-Path -LiteralPath ".env")) {
    Copy-Item -LiteralPath ".env.example" -Destination ".env"
    Write-Warning ".env was created. Configure the Google Cloud project and credential path."
}

New-Item -ItemType Directory -Force -Path "storage", "uploads" | Out-Null
Invoke-Native { & ".\.venv\Scripts\python.exe" -m alembic -c "apps\api\alembic.ini" upgrade head }

Write-Output "MangaFlow setup is complete. Run scripts\start-dev.ps1 to start."
