#requires -Version 5.1
<#
.SYNOPSIS
  NUI-10 P4-1 drag tool (OLE mode): performs a REAL OLE drag-and-drop of a
  file (CF_HDROP) from a helper process onto a target window, the same
  integration channel Windows Explorer uses.

.DESCRIPTION
  Why this exists: synthetic mouse events (mouse_event / SendInput) cannot
  produce an OLE drop — NUI-9 P4-3a proved the WPF drop target only fires
  IDropTarget::Drop when a real OLE drag session (DoDragDrop) delivers the
  data object. This tool starts that session from a helper process:

    1. A tiny topmost carrier window is placed at the start point so the
       initial LEFT DOWN never lands on the target app.
    2. LEFT DOWN is injected at the start point.
    3. Control.DoDragDrop starts an OLE drag carrying a FileDropList
       (marshalled to CF_HDROP for OLE consumers) — the target app receives
       DragEnter/DragOver with a genuine shell data object.
    4. A worker thread walks the cursor to the target point in small steps
       and releases the button; the target's IDropTarget::Drop fires and
       DoDragDrop returns the effect the target granted (Copy expected).

  Verified target: MangaFlow.Native ReferenceWorkspace upload button
  (AllowDrop=true, DragOver checks DataFormats.FileDrop, Drop -> Upload).

.PARAMETER FilePath
  Full path of an existing file to drop (any extension; use a small PNG for
  reference-image upload tests).
.PARAMETER FromX/FromY
  Screen coordinates where the drag session starts (carrier window position).
  Pick a spot that does NOT overlap the target app (e.g. over a terminal).
.PARAMETER ToX/ToY
  Screen coordinates of the target drop zone centre (resolve via UIA at call
  time; e.g. the ReferenceWorkspace upload button rectangle).
.PARAMETER Steps/StepDelayMs/StartDelayMs/HoldBeforeMoveMs
  Drag motion shaping; defaults mirror a slow human drag.

.OUTPUTS
  One line: OLE_DROP_RESULT <granted effect name> <effect code>
  Codes: 0=None 1=Copy 2=Move 4=Link. 0 usually means the target rejected the
  drop (DragOver returned None) or no drop target engaged.

.EXAMPLE
  .\nui10_ole_drop.ps1 -FilePath D:\tmp\ref.png -FromX 40 -FromY 900 -ToX 960 -ToY 540
#>
param(
  [Parameter(Mandatory = $true)][string]$FilePath,
  [Parameter(Mandatory = $true)][int]$FromX,
  [Parameter(Mandatory = $true)][int]$FromY,
  [Parameter(Mandatory = $true)][int]$ToX,
  [Parameter(Mandatory = $true)][int]$ToY,
  [int]$Steps = 30,
  [int]$StepDelayMs = 20,
  [int]$StartDelayMs = 400,
  [int]$HoldBeforeMoveMs = 120
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'

if (-not (Test-Path -LiteralPath $FilePath)) { throw "FilePath does not exist: $FilePath" }
$FilePath = (Resolve-Path -LiteralPath $FilePath).Path

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

if (-not ('Nui10OleDrop.Native' -as [type])) {
  Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;

namespace Nui10OleDrop
{
    public static class Native
    {
        [StructLayout(LayoutKind.Sequential)]
        private struct MOUSEINPUT
        {
            public int dx;
            public int dy;
            public uint mouseData;
            public uint dwFlags;
            public uint time;
            public IntPtr dwExtraInfo;
        }

        [StructLayout(LayoutKind.Sequential)]
        private struct INPUT
        {
            public uint type;
            public MOUSEINPUT mi;
        }

        [DllImport("user32.dll", SetLastError = true)]
        private static extern uint SendInput(uint nInputs, INPUT[] pInputs, int cbSize);

        private const uint MOUSEEVENTF_MOVE = 0x0001;
        private const uint MOUSEEVENTF_LEFTDOWN = 0x0002;
        private const uint MOUSEEVENTF_LEFTUP = 0x0004;
        private const uint MOUSEEVENTF_ABSOLUTE = 0x8000;

        [DllImport("user32.dll")]
        private static extern int GetSystemMetrics(int index);

        private static void Send(uint flags, int x, int y)
        {
            int w = GetSystemMetrics(0);
            int h = GetSystemMetrics(1);
            INPUT[] one = new INPUT[1];
            one[0] = new INPUT
            {
                type = 0,
                mi = new MOUSEINPUT
                {
                    dx = (Math.Max(0, Math.Min(x, w - 1)) * 65536) / w,
                    dy = (Math.Max(0, Math.Min(y, h - 1)) * 65536) / h,
                    dwFlags = flags
                }
            };
            if ((flags & MOUSEEVENTF_MOVE) == 0)
            {
                one[0].mi.dx = 0;
                one[0].mi.dy = 0;
            }
            uint sent = SendInput(1, one, Marshal.SizeOf(typeof(INPUT)));
            if (sent != 1) throw new InvalidOperationException("SendInput rejected the event");
        }

        public static void MoveTo(int x, int y) { Send(MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE, x, y); }
        public static void LeftDown() { Send(MOUSEEVENTF_LEFTDOWN, 0, 0); }
        public static void LeftUp() { Send(MOUSEEVENTF_LEFTUP, 0, 0); }
    }
}
'@
}

# Carrier: a small topmost window so the initial press lands on the helper,
# never on the target application.
$carrier = New-Object System.Windows.Forms.Form
$carrier.FormBorderStyle = [System.Windows.Forms.FormBorderStyle]::None
$carrier.ShowInTaskbar = $false
$carrier.TopMost = $true
$carrier.StartPosition = [System.Windows.Forms.FormStartPosition]::Manual
$carrier.Location = New-Object System.Drawing.Point($FromX, $FromY)
$carrier.Size = New-Object System.Drawing.Size(48, 32)
$carrier.Opacity = 0.35
$carrier.Text = 'nui10 ole drop carrier'
$carrier.Show()
# Take real foreground before injecting LEFT DOWN: SendInput routes the press
# into the FOREGROUND thread's queue, and a background process cannot steal
# foreground with Form.Activate alone (observed 2026-09-20: press landed on
# the target app, DoDragDrop bailed None in 9 ms, or hung waiting for a
# release its thread never saw). AttachThreadInput borrows the foreground
# thread's input state so SetForegroundWindow succeeds from background.
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class Nui10Fg {
  [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
  [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
  [DllImport("user32.dll")] public static extern bool AttachThreadInput(uint a, uint b, bool attach);
  [DllImport("kernel32.dll")] public static extern uint GetCurrentThreadId();
  [DllImport("user32.dll")] public static extern short GetAsyncKeyState(int v);
  public static bool Take(IntPtr target) {
    uint pid; uint fgThread = GetWindowThreadProcessId(GetForegroundWindow(), out pid);
    uint me = GetCurrentThreadId();
    bool ok = false;
    if (fgThread != 0 && fgThread != me && AttachThreadInput(fgThread, me, true)) {
      ok = SetForegroundWindow(target);
      AttachThreadInput(fgThread, me, false);
    } else {
      ok = SetForegroundWindow(target);
    }
    return ok;
  }
  public static bool LeftIsDown() { return (GetAsyncKeyState(0x01) & 0x8000) != 0; }
}
'@
$deadline = (Get-Date).AddSeconds(3)
while ($carrier.Handle -ne [Nui10Fg]::GetForegroundWindow() -and (Get-Date) -lt $deadline) {
  [void][Nui10Fg]::Take($carrier.Handle)
  Start-Sleep -Milliseconds 120
  [System.Windows.Forms.Application]::DoEvents()
}
Write-Output ("carrier foreground: " + ($carrier.Handle -eq [Nui10Fg]::GetForegroundWindow()))

[Nui10OleDrop.Native]::MoveTo($FromX + 20, $FromY + 12)
Start-Sleep -Milliseconds 80
[Nui10OleDrop.Native]::LeftDown()
Start-Sleep -Milliseconds $HoldBeforeMoveMs
Write-Output ("left button down after injection: " + [Nui10Fg]::LeftIsDown())

# Worker thread: after DoDragDrop enters its modal loop, walk the cursor to
# the target and release. SendInput is global, safe from an MTA thread.
$sync = [hashtable]::Synchronized(@{
  FromX = $FromX; FromY = $FromY; ToX = $ToX; ToY = $ToY
  Steps = $Steps; StepDelayMs = $StepDelayMs; StartDelayMs = $StartDelayMs
  Done = $false
})
$runspace = [runspacefactory]::CreateRunspace()
$runspace.Open()
$pump = [powershell]::Create()
$pump.Runspace = $runspace
$pump.AddScript({
  param($sync)
  if (-not ('Nui10OleDrop.Native' -as [type])) { throw 'input type not loaded in worker runspace' }
  Start-Sleep -Milliseconds $sync.StartDelayMs
  for ($i = 1; $i -le $sync.Steps; $i++) {
    $x = $sync.FromX + [int][math]::Floor(($sync.ToX - $sync.FromX) * $i / $sync.Steps) + 20
    $y = $sync.FromY + [int][math]::Floor(($sync.ToY - $sync.FromY) * $i / $sync.Steps) + 12
    [Nui10OleDrop.Native]::MoveTo($x, $y)
    Start-Sleep -Milliseconds $sync.StepDelayMs
  }
  Start-Sleep -Milliseconds 150
  [Nui10OleDrop.Native]::LeftUp()
  # Cancel guard: if the OLE session is still alive 2.5 s after release (the
  # modal loop never saw our up), ESC aborts it so DoDragDrop cannot hang the
  # tool forever. Benign once the drag has ended (no modal open in our flows).
  Start-Sleep -Milliseconds 2500
  [System.Windows.Forms.SendKeys]::SendWait('{ESC}')
  Start-Sleep -Milliseconds 400
  [System.Windows.Forms.SendKeys]::SendWait('{ESC}')
  $sync.Done = $true
}) | Out-Null
$pump.AddArgument($sync) | Out-Null
$handle = $pump.BeginInvoke()

# STA main thread: start the OLE drag with a FileDropList (CF_HDROP on the wire).
$data = New-Object System.Windows.Forms.DataObject
$list = New-Object System.Collections.Specialized.StringCollection
$list.Add($FilePath) | Out-Null
$data.SetFileDropList($list)
$allowed = [System.Windows.Forms.DragDropEffects]::Copy -bor [System.Windows.Forms.DragDropEffects]::Move -bor [System.Windows.Forms.DragDropEffects]::Link
$effect = $carrier.DoDragDrop($data, $allowed)

if (-not $handle.AsyncWaitHandle.WaitOne(8000)) { $pump.Stop() } else { $pump.EndInvoke($handle) }
$carrier.Close()
$runspace.Close()
$pump.Dispose()

Write-Output ("OLE_DROP_RESULT {0} {1}" -f $effect, [int]$effect)
