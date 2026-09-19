$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes
$proc = Get-Process MangaFlow.Native -ErrorAction Stop | Select-Object -First 1
$root = [System.Windows.Automation.AutomationElement]::RootElement
$cond = New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ProcessIdProperty, $proc.Id)
$win = $root.FindFirst([System.Windows.Automation.TreeScope]::Children, $cond)
$all = $win.FindAll([System.Windows.Automation.TreeScope]::Descendants, [System.Windows.Automation.Condition]::TrueCondition)
Write-Output ("total: " + $all.Count + " pid=" + $proc.Id)
$seen = @{}
foreach ($el in $all) {
    $c = $el.Current
    $key = $c.ClassName + "|" + $c.Name
    if ($seen.ContainsKey($key)) { continue }
    $seen[$key] = 1
    if ($c.Name -and $c.Name.Length -lt 40) { Write-Output ($c.ClassName + " :: [" + $c.Name + "]") }
}
