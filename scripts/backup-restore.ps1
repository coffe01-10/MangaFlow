[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("backup", "restore", "verify", "create-fixture")]
    [string] $Action,

    [string] $SourceRoot,
    [string] $Archive,
    [string] $Destination,
    [string] $Report,
    [switch] $DryRun
)

$ErrorActionPreference = "Stop"
$root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
Set-Location -LiteralPath $root

$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) {
    throw "Missing .venv. Run scripts\setup-codex.ps1 first."
}

$script = Join-Path $root "scripts\backup_restore.py"
$argumentList = @(
    $script,
    "--repo-root", $root,
    $Action.ToLowerInvariant()
)

if ($PSBoundParameters.ContainsKey("SourceRoot") -and $SourceRoot) {
    $argumentList += @("--source-root", ([IO.Path]::GetFullPath($SourceRoot)))
}
if ($PSBoundParameters.ContainsKey("Archive") -and $Archive) {
    $argumentList += @("--archive", ([IO.Path]::GetFullPath($Archive)))
}
if ($PSBoundParameters.ContainsKey("Destination") -and $Destination) {
    $argumentList += @("--destination", ([IO.Path]::GetFullPath($Destination)))
}
if ($PSBoundParameters.ContainsKey("Report") -and $Report) {
    $argumentList += @("--report", ([IO.Path]::GetFullPath($Report)))
}
if ($DryRun) {
    $argumentList += "--dry-run"
}

& $python @argumentList
exit $LASTEXITCODE
