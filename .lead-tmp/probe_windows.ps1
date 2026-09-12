Add-Type @"
using System;
using System.Text;
using System.Collections.Generic;
using System.Runtime.InteropServices;
public class WinProbe {
    [DllImport("user32.dll")] static extern bool EnumWindows(EnumWindowsProc cb, IntPtr lp);
    [DllImport("user32.dll")] static extern int GetClassName(IntPtr h, StringBuilder s, int n);
    [DllImport("user32.dll")] static extern int GetWindowText(IntPtr h, StringBuilder s, int n);
    [DllImport("user32.dll")] static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
    [DllImport("user32.dll")] static extern bool IsWindowVisible(IntPtr h);
    delegate bool EnumWindowsProc(IntPtr h, IntPtr lp);
    public static List<string> WindowsOf(uint target) {
        var result = new List<string>();
        EnumWindows((h, lp) => {
            uint pid; GetWindowThreadProcessId(h, out pid);
            if (pid == target) {
                var cn = new StringBuilder(256); GetClassName(h, cn, 256);
                var tt = new StringBuilder(512); GetWindowText(h, tt, 512);
                result.Add(string.Format("class={0} visible={1} title={2}", cn, IsWindowVisible(h), tt));
            }
            return true;
        }, IntPtr.Zero);
        return result;
    }
}
"@
$p = Get-Process -Name 'MangaFlow.Native.Tests' -ErrorAction Stop
[WinProbe]::WindowsOf($p.Id)
