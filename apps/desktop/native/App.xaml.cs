using System.IO;
using System.Security.Cryptography;
using System.Text;
using System.Windows;
using System.Windows.Input;

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
        var key = Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(data.TrimEnd(Path.DirectorySeparatorChar).ToUpperInvariant())))[..24];
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
}
