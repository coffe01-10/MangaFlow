param(
    [int]$Samples = 20,
    [string]$OutCsv = "D:\自媒体\漫画工作流\output\nui67-acceptance\run-p2\perf\g4-canvas-drag.csv"
)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes
Add-Type -Namespace G4 -Name Input -MemberDefinition @'
[DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
[DllImport("user32.dll")] public static extern bool SetCursorPos(int X, int Y);
[DllImport("user32.dll")] public static extern void mouse_event(uint dwFlags, int dx, int dy, uint dwData, UIntPtr dwExtra);
'@

# G4 口径(真实窗口,无 GPU 采样器):对分镜画布 P.001 面板发起 SendInput 拖拽
# (按下→分步移动→抬起),从拖拽开始计时,UIA 轮询状态条出现「有未保存修改」
# (脏状态可见)为止;随后 UIA Invoke 保存本页并等回「已保存」。测的是
# 「拖拽派发 → 脏状态可见」的交互延迟,不是 GPU 帧时间(呈现层采样 BLOCKED,
# 见台账 G1)。全部样本保留,失败不剔除。同一窗口独占,期间无其他重负载。

$sw = [System.Diagnostics.Stopwatch]::StartNew()
$proc = Get-Process MangaFlow.Native -ErrorAction Stop | Select-Object -First 1
$root = [System.Windows.Automation.AutomationElement]::RootElement
$cond = New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ProcessIdProperty, $proc.Id)
$win = $root.FindFirst([System.Windows.Automation.TreeScope]::Children, $cond)
if (-not $win) { throw "main window not found" }
[G4.Input]::SetForegroundWindow($proc.MainWindowHandle) | Out-Null
Start-Sleep -Milliseconds 400

function Find-ByName([System.Windows.Automation.AutomationElement]$win, [string]$name) {
    $all = $win.FindAll([System.Windows.Automation.TreeScope]::Descendants, [System.Windows.Automation.Condition]::TrueCondition)
    foreach ($el in $all) { if ($el.Current.Name -eq $name) { return $el } }
    return $null
}
function Status-Text([System.Windows.Automation.AutomationElement]$win) {
    # 状态条文案随服务端版本漂移（V1/V2/V3…），按前缀匹配而不是写死版本号
    $all = $win.FindAll([System.Windows.Automation.TreeScope]::Descendants, [System.Windows.Automation.Condition]::TrueCondition)
    foreach ($el in $all) {
        $n = $el.Current.Name
        if ($n -like '有未保存修改*') { return 'DIRTY' }
        if ($n -like '已保存 · 当前 V*') { return 'CLEAN' }
    }
    return 'UNKNOWN'
}
function Drag([int]$x1, [int]$y1, [int]$x2, [int]$y2) {
    [G4.Input]::SetCursorPos($x1, $y1) | Out-Null
    Start-Sleep -Milliseconds 60
    [G4.Input]::mouse_event(0x0002, 0, 0, 0, [UIntPtr]::Zero)  # LEFTDOWN
    Start-Sleep -Milliseconds 40
    for ($s = 1; $s -le 6; $s++) {
        $x = $x1 + [int](($x2 - $x1) * $s / 6)
        $y = $y1 + [int](($y2 - $y1) * $s / 6)
        [G4.Input]::SetCursorPos($x, $y) | Out-Null
        Start-Sleep -Milliseconds 25
    }
    [G4.Input]::mouse_event(0x0004, 0, 0, 0, [UIntPtr]::Zero)  # LEFTUP
}

# 面板拖拽起点改为 UIA 矩形换算（NUI-9 P5-1 第二版）：视口比例点会随页面
# 平移/缩放/检查器开合漂移（本轮实测 885,735 已落在页面右缘外）。改用
# 阅读序角标做锚——角标「格 01」绑定面板 P.001 的画布位置，打开阅读序后
# 从 UIA 取角标矩形，向左下偏移进面板内部下手。缩放/平移无关。
$orderBtn = Find-ByName $win '阅读序'
if ($orderBtn) { $orderBtn.GetCurrentPattern([System.Windows.Automation.TogglePattern]::Pattern).Toggle(); Start-Sleep -Milliseconds 400 }
$badge = Find-ByName $win '格 01'
if (-not $badge) { throw "order badge 格 01 not found via UIA（阅读序未开或画布无面板）" }
$br = $badge.Current.BoundingRectangle
$panelX = [int]($br.X - 12)
$panelY = [int]($br.Y + $br.Height + 8)
Write-Output ("badge 格 01 {0},{1} {2}x{3} -> drag start {4},{5}" -f [int]$br.X, [int]$br.Y, [int]$br.Width, [int]$br.Height, $panelX, $panelY)
$orderBtn.GetCurrentPattern([System.Windows.Automation.TogglePattern]::Pattern).Toggle() | Out-Null   # 还原阅读序
Start-Sleep -Milliseconds 300
$direction = 1
$rows = New-Object System.Collections.Generic.List[object]

# warm-up 1 轮
Drag $panelX $panelY ($panelX + 60) $panelY
Start-Sleep -Milliseconds 300
$saveBtn = Find-ByName $win '保存本页'
if ($saveBtn) { $saveBtn.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern).Invoke() }
Start-Sleep -Milliseconds 800

# 每个样本前重新锚定：拖拽会移动面板，脚本开头的固定点会漂移（首轮 3/20 的
# 教训）。锚定在计时窗口之外（toggle 阅读序→取格 01 角标→还原），不影响采样。
function Get-PanelAnchor([System.Windows.Automation.AutomationElement]$win) {
    $all = $win.FindAll([System.Windows.Automation.TreeScope]::Descendants, [System.Windows.Automation.Condition]::TrueCondition)
    $order = $null
    foreach ($el in $all) {
        if ($el.Current.Name -eq '阅读序' -and $el.Current.ControlType.ProgrammaticName -match 'Button') { $order = $el; break }
    }
    if (-not $order) { throw "reading-order toggle not found" }
    $order.GetCurrentPattern([System.Windows.Automation.TogglePattern]::Pattern).Toggle()
    Start-Sleep -Milliseconds 350
    $badge = Find-ByName $win '格 01'
    if (-not $badge) { $order.GetCurrentPattern([System.Windows.Automation.TogglePattern]::Pattern).Toggle() | Out-Null; throw "badge 格 01 not found" }
    $r = $badge.Current.BoundingRectangle
    $point = @{ X = [int]($r.X - 12); Y = [int]($r.Y + $r.Height + 8) }
    $order.GetCurrentPattern([System.Windows.Automation.TogglePattern]::Pattern).Toggle() | Out-Null
    Start-Sleep -Milliseconds 250
    return $point
}

for ($i = 1; $i -le $Samples; $i++) {
    $anchor = Get-PanelAnchor $win
    $x1 = $anchor.X; $panelY = $anchor.Y
    $x2 = $x1 + 60 * $direction
    $direction *= -1
    $watch = [System.Diagnostics.Stopwatch]::StartNew()
    Drag $x1 $panelY $x2 $panelY
    $dirtyAt = -1
    while ($watch.ElapsedMilliseconds -lt 8000) {
        if ((Status-Text $win) -eq 'DIRTY') { $dirtyAt = $watch.ElapsedMilliseconds; break }
        Start-Sleep -Milliseconds 8
    }
    if ($dirtyAt -lt 0) { $rows.Add([pscustomobject]@{ Sample = $i; Status = 'FAIL_DIRTY_TIMEOUT'; Ms = -1 }); continue }
    # 保存本页并等回 CLEAN
    $saveBtn = Find-ByName $win '保存本页'
    if (-not $saveBtn) { $rows.Add([pscustomobject]@{ Sample = $i; Status = 'FAIL_NO_SAVE_BUTTON'; Ms = $dirtyAt }); continue }
    $saveBtn.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern).Invoke()
    $savedAt = -1
    while ($watch.ElapsedMilliseconds -lt 15000) {
        if ((Status-Text $win) -eq 'CLEAN') { $savedAt = $watch.ElapsedMilliseconds; break }
        Start-Sleep -Milliseconds 8
    }
    $rows.Add([pscustomobject]@{ Sample = $i; Status = 'OK'; Ms = $dirtyAt; SaveMs = $savedAt })
}

New-Item -ItemType Directory -Force -Path (Split-Path $OutCsv) | Out-Null
$rows | Export-Csv -Path $OutCsv -NoTypeInformation -Encoding UTF8
$ok = $rows | Where-Object { $_.Status -eq 'OK' }
$sorted = @($ok | Sort-Object Ms)
if ($sorted.Count -gt 0) {
    $p50 = $sorted[[int][Math]::Floor(($sorted.Count - 1) * 0.5)].Ms
    $p95 = $sorted[[int][Math]::Ceiling($sorted.Count * 0.95) - 1].Ms
    Write-Output ("G4 canvas drag: {0}/{1} OK, dirty-visible P50={2}ms P95={3}ms; save roundtrip P50={4}ms; samples in {5}" -f $ok.Count, $rows.Count, $p50, $p95, (@($ok | Sort-Object SaveMs)[[int][Math]::Floor(($ok.Count - 1) / 2)].SaveMs), $OutCsv)
} else {
    Write-Output "G4: all samples FAILED — kept in $OutCsv"
    $rows | Format-Table -AutoSize | Out-String | Write-Output
}
