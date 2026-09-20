param([int]$x = 900, [int]$y = 600, [int]$ticks = -4)
$ErrorActionPreference = 'Stop'
Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
public static class N9W {
    [DllImport("user32.dll")] public static extern bool SetCursorPos(int x, int y);
    [DllImport("user32.dll")] public static extern void mouse_event(uint f, uint dx, uint dy, uint data, UIntPtr extra);
}
"@
[N9W]::SetCursorPos($x, $y) | Out-Null
Start-Sleep -Milliseconds 150
foreach ($i in 1..[Math]::Abs($ticks)) {
    $d = [uint32]4287100000
    if ($ticks -gt 0) { $d = [uint32]120 }
    [N9W]::mouse_event(0x0800, 0, 0, $d, [UIntPtr]::Zero)
    Start-Sleep -Milliseconds 90
}
Write-Output "wheeled ($x,$y) $ticks"
