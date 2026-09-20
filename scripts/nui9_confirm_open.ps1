Add-Type -AssemblyName System.Windows.Forms
[System.Windows.Forms.SendKeys]::SendWait('{ENTER}')
Start-Sleep -Milliseconds 3000
$proc = Get-Process MangaFlow.Native -ErrorAction Stop | Select-Object -First 1
$root = [System.Windows.Automation.AutomationElement]::RootElement
$cond = New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ProcessIdProperty, $proc.Id)
$win = $root.FindFirst([System.Windows.Automation.TreeScope]::Children, $cond)
$all = $win.FindAll([System.Windows.Automation.TreeScope]::Descendants, [System.Windows.Automation.Condition]::TrueCondition)
$found = @()
foreach ($el in $all) {
    $n = $el.Current.Name
    if ($n -like '*nui9-test-ref*' -or $n -like '*上传*') { $found += $n }
}
if ($found.Count -gt 0) { $found | Select-Object -First 6 | ForEach-Object { Write-Output ("RESULT: " + $_.Substring(0, [Math]::Min(90, $_.Length))) } }
else { Write-Output 'RESULT: no upload element found' }
