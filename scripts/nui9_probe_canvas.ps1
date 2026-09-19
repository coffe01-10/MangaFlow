$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
$proc = Get-Process MangaFlow.Native -ErrorAction Stop | Select-Object -First 1
$root = [System.Windows.Automation.AutomationElement]::RootElement
$cond = New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ProcessIdProperty, $proc.Id)
$win = $root.FindFirst([System.Windows.Automation.TreeScope]::Children, $cond)
# find the large Custom canvas host
$all = $win.FindAll([System.Windows.Automation.TreeScope]::Descendants, [System.Windows.Automation.Condition]::TrueCondition)
$canvas = $null; $best = 0
foreach ($el in $all) {
    $r = $el.Current.BoundingRectangle
    if ($el.Current.ControlType.ProgrammaticName -match 'Custom' -and $r.Width -gt 800 -and $r.Height -gt 500 -and $r.Width * $r.Height -gt $best) { $canvas = $el; $best = $r.Width * $r.Height }
}
Write-Output ("canvas: " + $canvas.Current.BoundingRectangle.ToString())
$walker = [System.Windows.Automation.TreeWalker]::ControlViewWalker
$kids = $canvas.FindAll([System.Windows.Automation.TreeScope]::Children, [System.Windows.Automation.Condition]::TrueCondition)
Write-Output ("canvas children: " + $kids.Count)
foreach ($k in $kids) { Write-Output ("  " + $k.Current.ControlType.ProgrammaticName + " [" + $k.Current.Name.Substring(0, [Math]::Min(30, $k.Current.Name.Length)) + "] " + $k.Current.BoundingRectangle.ToString()) }
# walk two more levels looking for page-sized elements
foreach ($k in $kids) {
    $gkids = $k.FindAll([System.Windows.Automation.TreeScope]::Children, [System.Windows.Automation.Condition]::TrueCondition)
    foreach ($g in $gkids) {
        $r = $g.Current.BoundingRectangle
        if ($r.Width -gt 60 -and $r.Height -gt 80) { Write-Output ("  gchild " + $g.Current.ControlType.ProgrammaticName + " [" + $g.Current.Name.Substring(0, [Math]::Min(30, $g.Current.Name.Length)) + "] " + $r.ToString()) }
    }
}
