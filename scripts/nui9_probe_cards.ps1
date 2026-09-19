$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
$proc = Get-Process MangaFlow.Native -ErrorAction Stop | Select-Object -First 1
$root = [System.Windows.Automation.AutomationElement]::RootElement
$cond = New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ProcessIdProperty, $proc.Id)
$win = $root.FindFirst([System.Windows.Automation.TreeScope]::Children, $cond)
$listCond = New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ControlTypeProperty, [System.Windows.Automation.ControlType]::List)
$list = $win.FindFirst([System.Windows.Automation.TreeScope]::Descendants, $listCond)
if (-not $list) { throw 'list not found' }
Write-Output ("list [" + $list.Current.Name + "] rect=" + $list.Current.BoundingRectangle.ToString())
$items = $list.FindAll([System.Windows.Automation.TreeScope]::Children, [System.Windows.Automation.Condition]::TrueCondition)
Write-Output ("children: " + $items.Count)
foreach ($item in $items) {
    Write-Output ("item [" + $item.Current.Name.Substring(0, [Math]::Min(40, $item.Current.Name.Length)) + "] " + $item.Current.ControlType.ProgrammaticName)
    $inner = $item.FindAll([System.Windows.Automation.TreeScope]::Children, [System.Windows.Automation.Condition]::TrueCondition)
    foreach ($c in $inner) {
        Write-Output ("  child [" + $c.Current.Name.Substring(0, [Math]::Min(40, $c.Current.Name.Length)) + "] " + $c.Current.ControlType.ProgrammaticName + " rect=" + $c.Current.BoundingRectangle.ToString())
    }
}
