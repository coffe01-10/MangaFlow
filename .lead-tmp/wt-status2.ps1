$ErrorActionPreference = 'Continue'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$out = git worktree list --porcelain
$current = $null
foreach ($line in $out) {
  if ($line -like 'worktree *') { $current = $line.Substring(9) }
  elseif ($line -eq 'bare' -or $line -like 'branch *' -or $line -like 'HEAD *' -or $line -eq 'detached') { }
}
# simpler: iterate worktree paths directly
$paths = @()
foreach ($line in $out) { if ($line -like 'worktree *') { $paths += $line.Substring(9) } }
foreach ($p in $paths) {
  Push-Location -LiteralPath $p
  $d = @(git status --porcelain)
  $ahead = git rev-list --count origin/master..HEAD 2>$null
  $behind = git rev-list --count HEAD..origin/master 2>$null
  $short = Split-Path $p -Leaf
  Write-Output "$short : dirty=$($d.Count) aheadOfOriginMaster=$ahead behindOriginMaster=$behind"
  if ($d.Count -gt 0) { $d | Select-Object -First 6 | ForEach-Object { Write-Output "    $_" } }
  Pop-Location
}
