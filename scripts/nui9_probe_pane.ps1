$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
$proc = Get-Process MangaFlow.Native -ErrorAction Stop | Select-Object -First 1
$root = [System.Windows.Automation.AutomationElement]::RootElement
$cond = New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ProcessIdProperty, $proc.Id)
$win = $root.FindFirst([System.Windows.Automation.TreeScope]::Children, $cond)
$all = $win.FindAll([System.Windows.Automation.TreeScope]::Descendants, [System.Windows.Automation.Condition]::TrueCondition)
$pane = $null
foreach ($el in $all) {
    $r = $el.Current.BoundingRectangle
    if ($el.Current.ControlType.ProgrammaticName -match 'Pane' -and [int]$r.X -eq 562 -and [int]$r.Y -eq 606) { $pane = $el; break }
}
if (-not $pane) { throw 'viewport pane not found' }
function Walk($el, $depth) {
    if ($depth -gt 4) { return }
    $kids = $el.FindAll([System.Windows.Automation.TreeScope]::Children, [System.Windows.Automation.Condition]::TrueCondition)
    foreach ($k in $kids) {
        $r = $k.Current.BoundingRectangle
        if ($r.Width -gt 40 -and $r.Height -gt 40) {
            Write-Output (("  " * $depth) + $k.Current.ControlType.ProgrammaticName + " [" + $k.Current.Name.Substring(0, [Math]::Min(24, $k.Current.Name.Length)) + "] " + [int]$r.X + "," + [int]$r.Y + " " + [int]$r.Width + "x" + [int]$r.Height)
            Walk $k ($depth + 1)
        }
    }
}
Walk $pane 0
