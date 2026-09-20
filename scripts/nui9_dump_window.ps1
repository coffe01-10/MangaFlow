$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
$root = [System.Windows.Automation.AutomationElement]::RootElement
$proc = Get-Process MangaFlow.Native | Select-Object -First 1
$cond = New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ProcessIdProperty, $proc.Id)
$win = $root.FindFirst([System.Windows.Automation.TreeScope]::Children, $cond)
if (-not $win) { throw 'no window' }
Write-Output ("window: [" + $win.Current.Name + "] rect: " + $win.Current.BoundingRectangle.X + "," + $win.Current.BoundingRectangle.Y)
$desc = $win.FindAll([System.Windows.Automation.TreeScope]::Descendants, [System.Windows.Automation.Condition]::TrueCondition)
Write-Output ("elements: " + $desc.Count)
foreach ($d in $desc) {
    $ct = $d.Current.ControlType.ProgrammaticName
    if ($ct -match 'ComboBox|Edit') {
        $r = $d.Current.BoundingRectangle
        Write-Output ("  $ct [" + $d.Current.Name + "] " + $r.X + "," + $r.Y + " " + $r.Width + "x" + $r.Height)
    }
}
$f = [System.Windows.Automation.AutomationElement]::FocusedElement
Write-Output ("focus: [" + $f.Current.Name + "] pid=" + $f.Current.ProcessId)
