param([int]$mode = 3)
Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
public static class N9Win {
    [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);
}
"@
$p = Get-Process MangaFlow.Native -ErrorAction Stop | Select-Object -First 1
[N9Win]::ShowWindow($p.MainWindowHandle, $mode) | Out-Null
Write-Output ("window mode set to " + $mode)
