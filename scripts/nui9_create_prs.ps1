# NUI-9 轮收口 PR 描述（P7-3）
# 每个 PR：基于 goal/nui8-release（77f93dcc，即 PR #983~#989 的 A~G 提交集合）的堆叠 PR。
# A~G 合入 master 后：gh pr edit --base master 再合（目标既定协议）。
$base = 'goal/nui8-release'
$prs = @(
    @{ head = 'nui9/pr-h-d5'; title = 'NUI-9 PR-H: D5 全局设置双头部——动作归壳+单头部（配离屏像素+交互回归）'; file = 'pr-h.md' },
    @{ head = 'nui9/pr-i-dialogs'; title = 'NUI-9 PR-I: NUI-8-A——43 处 YesNo 确认迁移 ConfirmDialog，配键盘契约回归'; file = 'pr-i.md' },
    @{ head = 'nui9/pr-j-a11y'; title = 'NUI-9 PR-J: 仪表盘项目卡 UIA 语义名与 Invoke 断言'; file = 'pr-j.md' },
    @{ head = 'nui9/pr-k-cancelrace'; title = 'NUI-9 PR-K: 缺陷 #4 取消竞态分流——取消在途请求不再误报传输失败'; file = 'pr-k.md' },
    @{ head = 'nui9/pr-l-seed-g4'; title = 'NUI-9 PR-L: 种子面板 bounds 键名修复（0×0 面板根因）+ G4 角标锚采样 N=20'; file = 'pr-l.md' },
    @{ head = 'nui9/pr-m-ledger'; title = 'NUI-9 PR-M: 轮台账 output/nui9-rc、实机 UIA 辅助脚本与证据'; file = 'pr-m.md' }
)
foreach ($p in $prs) {
    $body = Get-Content ("$env:TEMP\nui8-pr-bodies\" + $p.file) -Encoding UTF8 -Raw
    $url = gh pr create --base $base --head $p.head --title $p.title --body $body 2>&1
    Write-Output ($p.head + ' -> ' + $url)
}
