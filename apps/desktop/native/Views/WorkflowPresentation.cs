using System.Windows;
using System.Windows.Controls;
using System.Windows.Controls.Primitives;
using System.Windows.Input;

namespace MangaFlow.Native.Views;

public sealed partial class WorkflowView
{
    private readonly WrapPanel statusMetrics = new();
    private FrameworkElement runnerPane = null!, minimapHost = null!;
    private Button focusButton = null!, fullscreenButton = null!;
    private bool focusMode;
    private (bool Library, bool Inspector, Visibility Metrics, Visibility Runner, Visibility Map, string? Drawer) savedPresentation;

    private Button BuildViewMenu()
    {
        var button = FlowAction("视图 ▾", (_, _) => { });
        button.ToolTip = "显示或收起面板与辅助信息";
        button.Click += (_, _) =>
        {
            var menu = new ContextMenu
            {
                PlacementTarget = button, Placement = PlacementMode.Bottom,
                Background = FlowResource("Surface"), Foreground = FlowResource("Ink"),
                BorderBrush = FlowResource("Line"), FontSize = 13,
            };
            void Toggle(string label, bool enabled, Action<bool> apply)
            {
                var item = new MenuItem { Header = label, IsCheckable = true, IsChecked = enabled };
                item.Click += (_, _) => apply(item.IsChecked);
                menu.Items.Add(item);
            }
            Toggle("节点库", IsLibraryVisible, ToggleLibrary);
            Toggle("属性面板", IsInspectorVisible, ToggleInspector);
            menu.Items.Add(new Separator());
            Toggle("版本与保存状态", statusMetrics.Visibility == Visibility.Visible, show => statusMetrics.Visibility = Show(show));
            Toggle("运行面板", runnerPane.Visibility == Visibility.Visible, show => runnerPane.Visibility = Show(show));
            Toggle("小地图", minimapHost.Visibility == Visibility.Visible, show => minimapHost.Visibility = Show(show));
            button.ContextMenu = menu;
            menu.IsOpen = true;
        };
        return button;
    }

    private static Visibility Show(bool visible) => visible ? Visibility.Visible : Visibility.Collapsed;

    private void ToggleFocusMode()
    {
        if (!focusMode)
        {
            savedPresentation = (libraryOpen, inspectorOpen, statusMetrics.Visibility,
                runnerPane.Visibility, minimapHost.Visibility, compactPane);
            libraryOpen = inspectorOpen = false;
            compactPane = null;
            statusMetrics.Visibility = runnerPane.Visibility = minimapHost.Visibility = Visibility.Collapsed;
        }
        else
        {
            (libraryOpen, inspectorOpen, statusMetrics.Visibility, runnerPane.Visibility,
                minimapHost.Visibility, compactPane) = savedPresentation;
        }
        focusMode = !focusMode;
        focusButton.Content = focusMode ? "退出专注" : "专注模式";
        UpdateSidePanes();
        UpdateShellPresentation();
    }

    private void ToggleStudioFullscreen()
    {
        studioFullscreen = !studioFullscreen;
        fullscreenButton.Content = studioFullscreen ? "退出全屏" : "全屏";
        UpdateShellPresentation();
    }

    private void UpdateShellPresentation()
    {
        if (Host is MainWindow window) window.SetWorkflowPresentation(focusMode, studioFullscreen);
    }

    internal bool HandlePresentationKey(Key key, ModifierKeys modifiers)
    {
        if (key == Key.F11 && modifiers == ModifierKeys.None) { ToggleStudioFullscreen(); return true; }
        if (key == Key.Enter && modifiers == (ModifierKeys.Control | ModifierKeys.Shift)) { ToggleFocusMode(); return true; }
        if (key != Key.Escape) return false;
        // Escape first cancels an in-progress wire, then leaves fullscreen, then focus mode.
        if (cancelWire != null) { cancelWire(); return true; }
        if (studioFullscreen) { ToggleStudioFullscreen(); return true; }
        if (focusMode) { ToggleFocusMode(); return true; }
        return false;
    }

    private void OnStudioKeyDown(object sender, KeyEventArgs e)
    {
        if (!e.IsRepeat && HandlePresentationKey(e.Key, Keyboard.Modifiers)) e.Handled = true;
    }

    internal bool HandleCanvasWheel(int delta, ModifierKeys modifiers)
    {
        if ((modifiers & ModifierKeys.Control) != 0)
        {
            if (delta != 0) ZoomCanvas(delta > 0 ? 1.1 : 1 / 1.1);
            return true;
        }
        if ((modifiers & ModifierKeys.Shift) == 0) return false;
        canvasScroll.ScrollToHorizontalOffset(canvasScroll.HorizontalOffset - delta);
        return true;
    }

    public override void Deactivate()
    {
        if (studioFullscreen) ToggleStudioFullscreen();
        if (focusMode) ToggleFocusMode();
        base.Deactivate();
    }
}
