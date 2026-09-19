$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
$proc = Get-Process MangaFlow.Native -ErrorAction Stop | Select-Object -First 1
$root = [System.Windows.Automation.AutomationElement]::RootElement
$cond = New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ProcessIdProperty, $proc.Id)
$win = $root.FindFirst([System.Windows.Automation.TreeScope]::Children, $cond)
$all = $win.FindAll([System.Windows.Automation.TreeScope]::Descendants, [System.Windows.Automation.Condition]::TrueCondition)
$order = $null; $zoomName = ''
foreach ($el in $all) {
    $n = $el.Current.Name
    if ($n -eq '阅读序' -and $el.Current.ControlType.ProgrammaticName -match 'Button') { $order = $el }
    if ($n -match '^\d+%$') { $zoomName = $n }
}
if (-not $order) { throw 'reading-order toggle not found' }
$order.GetCurrentPattern([System.Windows.Automation.TogglePattern]::Pattern).Toggle()
Start-Sleep -Milliseconds 400
$all2 = $win.FindAll([System.Windows.Automation.TreeScope]::Descendants, [System.Windows.Automation.Condition]::TrueCondition)
foreach ($el in $all2) {
    $n = $el.Current.Name
    if ($n -match '^格 ') {
        $r = $el.Current.BoundingRectangle
        Write-Output ("badge [" + $n + "] " + [int]$r.X + "," + [int]$r.Y + " " + [int]$r.Width + "x" + [int]$r.Height + " center=(" + [int]($r.X + $r.Width/2) + "," + [int]($r.Y + $r.Height/2) + ")")
    }
}
Write-Output ("zoom: " + $zoomName)
$order.GetCurrentPattern([System.Windows.Automation.TogglePattern]::Pattern).Toggle()
Start-Sleep -Milliseconds 300
Write-Output 'reading-order restored'
