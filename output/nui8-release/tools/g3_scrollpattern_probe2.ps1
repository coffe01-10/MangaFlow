$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes

$proc = Get-Process MangaFlow.Native -ErrorAction Stop | Select-Object -First 1
$root = [System.Windows.Automation.AutomationElement]::RootElement
$cond = New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ProcessIdProperty, $proc.Id)
$win = $root.FindFirst([System.Windows.Automation.TreeScope]::Children, $cond)
$all = $win.FindAll([System.Windows.Automation.TreeScope]::Descendants, [System.Windows.Automation.Condition]::TrueCondition)
$lines = New-Object System.Collections.Generic.List[string]

function Dump($sp) {
    try { $i = $sp.Current; return "extent=$([Math]::Round($i.Extent.Height,0))x$([Math]::Round($i.Extent.Width,0)) viewport=$([Math]::Round($i.Viewport.Height,0))x$([Math]::Round($i.Viewport.Width,0)) vPct=$([Math]::Round($i.VerticalScrollPercent,1))" }
    catch { return "read THREW: " + $_.Exception.Message }
}

foreach ($el in $all) {
    $sp = $null
    if (-not $el.TryGetCurrentPattern([System.Windows.Automation.ScrollPattern]::Pattern, [ref]$sp)) { continue }
    if (-not $sp.Current.VerticallyScrollable) { continue }
    $lines.Add("TARGET name=" + $el.Current.Name + " before: " + (Dump $sp))
    try {
        $sp.SetScrollPercent(-1, 100)
        Start-Sleep -Milliseconds 500
        $lines.Add("  SetScrollPercent(-1,100) OK after: " + (Dump $sp))
        $sp.SetScrollPercent(-1, 0)
        Start-Sleep -Milliseconds 500
        $lines.Add("  SetScrollPercent(-1,0) OK after: " + (Dump $sp))
    } catch {
        $lines.Add("  SetScrollPercent THREW: " + $_.Exception.GetType().Name + ": " + $_.Exception.Message)
    }
    try {
        $sa = [System.Windows.Automation.ScrollAmount]::LargeDown
        $sp.Scroll($sa)
        $lines.Add("  Scroll(LargeDown) OK after: " + (Dump $sp))
    } catch {
        $lines.Add("  Scroll(LargeDown) THREW: " + $_.Exception.GetType().Name + ": " + $_.Exception.Message)
    }
}
$lines | Out-File -FilePath "D:\自媒体\漫画工作流\output\nui8-release\perf\g3-scrollpattern-probe2.txt" -Encoding utf8
Write-Output ("probe2 written, lines=" + $lines.Count)
