param(
    [switch]$RunLive,
    [switch]$StartContainers,
    [switch]$StopContainers,
    [switch]$DryRun,
    [string]$PgUrl = $env:MANGAFLOW_ACCEPTANCE_PG_URL,
    [string]$RedisUrl = $env:MANGAFLOW_ACCEPTANCE_REDIS_URL,
    [string]$Python = $env:MANGAFLOW_PYTHON
)

$ErrorActionPreference = "Stop"
$taskRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
if (-not $Python) { $Python = Join-Path $taskRoot ".venv\Scripts\python.exe" }
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    throw "Python interpreter missing; pass -Python with an existing interpreter path."
}

$taskPgBefore = $env:MANGAFLOW_ACCEPTANCE_PG_URL
$taskRedisBefore = $env:MANGAFLOW_ACCEPTANCE_REDIS_URL
$taskArguments = @((Join-Path $PSScriptRoot "run_phase2_acceptance.py"))
if ($RunLive) { $taskArguments += "--run-live" }
if ($StartContainers) { $taskArguments += "--start-containers" }
if ($StopContainers) { $taskArguments += "--stop-containers" }
if ($DryRun) { $taskArguments += "--dry-run" }
$taskExitCode = 2
try {
    $env:MANGAFLOW_ACCEPTANCE_PG_URL = $PgUrl
    $env:MANGAFLOW_ACCEPTANCE_REDIS_URL = $RedisUrl
    # URL values are process data, never interpolated into Python source/shell code.
    & $Python @taskArguments
    $taskExitCode = $LASTEXITCODE
} finally {
    $env:MANGAFLOW_ACCEPTANCE_PG_URL = $taskPgBefore
    $env:MANGAFLOW_ACCEPTANCE_REDIS_URL = $taskRedisBefore
}
exit $taskExitCode
