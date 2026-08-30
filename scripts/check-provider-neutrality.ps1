#Requires -Version 5.1
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
$patterns = 'VERTEX_NATIVE', 'vertex-ai', 'vertex_configured'
$allowlistPath = Join-Path $PSScriptRoot 'provider-neutrality-allowlist.txt'
if (-not (Test-Path -LiteralPath $allowlistPath)) {
  Exit-EnvironmentError 'allowlist missing'
}
$allowed = @{}
Get-Content -LiteralPath $allowlistPath | ForEach-Object {
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
