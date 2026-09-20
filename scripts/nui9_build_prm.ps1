$ErrorActionPreference = 'Continue'
git checkout -q -b nui9/pr-m-ledger 77f93dcc 2>&1 | Out-Null
foreach ($sha in '1dbd5311', '1802dfa0', 'f62280f7', '759aa8fe', '67c8d643', '20ba968e', 'b531b2e8', 'a252a7b1') {
    git cherry-pick -n $sha 2>&1 | Out-Null
    # 冲突的台账文件一律取本轮版本
    $conflicted = git status --porcelain | Select-String -Pattern '^(UU|AA|DD)'
    if ($conflicted) {
        foreach ($line in $conflicted) {
            $path = ($line.ToString().Substring(3)).Trim()
            git checkout --theirs -- $path 2>$null
            git add -- $path 2>$null
        }
    }
    git commit -m "ledger: $sha" 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) {
        # 没有实际变更（全部冲突已丢弃）时跳过空提交
        git commit --allow-empty -m "ledger: $sha" 2>&1 | Out-Null
    }
    Write-Output "applied $sha"
}
git log --oneline | Select-Object -First 10
