using System.IO;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text;
using System.Windows;
using System.Windows.Input;
using Microsoft.Win32.SafeHandles;

namespace MangaFlow.Native;

public partial class App : Application
{
    private Mutex? instance;
    private EventWaitHandle? activation;
    private RegisteredWaitHandle? activationWait;
    private bool ownsMutex;

    static App()
    {
        // Web parity: Enter activates the focused button/toggle exactly like Space.
        // Applies to every ButtonBase (incl. filter pills, checkboxes) across all windows.
        EventManager.RegisterClassHandler(typeof(System.Windows.Controls.Primitives.ButtonBase),
            UIElement.PreviewKeyDownEvent, new KeyEventHandler(ActivateOnEnter));
    }

    private static void ActivateOnEnter(object sender, KeyEventArgs e)
    {
        // Held-down Enter must not machine-gun clicks: real presses fire once per key-down.
        if (e.Key != Key.Enter || e.IsRepeat || e.Handled || e.OriginalSource != sender) return;
        e.Handled = true;
        // Drive the real click pipeline (ButtonBase.OnClick): plain buttons raise Click
        // and toggles/checkboxes/radios also flip their state.
        var button = (System.Windows.Controls.Primitives.ButtonBase)sender;
        button.GetType().GetMethod("OnClick", System.Reflection.BindingFlags.Instance | System.Reflection.BindingFlags.NonPublic)
            ?.Invoke(button, null);
    }

    protected override void OnStartup(StartupEventArgs e)
    {
        base.OnStartup(e);
        var repository = Environment.GetEnvironmentVariable("MANGAFLOW_NATIVE_REPO");
        if (string.IsNullOrWhiteSpace(repository))
        {
            var dir = new DirectoryInfo(AppContext.BaseDirectory);
            while (dir != null && !File.Exists(Path.Combine(dir.FullName, "apps", "api", "alembic.ini"))) dir = dir.Parent;
            repository = dir?.FullName;
        }
        if (repository == null)
        {
            MessageBox.Show("未找到 MangaFlow 后端。请使用 start-native.ps1 启动。", "MangaFlow");
            Shutdown(1);
            return;
        }
        var data = Path.GetFullPath(Environment.GetEnvironmentVariable("MANGAFLOW_DESKTOP_USER_DATA")
            ?? Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "MangaFlow", "Native"));
        // #410: the mutex must key on the CANONICAL directory, not the spelling the
        // launcher happened to use (subst drive / junction / 8.3 alias of one directory
        // must map to one mutex, or two clients run two sidecar API servers on one SQLite).
        var key = SingleInstanceKey(CanonicalizeUserDataPath(data));
        instance = new Mutex(false, "Local\\MangaFlow.Native." + key);
        try { ownsMutex = instance.WaitOne(0); }
        catch (AbandonedMutexException) { ownsMutex = true; }
        activation = new EventWaitHandle(false, EventResetMode.AutoReset, "Local\\MangaFlow.Native.Activate." + key);
        if (!ownsMutex)
        {
            activation.Set();
            Shutdown();
            return;
        }
        var window = new MainWindow(repository, data);
        MainWindow = window;
        activationWait = ThreadPool.RegisterWaitForSingleObject(activation, (_, _) => Dispatcher.BeginInvoke(() =>
        {
            if (window.WindowState == WindowState.Minimized) window.WindowState = WindowState.Normal;
            window.Show();
            window.Activate();
        }), null, Timeout.Infinite, false);
        window.Show();
    }

    protected override void OnExit(ExitEventArgs e)
    {
        activationWait?.Unregister(null);
        activation?.Dispose();
        if (ownsMutex) instance?.ReleaseMutex();
        instance?.Dispose();
        base.OnExit(e);
    }

    // #410: resolve junctions / subst drives / 8.3 short names of the user-data
    // directory to its final path BEFORE hashing, so every spelling of one
    // directory yields one mutex. Any resolution failure falls back to the
    // lexical full path (the pre-#410 key), never blocks startup, and never throws.
    internal static string CanonicalizeUserDataPath(string userData)
    {
        var full = Path.GetFullPath(userData);
        try { return TryResolveFinalPath(full) ?? full; }
        catch { return full; }
    }

    // Exact mutex-key scheme, extracted so checks can pin it (a silent change
    // would orphan every running instance): SHA256 over the UTF-8 bytes of the
    // canonical path with its trailing separator trimmed, upper-cased
    // invariantly, rendered as upper-case hex, first 24 characters.
    internal static string SingleInstanceKey(string canonicalUserDataPath) =>
        Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(
            canonicalUserDataPath.TrimEnd(Path.DirectorySeparatorChar).ToUpperInvariant())))[..24];

    // Walk from the leaf up to the deepest EXISTING ancestor, resolve that
    // ancestor to its final path, and re-append the not-yet-created tail. This
    // keeps the key stable across the directory's first creation: the junction
    // or subst ancestor resolves the same way whether or not the leaf exists.
    private static string? TryResolveFinalPath(string fullPath)
    {
        if (!OperatingSystem.IsWindows()) return null;
        var candidate = fullPath;
        var tail = "";
        while (true)
        {
            var final = TryGetFinalPathByHandle(candidate);
            if (final != null) return tail.Length == 0 ? final : final + tail;
            var parent = Directory.GetParent(candidate);
            if (parent == null) return null;  // reached a root without a resolvable ancestor
            tail = candidate.Substring(parent.FullName.Length) + tail;
            candidate = parent.FullName;
        }
    }

    private static string? TryGetFinalPathByHandle(string directoryPath)
    {
        // Open with BACKUP_SEMANTICS so directory handles open without any access right;
        // share everything so concurrent owners of the directory are not disturbed.
        using var handle = CreateFileW(directoryPath, 0u, 7u, IntPtr.Zero, 3u, 0x02000000u, IntPtr.Zero);
        if (handle.IsInvalid) return null;
        var buffer = new StringBuilder(512);
        var length = GetFinalPathNameByHandleW(handle, buffer, (uint)buffer.Capacity, 0u);
        if (length == 0) return null;
        if (length >= (uint)buffer.Capacity)
        {
            buffer = new StringBuilder((int)length + 1);
            length = GetFinalPathNameByHandleW(handle, buffer, (uint)buffer.Capacity, 0u);
            if (length == 0 || length >= (uint)buffer.Capacity) return null;
        }
        var final = buffer.ToString();
        // Normalize the \\?\ device-morphic prefix away: the key must not depend on it.
        if (final.StartsWith(@"\\?\UNC\", StringComparison.Ordinal)) final = @"\\" + final[8..];
        else if (final.StartsWith(@"\\?\", StringComparison.Ordinal)) final = final[4..];
        return final.Length == 0 ? null : final;
    }

    [DllImport("kernel32.dll", SetLastError = true, CharSet = CharSet.Unicode, ExactSpelling = true)]
    private static extern SafeFileHandle CreateFileW(string fileName, uint desiredAccess, uint shareMode,
        IntPtr securityAttributes, uint creationDisposition, uint flagsAndAttributes, IntPtr templateFile);

    [DllImport("kernel32.dll", SetLastError = true, CharSet = CharSet.Unicode, ExactSpelling = true)]
    private static extern uint GetFinalPathNameByHandleW(SafeFileHandle handle, StringBuilder buffer,
        uint bufferLength, uint flags);
}
