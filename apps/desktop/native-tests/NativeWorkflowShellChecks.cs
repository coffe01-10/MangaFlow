using System.IO;
using System.Reflection;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;
using MangaFlow.Native;
using MangaFlow.Native.Services;
using MangaFlow.Native.Views;

internal static class NativeWorkflowShellChecks
{
    internal static void Run(string output)
    {
        var root = Path.Combine(output, "shell-prefs-" + Guid.NewGuid().ToString("N"));
        var previous = (string)typeof(KeyValueStore).GetField("path", BindingFlags.NonPublic | BindingFlags.Static)!.GetValue(null)!;
        MainWindow? window = null;
        try
        {
            new Preferences { SidebarCollapsed = true }.Save(root);
            window = new MainWindow("", root);
            typeof(MainWindow).GetField("page", BindingFlags.NonPublic | BindingFlags.Instance)!.SetValue(window, "workflow");
            var view = new WorkflowView();
            ((ContentControl)window.FindName("ContentHost")).Content = view;
            window.SetWorkflowPresentation(false, false);
            var initialStyle = window.WindowStyle; var initialResize = window.ResizeMode;
            var initialState = window.WindowState; var initialWidth = window.Width; var initialHeight = window.Height;
            var chrome = (FrameworkElement)window.FindName("ShellTopbar");
            var queue = (FrameworkElement)window.FindName("QueueArea");
            var sidebar = (ColumnDefinition)window.FindName("SidebarColumn");
            Require(((Button)window.FindName("WorkflowNav")).Visibility == Visibility.Collapsed, "current workflow hides redundant navigation entry");
            Require(view.HandlePresentationKey(Key.F11, ModifierKeys.None), "F11 enters hosted fullscreen");
            Require(window.WindowStyle == WindowStyle.None && window.WindowState == WindowState.Maximized
                && window.ResizeMode == ResizeMode.NoResize, "fullscreen removes frame and maximizes host");
            Require(chrome.Visibility == Visibility.Collapsed && queue.Visibility == Visibility.Collapsed && sidebar.Width.Value == 0,
                "fullscreen removes shell chrome without overwriting sidebar preference");
            view.HandlePresentationKey(Key.Escape, ModifierKeys.None);
            Require(window.WindowStyle == initialStyle && window.ResizeMode == initialResize && window.WindowState == initialState
                && window.Width == initialWidth && window.Height == initialHeight, "fullscreen restores original window shape");
            Require(sidebar.Width.Value == 64 && chrome.Visibility == Visibility.Visible && queue.Visibility == Visibility.Visible,
                "fullscreen restores collapsed sidebar and queue container");
            view.HandlePresentationKey(Key.Enter, ModifierKeys.Control | ModifierKeys.Shift);
            Require(chrome.Visibility == Visibility.Collapsed && window.WindowStyle == initialStyle, "focus hides chrome without changing window state");
            view.HandlePresentationKey(Key.F11, ModifierKeys.None);
            view.Deactivate();
            Require(chrome.Visibility == Visibility.Visible && sidebar.Width.Value == 64 && window.WindowStyle == initialStyle,
                "navigation away restores shell and fullscreen state");
            window.WindowState = WindowState.Maximized;
            view.HandlePresentationKey(Key.F11, ModifierKeys.None); view.HandlePresentationKey(Key.Escape, ModifierKeys.None);
            Require(window.WindowState == WindowState.Maximized, "already maximized window stays maximized after fullscreen");
            Console.WriteLine("PASS: workflow fullscreen window state, focused shell, Escape, restored navigation preferences and leave cleanup.");
        }
        finally
        {
            if (window != null)
            {
                typeof(MainWindow).GetField("closed", BindingFlags.NonPublic | BindingFlags.Instance)!.SetValue(window, true);
                window.Close();
            }
            KeyValueStore.UseLocation(previous);
            if (Directory.Exists(root)) Directory.Delete(root, true);
        }
    }

    private static void Require(bool condition, string message) { if (!condition) throw new Exception(message); }
}
