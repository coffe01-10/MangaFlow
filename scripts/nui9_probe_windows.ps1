$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
$proc = Get-Process MangaFlow.Native -ErrorAction Stop | Select-Object -First 1
$root = [System.Windows.Automation.AutomationElement]::RootElement
$cond = New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ProcessIdProperty, $proc.Id)
$win = $root.FindFirst([System.Windows.Automation.TreeScope]::Children, $cond)
$r = $win.Current.BoundingRectangle
Write-Output ("MangaFlow window rect: " + [int]$r.X + "," + [int]$r.Y + " " + [int]$r.Width + "x" + [int]$r.Height)
# ZCode window rect
$zproc = Get-Process ZCode -ErrorAction SilentlyContinue | Select-Object -First 1
if ($zproc) {
    $zcond = New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ProcessIdProperty, $zproc.Id)
    $zwin = $root.FindFirst([System.Windows.Automation.TreeScope]::Children, $zcond)
    if ($zwin) {
        $zr = $zwin.Current.BoundingRectangle
        Write-Output ("ZCode window rect: " + [int]$zr.X + "," + [int]$zr.Y + " " + [int]$zr.Width + "x" + [int]$zr.Height)
    } else { Write-Output 'ZCode: no top window' }
} else { Write-Output 'ZCode: no process' }
