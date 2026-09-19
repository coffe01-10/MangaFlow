$ErrorActionPreference='Stop'
Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes
$proc = Get-Process MangaFlow.Native | Select-Object -First 1
$root = [System.Windows.Automation.AutomationElement]::RootElement
$cond = New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ProcessIdProperty, $proc.Id)
$win = $root.FindFirst([System.Windows.Automation.TreeScope]::Children, $cond)
$all = $win.FindAll([System.Windows.Automation.TreeScope]::Descendants, [System.Windows.Automation.Condition]::TrueCondition)
Write-Output ("total=" + $all.Count)
foreach ($el in $all) {
  $n = $el.Current.Name
  if ($n -and ($n -match '项目|素材库|NUI67')) {
    $ip=$null; $si=$null
    $hasInvoke = $el.TryGetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern,[ref]$ip)
    $hasSelect = $el.TryGetCurrentPattern([System.Windows.Automation.SelectionItemPattern]::Pattern,[ref]$si)
    Write-Output ("[" + $el.Current.ControlType.ProgrammaticName + "] name=<" + $n + "> invoke=" + $hasInvoke + " select=" + $hasSelect)
  }
}
