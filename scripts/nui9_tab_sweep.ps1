param(
    [Parameter(Mandatory = $true)][string]$Label,
    [int]$Tabs = 12
)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
Add-Type -AssemblyName System.Windows.Forms
$proc = Get-Process MangaFlow.Native -ErrorAction Stop | Select-Object -First 1
$root = [System.Windows.Automation.AutomationElement]::RootElement
$cond = New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ProcessIdProperty, $proc.Id)
$win = $root.FindFirst([System.Windows.Automation.TreeScope]::Children, $cond)
$out = "output/nui9-rc/p4-4/$Label-tabs.txt"
New-Item -ItemType Directory -Force -Path "output/nui9-rc/p4-4" | Out-Null
$lines = @()
# 起点（未按 Tab 前）的焦点
$f = [System.Windows.Automation.AutomationElement]::FocusedElement
$lines += ("start: [" + $f.Current.Name + "] " + $f.Current.ControlType.ProgrammaticName)
foreach ($i in 1..$Tabs) {
    [System.Windows.Forms.SendKeys]::SendWait('{TAB}')
    Start-Sleep -Milliseconds 350
    $f = [System.Windows.Automation.AutomationElement]::FocusedElement
    if (-not $f) { $lines += ("tab ${i}: <no focus>"); continue }
    $r = $f.Current.BoundingRectangle
    $inWindow = $f.Current.ProcessId -eq $proc.Id
    $lines += ("tab ${i}: [" + $f.Current.Name + "] " + $f.Current.ControlType.ProgrammaticName + " rect=(" + [int]$r.X + "," + [int]$r.Y + "," + [int]$r.Width + "x" + [int]$r.Height + ") inProc=$inWindow")
}
$lines | Set-Content -Path $out -Encoding UTF8
Write-Output ("sweep done -> " + $out)
