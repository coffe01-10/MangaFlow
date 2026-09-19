param(
    [int]$Samples = 20,
    [string]$OutCsv = "D:\自媒体\漫画工作流\output\nui67-acceptance\run-p2\perf\g4-workflow-layout.csv"
)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes

# G4(工作流画布)口径:纯 UIA 通道。Invoke「自动布局」(真实画布操作,重排节点
# 并置脏),从 Invoke 起计时,轮询状态条出现「待保存」为止;随后 Invoke「保存」
# 等回「已保存」。测的是「画布操作派发 → 脏状态可见」的交互延迟,不是 GPU 帧时间
# (呈现层采样无工具,BLOCKED,见台账 G1)。全部样本保留,失败不剔除。

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
function Status-Text([System.Windows.Automation.AutomationElement]$win) {
    if (Find-ByName $win '待保存') { return 'DIRTY' }
    if (Find-ByName $win '已保存') { return 'CLEAN' }
    return 'UNKNOWN'
}

$rows = New-Object System.Collections.Generic.List[object]
for ($i = 1; $i -le $Samples; $i++) {
    $btn = $null
    $wait = [System.Diagnostics.Stopwatch]::StartNew()
    while ($wait.ElapsedMilliseconds -lt 15000) {
        $btn = Find-ByName $win '自动布局'
        if ($btn) { break }
        Start-Sleep -Milliseconds 200
    }
    if (-not $btn) { $rows.Add([pscustomobject]@{ Sample = $i; Status = 'FAIL_NO_BUTTON'; DirtyMs = -1 }); continue }
    $watch = [System.Diagnostics.Stopwatch]::StartNew()
    try { $btn.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern).Invoke() } catch { $rows.Add([pscustomobject]@{ Sample = $i; Status = 'FAIL_INVOKE'; DirtyMs = -1 }); continue }
    $dirtyAt = -1
    while ($watch.ElapsedMilliseconds -lt 8000) {
        if ((Status-Text $win) -eq 'DIRTY') { $dirtyAt = $watch.ElapsedMilliseconds; break }
        Start-Sleep -Milliseconds 8
    }
    if ($dirtyAt -lt 0) { $rows.Add([pscustomobject]@{ Sample = $i; Status = 'FAIL_DIRTY_TIMEOUT'; DirtyMs = -1 }); continue }
    $saveBtn = Find-ByName $win '保存'
    $savedAt = -1
    if ($saveBtn) {
        $saveBtn.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern).Invoke()
        while ($watch.ElapsedMilliseconds -lt 15000) {
            if ((Status-Text $win) -eq 'CLEAN') { $savedAt = $watch.ElapsedMilliseconds; break }
            Start-Sleep -Milliseconds 8
        }
    }
    $rows.Add([pscustomobject]@{ Sample = $i; Status = 'OK'; DirtyMs = $dirtyAt; SaveMs = $savedAt })
}

New-Item -ItemType Directory -Force -Path (Split-Path $OutCsv) | Out-Null
$rows | Export-Csv -Path $OutCsv -NoTypeInformation -Encoding UTF8
$ok = $rows | Where-Object { $_.Status -eq 'OK' }
$sorted = @($ok | Sort-Object DirtyMs)
if ($sorted.Count -gt 0) {
    $p50 = $sorted[[int][Math]::Floor(($sorted.Count - 1) * 0.5)].DirtyMs
    $p95 = $sorted[[int][Math]::Ceiling($sorted.Count * 0.95) - 1].DirtyMs
    Write-Output ("G4 workflow auto-layout: {0}/{1} OK, dirty-visible P50={2}ms P95={3}ms; samples in {4}" -f $ok.Count, $rows.Count, $p50, $p95, $OutCsv)
} else {
    Write-Output "G4 workflow: all samples FAILED — kept in $OutCsv"
    $rows | Format-Table -AutoSize | Out-String | Write-Output
}
