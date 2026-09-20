# NUI-9 real-machine UIA helper.
# Usage: powershell -File scripts/nui9_uia_helper.ps1 <action> [args...]
# Actions:
#   set-value <namePattern> <value>          set ValuePattern on an Edit whose Name matches
#   invoke <name>                            invoke a Button by exact Name
#   invoke-substr <namePart>                 invoke first Button whose Name contains namePart
#   focus-name <namePart>                    return the Name of the focused element
#   list-windows                             list top windows of the MangaFlow process
param(
    [Parameter(Mandatory = $true)][string]$Action,
    [string]$Name,
    [string]$Value
)
$ErrorActionPreference = "Stop"
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes

$proc = Get-Process MangaFlow.Native -ErrorAction Stop
$root = [System.Windows.Automation.AutomationElement]::RootElement
$cond = New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ProcessIdProperty, $proc.Id)
$win = $root.FindFirst([System.Windows.Automation.TreeScope]::Children, $cond)
if (-not $win) { throw "MangaFlow window not found for pid $($proc.Id)" }

function Find-Button([string]$name, [bool]$exact) {
    $buttons = $win.FindAll([System.Windows.Automation.TreeScope]::Descendants,
        (New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ControlTypeProperty, [System.Windows.Automation.ControlType]::Button)))
    foreach ($b in $buttons) {
        if ($exact -and $b.Current.Name -eq $name) { return $b }
        if (-not $exact -and $b.Current.Name.Contains($name)) { return $b }
    }
    return $null
}

switch ($Action) {
    "set-value" {
        $edits = $win.FindAll([System.Windows.Automation.TreeScope]::Descendants,
            (New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ControlTypeProperty, [System.Windows.Automation.ControlType]::Edit)))
        foreach ($e in $edits) {
            if ($e.Current.Name -like "*$Name*") {
                $vp = $e.GetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern)
                $vp.SetValue($Value)
                Write-Output "set [$($e.Current.Name)] = $Value"
                exit 0
            }
        }
        throw "edit matching '$Name' not found"
    }
    "invoke" {
        $b = Find-Button $Name $true
        if (-not $b) { throw "button '$Name' not found" }
        ($b.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern)).Invoke()
        Write-Output "invoked [$Name]"
    }
    "invoke-substr" {
        $b = Find-Button $Name $false
        if (-not $b) { throw "button containing '$Name' not found" }
        ($b.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern)).Invoke()
        Write-Output "invoked-substr [$($b.Current.Name)]"
    }
    "focus-name" {
        $f = [System.Windows.Automation.AutomationElement]::FocusedElement
        Write-Output "focus: [$($f.Current.Name)] type=$($f.Current.ControlType.ProgrammaticName) pid=$($f.Current.ProcessId)"
    }
    "list-windows" {
        $wins = $root.FindAll([System.Windows.Automation.TreeScope]::Children, $cond)
        foreach ($w in $wins) { Write-Output "window: [$($w.Current.Name)] $($w.Current.ClassName)" }
    }
    "click" {
        # synthetic click at physical screen coordinates (from dump centers)
        $x = [int]$Name; $y = [int]$Value
        Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
public static class Mouse {
    [DllImport("user32.dll")] public static extern bool SetCursorPos(int x, int y);
    [DllImport("user32.dll")] public static extern void mouse_event(uint f, uint dx, uint dy, uint data, UIntPtr extra);
}
"@
        [Mouse]::SetCursorPos($x, $y) | Out-Null
        Start-Sleep -Milliseconds 150
        [Mouse]::mouse_event(2, 0, 0, 0, [UIntPtr]::Zero)  # LEFTDOWN
        [Mouse]::mouse_event(4, 0, 0, 0, [UIntPtr]::Zero)  # LEFTUP
        Write-Output "clicked ($x,$y)"
    }
    "key" {
        Add-Type -AssemblyName System.Windows.Forms
        [System.Windows.Forms.SendKeys]::SendWait($Name)
        Write-Output "sent keys [$Name]"
    }
    "wheel" {
        # wheel <x> <y> <ticks>：在指定点滚轮，负值向下翻页
        $x = [int]$Name; $y = [int]($Value -split ' ')[0]; $ticks = [int](($Value -split ' ')[1])
        Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
public static class Wheel {
    [DllImport("user32.dll")] public static extern bool SetCursorPos(int x, int y);
    [DllImport("user32.dll")] public static extern void mouse_event(uint f, uint dx, uint dy, uint data, UIntPtr extra);
}
"@
        [Wheel]::SetCursorPos($x, $y) | Out-Null
        Start-Sleep -Milliseconds 120
        for ($i = 0; $i -lt [Math]::Abs($ticks); $i++) {
            $delta = if ($ticks -lt 0) { [uint32]4287100000 } else { [uint32]120 }   # -120 as unsigned
            [Wheel]::mouse_event(0x0800, 0, 0, $delta, [UIntPtr]::Zero)
            Start-Sleep -Milliseconds 90
        }
        Write-Output "wheeled at ($x,$y) ticks=$ticks"
    }
    default { throw "unknown action $Action" }
}
