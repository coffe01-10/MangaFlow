$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
public static class N9D {
    [DllImport("user32.dll")] public static extern bool SetCursorPos(int x, int y);
    [DllImport("user32.dll")] public static extern void mouse_event(uint f, uint dx, uint dy, uint data, UIntPtr extra);
}
"@
function Click([int]$x, [int]$y) {
    [N9D]::SetCursorPos($x, $y) | Out-Null
    Start-Sleep -Milliseconds 120
    [N9D]::mouse_event(2, 0, 0, 0, [UIntPtr]::Zero)
    [N9D]::mouse_event(4, 0, 0, 0, [UIntPtr]::Zero)
}
function FindButton([string]$name) {
    $all = $win.FindAll([System.Windows.Automation.TreeScope]::Descendants, [System.Windows.Automation.Condition]::TrueCondition)
    foreach ($el in $all) {
        if ($el.Current.Name -eq $name -and $el.Current.ControlType.ProgrammaticName -match 'Button') {
            $r = $el.Current.BoundingRectangle
            if ($r.Width -gt 0) { return @($el, $r) }
        }
    }
    return $null
}
$proc = Get-Process MangaFlow.Native -ErrorAction Stop | Select-Object -First 1
$root = [System.Windows.Automation.AutomationElement]::RootElement
$cond = New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ProcessIdProperty, $proc.Id)
$win = $root.FindFirst([System.Windows.Automation.TreeScope]::Children, $cond)

$nav = FindButton '设置'
if ($nav) { Click ([int]($nav[1].X + $nav[1].Width / 2)) ([int]($nav[1].Y + $nav[1].Height / 2)); Start-Sleep -Milliseconds 1500 }
$usage = FindButton '用量与成本看板'
if (-not $usage) { throw '用量与成本看板 not found' }
Click ([int]($usage[1].X + $usage[1].Width / 2)) ([int]($usage[1].Y + $usage[1].Height / 2))
Start-Sleep -Milliseconds 1800

$exp = $null
foreach ($round in 1..3) {
    $exp = FindButton '导出 CSV'
    if ($exp) { break }
    Click 900 500
    foreach ($i in 1..4) { [N9D]::mouse_event(0x0800, 0, 0, [uint32]4287100000, [UIntPtr]::Zero); Start-Sleep -Milliseconds 60 }
    Start-Sleep -Milliseconds 400
}
if (-not $exp) { throw '导出 CSV not found' }
Write-Output ("导出 CSV at " + [int]$exp[1].X + "," + [int]$exp[1].Y)
Click ([int]($exp[1].X + $exp[1].Width / 2)) ([int]($exp[1].Y + $exp[1].Height / 2))
Start-Sleep -Milliseconds 1500
$f = [System.Windows.Automation.AutomationElement]::FocusedElement
Write-Output ("focus: [" + $f.Current.Name + "]")
$walker = [System.Windows.Automation.TreeWalker]::ControlViewWalker
$node = $f
while ($true) {
    if ($node.Current.ControlType.ProgrammaticName -match 'Window') { break }
    $parent = $walker.GetParent($node)
    if (-not $parent) { break }
    $node = $parent
}
Write-Output ("window: [" + $node.Current.Name + "]")
$desc = $node.FindAll([System.Windows.Automation.TreeScope]::Descendants, [System.Windows.Automation.Condition]::TrueCondition)
foreach ($d in $desc) {
    if ($d.Current.ControlType.ProgrammaticName -match 'Button|Text') { Write-Output ("  " + $d.Current.ControlType.ProgrammaticName + " [" + $d.Current.Name + "]") }
}
Add-Type -AssemblyName System.Windows.Forms
[System.Windows.Forms.SendKeys]::SendWait('{ENTER}')
Start-Sleep -Milliseconds 600
Write-Output 'dismissed'
