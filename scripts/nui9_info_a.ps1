$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
public static class N9B {
    [DllImport("user32.dll")] public static extern bool SetCursorPos(int x, int y);
    [DllImport("user32.dll")] public static extern void mouse_event(uint f, uint dx, uint dy, uint data, UIntPtr extra);
}
"@
function Click([int]$x, [int]$y) {
    [N9B]::SetCursorPos($x, $y) | Out-Null
    Start-Sleep -Milliseconds 120
    [N9B]::mouse_event(2, 0, 0, 0, [UIntPtr]::Zero)
    [N9B]::mouse_event(4, 0, 0, 0, [UIntPtr]::Zero)
}
$proc = Get-Process MangaFlow.Native -ErrorAction Stop | Select-Object -First 1
$root = [System.Windows.Automation.AutomationElement]::RootElement
$cond = New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ProcessIdProperty, $proc.Id)
$win = $root.FindFirst([System.Windows.Automation.TreeScope]::Children, $cond)
Start-Sleep -Milliseconds 500

# 找删除项目按钮（危险区），先滚到底
$order = $null
$all = $win.FindAll([System.Windows.Automation.TreeScope]::Descendants, [System.Windows.Automation.Condition]::TrueCondition)
$del = $null
foreach ($el in $all) {
    $n = $el.Current.Name
    if ($n -eq '删除项目') {
        $r = $el.Current.BoundingRectangle
        if ($r.Y -gt 150 -and $r.Bottom -lt 990) { $del = $el; $delRect = $r; break }
    }
}
if (-not $del) {
    # 滚动几次再找
    foreach ($round in 1..5) {
        Click 900 600; Start-Sleep -Milliseconds 150
        foreach ($i in 1..4) { [N9B]::mouse_event(0x0800, 0, 0, [uint32]4287100000, [UIntPtr]::Zero); Start-Sleep -Milliseconds 60 }
        Start-Sleep -Milliseconds 400
        $all = $win.FindAll([System.Windows.Automation.TreeScope]::Descendants, [System.Windows.Automation.Condition]::TrueCondition)
        foreach ($el in $all) {
            $n = $el.Current.Name
            if ($n -eq '删除项目') {
                $r = $el.Current.BoundingRectangle
                if ($r.Y -gt 150 -and $r.Bottom -lt 990) { $del = $el; $delRect = $r; break }
            }
        }
        if ($del) { break }
    }
}
if (-not $del) { throw 'visible 删除项目 button not found' }
Write-Output ("删除项目 at " + [int]$delRect.X + "," + [int]$delRect.Y)
Click ([int]($delRect.X + $delRect.Width / 2)) ([int]($delRect.Y + $delRect.Height / 2))
Start-Sleep -Milliseconds 900
$f = [System.Windows.Automation.AutomationElement]::FocusedElement
Write-Output ("focus: [" + $f.Current.Name + "] type=" + $f.Current.ControlType.ProgrammaticName)
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
    if ($d.Current.ControlType.ProgrammaticName -match 'Button') { Write-Output ("  Button [" + $d.Current.Name + "]") }
}
Add-Type -AssemblyName System.Windows.Forms
[System.Windows.Forms.SendKeys]::SendWait('{ENTER}')
Start-Sleep -Milliseconds 600
Write-Output 'dismissed (OK/Enter)'
