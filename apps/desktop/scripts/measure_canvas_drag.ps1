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
    $el = Find-ByName $win '有未保存修改 · 当前 V2'
    if ($el) { return 'DIRTY' }
    $el2 = Find-ByName $win ('已保存 · 当前 V' + '2')
    if ($el2) { return 'CLEAN' }
    $el3 = Find-ByName $win '已保存 · 当前 V1'
    if ($el3) { return 'CLEAN' }
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

# 面板 P.001 拖块在画布上的屏幕坐标(125% 缩放,25% zoom;与截图核对过)
$panelX = 885; $panelY = 735
$direction = 1
$rows = New-Object System.Collections.Generic.List[object]

# warm-up 1 轮
Drag $panelX $panelY ($panelX + 60) $panelY
Start-Sleep -Milliseconds 300
$saveBtn = Find-ByName $win '保存本页'
if ($saveBtn) { $saveBtn.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern).Invoke() }
Start-Sleep -Milliseconds 800

for ($i = 1; $i -le $Samples; $i++) {
    $x1 = $panelX; $x2 = $panelX + 60 * $direction
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
