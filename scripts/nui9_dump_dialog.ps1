# Dump the dialog window containing the focused element, then optionally send Esc.
param([switch]$SendEsc)
$ErrorActionPreference = "Stop"
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes

$f = [System.Windows.Automation.AutomationElement]::FocusedElement
$walker = [System.Windows.Automation.TreeWalker]::ControlViewWalker
$node = $f
while ($true) {
    $ct = $node.Current.ControlType.ProgrammaticName
    if ($ct -match "Window") { break }
    $parent = $walker.GetParent($node)
    if ($parent -eq $null) { break }
    $node = $parent
}
Write-Output ("dialog window: [" + $node.Current.Name + "]")
$desc = $node.FindAll([System.Windows.Automation.TreeScope]::Descendants, [System.Windows.Automation.Condition]::TrueCondition)
foreach ($d in $desc) {
    $ct = $d.Current.ControlType.ProgrammaticName
    if ($ct -match "Button|Text|Window") { Write-Output ("  " + $ct + " [" + $d.Current.Name + "]") }
}
if ($SendEsc) {
    Add-Type -AssemblyName System.Windows.Forms
    [System.Windows.Forms.SendKeys]::SendWait("{ESC}")
    Start-Sleep -Milliseconds 800
    $after = [System.Windows.Automation.AutomationElement]::FocusedElement
    Write-Output ("after-esc focus: [" + $after.Current.Name + "]")
}
