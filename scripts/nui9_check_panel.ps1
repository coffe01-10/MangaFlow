$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
$proc = Get-Process MangaFlow.Native -ErrorAction Stop | Select-Object -First 1
$root = [System.Windows.Automation.AutomationElement]::RootElement
$cond = New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ProcessIdProperty, $proc.Id)
$win = $root.FindFirst([System.Windows.Automation.TreeScope]::Children, $cond)
$all = $win.FindAll([System.Windows.Automation.TreeScope]::Descendants, [System.Windows.Automation.Condition]::TrueCondition)
foreach ($el in $all) {
    $n = $el.Current.Name
    if ($n -like 'P.001 / PANEL*' -or $n -like '*几何（只读）*' -or $n -like 'X *% · Y *%*') {
        Write-Output ("FOUND: [" + $n.Substring(0, [Math]::Min(70, $n.Length)) + "]")
    }
}
Write-Output 'probe-done'
