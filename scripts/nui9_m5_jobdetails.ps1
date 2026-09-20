$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
Add-Type -AssemblyName System.Windows.Forms
$proc = Get-Process MangaFlow.Native -ErrorAction Stop | Select-Object -First 1
$root = [System.Windows.Automation.AutomationElement]::RootElement
$cond = New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ProcessIdProperty, $proc.Id)
$win = $root.FindFirst([System.Windows.Automation.TreeScope]::Children, $cond)
$all = $win.FindAll([System.Windows.Automation.TreeScope]::Descendants, [System.Windows.Automation.Condition]::TrueCondition)
$btn = $null
foreach ($el in $all) {
    if ($el.Current.Name -eq '调用与成本' -and $el.Current.ControlType.ProgrammaticName -match 'Button') {
        $r = $el.Current.BoundingRectangle
        if ($r.Width -gt 0 -and $r.Y -gt 200) { $btn = @($el, $r); break }
    }
}
if (-not $btn) { throw '调用与成本 not found' }
Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
public static class N9I {
    [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
    [DllImport("user32.dll")] public static extern bool SetCursorPos(int x, int y);
    [DllImport("user32.dll")] public static extern void mouse_event(uint f, uint dx, uint dy, uint data, UIntPtr extra);
}
"@
[N9I]::SetForegroundWindow($proc.MainWindowHandle) | Out-Null
Start-Sleep -Milliseconds 400
$x = [int]($btn[1].X + $btn[1].Width / 2); $y = [int]($btn[1].Y + $btn[1].Height / 2)
[N9I]::SetCursorPos($x, $y) | Out-Null
Start-Sleep -Milliseconds 120
[N9I]::mouse_event(2, 0, 0, 0, [UIntPtr]::Zero)
[N9I]::mouse_event(4, 0, 0, 0, [UIntPtr]::Zero)
Start-Sleep -Milliseconds 1500
$f = [System.Windows.Automation.AutomationElement]::FocusedElement
Write-Output ("focus: [" + $f.Current.Name + "] pid=" + $f.Current.ProcessId)
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
$btns = 0
foreach ($d in $desc) {
    if ($d.Current.ControlType.ProgrammaticName -match 'Button') { $btns++ }
}
Write-Output ("buttons in window: " + $btns)
Add-Type -AssemblyName System.Windows.Forms
[System.Windows.Forms.SendKeys]::SendWait('{ESC}')
Start-Sleep -Milliseconds 900
$f2 = [System.Windows.Automation.AutomationElement]::FocusedElement
Write-Output ("after-esc focus: [" + $f2.Current.Name + "]")
