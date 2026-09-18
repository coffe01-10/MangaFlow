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
# Existence is not capability: Node 18 / Python 3.11 pass the lookup and
# then die mid-build with syntax errors that point nowhere near the real
# cause (the toolchain floor is Node 22 / Python 3.12). The probes avoid
# embedded double quotes: PS 5.1 passes them to native commands unescaped,
# so the interpreter would see its string literals stripped.
$nodeMajor = [int](& node -p "process.versions.node.split('.')[0]")
if ($nodeMajor -lt 22) {
    throw "Node.js ${nodeMajor}.x is too old. Install Node.js 22 or newer."
}
if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    throw "Python was not found. Install Python 3.12 or newer."
}
& python -c 'import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)'
if ($LASTEXITCODE -ne 0) {
    $pythonVersion = (& python -c 'import sys; print(sys.version)')
    throw "Python ${pythonVersion} is too old. Install Python 3.12 or newer."
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
