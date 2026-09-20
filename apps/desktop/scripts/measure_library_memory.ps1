param(
    [int]$Samples = 20,
    [string]$OutCsv = "D:\自媒体\漫画工作流\output\nui67-acceptance\run-g3\perf\g3-library-memory.csv",
    [string]$SectionName = '生成素材库',
    [string]$ProjectName = 'NUI67 长列表内存项目',
    [switch]$NoNavigate,
    [switch]$NoNodeProbe
)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes

# G3 口径(长列表内存):真实窗口停在生成素材库(候选列表由服务端游标分页,
# 首屏喂给客户端的条数以页面顶部的「N 个候选」为准),每 1.5s 采样一次
# WorkingSet64 / PrivateMemorySize64 / Gen2,期间以 UIA ScrollPattern 在
# 底/顶之间往返(奇数样本到底、偶数样本回顶)。全部样本保留。
# 两条探针各自的用途要分开读:
#   RealizedNodes  —— 每样本做一次全树 FindAll,本身就是负载(-NoNodeProbe 可关掉);
#   ExtentHeight/ViewportHeight/VerticalPercent —— 证明视口真的在动,而不是空转。

$proc = Get-Process MangaFlow.Native -ErrorAction Stop | Select-Object -First 1
$root = [System.Windows.Automation.AutomationElement]::RootElement
$cond = New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ProcessIdProperty, $proc.Id)
$win = $root.FindFirst([System.Windows.Automation.TreeScope]::Children, $cond)
if (-not $win) { throw "main window not found" }

function Find-All([System.Windows.Automation.AutomationElement]$win) {
    return $win.FindAll([System.Windows.Automation.TreeScope]::Descendants, [System.Windows.Automation.Condition]::TrueCondition)
}

# Open the long-list project from the dashboard, then the long-list page
# (the client starts on the dashboard, and a G3 sample taken on the wrong
# page measures nothing).
function Invoke-ByName([string]$pattern) {
    foreach ($el in Find-All $win) {
        if ($el.Current.Name -like $pattern) {
            $ip = $null
            if ($el.TryGetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern, [ref]$ip)) { $ip.Invoke(); return $true }
            $si = $null
            if ($el.TryGetCurrentPattern([System.Windows.Automation.SelectionItemPattern]::Pattern, [ref]$si)) { $si.Select(); return $true }
        }
    }
    return $false
}

if (-not $NoNavigate) {
    $back = Invoke-ByName '← 项目'
    Start-Sleep -Seconds 2
    $opened = Invoke-ByName "*$ProjectName*"
    Start-Sleep -Seconds 3
    $selected = Invoke-ByName "$SectionName*"
    Start-Sleep -Seconds 3
}

# The library scroller carries AutomationProperties.Name 素材库滚动区 (D8);
# matching it by substring keeps this sampler honest if the label grows.
$scroll = $null
foreach ($el in Find-All $win) { if ($el.Current.Name -like '*滚动*') { $scroll = $el; break } }
$scrollPatternAvailable = $false
$sp = $null
if ($scroll) {
    if ($scroll.TryGetCurrentPattern([System.Windows.Automation.ScrollPattern]::Pattern, [ref]$sp)) { $scrollPatternAvailable = $true }
}
# Sampling the wrong page looks exactly like a healthy flat memory curve, so
# refuse instead of writing a CSV nobody can tell apart from a real G3 run.
if (-not $scrollPatternAvailable) { throw "scroll target not found on the long-list page (navigated=$opened/$selected)" }

$rows = New-Object System.Collections.Generic.List[object]
for ($i = 1; $i -le $Samples; $i++) {
    $proc.Refresh()
    $ws = $proc.WorkingSet64 / 1MB
    $priv = $proc.PrivateMemorySize64 / 1MB
    # Drive scrolling: down on odd samples, up on even samples (two full sweeps).
    $extent = 0.0; $viewport = 0.0; $percent = -1.0
    if ($scrollPatternAvailable) {
        # The scroll command and the geometry read get separate guards: a failed
        # read must not hide whether the viewport was actually driven, and a run
        # that never scrolled would otherwise look like a clean flat memory curve.
        # ScrollVerticalPercent is a property of ScrollPatternInformation (read-only
        # through $sp.Current); the writable path on the client pattern is
        # SetScrollPercent(horizontal, vertical) with -1 meaning "leave this axis".
        try {
            if ($i % 2 -eq 1) { $sp.SetScrollPercent(-1, 100) } else { $sp.SetScrollPercent(-1, 0) }
            $scrolledThisSample = $true
        } catch { $scrolledThisSample = $false }
        try {
            Start-Sleep -Milliseconds 400
            $extent = [Math]::Round($sp.Current.Extent.Height, 0)
            $viewport = [Math]::Round($sp.Current.Viewport.Height, 0)
            $percent = [Math]::Round($sp.Current.VerticalScrollPercent, 1)
        } catch { }
    }
    # Realized UIA nodes are the virtualization probe, but the full-tree FindAll
    # that reads them is itself load on the measured process, so it can be switched off.
    $realized = -1
    if (-not $NoNodeProbe) { $realized = (Find-All $win).Count }
    $gen2 = 0
    try { $gen2 = (Get-Counter "\Process(MangaFlow.Native)\# Gen2 collections" -ErrorAction Stop).CounterSamples[0].CookedValue } catch { }
    $rows.Add([pscustomobject]@{
        Sample = $i; WorkingSetMB = [Math]::Round($ws, 1); PrivateMB = [Math]::Round($priv, 1)
        RealizedNodes = $realized; Gen2Collections = $gen2; Scrolled = $scrollPatternAvailable
        ScrollDriven = $scrolledThisSample
        ExtentHeight = $extent; ViewportHeight = $viewport; VerticalPercent = $percent
    })
    Start-Sleep -Milliseconds 1500
}

$driven = @($rows | Where-Object { $_.ScrollDriven }).Count
if ($driven -lt $Samples) { throw "only $driven/$Samples samples actually drove the scroller; this run is not a G3 scroll-pressure sample set" }

New-Item -ItemType Directory -Force -Path (Split-Path $OutCsv) | Out-Null
$rows | Export-Csv -Path $OutCsv -NoTypeInformation -Encoding UTF8
$sorted = @($rows | Sort-Object WorkingSetMB)
$p50 = $sorted[[int][Math]::Floor(($sorted.Count - 1) * 0.5)].WorkingSetMB
$first = $rows[0].WorkingSetMB
$last = $rows[$rows.Count - 1].WorkingSetMB
$growth = [Math]::Round($last - $first, 1)
$percents = @($rows | ForEach-Object { $_.VerticalPercent } | Sort-Object -Unique)
Write-Output ("G3 long-list memory: samples={0} navigated={7} WS P50={1}MB first={2}MB last={3}MB growth={4}MB max={5}MB (scrollPattern={6}); realized nodes first/last={8}/{9}; extent/viewport={10}/{11}px percent={12}; csv={13}" -f `
    $rows.Count, $p50, $first, $last, $growth, $sorted[$sorted.Count-1].WorkingSetMB, $scrollPatternAvailable, $selected, $rows[0].RealizedNodes, $rows[$rows.Count-1].RealizedNodes, $rows[0].ExtentHeight, $rows[0].ViewportHeight, ($percents -join ','), $OutCsv)
