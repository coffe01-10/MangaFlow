$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
Add-Type -AssemblyName System.Drawing
Add-Type -AssemblyName System.Windows.Forms
Add-Type -TypeDefinition @"
using System; using System.Runtime.InteropServices;
public static class MCS {
    [DllImport("user32.dll")] public static extern bool SetCursorPos(int x, int y);
    [DllImport("user32.dll")] public static extern void mouse_event(uint f, uint dx, uint dy, uint data, UIntPtr extra);
}
"@
# Chinese-free literals (PS5.1 BOM-less files mojibake CJK strings): pick the
# widest ComboBox on the page — the project-settings routing selector.
$root = [System.Windows.Automation.AutomationElement]::RootElement
$proc = Get-Process MangaFlow.Native | Select-Object -First 1
$cond = New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ProcessIdProperty, $proc.Id)
$win = $root.FindFirst([System.Windows.Automation.TreeScope]::Children, $cond)
$cbcond = New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ControlTypeProperty, [System.Windows.Automation.ControlType]::ComboBox)
$combos = $win.FindAll([System.Windows.Automation.TreeScope]::Descendants, $cbcond)
$combo = $null
foreach ($e in $combos) {
    $r = $e.Current.BoundingRectangle
    Write-Output ("candidate: [" + $e.Current.Name + "] " + $r.Width + "x" + $r.Height)
    if ($r.Width -gt 300 -and $combo -eq $null) { $combo = $e }
}
if (-not $combo) { throw 'combo not found' }
# SetFocus (not synthetic click): WPF BringIntoView scrolls the control into
# the ScrollViewer viewport and puts REAL keyboard focus on it in one step.
$combo.SetFocus()
Start-Sleep -Milliseconds 900
$r = $combo.Current.BoundingRectangle
$f = [System.Windows.Automation.AutomationElement]::FocusedElement
Write-Output ("focus: [" + $f.Current.Name + "] " + $f.Current.ControlType.ProgrammaticName + " rect now: " + $r.X + "," + $r.Y + " " + $r.Width + "x" + $r.Height)
$m = 18
$bx = [int]($r.X) - $m; $by = [int]($r.Y) - $m
$bw = [int]($r.Width) + 2 * $m; $bh = [int]($r.Height) + 2 * $m
$bmp = New-Object System.Drawing.Bitmap($bw, $bh)
$g = [System.Drawing.Graphics]::FromImage($bmp)
$g.CopyFromScreen($bx, $by, 0, 0, (New-Object System.Drawing.Size($bw, $bh)))
$bmp.Save('C:\Users\chen\AppData\Local\Temp\combo-shot.png', [System.Drawing.Imaging.ImageFormat]::Png)
$g.Dispose(); $bmp.Dispose()
Write-Output "saved combo shot from $bx,$by ${bw}x${bh}"
