using System.Windows;
using System.Windows.Controls;
using System.Windows.Controls.Primitives;
using System.Windows.Media;
using MangaFlow.Native.Controls;

namespace MangaFlow.Native.Views;

public sealed partial class StoryboardView
{
    private readonly Grid desk = new(), worktable = new();
    private readonly ColumnDefinition stripColumn = new() { Width = new GridLength(96) };
    private readonly ColumnDefinition directorColumn = new() { Width = new GridLength(370), MinWidth = 280, MaxWidth = 540 };
    private readonly ScrollViewer pageStripScroll = new() { VerticalScrollBarVisibility = ScrollBarVisibility.Auto, HorizontalScrollBarVisibility = ScrollBarVisibility.Disabled };
    private readonly ScrollViewer deskScroll = new() { VerticalScrollBarVisibility = ScrollBarVisibility.Disabled, HorizontalScrollBarVisibility = ScrollBarVisibility.Disabled };
    private readonly ScrollViewer directorScroll = new() { VerticalScrollBarVisibility = ScrollBarVisibility.Auto, HorizontalScrollBarVisibility = ScrollBarVisibility.Disabled };
    private readonly Border saveState = new(), pageSummary = new();
    private readonly TextBlock pageCountText = Kit.Caption("0 页"), pageLoadText = new() { FontWeight = FontWeights.Bold, FontSize = 13 }, pageSourceText = Kit.Caption("");
    private readonly ComboBox compactPageSelector = new() { MinHeight = 40, Visibility = Visibility.Collapsed, Margin = new Thickness(0, 0, 0, 10) };
    private readonly GridSplitter directorSplitter = new() { Width = 6, HorizontalAlignment = HorizontalAlignment.Stretch, Background = AssetPageUi.Brush("Line"), ResizeDirection = GridResizeDirection.Columns, ResizeBehavior = GridResizeBehavior.PreviousAndNext };
    private readonly Button directorToggle = new() { Content = "隐藏导演台", Style = (Style)Application.Current.FindResource("Compact") };
    private bool narrowDesk, layoutReady, syncingPageSelector, inspectorOpen = true, fitPending = true, fitToViewport = true;

    private void BuildLayout()
    {
        var root = new Grid { Background = AssetPageUi.Brush("Paper"), Margin = new Thickness(0, 0, 0, 12), UseLayoutRounding = true, SnapsToDevicePixels = true };
        TextOptions.SetTextFormattingMode(root, TextFormattingMode.Display); RenderOptions.SetClearTypeHint(root, ClearTypeHint.Enabled);
        root.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto }); root.RowDefinitions.Add(new RowDefinition());
        var heading = new StackPanel(); heading.Children.Add(new TextBlock { Text = "PAGE CAPACITY / 动态分页", Foreground = AssetPageUi.Brush("Muted"), FontSize = 10, FontWeight = FontWeights.Bold });
        heading.Children.Add(new TextBlock { Text = "内容有多少，页面就有多少", FontFamily = (FontFamily)FindResource("Serif"), FontSize = 23, Margin = new Thickness(0, 10, 0, 0), TextWrapping = TextWrapping.Wrap });
        chapterSelector.Width = 205; chapterSelector.MinHeight = 42;
        var chapterActions = new StackPanel { Orientation = Orientation.Horizontal }; chapterActions.Children.Add(chapterSelector); pageCountText.Margin = new Thickness(12, 0, 0, 0); pageCountText.VerticalAlignment = VerticalAlignment.Center; chapterActions.Children.Add(pageCountText);
        root.Children.Add(new Border { Child = new PageHeading(heading, chapterActions), BorderBrush = AssetPageUi.Brush("Ink"), BorderThickness = new Thickness(0, 0, 0, 1), Padding = new Thickness(0, 0, 0, 16), Margin = new Thickness(0, 0, 0, 14) });
        chapterSelector.SelectionChanged += async (_, _) =>
        {
            if (chapterSelector.SelectedItem is not ComboBoxItem { Tag: string id } || id == chapterId) return;
            if (!await ConfirmLeaveAsync()) { SelectChapter(chapterId); return; }
            chapterId = id; await LoadPagesAsync();
        };
        desk.ColumnDefinitions.Add(stripColumn); desk.ColumnDefinitions.Add(new ColumnDefinition());
        pageStripScroll.Content = pageBar; pageStripScroll.Margin = new Thickness(0, 0, 12, 0); desk.Children.Add(pageStripScroll);
        var main = new Grid();
        for (int i = 0; i < 5; i++) main.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        main.RowDefinitions.Add(new RowDefinition());
        System.Windows.Automation.AutomationProperties.SetName(compactPageSelector, "选择分镜页面");
        compactPageSelector.SelectionChanged += async (_, _) =>
        {
            if (syncingPageSelector || compactPageSelector.SelectedItem is not ComboBoxItem { Tag: PageItem item } || item.Id == currentPage?.Id) return;
            if (!await ConfirmLeaveAsync()) { RefreshPageSelector(); return; }
            await SelectPageAsync(item);
        };
        main.Children.Add(compactPageSelector);
        statusLine.FontSize = 14; statusLine.FontWeight = FontWeights.Bold; statusLine.Foreground = AssetPageUi.Brush("Success"); statusLine.VerticalAlignment = VerticalAlignment.Center;
        focusButton.Click += (_, _) => ApplyFocusMode(); focusButton.MinHeight = 38;
        saveState.Child = new PageHeading(statusLine, focusButton); saveState.Padding = new Thickness(12, 8, 12, 8); saveState.BorderThickness = new Thickness(1); saveState.BorderBrush = AssetPageUi.Brush("Success"); saveState.Background = AssetPageUi.Brush("SuccessBg"); saveState.Margin = new Thickness(0, 0, 0, 12);
        Grid.SetRow(saveState, 1); main.Children.Add(saveState);
        replanButton.Click += async (_, _) => await ReplanFromPageAsync(); replanButton.MinHeight = 40;
        var summary = new StackPanel(); summary.Children.Add(pageLoadText); pageSourceText.Margin = new Thickness(0, 5, 0, 0); pageSourceText.TextWrapping = TextWrapping.Wrap; summary.Children.Add(pageSourceText);
        pageSummary.Child = new PageHeading(summary, replanButton); pageSummary.Margin = new Thickness(0, 0, 0, 12); Grid.SetRow(pageSummary, 2); main.Children.Add(pageSummary);
        BuildConflictBar(); Grid.SetRow(conflictBar, 3); main.Children.Add(conflictBar);
        var toolbar = BuildToolbar(); Grid.SetRow(toolbar, 4); main.Children.Add(toolbar);
        worktable.ColumnDefinitions.Add(new ColumnDefinition()); worktable.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(8) }); worktable.ColumnDefinitions.Add(directorColumn);
        worktable.RowDefinitions.Add(new RowDefinition()); worktable.RowDefinitions.Add(new RowDefinition { Height = new GridLength(0) });
        pageHost.Background = Brushes.White; pageHost.Child = page; pageHost.BorderBrush = AssetPageUi.Brush("Ink"); pageHost.BorderThickness = new Thickness(1);
        viewport.Content = new Border { Child = pageHost, Margin = new Thickness(32), HorizontalAlignment = HorizontalAlignment.Center, VerticalAlignment = VerticalAlignment.Center };
        viewport.BorderBrush = AssetPageUi.Brush("Ink"); viewport.BorderThickness = new Thickness(1); viewport.PreviewMouseWheel += OnViewportWheel;
        viewport.SizeChanged += (_, _) => { if ((fitPending || fitToViewport) && currentPage != null && viewport.ActualWidth > 100 && viewport.ActualHeight > 100) { fitPending = false; FitViewport(); } };
        worktable.Children.Add(viewport); Grid.SetColumn(directorSplitter, 1); worktable.Children.Add(directorSplitter);
        inspector.Background = AssetPageUi.Brush("Surface"); directorScroll.Content = inspector; directorScroll.Background = AssetPageUi.Brush("Surface"); directorScroll.BorderBrush = AssetPageUi.Brush("Ink"); directorScroll.BorderThickness = new Thickness(1); Grid.SetColumn(directorScroll, 2); worktable.Children.Add(directorScroll);
        Grid.SetRow(worktable, 5); main.Children.Add(worktable);
        deskScroll.Content = main; Grid.SetColumn(deskScroll, 1); desk.Children.Add(deskScroll); Grid.SetRow(desk, 1); root.Children.Add(desk);
        root.SizeChanged += (_, _) => ConfigureDesk(root.ActualWidth);
        ConfigureDesk(1100); Content = root;
    }

    private FrameworkElement BuildToolbar()
    {
        var wrapped = new WrapPanel();
        void Add(FrameworkElement control) { control.Margin = new Thickness(0, 0, 5, 5); if (control is Control c) c.MinHeight = 38; wrapped.Children.Add(control); }
        Add(Kit.Act("－", (_, _) => SetZoom(zoom / 1.25), "Compact")); zoomLabel.MinWidth = 42; zoomLabel.TextAlignment = TextAlignment.Center; Add(zoomLabel);
        Add(Kit.Act("＋", (_, _) => SetZoom(zoom * 1.25), "Compact")); Add(Kit.Act("适配窗口", (_, _) => FitViewport(), "Compact")); Add(Kit.Act("复位", (_, _) => SetZoom(1), "Compact"));
        Add(snapButton); orderButton.Click += (_, _) => RenderOrderBadges(); Add(orderButton);
        bleedButton.Click += (_, _) => RenderGuidesOverlay(); Add(bleedButton); safeButton.Click += (_, _) => RenderGuidesOverlay(); Add(safeButton);
        undoButton.Click += (_, _) => Undo(); redoButton.Click += (_, _) => Redo(); Add(undoButton); Add(redoButton);
        var menu = new ContextMenu(); var rebuild = new MenuItem { Header = "重建本页版式…" }; rebuild.Click += (_, _) => RebuildLayout(); menu.Items.Add(rebuild);
        var menuButton = Kit.Act("页菜单 ▾", (_, _) => { }, "Compact"); menuButton.ContextMenu = menu;
        menuButton.Click += (_, _) => { menu.PlacementTarget = menuButton; menu.Placement = PlacementMode.Bottom; menu.IsOpen = true; }; Add(menuButton);
        saveButton.Click += async (_, _) => await SaveAsync(); Add(saveButton);
        directorToggle.Click += (_, _) => { inspectorOpen = !inspectorOpen; ConfigureDesk(ActualWidth, true); }; Add(directorToggle);
        var block = new StackPanel(); block.Children.Add(wrapped);
        block.Children.Add(new TextBlock { Text = "Tab 切换格子 · 方向键微调（Shift 加速） · 回车打开属性 · Delete 删除气泡", FontSize = 11, Foreground = AssetPageUi.Brush("Muted"), TextWrapping = TextWrapping.Wrap, Margin = new Thickness(0, 3, 0, 0) });
        return new Border { Child = block, Padding = new Thickness(9), Background = AssetPageUi.Brush("Surface"), BorderBrush = AssetPageUi.Brush("Line"), BorderThickness = new Thickness(1), Margin = new Thickness(0, 0, 0, 10) };
    }

    private void ConfigureDesk(double width, bool force = false)
    {
        bool next = width < 850;
        if (layoutReady && narrowDesk == next && !force) return;
        narrowDesk = next; layoutReady = true;
        bool focused = focusButton.IsChecked == true;
        pageBar.Visibility = focused ? Visibility.Collapsed : Visibility.Visible;
        pageStripScroll.Visibility = narrowDesk || focused ? Visibility.Collapsed : Visibility.Visible;
        stripColumn.Width = new GridLength(narrowDesk || focused ? 0 : 96);
        compactPageSelector.Visibility = narrowDesk && !focused ? Visibility.Visible : Visibility.Collapsed;
        pageSummary.Visibility = focused ? Visibility.Collapsed : Visibility.Visible;
        directorToggle.Content = inspectorOpen ? "隐藏导演台" : "展开导演台";
        directorScroll.Visibility = inspectorOpen ? Visibility.Visible : Visibility.Collapsed;
        directorSplitter.Visibility = inspectorOpen && !narrowDesk ? Visibility.Visible : Visibility.Collapsed;
        directorColumn.MinWidth = narrowDesk || !inspectorOpen ? 0 : 280;
        directorColumn.Width = new GridLength(narrowDesk || !inspectorOpen ? 0 : 370);
        worktable.ColumnDefinitions[1].Width = new GridLength(narrowDesk || !inspectorOpen ? 0 : 8);
        Grid.SetRow(directorScroll, narrowDesk ? 1 : 0); Grid.SetColumn(directorScroll, narrowDesk ? 0 : 2);
        worktable.Height = narrowDesk ? inspectorOpen ? 900 : 420 : double.NaN;
        worktable.RowDefinitions[0].Height = narrowDesk ? new GridLength(420) : new GridLength(1, GridUnitType.Star);
        worktable.RowDefinitions[1].Height = new GridLength(narrowDesk && inspectorOpen ? 480 : 0);
        directorScroll.Margin = narrowDesk ? new Thickness(0, 12, 0, 0) : new Thickness(0);
        deskScroll.VerticalScrollBarVisibility = narrowDesk ? ScrollBarVisibility.Auto : ScrollBarVisibility.Disabled;
        focusButton.Content = focused ? "退出专注" : "专注模式";
    }

    private static Border InspectorSection(UIElement content) => new()
    {
        Child = content, Padding = new Thickness(14), Background = AssetPageUi.Brush("Surface"),
        BorderBrush = AssetPageUi.Brush("Line"), BorderThickness = new Thickness(0, 0, 0, 1),
    };

    private Border InspectorHeading(string index, string title, UIElement? action)
    {
        var heading = new StackPanel();
        heading.Children.Add(new TextBlock { Text = index, FontSize = 10, FontWeight = FontWeights.Bold, Foreground = AssetPageUi.Brush("Accent") });
        heading.Children.Add(new TextBlock { Text = title, FontFamily = (FontFamily)FindResource("Serif"), FontSize = 20, Margin = new Thickness(0, 8, 0, 0) });
        return InspectorSection(action == null ? heading : new PageHeading(heading, action));
    }

    private static Border InspectorDetail(string label, string value, string fallback = "待补充")
    {
        var row = new Grid(); row.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(64) }); row.ColumnDefinitions.Add(new ColumnDefinition());
        row.Children.Add(new TextBlock { Text = label, FontSize = 11, Foreground = AssetPageUi.Brush("Muted") });
        var content = new TextBlock { Text = string.IsNullOrWhiteSpace(value) ? fallback : value, FontSize = 12, TextWrapping = TextWrapping.Wrap };
        Grid.SetColumn(content, 1); row.Children.Add(content);
        return new Border { Child = row, Padding = new Thickness(0, 10, 0, 10), BorderBrush = AssetPageUi.Brush("Line"), BorderThickness = new Thickness(0, 0, 0, 1) };
    }

    private void RefreshPageSelector()
    {
        syncingPageSelector = true; compactPageSelector.Items.Clear();
        foreach (var item in pages) compactPageSelector.Items.Add(new ComboBoxItem { Tag = item, Content = $"P.{item.PageNumber:D3} · {item.PanelCount} 格 · {(item.ContinuityStatus == "NEEDS_REVIEW" ? "待复查" : item.StateLabel)}" });
        compactPageSelector.SelectedItem = compactPageSelector.Items.OfType<ComboBoxItem>().FirstOrDefault(i => ((PageItem)i.Tag).Id == currentPage?.Id);
        syncingPageSelector = false; pageCountText.Text = $"{pages.Count} 页";
    }

    private void UpdatePageSummary()
    {
        if (currentPage == null) { pageLoadText.Text = "尚未生成分页分镜"; pageSourceText.Text = "先完成漫画剧本，再计算分页。"; return; }
        var info = storyboard.Element("page");
        pageLoadText.Text = $"第 {currentPage.PageNumber} 页 · {info.Decimal("estimated_text_chars", currentPage.CharacterCount)}/180 字 · {info.Decimal("estimated_bubbles", currentPage.BubbleCount)}/8 气泡";
        pageSourceText.Text = $"来自漫画剧本：{info.Array("scene_ids").Count} 个场景 · {info.Array("beat_ids").Count} 个情节拍；修改不会删除已有候选。";
    }
}
