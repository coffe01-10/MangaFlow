param(
    [int]$Samples = 20,
    [string]$OutCsv = "D:\自媒体\漫画工作流\output\nui67-acceptance\run-p2\perf\g2-page-switch.csv"
)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes

# G2 口径:真实窗口内,对侧栏 ListBox 行发起 UIA SelectionItem,轮询面包屑文本
# 变化(「项目 / 页面」)直至出现目标页名。样本含 UIA 轮询粒度(每次查询 ~10-30ms),
# 记录的是切换到「UIA 可见完成」的上限,不是 GPU 首帧。全部样本保留,失败不剔除。

$sw = [System.Diagnostics.Stopwatch]::StartNew()
$proc = Get-Process MangaFlow.Native -ErrorAction Stop | Select-Object -First 1
$root = [System.Windows.Automation.AutomationElement]::RootElement
$cond = New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ProcessIdProperty, $proc.Id)
$win = $root.FindFirst([System.Windows.Automation.TreeScope]::Children, $cond)
if (-not $win) { throw "main window not found for pid $($proc.Id)" }
Write-Output "window: $($win.Current.Name)"

$pages = @('原作与修订', '参考资产', '漫画剧本', '分页与分镜', '单页生成', '生成素材库', '任务中心', '流程编排', '项目设置')
# 每页完成标记：切换完成后该页才会渲染出的唯一文本（比面包屑可靠——原生 UIA
# 中面包屑不是单一元素）。口径见文件头注释。
$markers = @{
    '原作与修订' = '完整导入，不压缩故事'
    '参考资产'   = '姓名、绰号与参考图绑定'
    '漫画剧本'   = '先写场景与情节拍，再进入分页'
    '分页与分镜' = '内容有多少，页面就有多少'
    '单页生成'   = 'DRAW / 单页抽卡'
    '生成素材库' = '保存每一次值得比较的结果'
    '任务中心'   = '每个生成任务都能看懂、取消和重试'
    '流程编排'   = '节点库'
    '项目设置'   = 'PROJECT SETTINGS / 08'
}
$rows = New-Object System.Collections.Generic.List[object]

function Find-Row([System.Windows.Automation.AutomationElement]$win, [string]$page) {
    $target = "$page，已接通"
    $all = $win.FindAll([System.Windows.Automation.TreeScope]::Descendants, [System.Windows.Automation.Condition]::TrueCondition)
    foreach ($el in $all) {
        if ($el.Current.Name -eq $target) { return $el }
    }
    return $null
}

function Test-Marker([System.Windows.Automation.AutomationElement]$win, [string]$marker) {
    $all = $win.FindAll([System.Windows.Automation.TreeScope]::Descendants, [System.Windows.Automation.Condition]::TrueCondition)
    foreach ($el in $all) {
        if ($el.Current.Name -eq $marker) { return $true }
    }
    return $false
}

# Warm-up: two switches, discarded (per acceptance plan warm-up rule).
foreach ($p in @($pages[0], $pages[1])) {
    $row = Find-Row $win $p
    if (-not $row) { throw "sidebar row not found: $p" }
    $row.GetCurrentPattern([System.Windows.Automation.SelectionItemPattern]::Pattern).Select()
    Start-Sleep -Milliseconds 600
}

for ($i = 1; $i -le $Samples; $i++) {
    $target = $pages[($i - 1) % $pages.Count]
    $row = Find-Row $win $target
    if (-not $row) { $rows.Add([pscustomobject]@{ Sample = $i; Target = $target; Status = 'FAIL_ROW_NOT_FOUND'; Ms = -1 }); continue }
    $watch = [System.Diagnostics.Stopwatch]::StartNew()
    try { $row.GetCurrentPattern([System.Windows.Automation.SelectionItemPattern]::Pattern).Select() } catch { $rows.Add([pscustomobject]@{ Sample = $i; Target = $target; Status = 'FAIL_SELECT'; Ms = -1 }); continue }
    $done = $false
    $marker = $markers[$target]
    while ($watch.ElapsedMilliseconds -lt 10000) {
        if (Test-Marker $win $marker) { $done = $true; break }
        Start-Sleep -Milliseconds 8
    }
    $ms = $watch.ElapsedMilliseconds
    $rows.Add([pscustomobject]@{ Sample = $i; Target = $target; Status = $(if ($done) { 'OK' } else { 'FAIL_TIMEOUT' }); Ms = $ms })
}

New-Item -ItemType Directory -Force -Path (Split-Path $OutCsv) | Out-Null
$rows | Export-Csv -Path $OutCsv -NoTypeInformation -Encoding UTF8
$ok = $rows | Where-Object { $_.Status -eq 'OK' }
$fail = $rows | Where-Object { $_.Status -ne 'OK' }
$sorted = $ok | Sort-Object Ms
$p50 = $sorted[[int][Math]::Floor(($sorted.Count - 1) * 0.5)].Ms
$p95 = $sorted[[int][Math]::Ceiling($sorted.Count * 0.95) - 1].Ms
Write-Output ("G2 page-switch: {0}/{1} OK, P50={2}ms P95={3}ms min={4} max={5}; samples in {6}" -f $ok.Count, $rows.Count, $p50, $p95, ($ok | Measure-Object Ms -Minimum).Minimum, ($ok | Measure-Object Ms -Maximum).Maximum, $OutCsv)
if ($fail.Count -gt 0) { Write-Output "FAILED SAMPLES (kept): "; $fail | Format-Table -AutoSize | Out-String | Write-Output }
