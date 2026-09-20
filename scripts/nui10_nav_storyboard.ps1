# NUI-10 helper: navigate the running native client to the storyboard page of
# the seeded project so measure_canvas_drag.ps1 can find the order badge.
# The dashboard card click is optional: when the client already sits inside the
# project, only the sidebar selection runs.
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes

$proc = Get-Process MangaFlow.Native -ErrorAction Stop | Select-Object -First 1
$root = [System.Windows.Automation.AutomationElement]::RootElement
$cond = New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ProcessIdProperty, $proc.Id)
$win = $root.FindFirst([System.Windows.Automation.TreeScope]::Children, $cond)
if (-not $win) { throw 'main window not found' }

function Get-All([System.Windows.Automation.AutomationElement]$win) {
  return $win.FindAll([System.Windows.Automation.TreeScope]::Descendants, [System.Windows.Automation.Condition]::TrueCondition)
}
function Invoke-First([System.Windows.Automation.AutomationElement]$win, [string]$name) {
  foreach ($el in (Get-All $win)) {
    if ($el.Current.Name -eq $name) {
      $ip = $null
      if ($el.TryGetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern, [ref]$ip)) { $ip.Invoke(); return $true }
      $si = $null
      if ($el.TryGetCurrentPattern([System.Windows.Automation.SelectionItemPattern]::Pattern, [ref]$si)) { $si.Select(); return $true }
      $xp = $null
      if ($el.TryGetCurrentPattern([System.Windows.Automation.ExpandCollapsePattern]::Pattern, [ref]$xp)) { $xp.Expand(); return $true }
    }
  }
  return $false
}

# 1. dashboard project card (no-op when the workspace is already open)
$card = 'NUI67 并排对照主项目'
$opened = Invoke-First $win $card
if ($opened) { Start-Sleep -Seconds 3 }

# 2. sidebar entry (ListItem name includes the connection suffix)
$nav = $false
foreach ($round in 1..3) {
  if (Invoke-First $win '分页与分镜，已接通') { $nav = $true; break }
  Start-Sleep -Seconds 2
}
if (-not $nav) { throw 'sidebar storyboard item not found' }
Start-Sleep -Seconds 3

# 3. ensure a page chip P.001 exists (page bar loaded)
$ok = $false
foreach ($round in 1..5) {
  foreach ($el in (Get-All $win)) {
    if ($el.Current.Name -like 'P.001*') { $ok = $true; break }
  }
  if ($ok) { break }
  Start-Sleep -Seconds 2
}
if (-not $ok) { throw 'page chip P.001 not found - storyboard page did not load' }
Write-Output 'NAV_OK storyboard page loaded (P.001 present)'
