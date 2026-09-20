$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
Add-Type -AssemblyName System.Windows.Forms
Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
public static class N9H {
    [DllImport("user32.dll")] public static extern bool SetCursorPos(int x, int y);
    [DllImport("user32.dll")] public static extern void mouse_event(uint f, uint dx, uint dy, uint data, UIntPtr extra);
}
"@
function Click([int]$x, [int]$y) {
    [N9H]::SetCursorPos($x, $y) | Out-Null
    Start-Sleep -Milliseconds 120
    [N9H]::mouse_event(2, 0, 0, 0, [UIntPtr]::Zero)
    [N9H]::mouse_event(4, 0, 0, 0, [UIntPtr]::Zero)
}
$proc = Get-Process MangaFlow.Native -ErrorAction Stop | Select-Object -First 1
$root = [System.Windows.Automation.AutomationElement]::RootElement
$cond = New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ProcessIdProperty, $proc.Id)
$win = $root.FindFirst([System.Windows.Automation.TreeScope]::Children, $cond)

# 1. 选真实角色
$combo = $win.FindFirst([System.Windows.Automation.TreeScope]::Descendants,
    (New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::NameProperty, '选择要创建模型包的角色')))
if (-not $combo) { throw 'combo not found' }
$ec = $combo.GetCurrentPattern([System.Windows.Automation.ExpandCollapsePattern]::Pattern)
$ec.Expand()
Start-Sleep -Milliseconds 900
$items = $combo.FindAll([System.Windows.Automation.TreeScope]::Descendants,
    (New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ControlTypeProperty, [System.Windows.Automation.ControlType]::ListItem)))
$pick = $null
foreach ($it in $items) {
    if (-not $pick -and $it.Current.Name -notlike '*还没有模型包*' -and $it.Current.Name -notlike '*选择角色*') { $pick = $it }
}
if (-not $pick) { $ec.Collapse() | Out-Null; throw 'no real character item found' }
Write-Output ("picking: [" + $pick.Current.Name + "]")
$pick.GetCurrentPattern([System.Windows.Automation.SelectionItemPattern]::Pattern).Select()
Start-Sleep -Milliseconds 800
$ec.Collapse() | Out-Null
Start-Sleep -Milliseconds 600

# 2. 找上传区元素并 ScrollIntoView
$all = $win.FindAll([System.Windows.Automation.TreeScope]::Descendants, [System.Windows.Automation.Condition]::TrueCondition)
$zone = $null
foreach ($el in $all) {
    if ($el.Current.Name -like '拖拽图片到这里*') {
        $r = $el.Current.BoundingRectangle
        if ($r.Width -gt 0) { $zone = @($el, $r); break }
    }
}
if (-not $zone) { throw 'upload zone not found' }
$si = $null
if ($zone[0].TryGetCurrentPattern([System.Windows.Automation.ScrollItemPattern]::Pattern, [ref]$si)) {
    $si.ScrollIntoView()
    Start-Sleep -Milliseconds 800
    $zone[1] = $zone[0].Current.BoundingRectangle
    Write-Output ("zone scrolled into view: " + [int]$zone[1].X + "," + [int]$zone[1].Y)
}
if ($zone[1].Y -lt 100 -or ($zone[1].Y + $zone[1].Height) -gt 1010) { throw ("zone still not in visible area: " + $zone[1].Y) }

# 3. 点击上传区 → 真实文件对话框
Click ([int]($zone[1].X + $zone[1].Width / 2)) ([int]($zone[1].Y + $zone[1].Height / 2))
Start-Sleep -Milliseconds 1800
$f = [System.Windows.Automation.AutomationElement]::FocusedElement
if ($f.Current.ProcessId -ne $proc.Id) { throw ("foreground is not MangaFlow (focused in " + $f.Current.ProcessId + ") — dialog did not open; ABORT") }
Write-Output 'file dialog open (foreground ok)'
[System.Windows.Forms.SendKeys]::SendWait('C:\自媒体\漫画工作流\output\nui9-rc\p4-3\nui9-test-ref.png')
Start-Sleep -Milliseconds 500
[System.Windows.Forms.SendKeys]::SendWait('{ENTER}')
Start-Sleep -Milliseconds 3000

# 4. 验证上传
$all2 = $win.FindAll([System.Windows.Automation.TreeScope]::Descendants, [System.Windows.Automation.Condition]::TrueCondition)
$found = @()
foreach ($el in $all2) {
    $n = $el.Current.Name
    if ($n -like '*nui9-test-ref*' -or $n -like '*上传*') { $found += $n }
}
if ($found.Count -gt 0) { $found | Select-Object -First 4 | ForEach-Object { Write-Output ("RESULT: " + $_.Substring(0, [Math]::Min(80, $_.Length))) } }
else { Write-Output 'RESULT: no upload-related element found' }
