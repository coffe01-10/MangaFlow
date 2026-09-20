$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
Add-Type -AssemblyName System.Windows.Forms
$proc = Get-Process MangaFlow.Native -ErrorAction Stop | Select-Object -First 1
$root = [System.Windows.Automation.AutomationElement]::RootElement
$cond = New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ProcessIdProperty, $proc.Id)
$win = $root.FindFirst([System.Windows.Automation.TreeScope]::Children, $cond)
$all = $win.FindAll([System.Windows.Automation.TreeScope]::Descendants, [System.Windows.Automation.Condition]::TrueCondition)
$edit = $null
foreach ($el in $all) {
    if ($el.Current.ControlType.ProgrammaticName -match 'Edit') {
        $r = $el.Current.BoundingRectangle
        if ($r.Width -gt 150) { $edit = @($el, $r); break }
    }
}
if (-not $edit) { throw 'filename edit not found' }
Write-Output ("edit at " + [int]$edit[1].X + "," + [int]$edit[1].Y + " " + [int]$edit[1].Width + "x" + [int]$edit[1].Height)
$vp = $null
if ($edit[0].TryGetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern, [ref]$vp)) {
    $vp.SetValue('C:\自媒体\漫画工作流\output\nui9-rc\p4-3\nui9-test-ref.png')
    Write-Output 'path set via ValuePattern'
} else {
    throw 'no ValuePattern on filename edit'
}
Start-Sleep -Milliseconds 400
# 确认：点“打开(&O)”按钮
$openBtn = $null
foreach ($el in $all) {
    $n = $el.Current.Name
    if ($n -like '打开*' -and $el.Current.ControlType.ProgrammaticName -match 'Button') {
        $r = $el.Current.BoundingRectangle
        if ($r.Width -gt 0) { $openBtn = @($el, $r); break }
    }
}
if ($openBtn) {
    $ip = $null
    if ($openBtn[0].TryGetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern, [ref]$ip)) {
        $ip.Invoke(); Write-Output 'invoked 打开'
    } else {
        Add-Type -AssemblyName System.Windows.Forms
        [System.Windows.Forms.SendKeys]::SendWait('{ENTER}')
        Write-Output 'Enter fallback'
    }
} else { throw '打开 button not found' }
Start-Sleep -Milliseconds 3000
# 验证上传
$all2 = $win.FindAll([System.Windows.Automation.TreeScope]::Descendants, [System.Windows.Automation.Condition]::TrueCondition)
$found = @()
foreach ($el in $all2) {
    $n = $el.Current.Name
    if ($n -like '*nui9-test-ref*' -or $n -like '*上传*') { $found += $n }
}
if ($found.Count -gt 0) { $found | Select-Object -First 5 | ForEach-Object { Write-Output ("RESULT: " + $_.Substring(0, [Math]::Min(80, $_.Length))) } }
else { Write-Output 'RESULT: no upload element found yet' }
