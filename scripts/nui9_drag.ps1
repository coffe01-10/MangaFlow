param([int]$x1, [int]$y1, [int]$x2, [int]$y2)
$ErrorActionPreference = 'Stop'
Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
public static class N9D2 {
    [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
    [DllImport("user32.dll")] public static extern bool SetCursorPos(int x, int y);
    [DllImport("user32.dll")] public static extern void mouse_event(uint f, uint dx, uint dy, uint data, UIntPtr extra);
}
"@
$p = Get-Process MangaFlow.Native -ErrorAction Stop | Select-Object -First 1
[N9D2]::SetForegroundWindow($p.MainWindowHandle) | Out-Null
Start-Sleep -Milliseconds 300
[N9D2]::SetCursorPos($x1, $y1) | Out-Null
Start-Sleep -Milliseconds 80
[N9D2]::mouse_event(0x0002, 0, 0, 0, [UIntPtr]::Zero)
Start-Sleep -Milliseconds 60
$steps = 8
for ($s = 1; $s -le $steps; $s++) {
    $x = $x1 + [int](($x2 - $x1) * $s / $steps)
    $y = $y1 + [int](($y2 - $y1) * $s / $steps)
    [N9D2]::SetCursorPos($x, $y) | Out-Null
    Start-Sleep -Milliseconds 30
}
[N9D2]::mouse_event(0x0004, 0, 0, 0, [UIntPtr]::Zero)
Start-Sleep -Milliseconds 300
Write-Output "dragged ($x1,$y1) -> ($x2,$y2)"
