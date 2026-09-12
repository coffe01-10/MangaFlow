#Requires -Version 5.1
# Provider-neutrality gate (Issue #41, audit M13): fixed-string greps over
# apps/** for every known Vertex SDK surface.
# Marker set: VERTEX_NATIVE, vertex-ai, vertex_configured, vertexai,
#   aiplatform, google-cloud-aiplatform.
# The set is the gate's whole heuristic — a new SDK surface (import path or
# pip name) must be added here AND in
# tests/test_provider_neutrality_gate.py's EXPECTED_PATTERNS, which pins
# this list (#412).
param(
  [switch]$UpdateAllowlist,
  [string]$RepositoryRoot
)
$ErrorActionPreference = 'Stop'

function Exit-EnvironmentError([string]$Message) {
  [Console]::Error.WriteLine($Message)
  exit 2
}

$repoRoot = if ([string]::IsNullOrWhiteSpace($RepositoryRoot)) {
  [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
} else {
  [IO.Path]::GetFullPath($RepositoryRoot)
}
if (-not (Test-Path -LiteralPath (Join-Path $repoRoot '.git'))) {
  Exit-EnvironmentError 'repository metadata missing'
}
if ($null -eq (Get-Command git -ErrorAction SilentlyContinue)) {
  Exit-EnvironmentError 'git command missing'
}
$patterns = 'VERTEX_NATIVE', 'vertex-ai', 'vertex_configured', 'vertexai', 'aiplatform', 'google-cloud-aiplatform'
$allowlistPath = Join-Path $PSScriptRoot 'provider-neutrality-allowlist.txt'
if (-not (Test-Path -LiteralPath $allowlistPath)) {
  Exit-EnvironmentError 'allowlist missing'
}
$allowed = @{}
# -Encoding UTF8: the file is WRITTEN UTF-8-no-BOM below, and Windows
# PowerShell 5.1's Get-Content defaults to ANSI — a non-ASCII allowlisted
# path would round-trip as mojibake into a permanent false violation
# (#462, same family as start-dev's .env read).
Get-Content -LiteralPath $allowlistPath -Encoding UTF8 | ForEach-Object {
  $line = $_.Trim()
  if ($line -and -not $line.StartsWith('#')) { $allowed[$line] = $true }
}
$hits = @()
foreach ($pattern in $patterns) {
  $patternHits = @(& git -C $repoRoot grep -n -I --fixed-strings -e $pattern -- apps 2>&1)
  $grepExit = $LASTEXITCODE
  if ($grepExit -gt 1) {
    $patternHits | ForEach-Object { [Console]::Error.WriteLine([string]$_) }
    Exit-EnvironmentError "git grep failed for pattern: $pattern"
  }
  if ($grepExit -eq 0) {
    $hits += $patternHits | ForEach-Object { ([string]$_).Replace('\', '/') }
  }
}
if ($UpdateAllowlist) {
  if ($allowed.Count -ne 0) {
    Exit-EnvironmentError '-UpdateAllowlist requires an empty allowlist'
  }
  $paths = @($hits | ForEach-Object { ($_ -split ':', 2)[0] } | Sort-Object -Unique)
  $content = @('# Generated baseline; remove a path when its final allowed hit is removed.') + $paths
  [IO.File]::WriteAllLines(
    $allowlistPath,
    $content,
    (New-Object Text.UTF8Encoding($false))
  )
  exit 0
}
$violations = @($hits | Where-Object {
  $path = ($_ -split ':', 2)[0]
  -not $allowed.ContainsKey($path)
} | Sort-Object -Unique)
if ($violations.Count -gt 0) { $violations; exit 1 }
exit 0
