$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
$proc = Get-Process MangaFlow.Native -ErrorAction Stop | Select-Object -First 1
$root = [System.Windows.Automation.AutomationElement]::RootElement
$cond = New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ProcessIdProperty, $proc.Id)
$win = $root.FindFirst([System.Windows.Automation.TreeScope]::Children, $cond)
Start-Sleep -Milliseconds 800
$all = $win.FindAll([System.Windows.Automation.TreeScope]::Descendants, [System.Windows.Automation.Condition]::TrueCondition)
$exp = $null
foreach ($el in $all) {
    if ($el.Current.Name -eq '导出 CSV' -and $el.Current.ControlType.ProgrammaticName -match 'Button') {
        $r = $el.Current.BoundingRectangle
        if ($r.Width -gt 0) { $exp = @($el, $r); break }
    }
}
if (-not $exp) { throw '导出 CSV not found' }
Write-Output ("导出 CSV at " + [int]$exp[1].X + "," + [int]$exp[1].Y)
Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
public static class N9E {
    [DllImport("user32.dll")] public static extern bool SetCursorPos(int x, int y);
    [DllImport("user32.dll")] public static extern void mouse_event(uint f, uint dx, uint dy, uint data, UIntPtr extra);
}
"@
[N9E]::SetCursorPos([int]($exp[1].X + $exp[1].Width / 2), [int]($exp[1].Y + $exp[1].Height / 2)) | Out-Null
Start-Sleep -Milliseconds 150
[N9E]::mouse_event(2, 0, 0, 0, [UIntPtr]::Zero)
[N9E]::mouse_event(4, 0, 0, 0, [UIntPtr]::Zero)
Start-Sleep -Milliseconds 1200
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
