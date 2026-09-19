$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
public static class N9 {
    [DllImport("user32.dll")] public static extern bool SetCursorPos(int x, int y);
    [DllImport("user32.dll")] public static extern void mouse_event(uint f, uint dx, uint dy, uint data, UIntPtr extra);
}
"@
function Click([int]$x, [int]$y) {
    [N9]::SetCursorPos($x, $y) | Out-Null
    Start-Sleep -Milliseconds 120
    [N9]::mouse_event(2, 0, 0, 0, [UIntPtr]::Zero)
    [N9]::mouse_event(4, 0, 0, 0, [UIntPtr]::Zero)
}
function Wheel([int]$x, [int]$y, [int]$ticks) {
    [N9]::SetCursorPos($x, $y) | Out-Null
    Start-Sleep -Milliseconds 120
    foreach ($i in 1..[Math]::Abs($ticks)) {
        $d = [uint32]4287100000
        if ($ticks -gt 0) { $d = [uint32]120 }
        [N9]::mouse_event(0x0800, 0, 0, $d, [UIntPtr]::Zero)
        Start-Sleep -Milliseconds 80
    }
}
$proc = Get-Process MangaFlow.Native -ErrorAction Stop | Select-Object -First 1
$root = [System.Windows.Automation.AutomationElement]::RootElement
$cond = New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ProcessIdProperty, $proc.Id)
$win = $root.FindFirst([System.Windows.Automation.TreeScope]::Children, $cond)

# 滚到素材库列表下部的可见删除按钮
Wheel 652 800 -6
Start-Sleep -Milliseconds 500
$all = $win.FindAll([System.Windows.Automation.TreeScope]::Descendants, [System.Windows.Automation.Condition]::TrueCondition)
$target = $null
foreach ($el in $all) {
    if ($el.Current.Name -eq '删除') {
        $r = $el.Current.BoundingRectangle
        if ($r.Y -gt 200 -and $r.Bottom -lt 980) { $target = $el; break }
    }
}
if (-not $target) { throw 'no visible 删除 button after scroll' }
$r = $target.Current.BoundingRectangle
Write-Output ("clicking 删除 at " + [int]$r.X + "," + [int]$r.Y)
Click ([int]($r.X + $r.Width / 2)) ([int]($r.Y + $r.Height / 2))
Start-Sleep -Milliseconds 900

# 确认对话框应出现（隐藏候选），转储 + Esc 取消
$f = [System.Windows.Automation.AutomationElement]::FocusedElement
Write-Output ("focus after click: [" + $f.Current.Name + "]")
$walker = [System.Windows.Automation.TreeWalker]::ControlViewWalker
$node = $f
while ($true) {
    if ($node.Current.ControlType.ProgrammaticName -match 'Window') { break }
    $parent = $walker.GetParent($node)
    if (-not $parent) { break }
    $node = $parent
}
Write-Output ("dialog window: [" + $node.Current.Name + "]")
$desc = $node.FindAll([System.Windows.Automation.TreeScope]::Descendants, [System.Windows.Automation.Condition]::TrueCondition)
foreach ($d in $desc) {
    $ct = $d.Current.ControlType.ProgrammaticName
    if ($ct -match 'Button') { Write-Output ("  Button [" + $d.Current.Name + "]") }
}
Add-Type -AssemblyName System.Windows.Forms
[System.Windows.Forms.SendKeys]::SendWait('{ESC}')
Start-Sleep -Milliseconds 700
$after = [System.Windows.Automation.AutomationElement]::FocusedElement
Write-Output ("after-esc focus: [" + $after.Current.Name + "]")
