$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes
$proc = Get-Process MangaFlow.Native -ErrorAction Stop | Select-Object -First 1
$root = [System.Windows.Automation.AutomationElement]::RootElement
$cond = New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ProcessIdProperty, $proc.Id)
$win = $root.FindFirst([System.Windows.Automation.TreeScope]::Children, $cond)
$all = $win.FindAll([System.Windows.Automation.TreeScope]::Descendants, [System.Windows.Automation.Condition]::TrueCondition)
Write-Output ("total elements: " + $all.Count)
foreach ($el in $all) {
    $n = $el.Current.Name
    if ($n -and $n -match '已接通') {
        Write-Output ("ROW: [" + $n + "] class=" + $el.Current.ClassName)
    }
}
$bc = $all | Where-Object { $_.Current.Name -and $_.Current.Name -match 'NUI67' } | Select-Object -First 8
foreach ($b in $bc) { Write-Output ("BC: [" + $b.Current.Name + "]") }
