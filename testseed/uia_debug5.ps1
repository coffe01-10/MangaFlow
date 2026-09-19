$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes
$proc = Get-Process MangaFlow.Native -ErrorAction Stop | Select-Object -First 1
$root = [System.Windows.Automation.AutomationElement]::RootElement
$cond = New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ProcessIdProperty, $proc.Id)
$wins = $root.FindAll([System.Windows.Automation.TreeScope]::Children, $cond)
Write-Output ("top-level windows for pid: " + $wins.Count)
foreach ($w in $wins) {
    Write-Output ("WIN: [" + $w.Current.Name + "] class=" + $w.Current.ClassName)
}
$win = $root.FindFirst([System.Windows.Automation.TreeScope]::Children, $cond)
$all = $win.FindAll([System.Windows.Automation.TreeScope]::Descendants, [System.Windows.Automation.Condition]::TrueCondition)
Write-Output ("elements in first window: " + $all.Count)
foreach ($el in $all) {
    $n = $el.Current.Name
    if ($n -and ($n -match '布局' -or $n -match '保存' -or $n -match '待' -or $n -match '已')) {
        Write-Output ("EL: [" + $n + "] class=" + $el.Current.ClassName)
    }
}
