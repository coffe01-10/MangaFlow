// NUI-10 P4-1 OLE drag-drop automation tool (3rd generation).
//
// Performs a REAL OLE drag session (ole32 DoDragDrop) carrying CF_HDROP
// (FileDrop) data from this helper process, and drops it onto whatever
// registered OLE drop target sits under the cursor's end point — e.g. the
// MangaFlow WPF client's reference-image upload zone, or an Explorer folder
// window (the canonical control target).
//
// Why a 3rd tool (lessons from 2026-09-20 rounds, see scripts/nui10_ole_drop.ps1
// and scripts/nui10_ole_drop_helper.cs):
//   v1 (PowerShell Add-Type): DoDragDrop returned DROPEFFECT_NONE in ~9ms —
//       QueryContinueDrag saw no MK_LBUTTON (no real button state) and dropped
//       immediately.
//   v2 (carrier form + WndProc DoDragDrop): press/ordering depended on stealing
//       foreground, had no watchdog (a stuck session hung the harness), and all
//       diagnostics went through redirected pipes.
//   v3 (this file): no carrier window and no foreground games. STA main calls
//       OleInitialize + DoDragDrop directly (DoDragDrop pumps its own modal
//       loop); a separate injector thread drives the real cursor via SendInput:
//       LEFTDOWN first, then a step walk to the target, then LEFTUP. The
//       IDropSource tolerates the pre-press window (drag continues until the
//       button has been seen down and then released), and a watchdog cancels
//       with ESC and hard-exits instead of hanging forever. All diagnostics go
//       to a log file — never a pipe.
//
// Modes:
//   filedrop  real OLE DoDragDrop carrying CF_HDROP files (exit code = effect).
//   inject    pure synthetic drag (down/walk/up, no OLE payload) for dragging
//             app-internal items such as storyboard panels or workflow nodes,
//             whose drag is WPF mouse-capture based, not OLE.
//
// Coordinates are PHYSICAL screen pixels — the same space as UIA rects and
// CopyFromScreen captures (PS/python probes are system-DPI-aware; do NOT scale).
//
// Usage:
//   ole_drag_drop --mode filedrop --files <p1[;p2...]> --to <x,y> [--from <x,y>]
//                 [--steps N] [--step-ms MS] [--hold-ms MS] [--settle-ms MS]
//                 [--start-delay-ms MS] [--log <path>]
//   ole_drag_drop --mode inject --from <x,y> --to <x,y> [same knobs]
//
// Exit codes (filedrop): 0/1/2/4 = DROPEFFECT_NONE/COPY/MOVE/LINK as returned
// by DoDragDrop; 124 = watchdog killed a stuck session; 125 = error (see log).
using System;
using System.Collections.Specialized;
using System.Diagnostics;
using System.IO;
using System.Runtime.InteropServices;
using System.Runtime.InteropServices.ComTypes;
using System.Threading;
using System.Windows.Forms;
using ComIDataObject = System.Runtime.InteropServices.ComTypes.IDataObject;

// System.Runtime.InteropServices.ComTypes carries IDataObject/IDropTarget but
// not IDropSource, so the source side is declared here with its real COM IID;
// the marshaler builds a CCW for the managed DropSource when DoDragDrop's
// P/Invoke marshals it.
[ComImport, Guid("00000121-0000-0000-C000-000000000046"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IOleDropSource
{
    [PreserveSig] int QueryContinueDrag(int fEscapePressed, int grfKeyState);
    [PreserveSig] int GiveFeedback(int dwEffect);
}

internal static class Program
{
    const uint MK_LBUTTON = 0x0001;
    const int DRAGDROP_S_DROP = 0x00040100;
    const int DRAGDROP_S_CANCEL = 0x00040101;
    const int DRAGDROP_S_USEDEFAULTCURSORS = 0x00040102;
    const uint EFFECT_COPY = 1, EFFECT_MOVE = 2, EFFECT_LINK = 4;

    const uint MOUSEEVENTF_MOVE = 0x0001;
    const uint MOUSEEVENTF_LEFTDOWN = 0x0002;
    const uint MOUSEEVENTF_LEFTUP = 0x0004;
    const uint MOUSEEVENTF_ABSOLUTE = 0x8000;

    static TextWriter log;

    [DllImport("ole32.dll")] static extern int OleInitialize(IntPtr pvReserved);
    [DllImport("ole32.dll")] static extern int DoDragDrop(ComIDataObject pDataObj, IOleDropSource pDropSource, uint dwOKEffects, out uint pdwEffect);
    [DllImport("user32.dll")] static extern int GetSystemMetrics(int nIndex);
    [DllImport("user32.dll")] static extern uint SendInput(uint nInputs, INPUT[] pInputs, int cbSize);
    [DllImport("user32.dll")] static extern void keybd_event(byte bVk, byte bScan, uint dwFlags, UIntPtr dwExtraInfo);
    [DllImport("user32.dll")] static extern bool SetProcessDPIAware();
    [DllImport("user32.dll")] static extern short GetKeyState(int nVirtKey);
    [DllImport("user32.dll")] static extern short GetAsyncKeyState(int nVirtKey);

    sealed class DropSource : IOleDropSource
    {
        public bool PressedEver;
        int _qcCount;
        public int QueryContinueDrag(int fEscapePressed, int grfKeyState)
        {
            _qcCount++;
            if (_qcCount <= 40 || (grfKeyState & MK_LBUTTON) == 0)
                Log("TRACE: QC#" + _qcCount + " esc=" + fEscapePressed + " keys=0x" + grfKeyState.ToString("X") + " pressedEver=" + PressedEver);
            if (fEscapePressed != 0) return DRAGDROP_S_CANCEL;
            if ((grfKeyState & MK_LBUTTON) != 0) PressedEver = true;
            else if (PressedEver) return DRAGDROP_S_DROP; // button released over target → drop
            return 0; // S_OK — keep dragging (also covers the pre-press window)
        }
        int _gfCount;
        public int GiveFeedback(int dwEffect)
        {
            _gfCount++;
            if (_gfCount <= 40) Log("TRACE: GF#" + _gfCount + " effect=" + dwEffect);
            return DRAGDROP_S_USEDEFAULTCURSORS;
        }
    }

    [StructLayout(LayoutKind.Sequential)]
    struct MOUSEINPUT { public int dx, dy; public uint mouseData, dwFlags, time; public IntPtr dwExtraInfo; }
    [StructLayout(LayoutKind.Sequential)]
    struct KEYBDINPUT { public ushort wVk, wScan; public uint dwFlags, time; public IntPtr dwExtraInfo; }
    [StructLayout(LayoutKind.Explicit)]
    struct INPUTUNION { [FieldOffset(0)] public MOUSEINPUT mi; [FieldOffset(0)] public KEYBDINPUT ki; }
    [StructLayout(LayoutKind.Sequential)]
    struct INPUT { public uint type; public INPUTUNION u; }

    static void SendMouse(uint flags, int x, int y)
    {
        int w = GetSystemMetrics(0), h = GetSystemMetrics(1);
        var inp = new INPUT[1];
        inp[0].type = 0; // INPUT_MOUSE
        inp[0].u.mi.dx = x * 65536 / Math.Max(1, w);
        inp[0].u.mi.dy = y * 65536 / Math.Max(1, h);
        inp[0].u.mi.dwFlags = flags | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_MOVE;
        uint rv = SendInput(1, inp, Marshal.SizeOf(typeof(INPUT)));
        if (rv != 1) Log("DIAG: SendInput returned " + rv);
    }

    static void TapEscape()
    {
        keybd_event(0x1B, 0, 0, UIntPtr.Zero);
        Thread.Sleep(60);
        keybd_event(0x1B, 0, 2, UIntPtr.Zero); // KEYEVENTF_KEYUP
    }

    static void Log(string s)
    {
        var line = DateTime.Now.ToString("HH:mm:ss.fff ") + s;
        Console.WriteLine(line);
        try { if (log != null) log.WriteLine(line); } catch { }
    }

    static ComIDataObject BuildFileData(string[] files)
    {
        var dob = new DataObject();
        var sc = new StringCollection();
        sc.AddRange(files);
        dob.SetFileDropList(sc);
        return (ComIDataObject)dob; // WinForms DataObject implements ComTypes.IDataObject
    }

    [STAThread]
    static int Main(string[] args)
    {
        SetProcessDPIAware(); // physical-pixel coordinates throughout — same space as UIA rects
        string mode = "filedrop", logPath = null, filesArg = null, fromArg = null, toArg = null;
        int steps = 30, stepMs = 25, holdMs = 250, settleMs = 400, startDelayMs = 400;
        for (int i = 0; i < args.Length; i++)
        {
            string a = args[i];
            string Next() { return i + 1 < args.Length ? args[++i] : null; }
            switch (a)
            {
                case "--mode": mode = Next(); break;
                case "--files": filesArg = Next(); break;
                case "--from": fromArg = Next(); break;
                case "--to": toArg = Next(); break;
                case "--steps": steps = int.Parse(Next()); break;
                case "--step-ms": stepMs = int.Parse(Next()); break;
                case "--hold-ms": holdMs = int.Parse(Next()); break;
                case "--settle-ms": settleMs = int.Parse(Next()); break;
                case "--start-delay-ms": startDelayMs = int.Parse(Next()); break;
                case "--log": logPath = Next(); break;
                default: Log("DIAG: unknown arg " + a); return 125;
            }
        }
        if (logPath != null) log = new StreamWriter(logPath, append: false) { AutoFlush = true };

        int w = GetSystemMetrics(0), h = GetSystemMetrics(1);
        if (string.IsNullOrEmpty(toArg)) { Log("DIAG: --to required"); return 125; }
        var to = ParsePoint(toArg, w, h);
        var from = fromArg != null ? ParsePoint(fromArg, w, h) : Clamp(to.x - 120, to.y + 120, w, h);
        Log("DIAG: mode=" + mode + " from=" + from + " to=" + to + " steps=" + steps + " screen=" + w + "x" + h);

        if (mode == "inject")
        {
            InjectDrag(from, to, steps, stepMs, holdMs, settleMs, startDelayMs, null, null, null);
            Log("INJECT_DONE");
            if (log != null) log.Dispose();
            return 0;
        }

        // ---- filedrop mode: real OLE drag session ----
        if (string.IsNullOrEmpty(filesArg)) { Log("DIAG: --files required in filedrop mode"); return 125; }
        var files = filesArg.Split(';');
        foreach (var f in files)
            if (!File.Exists(f)) { Log("DIAG: file missing: " + f); return 125; }

        int hr = OleInitialize(IntPtr.Zero);
        Log("DIAG: OleInitialize hr=0x" + hr.ToString("X8"));
        if (hr < 0) { if (log != null) log.Dispose(); return 125; }

        var source = new DropSource();
        ComIDataObject data;
        try { data = BuildFileData(files); }
        catch (Exception ex) { Log("DIAG: DataObject build failed: " + ex.Message); if (log != null) log.Dispose(); return 125; }

        var done = new ManualResetEvent(false);
        var injector = new Thread(() => InjectDrag(from, to, steps, stepMs, holdMs, settleMs, startDelayMs, done, source, files));
        injector.IsBackground = true;
        injector.Start();

        Log("TRACE: pre-DoDragDrop GetKeyState(LB)=0x" + GetKeyState(0x01).ToString("X") + " GetAsyncKeyState(LB)=0x" + GetAsyncKeyState(0x01).ToString("X"));
        var sw = Stopwatch.StartNew();
        uint effect;
        hr = DoDragDrop(data, source, EFFECT_COPY | EFFECT_MOVE | EFFECT_LINK, out effect);
        Log("TRACE: post-DoDragDrop GetKeyState(LB)=0x" + GetKeyState(0x01).ToString("X"));
        sw.Stop();
        done.Set();
        Log("OLE_DROP_RESULT effect=" + effect + " hr=0x" + hr.ToString("X8") + " ms=" + sw.ElapsedMilliseconds);
        Thread.Sleep(200); // let the injector watchdog see done and exit cleanly
        if (log != null) log.Dispose();
        return hr == 0 ? (int)(effect & 7u) : 125;
    }

    // Runs on the injector thread. For filedrop mode it also watchdogs the
    // DoDragDrop call: after LEFTUP, if the main thread hasn't reported done
    // within ~3s it taps ESC (cancel) and, failing that, kills the process
    // with exit code 124 so a stuck OLE session can never hang the harness.
    static void InjectDrag((int x, int y) from, (int x, int y) to, int steps, int stepMs, int holdMs, int settleMs, int startDelayMs, ManualResetEvent done, DropSource source, string[] files)
    {
        try
        {
            Thread.Sleep(startDelayMs);
            SendMouse(MOUSEEVENTF_ABSOLUTE, from.x, from.y);
            Thread.Sleep(80);
            SendMouse(MOUSEEVENTF_LEFTDOWN, from.x, from.y);
            Log("DIAG: DOWN at " + from);
            Thread.Sleep(holdMs);
            if (from == to) { SendMouse(MOUSEEVENTF_ABSOLUTE, from.x + 4, from.y); Thread.Sleep(stepMs); }
            for (int i = 1; i <= steps; i++)
            {
                int x = from.x + (to.x - from.x) * i / steps;
                int y = from.y + (to.y - from.y) * i / steps;
                SendMouse(MOUSEEVENTF_ABSOLUTE, x, y);
                Thread.Sleep(stepMs);
            }
            Thread.Sleep(settleMs);
            SendMouse(MOUSEEVENTF_LEFTUP, to.x, to.y);
            Log("DIAG: UP at " + to + " async(LB)=0x" + GetAsyncKeyState(0x01).ToString("X"));
            if (done == null) return; // inject mode: nothing to wait for

            for (int i = 0; i < 30 && !done.WaitOne(100); i++) { }
            if (!done.WaitOne(0))
            {
                Log("DIAG: watchdog — session still running after UP, tapping ESC");
                TapEscape();
                done.WaitOne(600);
                if (!done.WaitOne(0))
                {
                    TapEscape();
                    done.WaitOne(900);
                }
                if (!done.WaitOne(0))
                {
                    Log("DIAG: watchdog — stuck session, killing process");
                    Environment.Exit(124);
                }
            }
        }
        catch (Exception ex)
        {
            Log("DIAG: injector error " + ex.Message);
            Environment.Exit(125);
        }
    }

    static (int x, int y) ParsePoint(string s, int w, int h)
    {
        var parts = s.Split(',');
        return Clamp(int.Parse(parts[0]), int.Parse(parts[1]), w, h);
    }

    static (int x, int y) Clamp(int x, int y, int w, int h)
    {
        return (Math.Max(4, Math.Min(x, w - 4)), Math.Max(4, Math.Min(y, h - 4)));
    }
}
