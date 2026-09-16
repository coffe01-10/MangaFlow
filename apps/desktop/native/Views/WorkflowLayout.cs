using System.Windows;
using System.Windows.Controls;
using System.Windows.Data;
using System.Windows.Media;
using MangaFlow.Native.Controls;

namespace MangaFlow.Native.Views;

public sealed partial class WorkflowView
{
    private readonly Grid studioBody = new();
    private FrameworkElement libraryPane = null!, inspectorPane = null!;
    private bool libraryOpen = true, inspectorOpen = true, compactStudio;
    private Button libraryToggle = null!, inspectorToggle = null!;
    private Button undoButton = null!, redoButton = null!, copyButton = null!;
    private Button runNodeButton = null!, runFromButton = null!, retryCreateButton = null!;
    private readonly TextBlock draftValue = MetricValue("—"), publishedValue = MetricValue("—"),
        saveValue = MetricValue("正在读取"), validationValue = MetricValue("未校验");
    private readonly TextBlock zoomLabel = new() { Text = "75%", FontSize = 12, VerticalAlignment = VerticalAlignment.Center, Margin = new Thickness(10, 0, 10, 0) };
    private readonly TextBlock inspectorHeading = MetricValue("属性面板");
    private static Brush FlowBrush(string hex) => (Brush)new BrushConverter().ConvertFromString(hex)!;
    private static TextBlock MetricValue(string text) => new() { Text = text, FontSize = 13, FontWeight = FontWeights.SemiBold, Foreground = FlowBrush("#e9e6dd"), TextWrapping = TextWrapping.Wrap };

    private void BuildStudio()
    {
        Resources.MergedDictionaries.Add(new ResourceDictionary { Source = new Uri("/MangaFlow.Native;component/Views/WorkflowTheme.xaml", UriKind.Relative) });
        Background = FlowBrush("#1a1d1b"); Foreground = FlowBrush("#e9e6dd");
        var root = new Grid { Background = Background };
        root.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        root.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        root.RowDefinitions.Add(new RowDefinition());
        root.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        root.Children.Add(BuildTopBar());
        var status = BuildFlowStatus(); Grid.SetRow(status, 1); root.Children.Add(status);
        studioBody.ClipToBounds = true;
        studioBody.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(238) });
        studioBody.ColumnDefinitions.Add(new ColumnDefinition());
        studioBody.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(286) });
        Grid.SetRow(studioBody, 2); root.Children.Add(studioBody);

        libraryPane = SidePane("NODE LIBRARY", MetricValue("节点库"), library, () => ToggleLibrary(false));
        studioBody.Children.Add(libraryPane);
        var inspectorDock = new Grid();
        inspectorDock.RowDefinitions.Add(new RowDefinition { Height = new GridLength(1, GridUnitType.Star) });
        inspectorDock.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto, MaxHeight = 150 });
        inspectorDock.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto, MaxHeight = 160 });
        inspectorDock.Children.Add(new ScrollViewer { Content = inspector, VerticalScrollBarVisibility = ScrollBarVisibility.Auto, Padding = new Thickness(12) });
        var versionsScroll = new ScrollViewer
        {
            Content = versionList, VerticalScrollBarVisibility = ScrollBarVisibility.Auto,
            Padding = new Thickness(12, 8, 12, 8), MaxHeight = 140,
        };
        var versionsPane = new Border
        {
            BorderBrush = FlowBrush("#383d39"), BorderThickness = new Thickness(0, 1, 0, 0), Child = versionsScroll,
        };
        Grid.SetRow(versionsPane, 1); inspectorDock.Children.Add(versionsPane);
        var history = new DockPanel();
        var historyLabel = new Border { BorderBrush = FlowBrush("#383d39"), BorderThickness = new Thickness(0, 1, 0, 1),
            Padding = new Thickness(12, 10, 12, 10), Child = MetricValue("运行历史") };
        DockPanel.SetDock(historyLabel, Dock.Top); history.Children.Add(historyLabel);
        history.Children.Add(new ScrollViewer { Content = runHistory, VerticalScrollBarVisibility = ScrollBarVisibility.Auto, Padding = new Thickness(12), MaxHeight = 112 });
        Grid.SetRow(history, 2); inspectorDock.Children.Add(history);
        inspectorPane = SidePane("INSPECTOR", inspectorHeading, inspectorDock, () => ToggleInspector(false), scroll: false);
        Grid.SetColumn(inspectorPane, 2); studioBody.Children.Add(inspectorPane);

        var center = new DockPanel { Background = FlowBrush("#1a1d1b"), ClipToBounds = true };
        var tools = BuildCanvasTools(); DockPanel.SetDock(tools, Dock.Top); center.Children.Add(tools);
        var zoom = new WrapPanel { Margin = new Thickness(12, 8, 12, 8) };
        zoom.Children.Add(FlowAction("−", (_, _) => ZoomCanvas(1 / 1.1)));
        zoom.Children.Add(zoomLabel);
        zoom.Children.Add(FlowAction("+", (_, _) => ZoomCanvas(1.1)));
        zoom.Children.Add(FlowAction("100%", (_, _) => { scale = 1; ApplyView(); }));
        var help = new TextBlock { Text = "Ctrl+滚轮缩放 · 空格/中键拖动画布 · 拖空框选", FontSize = 11, Foreground = FlowBrush("#a4ada7"),
            VerticalAlignment = VerticalAlignment.Center, Margin = new Thickness(12, 0, 0, 0), TextWrapping = TextWrapping.Wrap };
        zoom.Children.Add(help);
        zoom.Children.Add(FlowAction("全屏工作区", (_, _) => ToggleStudioFullscreen()));
        DockPanel.SetDock(zoom, Dock.Bottom); center.Children.Add(zoom);
        canvas.Background = CanvasDots();
        canvasScroll.Content = canvas; canvasScroll.Background = FlowBrush("#1a1d1b");
        canvasScroll.VerticalScrollBarVisibility = canvasScroll.HorizontalScrollBarVisibility = ScrollBarVisibility.Auto;
        var canvasHost = new Grid();
        canvasHost.Children.Add(canvasScroll);
        minimap.Background = FlowBrush("#1a1d1b");
        minimap.ClipToBounds = true;
        System.Windows.Automation.AutomationProperties.SetName(minimap, "工作流小地图");
        minimap.MouseLeftButtonDown += OnMinimapNavigate;
        var minimapHost = new Border
        {
            Width = 176, Height = 118, HorizontalAlignment = HorizontalAlignment.Right, VerticalAlignment = VerticalAlignment.Bottom,
            Margin = new Thickness(0, 0, 12, 12), BorderBrush = FlowBrush("#383d39"), BorderThickness = new Thickness(1),
            Background = FlowBrush("#171a18"), Child = minimap, Padding = new Thickness(4),
        };
        canvasHost.Children.Add(minimapHost);
        center.Children.Add(canvasHost); Grid.SetColumn(center, 1); studioBody.Children.Add(center);
        var runner = BuildRunner(); Grid.SetRow(runner, 3); root.Children.Add(runner);
        Content = root;
        SizeChanged += (_, e) =>
        {
            bool next = e.NewSize.Width < 980;
            if (next != compactStudio) { compactStudio = next; libraryOpen = inspectorOpen = !next; }
            UpdateSidePanes();
        };
        UpdateSidePanes(); RefreshCanvasButtons();
    }

    private FrameworkElement SidePane(string caption, TextBlock heading, UIElement content, Action close, bool scroll = true)
    {
        var dock = new DockPanel { Background = FlowBrush("#202421") };
        var labels = new StackPanel();
        labels.Children.Add(new TextBlock { Text = caption, FontSize = 10, Foreground = FlowBrush("#a4ada7") });
        heading.FontFamily = (FontFamily)Application.Current.FindResource("Serif");
        heading.Margin = new Thickness(0, 3, 0, 0); labels.Children.Add(heading);
        var closeButton = FlowAction("×", (_, _) => close());
        System.Windows.Automation.AutomationProperties.SetName(closeButton, caption == "INSPECTOR" ? "关闭属性面板" : "关闭节点库");
        closeButton.BorderThickness = new Thickness(0);
        var header = new Border { BorderBrush = FlowBrush("#383d39"), BorderThickness = new Thickness(0, 0, 0, 1),
            Padding = new Thickness(12, 8, 8, 8), Child = new PageHeading(labels, closeButton) };
        DockPanel.SetDock(header, Dock.Top); dock.Children.Add(header);
        dock.Children.Add(scroll ? new ScrollViewer { Content = content, VerticalScrollBarVisibility = ScrollBarVisibility.Auto, Padding = new Thickness(12) } : content);
        return new Border { Background = FlowBrush("#202421"), BorderBrush = FlowBrush("#383d39"),
            BorderThickness = new Thickness(1, 0, 1, 0), Child = dock };
    }

    private FrameworkElement BuildFlowStatus()
    {
        var metrics = new WrapPanel();
        foreach (var (label, value) in new[] { ("草稿版本", draftValue), ("已发布版本", publishedValue), ("保存状态", saveValue), ("校验问题", validationValue) })
        {
            var stack = new StackPanel { VerticalAlignment = VerticalAlignment.Center };
            stack.Children.Add(new TextBlock { Text = label, FontSize = 11, Foreground = FlowBrush("#9aa39d") });
            value.Margin = new Thickness(0, 3, 0, 0); stack.Children.Add(value);
            metrics.Children.Add(new Border { Width = 126, MinHeight = 44, Padding = new Thickness(12, 2, 12, 2),
                BorderBrush = FlowBrush("#383d39"), BorderThickness = new Thickness(0, 0, 1, 0), Child = stack });
        }
        var stackAll = new StackPanel();
        stackAll.Children.Add(new PageHeading(metrics, FlowAction("运行已发布流程", async (_, _) => await RunAsync([], []), "CompactInk")));
        statusLine.FontSize = 11; statusLine.TextWrapping = TextWrapping.Wrap; statusLine.Margin = new Thickness(12, 6, 12, 0);
        var noticeStyle = new Style(typeof(TextBlock));
        var hidden = new DataTrigger { Binding = new Binding("Text") { RelativeSource = RelativeSource.Self }, Value = "" };
        hidden.Setters.Add(new Setter(VisibilityProperty, Visibility.Collapsed)); noticeStyle.Triggers.Add(hidden); statusLine.Style = noticeStyle;
        stackAll.Children.Add(statusLine);
        retryCreateButton = FlowAction("重试创建", async (_, _) => await RetryCreateMissingWorkflowsAsync(), "Compact");
        retryCreateButton.Margin = new Thickness(12, 6, 0, 0);
        retryCreateButton.Visibility = Visibility.Collapsed;
        stackAll.Children.Add(retryCreateButton);
        return new Border { Child = stackAll, Background = FlowBrush("#171a18"), BorderBrush = FlowBrush("#383d39"),
            BorderThickness = new Thickness(0, 0, 0, 1), Padding = new Thickness(0, 6, 12, 6) };
    }

    private FrameworkElement BuildCanvasTools()
    {
        var bar = new WrapPanel { Margin = new Thickness(10, 10, 10, 4) };
        libraryToggle = FlowAction("节点库", (_, _) => ToggleLibrary(true));
        inspectorToggle = FlowAction("属性", (_, _) => ToggleInspector(true));
        undoButton = FlowAction("撤销", (_, _) => Undo()); redoButton = FlowAction("重做", (_, _) => Redo());
        copyButton = FlowAction("复制", (_, _) => DuplicateSelected());
        removeButton = FlowAction("删除", (_, _) => DeleteSelection());
        foreach (var button in new[] { libraryToggle, undoButton, redoButton, FlowAction("自动布局", (_, _) => AutoLayout()),
            copyButton, removeButton, FlowAction("查看全图", (_, _) => FitView()), inspectorToggle })
            bar.Children.Add(button);
        return bar;
    }

    private void ToggleLibrary(bool open) { libraryOpen = open; if (compactStudio && open) inspectorOpen = false; UpdateSidePanes(); }
    private void ToggleInspector(bool open) { inspectorOpen = open; if (compactStudio && open) libraryOpen = false; UpdateSidePanes(); }
    private void UpdateSidePanes()
    {
        studioBody.ColumnDefinitions[0].Width = new GridLength(!compactStudio && libraryOpen ? 238 : 0);
        studioBody.ColumnDefinitions[2].Width = new GridLength(!compactStudio && inspectorOpen ? 286 : 0);
        foreach (var (pane, open, left) in new[] { (libraryPane, libraryOpen, true), (inspectorPane, inspectorOpen, false) })
        {
            pane.Visibility = open ? Visibility.Visible : Visibility.Collapsed;
            Grid.SetColumn(pane, compactStudio ? 0 : left ? 0 : 2); Grid.SetColumnSpan(pane, compactStudio ? 3 : 1);
            pane.Width = compactStudio ? Math.Min(286, Math.Max(0, ActualWidth)) : double.NaN;
            pane.HorizontalAlignment = compactStudio ? left ? HorizontalAlignment.Left : HorizontalAlignment.Right : HorizontalAlignment.Stretch;
            Panel.SetZIndex(pane, compactStudio ? 3 : 0);
        }
        libraryToggle.Visibility = libraryOpen ? Visibility.Collapsed : Visibility.Visible;
        inspectorToggle.Visibility = inspectorOpen ? Visibility.Collapsed : Visibility.Visible;
    }

    private void RefreshCanvasButtons()
    {
        if (undoButton == null) return;
        undoButton.IsEnabled = historyIndex > 1; redoButton.IsEnabled = historyIndex < history.Count;
        copyButton.IsEnabled = selected != null || selectedNodes.Count > 0;
        if (runNodeButton != null) runNodeButton.IsEnabled = selected != null;
        if (runFromButton != null) runFromButton.IsEnabled = selected != null;
    }

    private void ZoomCanvas(double factor) { scale = Math.Clamp(scale * factor, 0.2, 1.6); ApplyView(); }
    private static Brush CanvasDots()
    {
        var group = new DrawingGroup();
        group.Children.Add(new GeometryDrawing(FlowBrush("#1a1d1b"), null, new RectangleGeometry(new Rect(0, 0, 24, 24))));
        group.Children.Add(new GeometryDrawing(FlowBrush("#39423c"), null, new EllipseGeometry(new Point(12, 12), 1, 1)));
        var brush = new DrawingBrush(group) { TileMode = TileMode.Tile, ViewportUnits = BrushMappingMode.Absolute, Viewport = new Rect(0, 0, 24, 24) };
        brush.Freeze(); return brush;
    }

    private Button FlowAction(string text, RoutedEventHandler handler, string style = "Compact", bool light = false)
    {
        var button = new Button { Content = text, Style = (Style)FindResource("FlowButton"), Margin = new Thickness(0, 0, 1, 3) };
        button.ContentTemplate = FlowIcon(text);
        if (light)
        {
            button.Foreground = FlowBrush("#171815"); button.BorderBrush = FlowBrush("#cbc6b9");
            button.Background = FlowBrush("#efede5");
        }
        if (style == "CompactInk")
        {
            button.Background = FlowBrush(light ? "#171815" : "#e8e4da");
            button.Foreground = FlowBrush(light ? "#ffffff" : "#151714");
        }
        button.Click += handler; return button;
    }

    private static readonly Dictionary<string, DataTemplate> FlowIcons = [];
    private static DataTemplate? FlowIcon(string name)
    {
        if (FlowIcons.TryGetValue(name, out var template)) return template;
        string? data = name switch
        {
            "导出" => "M12 3V16 M6 10L12 16 18 10 M3 16V21H21V16",
            "导入" => "M12 16V3 M6 9L12 3 18 9 M3 16V21H21V16",
            "保存" => "M3 3H18L21 6V21H3Z M7 3V9H17V3 M7 21V14H17V21",
            "校验" => "M4 12L9 17 20 6",
            "发布" => "M2 11L22 2 13 22 10 14Z M10 14L22 2",
            "撤销" => "M8 3L3 8 8 13 M3 8H15A6 6 0 0 1 15 20",
            "重做" => "M16 3L21 8 16 13 M21 8H9A6 6 0 0 0 9 20",
            "复制" => "M8 8H21V21H8Z M16 8V3H3V16H8",
            "删除" => "M3 6H21 M9 6V3H15V6 M5 6L6 21H18L19 6 M10 10V17 M14 10V17",
            "节点库" => "M12 3V21 M3 12H21",
            "属性" => "M3 3H21V21H3Z M15 3V21 M18 8H20",
            "自动布局" or "查看全图" => "M3 3H10V10H3Z M14 3H21V10H14Z M3 14H10V21H3Z M14 14H21V21H14Z",
            "运行节点" or "从这里运行" or "运行工作流" or "运行已发布流程" => "M6 3L21 12 6 21Z",
            _ => null,
        };
        if (data == null) return null;
        var row = new FrameworkElementFactory(typeof(StackPanel));
        row.SetValue(StackPanel.OrientationProperty, Orientation.Horizontal);
        var path = new FrameworkElementFactory(typeof(System.Windows.Shapes.Path));
        path.SetValue(System.Windows.Shapes.Path.DataProperty, Geometry.Parse(data));
        path.SetValue(System.Windows.Shapes.Shape.StrokeThicknessProperty, 1.4);
        path.SetValue(System.Windows.Shapes.Shape.StretchProperty, Stretch.Uniform);
        path.SetValue(WidthProperty, 12.0); path.SetValue(HeightProperty, 12.0);
        path.SetValue(MarginProperty, new Thickness(0, 0, 6, 0)); path.SetValue(VerticalAlignmentProperty, VerticalAlignment.Center);
        path.SetBinding(System.Windows.Shapes.Shape.StrokeProperty, new Binding("Foreground") { RelativeSource = new RelativeSource(RelativeSourceMode.FindAncestor, typeof(Button), 1) });
        var label = new FrameworkElementFactory(typeof(TextBlock)); label.SetBinding(TextBlock.TextProperty, new Binding());
        label.SetValue(VerticalAlignmentProperty, VerticalAlignment.Center);
        row.AppendChild(path); row.AppendChild(label);
        template = new DataTemplate { VisualTree = row }; FlowIcons[name] = template; return template;
    }
}
