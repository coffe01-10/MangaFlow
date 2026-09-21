#requires -Version 5.1
<#
.SYNOPSIS
  NUI-10 P4-1 drag tool (SendInput mode): precise left-button drag between two
  screen points with intermediate move steps, for WPF canvases that move things
  with plain mouse handling (storyboard panel drag, workflow node drag/wire).

.DESCRIPTION
  Injects a real input sequence the OS cannot distinguish from a physical drag:
  cursor to start -> LEFT DOWN -> N interpolated moves with per-step delay ->
  LEFT UP. This is the same channel G4 proved delivers down/move/up to WPF;
  the tool exists so callers get a reusable, documented primitive with fresh
  UIA-derived coordinates (resolve anchors at call time, see examples).

  Not an OLE drag: for file drop targets (reference image upload) use
  nui10_ole_drop.ps1 instead. mouse_event-style one-jump drags (down, single
  far move, up) are what failed on the workflow canvas in NUI-9 P4-3b; this
  tool's many small steps keep Win32 mouse capture tracking stable.

.PARAMETER FromX/FromY
  Screen coordinates (primary monitor, physical pixels) where LEFT DOWN lands.
.PARAMETER ToX/ToY
  Screen coordinates where LEFT UP lands.
.PARAMETER Steps
  Number of interpolated move events between start and end (default 24).
.PARAMETER StepDelayMs
  Sleep between move steps in ms (default 15).
.PARAMETER HoldBeforeMoveMs
  Delay between LEFT DOWN and the first move (default 120).
.PARAMETER HoldAfterMoveMs
  Delay between the last move and LEFT UP (default 120).

.EXAMPLE
  # Drag the workflow node whose UIA rect was just queried (fresh, screen space):
  $rect = .\dump_uia_rects.ps1 ... # resolve the node's rectangle first
  .\nui10_canvas_drag.ps1 -FromX $rect.X -FromY $rect.Y -ToX ($rect.X+150) -ToY ($rect.Y+80)

.EXAMPLE
  .\nui10_canvas_drag.ps1 -FromX 1010 -FromY 470 -ToX 1160 -ToY 550 -Steps 30
#>
param(
  [Parameter(Mandatory = $true)][int]$FromX,
  [Parameter(Mandatory = $true)][int]$FromY,
  [Parameter(Mandatory = $true)][int]$ToX,
  [Parameter(Mandatory = $true)][int]$ToY,
  [int]$Steps = 24,
  [int]$StepDelayMs = 15,
  [int]$HoldBeforeMoveMs = 120,
  [int]$HoldAfterMoveMs = 120
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'

if (-not ('Nui10CanvasDrag.Input' -as [type])) {
  Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;

namespace Nui10CanvasDrag
{
    public static class Input
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

        [DllImport("user32.dll")]
        private static extern bool GetCursorPos(out POINT p);

        [StructLayout(LayoutKind.Sequential)]
        private struct POINT
        {
            public int X;
            public int Y;
        }

        private const uint MOUSEEVENTF_MOVE = 0x0001;
        private const uint MOUSEEVENTF_LEFTDOWN = 0x0002;
        private const uint MOUSEEVENTF_LEFTUP = 0x0004;
        private const uint MOUSEEVENTF_ABSOLUTE = 0x8000;

        [DllImport("user32.dll")]
        private static extern int GetSystemMetrics(int index);

        private static INPUT MoveInput(int x, int y)
        {
            // Normalize to the primary monitor (single-screen environment is an
            // accepted hard boundary of these rounds; no VIRTUALDESK).
            int w = GetSystemMetrics(0);
            int h = GetSystemMetrics(1);
            if (x < 0) x = 0;
            if (y < 0) y = 0;
            if (x > w - 1) x = w - 1;
            if (y > h - 1) y = h - 1;
            return new INPUT
            {
                type = 0,
                mi = new MOUSEINPUT
                {
                    dx = (x * 65536) / w,
                    dy = (y * 65536) / h,
                    dwFlags = MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE
                }
            };
        }

        private static void Send(INPUT input)
        {
            INPUT[] one = new INPUT[1];
            one[0] = input;
            uint sent = SendInput(1, one, Marshal.SizeOf(typeof(INPUT)));
            if (sent != 1) throw new InvalidOperationException("SendInput rejected the event");
        }

        public static void MoveTo(int x, int y) { Send(MoveInput(x, y)); }
        public static void LeftDown() { Send(new INPUT { type = 0, mi = new MOUSEINPUT { dwFlags = MOUSEEVENTF_LEFTDOWN } }); }
        public static void LeftUp() { Send(new INPUT { type = 0, mi = new MOUSEINPUT { dwFlags = MOUSEEVENTF_LEFTUP } }); }
        public static int[] Cursor() { POINT p; if (!GetCursorPos(out p)) throw new InvalidOperationException("GetCursorPos failed"); return new int[] { p.X, p.Y }; }
    }
}
'@
}

[Nui10CanvasDrag.Input]::MoveTo($FromX, $FromY)
Start-Sleep -Milliseconds 60
[Nui10CanvasDrag.Input]::LeftDown()
Start-Sleep -Milliseconds $HoldBeforeMoveMs
for ($i = 1; $i -le $Steps; $i++) {
  $x = $FromX + [int][math]::Floor(($ToX - $FromX) * $i / $Steps)
  $y = $FromY + [int][math]::Floor(($ToY - $FromY) * $i / $Steps)
  [Nui10CanvasDrag.Input]::MoveTo($x, $y)
  Start-Sleep -Milliseconds $StepDelayMs
}
Start-Sleep -Milliseconds $HoldAfterMoveMs
[Nui10CanvasDrag.Input]::LeftUp()
Start-Sleep -Milliseconds 80
$cursor = [Nui10CanvasDrag.Input]::Cursor()
Write-Output ("DRAG_OK from=({0},{1}) to=({2},{3}) steps={4} cursorNow=({5},{6})" -f $FromX, $FromY, $ToX, $ToY, $Steps, $cursor[0], $cursor[1])
