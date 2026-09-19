param(
    [string]$NameFilter = '',
    [switch]$OnlyInteractive
)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes

# Read-only UIA dump: prints ControlType/Name/BoundingRectangle for every descendant
# of the MangaFlow main window so acceptance scripts can compute exact screen points
# for canvas gestures instead of guessing them from a screenshot. No input is sent.

$proc = Get-Process MangaFlow.Native -ErrorAction Stop | Select-Object -First 1
$root = [System.Windows.Automation.AutomationElement]::RootElement
$cond = New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ProcessIdProperty, $proc.Id)
$win = $root.FindFirst([System.Windows.Automation.TreeScope]::Children, $cond)
if (-not $win) { throw 'main window not found' }

$all = $win.FindAll([System.Windows.Automation.TreeScope]::Descendants, [System.Windows.Automation.Condition]::TrueCondition)
foreach ($el in $all) {
    $r = $el.Current.BoundingRectangle
    $type = $el.Current.ControlType.ProgrammaticName -replace 'ControlType\.', ''
    $name = $el.Current.Name
    if ($OnlyInteractive -and -not $el.GetSupportedPatterns()) { continue }
    if ($NameFilter -and $name -notlike "*$NameFilter*") { continue }
    if ($r.Width -le 0 -or $r.Height -le 0) { continue }
    '{0}|{1}|x={2} y={3} w={4} h={5}|center=({6},{7})' -f $type, $name, [int]$r.X, [int]$r.Y, [int]$r.Width, [int]$r.Height, [int]($r.X + $r.Width / 2), [int]($r.Y + $r.Height / 2)
}
