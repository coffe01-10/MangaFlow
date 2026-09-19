$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes

$proc = Get-Process MangaFlow.Native -ErrorAction Stop | Select-Object -First 1
$root = [System.Windows.Automation.AutomationElement]::RootElement
$cond = New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ProcessIdProperty, $proc.Id)
$win = $root.FindFirst([System.Windows.Automation.TreeScope]::Children, $cond)
$all = $win.FindAll([System.Windows.Automation.TreeScope]::Descendants, [System.Windows.Automation.Condition]::TrueCondition)
$lines = New-Object System.Collections.Generic.List[string]

foreach ($el in $all) {
    $sp = $null
    if ($el.TryGetCurrentPattern([System.Windows.Automation.ScrollPattern]::Pattern, [ref]$sp)) {
        $info = $sp.Current
        $lines.Add(("SCROLLER name={0} extent={1}x{2} viewport={3}x{4} vPct={5} hPct={6} vScrollable={7}" -f `
            $el.Current.Name, [Math]::Round($info.Extent.Height,0), [Math]::Round($info.Extent.Width,0), `
            [Math]::Round($info.Viewport.Height,0), [Math]::Round($info.Viewport.Width,0), `
            [Math]::Round($info.VerticalScrollPercent,1), [Math]::Round($info.HorizontalScrollPercent,1), `
            $info.VerticallyScrollable))
        if ($el.Current.Name.Length -gt 0) {
            try { $sp.ScrollVerticalPercent = 100; $lines.Add("  SetScrollPercent OK -> $([Math]::Round($sp.Current.VerticalScrollPercent,1))") }
            catch { $lines.Add("  SetScrollPercent THREW: " + $_.Exception.GetType().Name + ": " + $_.Exception.Message) }
            try { $sp.Scroll([System.Windows.Automation.ScrollAmount]::LargeDown); $lines.Add("  Scroll(LargeDown) OK -> $([Math]::Round($sp.Current.VerticalScrollPercent,1))") }
            catch { $lines.Add("  Scroll(LargeDown) THREW: " + $_.Exception.GetType().Name + ": " + $_.Exception.Message) }
            try {
                for ($k = 0; $k -lt 30; $k++) { $sp.Scroll([System.Windows.Automation.ScrollAmount]::LargeDown) }
                $lines.Add("  30x Scroll(LargeDown) OK -> pct=$([Math]::Round($sp.Current.VerticalScrollPercent,1)) viewport=$([Math]::Round($sp.Current.Viewport.Height,0)) extent=$([Math]::Round($sp.Current.Extent.Height,0))")
                for ($k = 0; $k -lt 60; $k++) { $sp.Scroll([System.Windows.Automation.ScrollAmount]::LargeUp) }
                $lines.Add("  back to top -> pct=$([Math]::Round($sp.Current.VerticalScrollPercent,1))")
            } catch { $lines.Add("  sweep THREW: " + $_.Exception.GetType().Name + ": " + $_.Exception.Message) }
        }
    }
}
$lines | Out-File -FilePath "D:\自媒体\漫画工作流\output\nui8-release\perf\g3-scrollpattern-probe.txt" -Encoding utf8
Write-Output ("probe written, scrollers=" + ($lines | Where-Object { $_ -like 'SCROLLER*' }).Count)
