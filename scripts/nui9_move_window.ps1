param([int]$x = 0, [int]$y = -60, [int]$w = 1650, [int]$h = 1020)
Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
public static class N9Move {
    [DllImport("user32.dll")] public static extern bool MoveWindow(IntPtr hWnd, int X, int Y, int nWidth, int nHeight, bool bRepaint);
    [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);
}
"@
$p = Get-Process MangaFlow.Native -ErrorAction Stop | Select-Object -First 1
[N9Move]::ShowWindow($p.MainWindowHandle, 9) | Out-Null   # restore
Start-Sleep -Milliseconds 400
[N9Move]::MoveWindow($p.MainWindowHandle, $x, $y, $w, $h, $true) | Out-Null
Start-Sleep -Milliseconds 400
Write-Output ("window moved to ($x,$y) ${w}x${h}")
