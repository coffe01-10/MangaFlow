using System.Windows;

namespace MangaFlow.Native;

public partial class MainWindow
{
    private bool workflowFocused, workflowFullscreen;
    private WindowStyle workflowWindowStyle;
    private ResizeMode workflowResizeMode;
    private WindowState workflowWindowState;
    private Rect workflowWindowBounds;

    internal void SetWorkflowPresentation(bool focused, bool fullscreen)
    {
        workflowFocused = focused;
        if (fullscreen != workflowFullscreen)
        {
            if (fullscreen)
            {
                workflowWindowStyle = WindowStyle;
                workflowResizeMode = ResizeMode;
                workflowWindowState = WindowState;
                workflowWindowBounds = WindowState == WindowState.Normal
                    ? new Rect(double.IsNaN(Left) ? 0 : Left, double.IsNaN(Top) ? 0 : Top, Width, Height)
                    : RestoreBounds;
                WindowState = WindowState.Normal;
                WindowStyle = WindowStyle.None;
                ResizeMode = ResizeMode.NoResize;
                WindowState = WindowState.Maximized;
            }
            else
            {
                WindowState = WindowState.Normal;
                WindowStyle = workflowWindowStyle;
                ResizeMode = workflowResizeMode;
                if (!workflowWindowBounds.IsEmpty)
                {
                    Left = workflowWindowBounds.Left; Top = workflowWindowBounds.Top;
                    Width = workflowWindowBounds.Width; Height = workflowWindowBounds.Height;
                }
                WindowState = workflowWindowState;
            }
            workflowFullscreen = fullscreen;
        }
        ApplySidebar();
        ApplyWorkflowChrome();
    }

    private void ApplyWorkflowChrome()
    {
        var hidden = workflowFocused || workflowFullscreen;
        ShellTopbar.Visibility = hidden ? Visibility.Collapsed : Visibility.Visible;
        QueueArea.Visibility = hidden ? Visibility.Collapsed : Visibility.Visible;
        if (hidden)
        {
            TopbarRow.Height = new GridLength(0);
            SidebarColumn.Width = new GridLength(0);
            SidebarPanel.Visibility = Visibility.Collapsed;
            ContentHost.Margin = new Thickness(0);
        }
    }
}
