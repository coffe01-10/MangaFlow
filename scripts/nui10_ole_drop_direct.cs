// NUI-10 P4-3a OLE drop DIRECT verifier: invokes the target window's
// registered IDropTarget (RegisterDragDrop stores it in the window property
// "OleDropTargetInterface") with a genuine shell data object carrying
// CF_HDROP. This exercises the app's real OLE drop channel end to end
// (OleDropTarget -> WPF DragEventArgs -> Drop handler) without needing an
// OS input-driven drag session, which cannot be synthesized headlessly
// (two tool generations + helper exe proved it on 2026-09-20: the OLE modal
// loop never engages injected input).
//
// The data object comes from OleGetClipboard after Clipboard.SetFileDropList
// — a real shell data object, exactly what Explorer supplies on a file drag.
//
// Usage: nui10_ole_drop_direct.exe <hwnd> <x> <y> <file>
// x/y: screen coords inside the drop zone (the target hit-tests them).
// Output: DIRECT_DROP <stage> <effect> lines; final DIRECT_DROP_VERDICT PASS|FAIL.
using System;
using System.Collections.Specialized;
using System.Runtime.InteropServices;
using System.Runtime.InteropServices.ComTypes;
using System.Windows.Forms;

[StructLayout(LayoutKind.Sequential)]
struct POINTL { public int x; public int y; }

[ComImport, Guid("00000122-0000-0000-C000-000000000046"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IOleDropTarget
{
    [PreserveSig] int DragEnter(System.Runtime.InteropServices.ComTypes.IDataObject pDataObj, int grfKeyState, POINTL pt, out int pdwEffect);
    [PreserveSig] int DragOver(int grfKeyState, POINTL pt, out int pdwEffect);
    [PreserveSig] int DragLeave();
    [PreserveSig] int Drop(System.Runtime.InteropServices.ComTypes.IDataObject pDataObj, int grfKeyState, POINTL pt, out int pdwEffect);
}

static class Program
{
    [DllImport("user32.dll", CharSet = CharSet.Unicode)] static extern IntPtr GetProp(IntPtr h, string name);
    [DllImport("ole32.dll")] static extern int OleGetClipboard(out System.Runtime.InteropServices.ComTypes.IDataObject obj);

    [STAThread]
    static void Main(string[] args)
    {
        if (args.Length < 4) { Console.WriteLine("usage: hwnd x y file"); Environment.Exit(2); }
        IntPtr hwnd = new IntPtr(long.Parse(args[0]));
        int x = int.Parse(args[1]), y = int.Parse(args[2]);
        string file = System.IO.Path.GetFullPath(args[3]);
        if (!System.IO.File.Exists(file)) { Console.WriteLine("file missing"); Environment.Exit(2); }

        IntPtr pTarget = GetProp(hwnd, "OleDropTargetInterface");
        if (pTarget == IntPtr.Zero) { Console.WriteLine("DIAG: no OleDropTargetInterface property — window never registered a drop target"); Environment.Exit(1); }
        var target = (IOleDropTarget)Marshal.GetObjectForIUnknown(pTarget);
        Console.WriteLine("DIAG: IDropTarget resolved from window property");

        Clipboard.SetFileDropList(new StringCollection { file });
        System.Runtime.InteropServices.ComTypes.IDataObject data;
        int hr = OleGetClipboard(out data);
        if (hr != 0) { Console.WriteLine("DIAG: OleGetClipboard failed 0x" + hr.ToString("X")); Environment.Exit(1); }
        Console.WriteLine("DIAG: shell clipboard data object with CF_HDROP acquired");

        const int MK_LBUTTON = 0x0001;
        var pt = new POINTL { x = x, y = y };
        int effect;

        int r1 = target.DragEnter(data, MK_LBUTTON, pt, out effect);
        Console.WriteLine("DIRECT_DROP DragEnter hr=0x" + r1.ToString("X") + " effect=" + effect);
        int enterEffect = effect;

        int r2 = target.DragOver(MK_LBUTTON, pt, out effect);
        Console.WriteLine("DIRECT_DROP DragOver hr=0x" + r2.ToString("X") + " effect=" + effect);

        int r3 = target.Drop(data, MK_LBUTTON, pt, out effect);
        Console.WriteLine("DIRECT_DROP Drop hr=0x" + r3.ToString("X") + " effect=" + effect);

        bool pass = r1 == 0 && r2 == 0 && r3 == 0 && enterEffect == 1 && effect == 1;
        Console.WriteLine("DIRECT_DROP_VERDICT " + (pass ? "PASS" : "FAIL"));
    }
}
