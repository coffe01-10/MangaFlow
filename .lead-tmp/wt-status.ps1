$roots = @('codex-v02-20b','glm-v02-22a','glm-v02-22b','ui-dev','wt-a','wt-b','wt-c','wt-d','wt-e')
foreach ($w in $roots) {
  $p = "D:/自媒体/漫画工作流-$w"
  Push-Location $p
  $d = @(git status --porcelain)
  $ahead = git rev-list --count master..HEAD 2>$null
  $behind = git rev-list --count HEAD..master 2>$null
  Write-Output "$w : dirty=$($d.Count) ahead=$ahead behind=$behind"
  if ($d.Count -gt 0) { $d | Select-Object -First 5 | ForEach-Object { Write-Output "    $_" } }
  Pop-Location
}
