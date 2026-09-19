$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes
$proc = Get-Process MangaFlow.Native -ErrorAction Stop | Select-Object -First 1
$root = [System.Windows.Automation.AutomationElement]::RootElement
$cond = New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ProcessIdProperty, $proc.Id)
$win = $root.FindFirst([System.Windows.Automation.TreeScope]::Children, $cond)
Write-Output ("win: " + $win.Current.Name)
$name = '原作与修订，已接通'
Write-Output ("searching name length: " + $name.Length + " codes: " + (([int[]][char[]]$name | ForEach-Object { $_.ToString('X4') }) -join ' '))
$cond2 = New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::NameProperty, $name)
$found = $win.FindFirst([System.Windows.Automation.TreeScope]::Descendants, $cond2)
if ($found) { Write-Output ("FOUND: " + $found.Current.Name) } else { Write-Output "NOT FOUND by PropertyCondition" }
# fallback: regex walk
$all = $win.FindAll([System.Windows.Automation.TreeScope]::Descendants, [System.Windows.Automation.Condition]::TrueCondition)
foreach ($el in $all) {
    $n = $el.Current.Name
    if ($n -eq $name) {
        Write-Output ("WALK FOUND: class=" + $el.Current.ClassName + " hasSelection=" + $el.GetSupportedPatterns().Count)
        $sel = $null
        if ($el.TryGetCurrentPattern([System.Windows.Automation.SelectionItemPattern]::Pattern, [ref]$sel)) { Write-Output "SelectionItemPattern available" }
        break
    }
}
