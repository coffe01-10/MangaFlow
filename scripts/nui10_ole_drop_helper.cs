// NUI-10 P4-3a OLE drag-drop helper: performs a REAL OLE drag (DoDragDrop,
// CF_HDROP FileDropList) from a dedicated process with a proper WinForms
// message pump. Two hard-won constraints (2026-09-20 live round):
//   1. DoDragDrop must run inside a pumped message loop — calling it from a
//      PowerShell host deadlocks its modal loop (never returns even for ESC).
//   2. DoDragDrop must be called FROM the carrier's own WM_LBUTTONDOWN
//      handler, exactly like a real Control.OnMouseDown → DoDragDrop. A press
//      injected before DoDragDrop is consumed by the queue before the drag
//      loop starts; the session never begins (no drag ghost, release unseen,
//      only ESC cancels the call at +4s).
//
// Usage: nui10_ole_drop_helper.exe <file> <fromX> <fromY> <toX> <toY> [steps]
// Output: OLE_DROP_RESULT <effect> <code>   (codes: 0 None 1 Copy 2 Move 4 Link)
//         plus diagnostic lines prefixed DIAG:.
using System;
using System.Collections.Specialized;
using System.Drawing;
using System.Runtime.InteropServices;
using System.Threading;
using System.Windows.Forms;

static class Program
{
    [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
    [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
    [DllImport("user32.dll")] public static extern bool AttachThreadInput(uint a, uint b, bool attach);
    [DllImport("kernel32.dll")] public static extern uint GetCurrentThreadId();

    static CarrierForm carrier;

    [STAThread]
    static void Main(string[] args)
    {
        if (args.Length < 5) { Console.WriteLine("usage: file fromX fromY toX toY [steps]"); Environment.Exit(2); }
        string file = System.IO.Path.GetFullPath(args[0]);
        if (!System.IO.File.Exists(file)) { Console.WriteLine("DIAG: file missing"); Environment.Exit(2); }
        int fx = int.Parse(args[1]), fy = int.Parse(args[2]);
        int tx = int.Parse(args[3]), ty = int.Parse(args[4]);
        int steps = args.Length > 5 ? int.Parse(args[5]) : 30;

        carrier = new CarrierForm(fx, fy, file, tx, ty, steps);
        Application.Run(carrier);
    }

    sealed class CarrierForm : Form
    {
        readonly string file; readonly int fx, fy, tx, ty, steps;
        bool dragStarted;

        internal CarrierForm(int fx, int fy, string file, int tx, int ty, int steps)
        {
            this.fx = fx; this.fy = fy; this.file = file; this.tx = tx; this.ty = ty; this.steps = steps;
            FormBorderStyle = FormBorderStyle.None; ShowInTaskbar = false; TopMost = true;
            StartPosition = FormStartPosition.Manual; Location = new Point(fx, fy);
            Size = new Size(48, 32); Opacity = 0.35; Text = "nui10 ole drop helper";
            Shown += (s2, e2) => BeginInvoke(new Action(Press));
        }

        void TakeForeground()
        {
            uint pid; uint fg = GetWindowThreadProcessId(GetForegroundWindow(), out pid);
            uint me = GetCurrentThreadId();
            if (fg != 0 && fg != me && AttachThreadInput(fg, me, true)) { SetForegroundWindow(Handle); AttachThreadInput(fg, me, false); }
            else SetForegroundWindow(Handle);
        }

        // Press phase: grab foreground, park the cursor on the carrier, inject
        // LEFT DOWN. The queue delivers WM_LBUTTONDOWN to WndProc, which starts
        // the drag the same way a real mouse-down handler would.
        void Press()
        {
            for (int i = 0; i < 20 && GetForegroundWindow() != Handle; i++) { TakeForeground(); Application.DoEvents(); Thread.Sleep(100); }
            Console.WriteLine("DIAG: carrier foreground = " + (GetForegroundWindow() == Handle));
            var walk = new Thread(WalkCursor) { IsBackground = true };
            walk.Start();
            SetCursor(fx + 20, fy + 12);
            Thread.Sleep(120);
            mouse_event(0x0002, 0, 0, 0, UIntPtr.Zero); // LEFT DOWN — WndProc takes it from here
            Console.WriteLine("DIAG: press injected at " + (fx + 20) + "," + (fy + 12));
        }

        static void TapEscape()
        {
            keybd_event(0x1B, 0, 0, UIntPtr.Zero); Thread.Sleep(80);
            keybd_event(0x1B, 0, 2, UIntPtr.Zero);
        }

        void WalkCursor()
        {
            try
            {
                Thread.Sleep(700);
                int w = GetSystemMetrics(0), h = GetSystemMetrics(1);
                for (int i = 1; i <= steps; i++)
                {
                    int x = fx + (tx - fx) * i / steps + 20;
                    int y = fy + (ty - fy) * i / steps + 12;
                    SetCursor(Math.Max(0, Math.Min(x, w - 1)), Math.Max(0, Math.Min(y, h - 1)));
                    Thread.Sleep(25);
                }
                Thread.Sleep(150);
                mouse_event(0x0004, 0, 0, 0, UIntPtr.Zero); // LEFT UP
                Console.WriteLine("DIAG: released at " + tx + "," + ty);
                Thread.Sleep(2500);
                TapEscape(); Thread.Sleep(300); TapEscape();
            }
            catch (Exception ex) { Console.WriteLine("DIAG: walk error " + ex.Message); }
        }

        protected override void WndProc(ref Message m)
        {
            const int WM_LBUTTONDOWN = 0x0201;
            if (m.Msg == WM_LBUTTONDOWN && !dragStarted)
            {
                dragStarted = true;
                Console.WriteLine("DIAG: WM_LBUTTONDOWN received, starting DoDragDrop");
                var data = new DataObject();
                var list = new StringCollection { file };
                data.SetFileDropList(list);
                var allowed = DragDropEffects.Copy | DragDropEffects.Move | DragDropEffects.Link;
                var sw = System.Diagnostics.Stopwatch.StartNew();
                var effect = DoDragDrop(data, allowed);
                sw.Stop();
                Console.WriteLine("OLE_DROP_RESULT " + effect + " " + (int)effect);
                Console.WriteLine("DIAG: DoDragDrop took " + sw.ElapsedMilliseconds + "ms");
                BeginInvoke(new Action(Application.Exit));
                // no base.WndProc — the press belongs to the drag now
                return;
            }
            base.WndProc(ref m);
        }

        [DllImport("user32.dll")] static extern int GetSystemMetrics(int i);
        [DllImport("user32.dll")] static extern bool SetCursorPos(int x, int y);
        [DllImport("user32.dll")] static extern void mouse_event(uint flags, int dx, int dy, uint data, UIntPtr extra);
        [DllImport("user32.dll")] static extern void keybd_event(byte key, byte scan, uint flags, UIntPtr extra);
        [DllImport("user32.dll")] static extern void SendInput(uint n, INPUT[] p, int cb);

        [StructLayout(LayoutKind.Sequential)] struct MOUSEINPUT { public int dx, dy; public uint mouseData, dwFlags, time; public IntPtr extra; }
        [StructLayout(LayoutKind.Sequential)] struct INPUT { public uint type; public MOUSEINPUT mi; }

        static void SetCursor(int x, int y)
        {
            int w = GetSystemMetrics(0), h = GetSystemMetrics(1);
            var one = new INPUT[1];
            one[0].type = 0;
            one[0].mi.dx = x * 65536 / w;
            one[0].mi.dy = y * 65536 / h;
            one[0].mi.dwFlags = 0x0001 | 0x8000; // MOVE | ABSOLUTE
            SendInput(1, one, Marshal.SizeOf(typeof(INPUT)));
        }
    }
}
