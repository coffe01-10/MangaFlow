$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes
$proc = Get-Process MangaFlow.Native -ErrorAction Stop | Select-Object -First 1
$root = [System.Windows.Automation.AutomationElement]::RootElement
$cond = New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ProcessIdProperty, $proc.Id)
$win = $root.FindFirst([System.Windows.Automation.TreeScope]::Children, $cond)

function Find-Row([System.Windows.Automation.AutomationElement]$win, [string]$page) {
    $target = "$page，已接通"
    $all = $win.FindAll([System.Windows.Automation.TreeScope]::Descendants, [System.Windows.Automation.Condition]::TrueCondition)
    foreach ($el in $all) {
        if ($el.Current.Name -eq $target) { return $el }
    }
    return $null
}
function Get-Breadcrumb([System.Windows.Automation.AutomationElement]$win) {
    $all = $win.FindAll([System.Windows.Automation.TreeScope]::Descendants, [System.Windows.Automation.Condition]::TrueCondition)
    foreach ($el in $all) {
        $n = $el.Current.Name
        if ($n -and $n -match '^NUI67 .+ / .+$') { return $n }
    }
    return '<none>'
}

Write-Output ("before: [" + (Get-Breadcrumb $win) + "]")
$row = Find-Row $win '参考资产'
Write-Output ("row found: " + ($null -ne $row))
$sel = $row.GetCurrentPattern([System.Windows.Automation.SelectionItemPattern]::Pattern)
$sel.Select()
Start-Sleep -Milliseconds 1500
Write-Output ("after 1500ms: [" + (Get-Breadcrumb $win) + "]")
# Also print all texts containing '/'
$all = $win.FindAll([System.Windows.Automation.TreeScope]::Descendants, [System.Windows.Automation.Condition]::TrueCondition)
foreach ($el in $all) {
    $n = $el.Current.Name
    if ($n -and $n.Contains('/') -and $n.Length -lt 60) { Write-Output ("SLASH: [" + $n + "]") }
}
