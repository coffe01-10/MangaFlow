Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
public static class N9FG2 {
    [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
    [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);
}
"@
$p = Get-Process MangaFlow.Native -ErrorAction Stop | Select-Object -First 1
[N9FG2]::ShowWindow($p.MainWindowHandle, 9) | Out-Null
Start-Sleep -Milliseconds 200
[N9FG2]::SetForegroundWindow($p.MainWindowHandle) | Out-Null
Start-Sleep -Milliseconds 300
Write-Output "MangaFlow foregrounded"
