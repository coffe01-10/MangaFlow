param(
    [int]$Samples = 20,
    [string]$OutCsv = "D:\自媒体\漫画工作流\output\nui67-acceptance\run-g3\perf\g3-library-memory.csv"
)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes

# G3 口径(长列表内存):真实窗口在长列表页(生成素材库,600 候选/120 批次),
# 每 1.5s 采样一次进程 WorkingSet64 与 PrivateMemorySize64,期间以 UIA
# ScrollPattern 驱动页面滚动到底再回顶(两轮),观察内存增量。全部样本保留。

$proc = Get-Process MangaFlow.Native -ErrorAction Stop | Select-Object -First 1
$root = [System.Windows.Automation.AutomationElement]::RootElement
$cond = New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ProcessIdProperty, $proc.Id)
$win = $root.FindFirst([System.Windows.Automation.TreeScope]::Children, $cond)
if (-not $win) { throw "main window not found" }

function Find-ByName([System.Windows.Automation.AutomationElement]$win, [string]$name) {
    $all = $win.FindAll([System.Windows.Automation.TreeScope]::Descendants, [System.Windows.Automation.Condition]::TrueCondition)
    foreach ($el in $all) { if ($el.Current.Name -eq $name) { return $el } }
    return $null
}

$rows = New-Object System.Collections.Generic.List[object]
$scroll = Find-ByName $win '滚动'  # library page scroller (AutomationProperties.Name)
$scrollPatternAvailable = $false
if ($scroll) {
    $sp = $null
    if ($scroll.TryGetCurrentPattern([System.Windows.Automation.ScrollPattern]::Pattern, [ref]$sp)) { $scrollPatternAvailable = $true }
}

for ($i = 1; $i -le $Samples; $i++) {
    $proc.Refresh()
    $ws = $proc.WorkingSet64 / 1MB
    $priv = $proc.PrivateMemorySize64 / 1MB
    # Drive scrolling: down on odd samples, up on even samples (two full sweeps).
    if ($scrollPatternAvailable) {
        try {
            if ($i % 2 -eq 1) { $sp.ScrollVerticalPercent = 100 } else { $sp.ScrollVerticalPercent = 0 }
        } catch { }
    }
    $rows.Add([pscustomobject]@{ Sample = $i; WorkingSetMB = [Math]::Round($ws, 1); PrivateMB = [Math]::Round($priv, 1) })
    Start-Sleep -Milliseconds 1500
}

New-Item -ItemType Directory -Force -Path (Split-Path $OutCsv) | Out-Null
$rows | Export-Csv -Path $OutCsv -NoTypeInformation -Encoding UTF8
$sorted = @($rows | Sort-Object WorkingSetMB)
$p50 = $sorted[[int][Math]::Floor(($sorted.Count - 1) * 0.5)].WorkingSetMB
$first = $rows[0].WorkingSetMB
$last = $rows[$rows.Count - 1].WorkingSetMB
$growth = [Math]::Round($last - $first, 1)
Write-Output ("G3 long-list memory: samples={0} WS P50={1}MB first={2}MB last={3}MB growth={4}MB max={5}MB (scrollPattern={6}); csv={7}" -f $rows.Count, $p50, $first, $last, $growth, $sorted[$sorted.Count-1].WorkingSetMB, $scrollPatternAvailable, $OutCsv)
