using System.Net.Http;
using System.Text.Json;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Controls.Primitives;
using System.Windows.Input;
using System.Windows.Media;
using System.Windows.Shapes;
using MangaFlow.Native.Controls;
using MangaFlow.Native.Services;

namespace MangaFlow.Native.Views;

/// <summary>
/// NUI-4: visual storyboard canvas. Panels and bubbles live in normalized 0–1 page
/// coordinates; gestures move FrameworkElements directly (zero re-render during drag)
/// and commit into an undo stack on release. Save is one atomic PUT with request_id.
/// </summary>
public sealed class StoryboardView : WorkspaceView
{
    private const double BasePageWidth = 640;
    private const double MinSize = 0.03, MinBubble = 0.02, SnapThreshold = 0.012;
    private static readonly double[] PageGuides = [0, 0.5, 1];

    private readonly ComboBox chapterSelector = Selector("章节选择", 240);
    private readonly StackPanel pageBar = new() { Orientation = Orientation.Horizontal };
    private readonly Canvas page = new() { Background = Brushes.White };
    private readonly Border pageHost = new();
    private readonly ScrollViewer viewport = new() { Background = new SolidColorBrush(Color.FromRgb(0xD8, 0xD2, 0xC6)), VerticalScrollBarVisibility = ScrollBarVisibility.Auto, HorizontalScrollBarVisibility = ScrollBarVisibility.Auto };
    private readonly StackPanel inspector = new();
    private readonly TextBlock statusLine = new() { Style = (Style)Application.Current.FindResource("Micro") };
    private readonly ToggleButton snapButton = new() { Content = "吸附", Style = (Style)Application.Current.FindResource("Pill"), IsChecked = true };
    private readonly ToggleButton orderButton = new() { Content = "阅读序", Style = (Style)Application.Current.FindResource("Pill"), IsChecked = true };
    private readonly Button saveButton = new() { Content = "保存本页", Style = (Style)Application.Current.FindResource("InkButton") };
    private readonly Button undoButton = new() { Content = "撤销", Style = (Style)Application.Current.FindResource("Compact") };
    private readonly Button redoButton = new() { Content = "重做", Style = (Style)Application.Current.FindResource("Compact") };
    private readonly TextBlock zoomLabel = new() { Text = "100%", VerticalAlignment = VerticalAlignment.Center, Margin = new Thickness(8, 0, 8, 0) };

    private List<ChapterItem> chapters = [];
    private List<PageItem> pages = [];
    private List<CharacterItem> characters = [];
    private List<OutfitItem> outfits = [];
    private string chapterId = "";
    private PageItem? currentPage;
    private JsonElement storyboard;
    private List<PanelNode> panels = [];
    private readonly List<BubbleNode> bubbles = [];
    private double zoom = 1;
    private double pageAspect = 257.0 / 182.0;
    private readonly CommandStack history = new();
    private Guid? requestId;
    private int historyIndexAtRequest;
    private bool saving, dirty;
    private PanelNode? selected;
    private BubbleNode? selectedBubble;

    public StoryboardView()
    {
        BuildLayout();
        PreviewKeyDown += OnCanvasKey;
        Focusable = true;
        page.MouseLeftButtonDown += (_, e) =>
        {
            if (e.OriginalSource is Canvas)
            {
                SelectPanel(null);
                e.Handled = true;
            }
        };
    }

    private void BuildLayout()
    {
        var root = new Grid { Margin = new Thickness(4, 0, 24, 24) };
        root.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        root.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        root.RowDefinitions.Add(new RowDefinition { Height = new GridLength(1, GridUnitType.Star) });

        var header = new Border { Style = (Style)Application.Current.FindResource("CanvasHeader") };
        var headerGrid = new Grid();
        headerGrid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        headerGrid.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        var heading = new StackPanel();
        heading.Children.Add(new TextBlock { Text = "PAGE CAPACITY", Style = (Style)Application.Current.FindResource("SectionIndex") });
        heading.Children.Add(new TextBlock
        {
            Text = "动态分页 · 内容有多少，页面就有多少",
            FontFamily = (FontFamily)Application.Current.FindResource("Serif"),
            FontSize = 21, FontWeight = FontWeights.Bold, Margin = new Thickness(0, 5, 0, 0),
        });
        headerGrid.Children.Add(heading);
        var stage = new StackPanel { Orientation = Orientation.Horizontal };
        stage.Children.Add(chapterSelector);
        stage.VerticalAlignment = VerticalAlignment.Bottom;
        headerGrid.Children.Add(stage);
        headerGrid.Children.Clear();
        header.Child = new PageHeading(heading, stage);
        root.Children.Add(header);

        pageBar.Margin = new Thickness(0, 0, 0, 12);
        Grid.SetRow(pageBar, 1);
        root.Children.Add(pageBar);
        chapterSelector.SelectionChanged += async (_, _) =>
        {
            if (chapterSelector.SelectedItem is ComboBoxItem { Tag: string id } && id != chapterId)
            {
                if (!await ConfirmLeaveAsync()) { SelectChapter(chapterId); return; }
                chapterId = id;
                await LoadPagesAsync();
            }
        };

        var split = new Grid();
        split.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        split.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(320) });
        // Canvas column.
        var canvasColumn = new DockPanel();
        var toolbar = BuildToolbar();
        DockPanel.SetDock(toolbar, Dock.Top);
        canvasColumn.Children.Add(toolbar);
        pageHost.Background = Brushes.White;
        pageHost.Child = page;
        pageHost.SizeChanged += (_, _) => UpdatePageSize();
        viewport.Content = new Border { Child = pageHost, Margin = new Thickness(24), HorizontalAlignment = HorizontalAlignment.Center, VerticalAlignment = VerticalAlignment.Center };
        viewport.PreviewMouseWheel += OnViewportWheel;
        canvasColumn.Children.Add(viewport);
        Grid.SetColumn(canvasColumn, 0);
        split.Children.Add(canvasColumn);
        var inspectorScroll = new ScrollViewer { VerticalScrollBarVisibility = ScrollBarVisibility.Auto, Content = inspector };
        Grid.SetColumn(inspectorScroll, 1);
        inspectorScroll.Margin = new Thickness(16, 0, 0, 0);
        split.Children.Add(inspectorScroll);
        root.Children.Add(split);
        Grid.SetRow(split, 2);
        Content = root;
    }

    private FrameworkElement BuildToolbar()
    {
        var bar = new DockPanel { Margin = new Thickness(0, 0, 0, 10) };
        var right = new StackPanel { Orientation = Orientation.Horizontal };
        undoButton.Click += (_, _) => Undo();
        redoButton.Click += (_, _) => Redo();
        undoButton.Margin = new Thickness(0, 0, 6, 0);
        right.Children.Add(undoButton);
        right.Children.Add(redoButton);
        var rebuild = Kit.Act("重建本页版式…", (_, _) => RebuildLayout(), "Compact");
        rebuild.Margin = new Thickness(6, 0, 10, 0);
        right.Children.Add(rebuild);
        saveButton.Click += async (_, _) => await SaveAsync();
        right.Children.Add(saveButton);
        DockPanel.SetDock(right, Dock.Right);
        bar.Children.Add(right);
        var left = new StackPanel { Orientation = Orientation.Horizontal };
        var zoomOut = Kit.Act("－", (_, _) => SetZoom(zoom / 1.25), "Compact");
        var zoomIn = Kit.Act("＋", (_, _) => SetZoom(zoom * 1.25), "Compact");
        var fit = Kit.Act("适配窗口", (_, _) => FitViewport(), "Compact");
        var reset = Kit.Act("复位", (_, _) => SetZoom(1), "Compact");
        left.Children.Add(zoomOut);
        left.Children.Add(zoomLabel);
        left.Children.Add(zoomIn);
        left.Children.Add(fit);
        left.Children.Add(reset);
        snapButton.Margin = new Thickness(14, 0, 6, 0);
        left.Children.Add(snapButton);
        orderButton.Margin = new Thickness(0, 0, 14, 0);
        orderButton.Click += (_, _) => { RenderOrderBadges(); };
        left.Children.Add(orderButton);
        left.Children.Add(statusLine);
        bar.Children.Add(left);
        // Match the web toolbar wrapping; a horizontal StackPanel otherwise measures
        // all controls at infinite width and paints over the inspector in narrow windows.
        var wrapped = new WrapPanel { Margin = bar.Margin };
        foreach (var group in new[] { left, right })
        {
            var children = group.Children.Cast<UIElement>().ToArray();
            group.Children.Clear();
            foreach (var child in children)
            {
                if (child is FrameworkElement element) element.Margin = new Thickness(0, 0, 6, 6);
                wrapped.Children.Add(child);
            }
        }
        return wrapped;
    }

    public override async void Activate(WorkspaceContext context)
    {
        base.Activate(context);
        try
        {
            var chapterRows = await Api.SendAsync($"projects/{ProjectId}/chapters", cancellation: lifetime.Token);
            var characterRows = await Api.SendAsync($"projects/{ProjectId}/characters", cancellation: lifetime.Token);
            var outfitRows = await Api.SendAsync($"projects/{ProjectId}/outfits", cancellation: lifetime.Token);
            if (lifetime.Token.IsCancellationRequested) return;
            chapters = chapterRows.EnumerateArray().Select(ChapterItem.From).ToList();
            characters = characterRows.EnumerateArray().Select(CharacterItem.From).ToList();
            outfits = outfitRows.EnumerateArray().Select(OutfitItem.From).ToList();
            chapterSelector.Items.Clear();
            foreach (var chapter in chapters)
                chapterSelector.Items.Add(new ComboBoxItem { Tag = chapter.Id, Content = $"第 {chapter.Ordinal} 章 · {chapter.Title}" });
            if (chapters.Count == 0)
            {
                inspector.Children.Clear();
                inspector.Children.Add(Kit.Caption("尚未生成分页分镜。先完成漫画剧本。"));
                return;
            }
            var target = chapters.FirstOrDefault(c => c.Id == chapterId) ?? chapters[0];
            SelectChapter(target.Id);
            chapterId = target.Id;
            await LoadPagesAsync();
        }
        catch (OperationCanceledException) { }
        catch (Exception error) when (error is not OperationCanceledException)
        {
            inspector.Children.Clear();
            inspector.Children.Add(Kit.Caption($"页面列表读取失败：{error.Message}"));
        }
    }

    private void SelectChapter(string id)
    {
        foreach (var item in chapterSelector.Items.OfType<ComboBoxItem>())
            if ((string?)item.Tag == id) { chapterSelector.SelectedItem = item; return; }
    }

    private async Task LoadPagesAsync()
    {
        try
        {
            var rows = await Api.SendAsync($"chapters/{chapterId}/pages", cancellation: lifetime.Token);
            if (lifetime.Token.IsCancellationRequested) return;
            pages = rows.EnumerateArray().Select(PageItem.From).ToList();
            RenderPageBar();
            var requested = KeyValueStore.Get("storyboard:page:" + ProjectId);
            var target = pages.FirstOrDefault(p => p.Id == requested) ?? pages.FirstOrDefault();
            if (target == null)
            {
                inspector.Children.Clear();
                inspector.Children.Add(Kit.Caption("本章还没有页面。先在原作页点击“从剧本计算分页”。"));
                page.Children.Clear();
                return;
            }
            await SelectPageAsync(target);
        }
         catch (OperationCanceledException) { }
        catch (Exception error) when (error is not OperationCanceledException)
        {
            inspector.Children.Add(Kit.Caption($"页面列表读取失败：{error.Message}"));
        }
    }

    private void RenderPageBar()
    {
        pageBar.Children.Clear();
        foreach (var item in pages)
        {
            var chip = new ToggleButton
            {
                Content = new StackPanel
                {
                    Children =
                    {
                        new TextBlock { Text = $"P.{item.PageNumber:D3}", FontWeight = FontWeights.Bold },
                        new TextBlock { Text = $"{item.PanelCount} 格 · {item.StateLabel}", Style = (Style)Application.Current.FindResource("Micro") },
                    },
                },
                Style = (Style)Application.Current.FindResource("Chip"),
                IsChecked = currentPage?.Id == item.Id, Margin = new Thickness(0, 0, 8, 0), MinWidth = 86,
            };
            var captured = item;
            chip.Click += async (_, _) =>
            {
                if (!await ConfirmLeaveAsync()) { chip.IsChecked = currentPage?.Id == captured.Id; return; }
                await SelectPageAsync(captured);
            };
            pageBar.Children.Add(chip);
        }
    }

    private async Task SelectPageAsync(PageItem item)
    {
        var previous = currentPage;
        try
        {
            storyboard = await Api.SendAsync($"pages/{item.Id}/storyboard", cancellation: lifetime.Token);
            if (lifetime.Token.IsCancellationRequested) return;
            // 拉取成功后才切换页签状态与记住的页号
            currentPage = item;
            KeyValueStore.Set("storyboard:page:" + ProjectId, item.Id);
            var canvasInfo = storyboard.Element("page").Element("canvas");
            double widthMm = canvasInfo.Decimal("width_mm") is var w && w > 0 ? w : 182;
            double heightMm = canvasInfo.Decimal("height_mm") is var h && h > 0 ? h : 257;
            pageAspect = heightMm / widthMm;
            panels = storyboard.Array("panels").Select(PanelNode.From).ToList();
            foreach (var panel in panels)
                panel.Element.MouseLeftButtonDown += (s, e) => BeginPanelDrag(s, e, panel);
            RebuildBubbles();
            history.Clear();
            requestId = null;
            dirty = false;
            UpdateStatus("已保存");
            RenderPageBar();
            RenderCanvas();
            RenderInspector();
            UpdatePageSize();
        }
         catch (OperationCanceledException) { }
        catch (Exception error) when (error is not OperationCanceledException)
        {
            currentPage = previous;   // 失败回滚页签选中态
            RenderPageBar();
            UpdateStatus("读取失败");
            inspector.Children.Clear();
            inspector.Children.Add(Kit.Caption($"本页分镜读取失败：{error.Message}"));
        }
    }

    private void RebuildBubbles()
    {
        bubbles.Clear();
        foreach (var panel in panels)
        {
            var index = 0;
            foreach (var dialogue in panel.Dialogues)
            {
                var bubble = BubbleNode.From(dialogue, panel, index++);
                bubble.Element.MouseLeftButtonDown += (s, e) => BeginBubbleDrag(s, e, bubble);
                bubbles.Add(bubble);
            }
        }
    }

    private void UpdatePageSize()
    {
        var width = BasePageWidth * zoom;
        var height = width * pageAspect;
        page.Width = width;
        page.Height = height;
        pageHost.Width = width + 2;
        pageHost.Height = height + 2;
        foreach (var panel in panels) panel.ApplyPosition(page);
        foreach (var bubble in bubbles) bubble.ApplyPosition(page);
    }

    private void RenderCanvas()
    {
        // 事件处理器已在节点创建处挂接一次，这里只重组 Children
        page.Children.Clear();
        foreach (var panel in panels) page.Children.Add(panel.Element);
        foreach (var bubble in bubbles) page.Children.Add(bubble.Element);
        RenderOrderBadges();
    }

    private void RenderOrderBadges()
    {
        foreach (var badge in page.Children.OfType<FrameworkElement>().Where(u => u.Tag as string == "order-badge").ToList())
            page.Children.Remove(badge);
        if (orderButton.IsChecked != true) return;
        foreach (var panel in panels)
        {
            var badge = new Border
            {
                Background = new SolidColorBrush(Color.FromRgb(0x15, 0x15, 0x12)),
                Padding = new Thickness(5, 2, 5, 2),
                Child = new TextBlock { Text = $"格 {panel.ReadingOrder:D2}", FontSize = 10, Foreground = Brushes.White },
            };
            badge.SetValue(FrameworkElement.TagProperty, "order-badge");
            Canvas.SetLeft(badge, panel.Rect.X * page.Width + 2);
            Canvas.SetTop(badge, panel.Rect.Y * page.Height + 2);
            Panel.SetZIndex(badge, 45);
            page.Children.Add(badge);
        }
    }

    // ============ Gesture engine: direct element mutation, commit on release ============
    private abstract class Gesture
    {
        public abstract void Move(Point pagePoint);
        public abstract void Commit(StoryboardView view);
    }

    private PanelGesture? panelGesture;
    private readonly List<Line> guideLines = [];

    private void BeginPanelDrag(object sender, MouseButtonEventArgs e, PanelNode panel)
    {
        if (e.ChangedButton != MouseButton.Left) return;
        SelectPanel(panel);
        var start = ToNormalized(e.GetPosition(page));
        panelGesture = new PanelGesture(Keyboard.Modifiers == ModifierKeys.Shift ? [.. panels] : [panel]);
        var element = (FrameworkElement)sender;
        element.CaptureMouse();
        MouseEventHandler moved = (_, me) =>
        {
            if (panelGesture == null) return;
            var current = ToNormalized(me.GetPosition(page));
            panelGesture.Offset = new Point(current.X - start.X, current.Y - start.Y);
            foreach (var target in panelGesture.Group)
            {
                var anchor = panelGesture.Origins[target.Id];   // 用手势开始时的快照，不用已被改写的 target.Rect
                var rect = new Rect(anchor.X + panelGesture.Offset.X, anchor.Y + panelGesture.Offset.Y, anchor.Width, anchor.Height);
                target.SetRectDirect(rect, page);
            }
        };
        MouseButtonEventHandler up = null!;
        MouseEventHandler lost = null!;
        var committed = false;   // up 与 LostMouseCapture 都会触发时只提交一次
        void Finish()
        {
            Mouse.RemoveMouseMoveHandler(element, moved);
            Mouse.RemoveMouseUpHandler(element, up);
            Mouse.RemoveLostMouseCaptureHandler(element, lost);
            if (committed) return;
            committed = true;
            panelGesture?.Commit(this);
            panelGesture = null;
            ClearGuides();
        }
        up = (_, _) =>
        {
            element.ReleaseMouseCapture();
            Finish();
        };
        lost = (_, _) => Finish();
        Mouse.AddMouseMoveHandler(element, moved);
        Mouse.AddMouseUpHandler(element, up);
        Mouse.AddLostMouseCaptureHandler(element, lost);
        e.Handled = true;
    }

    private void BeginBubbleDrag(object sender, MouseButtonEventArgs e, BubbleNode bubble)
    {
        if (e.ChangedButton != MouseButton.Left) return;
        SelectBubble(bubble);
        var start = e.GetPosition(page);
        var origin = bubble.Rect;
        var movedBeforeGesture = bubble.Moved;
        var element = (FrameworkElement)sender;
        element.CaptureMouse();
        MouseEventHandler moved = (_, me) =>
        {
            var current = me.GetPosition(page);
            var dx = (current.X - start.X) / page.Width;
            var dy = (current.Y - start.Y) / page.Height;
            var next = new Rect(origin.X + dx, origin.Y + dy, origin.Width, origin.Height);
            var host = panels.FirstOrDefault(p => p.Id == bubble.PanelId);
            if (host != null) next = ClampInto(next, host.Rect);
            bubble.SetRectDirect(next, page);
        };
        MouseButtonEventHandler up = null!;
        MouseEventHandler lost = null!;
        var committed = false;   // up 与 LostMouseCapture 都会触发时只提交一次
        void Finish()
        {
            Mouse.RemoveMouseMoveHandler(element, moved);
            Mouse.RemoveMouseUpHandler(element, up);
            Mouse.RemoveLostMouseCaptureHandler(element, lost);
            if (committed) return;
            committed = true;
            var final = bubble.Rect;
            var host = panels.FirstOrDefault(p => p.Id == bubble.PanelId);
            if (host != null && !Covers(host.Rect, final))
            {
                bubble.SetRectDirect(origin, page);  // bubbles never leave their panel
                bubble.Moved = movedBeforeGesture;   // 回弹后还原到手势前的定位语义
            }
            else if (final != origin)
            {
                history.Push(new GeometryCommand("拖动气泡", [new BubbleChange(bubble.Id, origin, final)]));
                MarkDirty();
            }
        }
        up = (_, _) =>
        {
            element.ReleaseMouseCapture();
            Finish();
        };
        lost = (_, _) => Finish();
        Mouse.AddMouseMoveHandler(element, moved);
        Mouse.AddMouseUpHandler(element, up);
        Mouse.AddLostMouseCaptureHandler(element, lost);
        e.Handled = true;
    }

    private static bool Covers(Rect host, Rect inner) =>
        inner.X >= host.X - 0.0005 && inner.Y >= host.Y - 0.0005 &&
        inner.Right <= host.Right + 0.0005 && inner.Bottom <= host.Bottom + 0.0005;

    private static Rect ClampInto(Rect rect, Rect bounds) => new(
        Math.Clamp(rect.X, bounds.X, Math.Max(bounds.X, bounds.Right - rect.Width)),
        Math.Clamp(rect.Y, bounds.Y, Math.Max(bounds.Y, bounds.Bottom - rect.Height)),
        Math.Min(rect.Width, bounds.Width), Math.Min(rect.Height, bounds.Height));

    private Point ToNormalized(Point point) => new(point.X / Math.Max(1, page.Width), point.Y / Math.Max(1, page.Height));

    private void ClearGuides()
    {
        foreach (var line in guideLines) page.Children.Remove(line);
        guideLines.Clear();
    }

    private void ShowGuide(double at, bool vertical)
    {
        var line = new Line { Stroke = new SolidColorBrush(Color.FromRgb(0x2B, 0xA6, 0xA0)), StrokeThickness = 1.5, StrokeDashArray = new DoubleCollection { 4, 3 } };
        if (vertical)
        {
            line.X1 = line.X2 = at * page.Width;
            line.Y1 = 0; line.Y2 = page.Height;
        }
        else
        {
            line.Y1 = line.Y2 = at * page.Height;
            line.X1 = 0; line.X2 = page.Width;
        }
        Panel.SetZIndex(line, 30);
        page.Children.Add(line);
        guideLines.Add(line);
    }

    private void SelectPanel(PanelNode? panel)
    {
        foreach (var candidate in panels) candidate.SetSelected(candidate == panel);
        foreach (var bubble in bubbles) bubble.SetSelected(false);
        selected = panel;
        selectedBubble = null;
        RenderInspector();
    }

    private void SelectBubble(BubbleNode bubble)
    {
        foreach (var candidate in panels) candidate.SetSelected(candidate.Id == bubble.PanelId);
        foreach (var bubbleNode in bubbles) bubbleNode.SetSelected(bubbleNode == bubble);
        selected = panels.FirstOrDefault(p => p.Id == bubble.PanelId);
        selectedBubble = bubble;
        RenderInspector();
    }

    // ============ Undo / redo ============
    private void Undo()
    {
        if (!history.CanUndo) return;
        var command = history.Undo();
        ApplyChanges(command.Changes, before: true);
        MarkDirty();
        RenderCanvas();
        RenderInspector();
    }

    private void Redo()
    {
        if (!history.CanRedo) return;
        var command = history.Redo();
        ApplyChanges(command.Changes, before: false);
        MarkDirty();
        RenderCanvas();
        RenderInspector();
    }

    private void ApplyChanges(List<GeometryChange> changes, bool before)
    {
        foreach (var change in changes)
        {
            if (change is PanelChange panelChange)
            {
                var panel = panels.FirstOrDefault(p => p.Id == panelChange.Id);
                if (panel != null) panel.Rect = before ? panelChange.Before : panelChange.After;
            }
            else if (change is BubbleChange bubbleChange)
            {
                var bubble = bubbles.FirstOrDefault(b => b.Id == bubbleChange.Id);
                if (bubble != null)
                {
                    var rect = before ? bubbleChange.Before : bubbleChange.After;
                    bubble.Rect = rect;
                }
            }
        }
        UpdatePageSize();
    }

    private void MarkDirty()
    {
        dirty = history.CanUndo;
        saveButton.IsEnabled = dirty && !saving;
        UpdateStatus(dirty ? "有未保存修改" : "已保存");
    }

    private void UpdateStatus(string label) => statusLine.Text = $"{label} · 当前 V{currentPage?.StoryboardVersion ?? 0}";

    // ============ Inspector ============
    private void RenderInspector()
    {
        inspector.Children.Clear();
        if (currentPage == null) return;
        inspector.Children.Add(new TextBlock
        {
            Text = $"第 {currentPage.PageNumber} 页 · {panels.Count} 格 · {bubbles.Count} 气泡",
            Style = (Style)Application.Current.FindResource("SectionIndex"),
        });
        inspector.Children.Add(Kit.Caption("修改不会删除已有候选；保存会整页提交几何。"));
        if (selected == null && selectedBubble == null)
        {
            inspector.Children.Add(Kit.Caption("点击画布中的格子或气泡查看属性。Tab 切换格子，方向键微调，Delete 删除气泡。"));
            return;
        }
        if (selectedBubble != null)
        {
            var bubble = selectedBubble;
            var card = new StackPanel { Margin = new Thickness(0, 10, 0, 0) };
            card.Children.Add(new TextBlock { Text = $"气泡 · {bubble.TargetText}", FontWeight = FontWeights.Bold, TextWrapping = TextWrapping.Wrap });
            card.Children.Add(new TextBlock
            {
                Text = bubble.TargetText.Length > 0 ? bubble.TargetText : "（空文本）",
                Style = (Style)Application.Current.FindResource("Micro"), Margin = new Thickness(0, 4, 0, 0),
            });
            var remove = Kit.Act("删除气泡", async (_, _) => await DeleteBubbleAsync(bubble), "CompactDanger");
            remove.Margin = new Thickness(0, 8, 0, 0);
            card.Children.Add(remove);
            inspector.Children.Add(new Border { Style = (Style)Application.Current.FindResource("Card"), Padding = new Thickness(14), Child = card });
            return;
        }
        if (selected is { } panel)
        {
            var card = new StackPanel { Margin = new Thickness(0, 10, 0, 0) };
            card.Children.Add(new TextBlock { Text = $"PANEL {panel.ReadingOrder:D2} · 分镜导演台", Style = (Style)Application.Current.FindResource("SectionIndex") });
            card.Children.Add(new TextBlock
            {
                Text = $"X {panel.Rect.X:P1} · Y {panel.Rect.Y:P1} · 宽 {panel.Rect.Width:P1} · 高 {panel.Rect.Height:P1}",
                FontFamily = (FontFamily)Application.Current.FindResource("Mono"), FontSize = 12, Margin = new Thickness(0, 6, 0, 4),
            });
            card.Children.Add(new TextBlock { Text = panel.Action, TextWrapping = TextWrapping.Wrap, FontWeight = FontWeights.Bold });
            if (panel.Dialogues.Count > 0)
            {
                card.Children.Add(new TextBlock { Text = $"对白 {panel.Dialogues.Count} 条", Style = (Style)Application.Current.FindResource("Micro"), Margin = new Thickness(0, 6, 0, 0) });
                foreach (var dialogue in panel.Dialogues)
                    card.Children.Add(new TextBlock
                    {
                        Text = $"{dialogue.Text("speaker_name", "旁白")}：{dialogue.Text("target_text")}",
                        Style = (Style)Application.Current.FindResource("Micro"), TextWrapping = TextWrapping.Wrap, Margin = new Thickness(0, 4, 0, 0),
                    });
            }
            var edit = Kit.Act("编辑本格", async (_, _) => await EditPanel(panel), "Compact");
            edit.Margin = new Thickness(0, 10, 0, 0);
            card.Children.Add(edit);
            inspector.Children.Add(new Border { Style = (Style)Application.Current.FindResource("Card"), Padding = new Thickness(14), Child = card });
        }
    }

    private async Task EditPanel(PanelNode panel)
    {
        try
        {
            var fresh = await Api.SendAsync($"pages/{currentPage!.Id}/storyboard", cancellation: lifetime.Token);
            var row = fresh.Array("panels").FirstOrDefault(p => p.Text("id") == panel.Id);
            if (row.ValueKind != JsonValueKind.Object) throw new InvalidOperationException("找不到该分镜格");
            var dialog = new PanelEditDialog(Host, row, characters, outfits);
            if (dialog.ShowDialog() != true || dialog.Result == null) return;
            await Api.SendAsync($"panels/{panel.Id}", HttpMethod.Patch, dialog.Result, cancellation: lifetime.Token);
            Cache.Invalidate("storyboard:" + currentPage.Id, "pages:" + chapterId);
            await SelectPageAsync(currentPage);
        }
         catch (OperationCanceledException) { }
        catch (Exception error) when (error is not OperationCanceledException)
        {
            MessageBox.Show(Host, error.Message, "保存本格未完成", MessageBoxButton.OK, MessageBoxImage.Warning);
        }
    }

    private async Task DeleteBubbleAsync(BubbleNode bubble)
    {
        if (MessageBox.Show(Host, "删除这个文字气泡？", "删除气泡", MessageBoxButton.YesNo, MessageBoxImage.Question) != MessageBoxResult.Yes) return;
        try
        {
            await Api.SendOptionalAsync($"dialogues/{bubble.Id}", HttpMethod.Delete,
                new { panel_version = bubble.PanelVersion }, lifetime.Token);
            bubbles.Remove(bubble);
            page.Children.Remove(bubble.Element);
            Cache.Invalidate("storyboard:" + currentPage?.Id, "pages:" + chapterId);
            MarkDirty();
            RenderInspector();
            RenderOrderBadges();
        }
         catch (OperationCanceledException) { }
        catch (Exception error) when (error is not OperationCanceledException)
        {
            MessageBox.Show(Host, error.Message, "删除未完成", MessageBoxButton.OK, MessageBoxImage.Warning);
        }
    }

    private void RebuildLayout()
    {
        if (currentPage == null) return;
        var dialog = new LayoutRebuildDialog(Host, currentPage.PageNumber, panels.Count);
        if (dialog.ShowDialog() != true) return;
        _ = RebuildLayoutAsync(dialog.PanelCount, dialog.LayoutMode);
    }

    private async Task RebuildLayoutAsync(int count, string mode)
    {
        try
        {
            await Api.SendAsync($"pages/{currentPage!.Id}/layout", HttpMethod.Patch,
                new { panel_count = count, layout_mode = mode }, cancellation: lifetime.Token);
            history.Clear();
            requestId = null;
            dirty = false;
            await SelectPageAsync(currentPage);
            UpdateStatus("版式已重建");
        }
         catch (OperationCanceledException) { }
        catch (Exception error) when (error is not OperationCanceledException)
        {
            MessageBox.Show(Host, error.Message, "重建未完成", MessageBoxButton.OK, MessageBoxImage.Warning);
        }
    }

    // ============ Save ============
    private async Task SaveAsync()
    {
        if (currentPage == null || saving || !dirty) return;
        saving = true;
        saveButton.IsEnabled = false;
        saveButton.Content = "保存中";
        try
        {
            var reuseRequest = requestId != null && historyIndexAtRequest == history.Index;
            var id = reuseRequest ? requestId!.Value : Guid.NewGuid();
            if (!reuseRequest)
            {
                // Bind the request id to the current undo state so a retry of the same
                // content replays safely while new edits get a fresh id.
                requestId = id;
                historyIndexAtRequest = history.Index;
            }
            var payload = BuildPayload(id);
            var response = await Api.SendAsync($"pages/{currentPage.Id}/storyboard-geometry", HttpMethod.Put, payload, cancellation: lifetime.Token);
            storyboard = response;
            requestId = null;
            history.Clear();
            dirty = false;
            var version = response.Element("page").Number("storyboard_version");
            var staleCount = response.Number("candidate_count");
            UpdateStatus($"已保存 · 当前 V{version}");
            State.Status = $"分镜已保存 · V{version} · 将使 {staleCount} 个候选过期";
            Cache.Invalidate("pages:" + chapterId, "workbench:", "library:" + ProjectId);
            await SelectPageAsync(currentPage);
        }
         catch (OperationCanceledException) { }
        catch (Exception error) when (error is not OperationCanceledException)
        {
            UpdateStatus("保存失败");
            MessageBox.Show(Host, error.Message + "\n\n画布草稿已保留，可重试保存或放弃草稿。", "保存未完成", MessageBoxButton.OK, MessageBoxImage.Warning);
        }
        finally
        {
            saving = false;
            saveButton.Content = "保存本页";
            saveButton.IsEnabled = dirty;
        }
    }

    private object BuildPayload(Guid id)
    {
        var pageValue = storyboard.Element("page");
        return new
        {
            request_id = id.ToString(),
            storyboard_version = currentPage?.StoryboardVersion ?? pageValue.Number("storyboard_version"),
            panels = panels.Select(panel => new
            {
                panel_id = panel.Id,
                bounds = Round4(panel.Rect),
                // 未改动时原样透传服务端几何（保住 polygon/z_order）；
                // 拖动过则退回构造值，否则服务端因 geometry.rect 与 bounds 不一致拒绝
                geometry = (object?)(panel.GeometryIsCurrent() ? panel.StoredGeometry : new
                {
                    type = "rect",
                    rect = Round4(panel.Rect),
                    polygon = (double[]?)null,
                    rotation = 0,
                    z_order = panel.ZOrder,
                }),
                reading_order = panel.ReadingOrder,
            }),
            dialogues = bubbles.Select(bubble => new
            {
                dialogue_id = bubble.Id,
                // 派生位置未动过的气泡传 null（服务端保持原状、不升版本）；
                // 其余以存储形状为底提交，保留 anchor/tail_target/rotation
                bubble = bubble.Legacy && !bubble.Moved
                    ? null
                    : (object?)bubble.BuildPayloadBubble(),
                reading_order = bubble.ReadingOrder,
            }),
        };
    }

    private static Dictionary<string, double> Round4(Rect rect) => new()
    {
        ["x"] = Math.Round(rect.X, 4),
        ["y"] = Math.Round(rect.Y, 4),
        ["width"] = Math.Round(rect.Width, 4),
        ["height"] = Math.Round(rect.Height, 4),
    };

    // ============ Keyboard ============
    private void OnCanvasKey(object sender, KeyEventArgs e)
    {
        if (currentPage == null) return;
        var step = (Keyboard.Modifiers == ModifierKeys.Shift ? 10 : 1) / Math.Max(1, page.Width);
        switch (e.Key)
        {
            case Key.Delete or Key.Back:
                if (selectedBubble != null) { _ = DeleteBubbleAsync(selectedBubble); e.Handled = true; }
                break;
            case Key.Tab:
                if (panels.Count > 0)
                {
                    var index = selected == null ? 0 : (panels.IndexOf(selected) + (Keyboard.Modifiers == ModifierKeys.Shift ? -1 : 1) + panels.Count) % panels.Count;
                    SelectPanel(panels[index]);
                    e.Handled = true;
                }
                break;
            case Key.Left or Key.Right or Key.Up or Key.Down:
                if (selectedBubble != null)
                {
                    var offset = e.Key switch
                    {
                        Key.Left => new Point(-step, 0), Key.Right => new Point(step, 0),
                        Key.Up => new Point(0, -step), _ => new Point(0, step),
                    };
                    var next = new Rect(selectedBubble.Rect.X + offset.X, selectedBubble.Rect.Y + offset.Y, selectedBubble.Rect.Width, selectedBubble.Rect.Height);
                    var host = panels.FirstOrDefault(p => p.Id == selectedBubble.PanelId);
                    if (host != null) next = ClampInto(next, host.Rect);
                    history.Push(new GeometryCommand("方向键微调", [new BubbleChange(selectedBubble.Id, selectedBubble.Rect, next)]));
                    selectedBubble.Rect = next;
                    selectedBubble.Moved = true;
                    selectedBubble.ApplyPosition(page);
                    MarkDirty();
                    e.Handled = true;
                }
                else if (selected != null)
                {
                    var offset = e.Key switch
                    {
                        Key.Left => new Point(-step, 0), Key.Right => new Point(step, 0),
                        Key.Up => new Point(0, -step), _ => new Point(0, step),
                    };
                    var next = new Rect(selected.Rect.X + offset.X, selected.Rect.Y + offset.Y, selected.Rect.Width, selected.Rect.Height);
                    history.Push(new GeometryCommand("方向键微调", [new PanelChange(selected.Id, selected.Rect, next)]));
                    selected.Rect = next;
                    selected.ApplyPosition(page);
                    MarkDirty();
                    e.Handled = true;
                }
                break;
        }
    }

    private void OnViewportWheel(object sender, MouseWheelEventArgs e)
    {
        if (Keyboard.Modifiers == ModifierKeys.Control)
        {
            SetZoom(zoom * (e.Delta > 0 ? 1.25 : 1 / 1.25));
            e.Handled = true;
        }
    }

    private void SetZoom(double value)
    {
        zoom = Math.Clamp(value, 0.25, 4);
        zoomLabel.Text = $"{(int)Math.Round(zoom * 100)}%";
        UpdatePageSize();
    }

    private void FitViewport()
    {
        var width = Math.Clamp(Math.Min((viewport.ActualWidth - 72) / BasePageWidth, (viewport.ActualHeight - 72) / (BasePageWidth * pageAspect)), 0.25, 4);
        SetZoom(width);
    }

    public override Task<bool> ConfirmLeaveAsync()
    {
        if (!dirty) return Task.FromResult(true);
        var result = MessageBox.Show(Host, "分镜画布有未保存的几何草稿，离开将丢失。确定离开吗？",
            "离开确认", MessageBoxButton.YesNo, MessageBoxImage.Question);
        return Task.FromResult(result == MessageBoxResult.Yes);
    }

    public override Task RefreshAsync()
    {
        if (currentPage != null) _ = SelectPageAsync(currentPage);
        return Task.CompletedTask;
    }

    // ============ Geometry node model ============
    private sealed class PanelNode
    {
        public required string Id { get; init; }
        public Rect Rect { get; set; }
        public int ReadingOrder { get; init; }
        public int ZOrder { get; init; }
        public int Version { get; init; }
        public string Action { get; init; } = "";
        public string ShotType { get; init; } = "";
        public string CameraAngle { get; init; } = "";
        public bool Bleed { get; init; }
        public bool Borderless { get; init; }
        public List<JsonElement> Dialogues { get; init; } = [];
        public JsonElement StoredGeometry { get; init; }
        public required Border Element { get; init; }

        public static PanelNode From(JsonElement row)
        {
            var bounds = row.Element("bounds");
            var geometry = row.Element("geometry");
            var rect = geometry.Element("rect");
            Rect position = rect.ValueKind == JsonValueKind.Object
                ? new Rect(rect.Decimal("x"), rect.Decimal("y"), rect.Decimal("width"), rect.Decimal("height"))
                : bounds.ValueKind == JsonValueKind.Object
                    ? new Rect(bounds.Decimal("x"), bounds.Decimal("y"), bounds.Decimal("width"), bounds.Decimal("height"))
                    : new Rect(0.05, 0.05, 0.4, 0.4);
            var bleed = row.Flag("bleed");
            var borderless = row.Flag("borderless");
            return new PanelNode
            {
                Id = row.Text("id"),
                Rect = position,
                ReadingOrder = row.Number("reading_order"),
                ZOrder = geometry.Number("z_order") is var z && z > 0 ? z : row.Number("reading_order"),
                Version = row.Number("version"),
                Action = row.Text("action"),
                ShotType = row.Text("shot_type"),
                CameraAngle = row.Text("camera_angle"),
                Bleed = bleed,
                Borderless = borderless,
                Dialogues = row.Array("dialogues"),
                StoredGeometry = geometry.ValueKind == JsonValueKind.Object ? geometry : default,
                Element = new Border
                {
                    BorderBrush = new SolidColorBrush(Color.FromRgb(0x4A, 0x45, 0x3D)),
                    BorderThickness = new Thickness(1.5),
                    Background = new SolidColorBrush(Color.FromArgb(0x0A, 0xFF, 0xFF, 0xFF)),
                    Cursor = Cursors.Hand,
                },
            };
        }

        // 存储几何可原样透传的条件：rect 型几何与当前 bounds 一致（服务端会校验二者一致），
        // 或本就无 rect（如 polygon），此时透传才能保住 polygon/z_order。
        public bool GeometryIsCurrent()
        {
            if (StoredGeometry.ValueKind != JsonValueKind.Object) return false;
            var rect = StoredGeometry.Element("rect");
            if (rect.ValueKind != JsonValueKind.Object) return true;
            return Math.Round(rect.Decimal("x"), 4) == Math.Round(Rect.X, 4)
                && Math.Round(rect.Decimal("y"), 4) == Math.Round(Rect.Y, 4)
                && Math.Round(rect.Decimal("width"), 4) == Math.Round(Rect.Width, 4)
                && Math.Round(rect.Decimal("height"), 4) == Math.Round(Rect.Height, 4);
        }

        public void ApplyPosition(Canvas canvas)
        {
            Canvas.SetLeft(Element, Rect.X * canvas.Width);
            Canvas.SetTop(Element, Rect.Y * canvas.Height);
            Element.Width = Math.Max(4, Rect.Width * canvas.Width);
            Element.Height = Math.Max(4, Rect.Height * canvas.Height);
            Panel.SetZIndex(Element, 10 + ZOrder);
        }

        public void SetRectDirect(Rect rect, Canvas canvas)
        {
            Rect = rect;
            ApplyPosition(canvas);
        }

        public void SetSelected(bool isSelected)
        {
            Element.BorderBrush = isSelected
                ? (Brush)Application.Current.FindResource("Accent")
                : new SolidColorBrush(Color.FromRgb(0x4A, 0x45, 0x3D));
            Element.BorderThickness = isSelected ? new Thickness(2.5) : new Thickness(1.5);
        }
    }

    private sealed class BubbleNode
    {
        public required string Id { get; init; }
        public required string PanelId { get; init; }
        public Rect Rect { get; set; }
        public string Shape { get; init; } = "rect";
        public string TargetText { get; init; } = "";
        public int ReadingOrder { get; init; }
        public int PanelVersion { get; init; }
        public JsonElement StoredBubble { get; init; }
        public bool Legacy { get; init; }          // 服务端无 rect，客户端按版式派生
        public bool Moved { get; set; }            // 拖拽或键盘微调过即视为已定位
        public required Border Element { get; init; }

        public static BubbleNode From(JsonElement dialogue, PanelNode panel, int index)
        {
            var shape = dialogue.Element("bubble");
            var hasRect = shape.Element("rect").ValueKind == JsonValueKind.Object;
            Rect rect;
            if (hasRect)
            {
                var r = shape.Element("rect");
                rect = new Rect(r.Decimal("x"), r.Decimal("y"), r.Decimal("width"), r.Decimal("height"));
            }
            else
            {
                // Legacy derivation mirrors the web geometry helper.
                rect = new Rect(
                    panel.Rect.X + 0.03 + (index % 3) * 0.06,
                    panel.Rect.Y + 0.03 + (index / 3) * 0.16,
                    Math.Min(0.2, panel.Rect.Width * 0.6),
                    Math.Min(0.13, panel.Rect.Height * 0.35));
            }
            var ellipse = shape.Text("type") == "ellipse";
            return new BubbleNode
            {
                Id = dialogue.Text("id"),
                PanelId = panel.Id,
                Rect = rect,
                Shape = ellipse ? "ellipse" : "rect",
                TargetText = dialogue.Text("target_text"),
                ReadingOrder = dialogue.Number("reading_order"),
                PanelVersion = panel.Version,
                StoredBubble = shape.ValueKind == JsonValueKind.Object ? shape : default,
                Legacy = !hasRect,
                Element = new Border
                {
                    Background = new SolidColorBrush(Color.FromArgb(0xE0, 0xFF, 0xFF, 0xFF)),
                    BorderBrush = new SolidColorBrush(Color.FromRgb(0x33, 0x30, 0x2A)),
                    BorderThickness = new Thickness(1.5),
                    CornerRadius = new CornerRadius(ellipse ? 999 : 12),
                    MinWidth = 12, MinHeight = 12,
                    Cursor = Cursors.Hand,
                    Child = new TextBlock
                    {
                        Text = ellipse ? "" : "💬",
                        FontSize = 11, HorizontalAlignment = HorizontalAlignment.Center,
                        VerticalAlignment = VerticalAlignment.Center,
                    },
                },
            };
        }

        public void ApplyPosition(Canvas canvas)
        {
            Canvas.SetLeft(Element, Rect.X * canvas.Width);
            Canvas.SetTop(Element, Rect.Y * canvas.Height);
            Element.Width = Math.Max(12, Rect.Width * canvas.Width);
            Element.Height = Math.Max(12, Rect.Height * canvas.Height);
            Panel.SetZIndex(Element, 20);
        }

        public void SetRectDirect(Rect rect, Canvas canvas)
        {
            Rect = rect;
            Moved = true;
            ApplyPosition(canvas);
        }

        // 提交时以服务端存储形状为底，更新 rect 并剥离服务端专有键；
        // 移动过的气泡丢弃旧的 text_region（服务端要求它位于新 rect 内）。
        public object BuildPayloadBubble()
        {
            if (StoredBubble.ValueKind != JsonValueKind.Object)
                return new { type = Shape, rect = Round4(Rect), rotation = 0 };
            var payload = JsonSerializer.Deserialize<Dictionary<string, object?>>(StoredBubble.GetRawText()) ?? [];
            var storedRect = StoredBubble.Element("rect");
            var moved = Math.Round(storedRect.Decimal("x"), 4) != Math.Round(Rect.X, 4)
                || Math.Round(storedRect.Decimal("y"), 4) != Math.Round(Rect.Y, 4)
                || Math.Round(storedRect.Decimal("width"), 4) != Math.Round(Rect.Width, 4)
                || Math.Round(storedRect.Decimal("height"), 4) != Math.Round(Rect.Height, 4);
            payload.Remove("mapped_from_legacy");
            payload["rect"] = Round4(Rect);
            if (moved) payload.Remove("text_region");
            return payload;
        }

        public void SetSelected(bool isSelected)
        {
            Element.BorderBrush = isSelected
                ? (Brush)Application.Current.FindResource("Accent")
                : new SolidColorBrush(Color.FromRgb(0x33, 0x30, 0x2A));
            Element.BorderThickness = isSelected ? new Thickness(2.5) : new Thickness(1.5);
        }
    }

    private sealed class PanelGesture(List<PanelNode> group)
    {
        public List<PanelNode> Group { get; } = group;
        public Dictionary<string, Rect> Origins { get; } = group.ToDictionary(g => g.Id, g => g.Rect);
        public Point Offset { get; set; }

        public void Commit(StoryboardView view)
        {
            var changes = new List<GeometryChange>();
            foreach (var target in Group)
            {
                var before = Origins[target.Id];
                var next = SnapRect(view, new Rect(before.X + Offset.X, before.Y + Offset.Y, before.Width, before.Height), target);
                if (next != before)
                {
                    changes.Add(new PanelChange(target.Id, before, next));
                    target.Rect = next;
                }
            }
            if (changes.Count > 0)
            {
                view.history.Push(new GeometryCommand("拖动格子", changes));
                view.MarkDirty();
            }
            view.UpdatePageSize();
        }

        private Rect SnapRect(StoryboardView view, Rect rect, PanelNode self)
        {
            var clamped = new Rect(
                Math.Clamp(rect.X, 0, 1 - Math.Min(rect.Width, 1)),
                Math.Clamp(rect.Y, 0, 1 - Math.Min(rect.Height, 1)),
                Math.Max(MinSize, Math.Min(rect.Width, 1)), Math.Max(MinSize, Math.Min(rect.Height, 1)));
            if (view.snapButton.IsChecked != true) return clamped;
            var targets = new List<double>(PageGuides);
            foreach (var other in view.panels.Where(p => !ReferenceEquals(p, self)))
            {
                targets.Add(other.Rect.X); targets.Add(other.Rect.X + other.Rect.Width / 2); targets.Add(other.Rect.Right);
                targets.Add(other.Rect.Y); targets.Add(other.Rect.Y + other.Rect.Height / 2); targets.Add(other.Rect.Bottom);
            }
            var bestX = BestDelta([clamped.X, clamped.X + clamped.Width / 2, clamped.Right], targets, clamped.X);
            var bestY = BestDelta([clamped.Y, clamped.Y + clamped.Height / 2, clamped.Bottom], targets, clamped.Y);
            view.ClearGuides();
            if (bestX.Delta <= SnapThreshold && bestX.Delta < Math.Abs(bestY.Delta)) view.ShowGuide(bestX.Guide, true);
            if (bestY.Delta <= SnapThreshold && bestY.Delta < Math.Abs(bestX.Delta)) view.ShowGuide(bestY.Guide, false);
            var moved = new Rect(clamped.X + (bestX.Delta <= SnapThreshold ? bestX.Delta : 0), clamped.Y + (bestY.Delta <= SnapThreshold ? bestY.Delta : 0), clamped.Width, clamped.Height);
            return new Rect(Math.Clamp(moved.X, 0, 1 - moved.Width), Math.Clamp(moved.Y, 0, 1 - moved.Height), moved.Width, moved.Height);
        }

        private static (double Delta, double Guide) BestDelta(double[] edges, List<double> targets, double current)
        {
            var best = (Delta: double.MaxValue, Guide: current);
            foreach (var edge in edges)
                foreach (var target in targets)
                {
                    var delta = target - edge;
                    if (Math.Abs(delta) < Math.Abs(best.Delta)) best = (delta, target);
                }
            return best;
        }
    }

    private sealed record GeometryCommand(string Label, List<GeometryChange> Changes);
    private abstract record GeometryChange(string Id);
    private sealed record PanelChange(string Id, Rect Before, Rect After) : GeometryChange(Id);
    private sealed record BubbleChange(string Id, Rect Before, Rect After) : GeometryChange(Id);

    private sealed class CommandStack
    {
        private readonly List<GeometryCommand> stack = [];
        public int Index { get; private set; }
        public bool CanUndo => Index > 0;
        public bool CanRedo => Index < stack.Count;

        public void Push(GeometryCommand command)
        {
            if (command.Changes.Count == 0) return;
            if (Index < stack.Count) stack.RemoveRange(Index, stack.Count - Index);
            stack.Add(command);
            Index++;
        }

        public GeometryCommand Undo() => stack[--Index];
        public GeometryCommand Redo() => stack[Index++];
        public void Clear() { stack.Clear(); Index = 0; }
    }
}

/// <summary>Panel narrative edit dialog: shot, action, cast presence, outfit, dialogues.</summary>
internal sealed class PanelEditDialog : Window
{
    public object? Result { get; private set; }

    public PanelEditDialog(Window owner, JsonElement panel, List<CharacterItem> characters, List<OutfitItem> outfits)
    {
        Owner = owner;
        Title = "编辑本格分镜";
        Width = 560;
        MaxHeight = 720;
        WindowStartupLocation = WindowStartupLocation.CenterOwner;
        ShowInTaskbar = false;
        Background = (Brush)Application.Current.FindResource("Paper");
        var scroll = new ScrollViewer { VerticalScrollBarVisibility = ScrollBarVisibility.Auto };
        var form = new StackPanel { Margin = new Thickness(24) };

        var shot = new ComboBox();
        foreach (var (key, label) in Labels.ShotType) shot.Items.Add(new ComboBoxItem { Tag = key, Content = label });
        Select(shot, panel.Text("shot_type"));
        var angle = new ComboBox();
        foreach (var (key, label) in Labels.CameraAngle) angle.Items.Add(new ComboBoxItem { Tag = key, Content = label });
        Select(angle, panel.Text("camera_angle"));
        var action = new TextBox { Text = panel.Text("action"), AcceptsReturn = true, MinHeight = 60 };
        var background = new TextBox { Text = panel.Text("background"), AcceptsReturn = true, MinHeight = 48 };
        var props = new TextBox { Text = string.Join("、", panel.Strings("props")) };
        var soundEffects = new TextBox { Text = string.Join("、", panel.Strings("sound_effects")) };

        form.Children.Add(Label("景别"));
        form.Children.Add(shot);
        form.Children.Add(Label("镜头角度"));
        form.Children.Add(angle);
        form.Children.Add(Label("动作与表演（必填）"));
        form.Children.Add(action);
        form.Children.Add(Label("背景"));
        form.Children.Add(background);
        form.Children.Add(Label("场景道具（用逗号分隔）"));
        form.Children.Add(props);
        form.Children.Add(Label("拟声词（用逗号分隔）"));
        form.Children.Add(soundEffects);
        form.Children.Add(Label("人物状态"));
        var presence = new Dictionary<string, ComboBox>();
        foreach (var character in characters)
        {
            var row = new StackPanel { Orientation = Orientation.Horizontal, Margin = new Thickness(0, 4, 0, 4) };
            row.Children.Add(new TextBlock { Text = character.PrimaryName, Width = 100, VerticalAlignment = VerticalAlignment.Center });
            var box = new ComboBox { Width = 180 };
            foreach (var (key, label) in Labels.CharacterPresence)
                box.Items.Add(new ComboBoxItem { Tag = key, Content = label });
            Select(box, panel.Element("presence").Text(character.Id, "NONE") is { Length: > 0 } state ? state : "NONE");
            presence[character.Id] = box;
            row.Children.Add(box);
            form.Children.Add(row);
        }
        var bleed = new CheckBox { Content = "出血格", IsChecked = panel.Flag("bleed"), Margin = new Thickness(0, 8, 0, 0) };
        var borderless = new CheckBox { Content = "无边框", IsChecked = panel.Flag("borderless"), Margin = new Thickness(0, 0, 0, 12) };
        form.Children.Add(bleed);
        form.Children.Add(borderless);

        var error = new TextBlock { Foreground = (Brush)Application.Current.FindResource("Danger"), TextWrapping = TextWrapping.Wrap, Margin = new Thickness(0, 6, 0, 6) };
        form.Children.Add(error);
        var actions = new StackPanel { Orientation = Orientation.Horizontal, HorizontalAlignment = HorizontalAlignment.Right };
        var cancel = new Button { Content = "取消", MinWidth = 96, Margin = new Thickness(0, 0, 10, 0) };
        cancel.Click += (_, _) => Close();
        var submit = new Button { Content = "保存本格分镜", MinWidth = 130, Style = (Style)Application.Current.FindResource("InkButton") };
        submit.Click += async (_, _) =>
        {
            if (action.Text.Trim().Length == 0) { error.Text = "动作与表演不能为空。"; return; }
            submit.IsEnabled = false;
            try
            {
                var presencePayload = presence.Where(p => (p.Value.SelectedItem as ComboBoxItem)?.Tag as string is { Length: > 0 } state && state != "NONE")
                    .ToDictionary(p => p.Key, p => (p.Value.SelectedItem as ComboBoxItem)!.Tag as string);
                Result = new
                {
                    version = panel.Number("version"),
                    shot_type = (shot.SelectedItem as ComboBoxItem)?.Tag,
                    camera_angle = (angle.SelectedItem as ComboBoxItem)?.Tag,
                    action = action.Text,
                    background = background.Text,
                    props = SplitList(props.Text),
                    sound_effects = SplitList(soundEffects.Text),
                    character_presence = presencePayload,
                    bleed = bleed.IsChecked == true,
                    borderless = borderless.IsChecked == true,
                };
                DialogResult = true;
            }
            catch (Exception reason)
            {
                error.Text = reason.Message;
                submit.IsEnabled = true;
            }
        };
        actions.Children.Add(cancel);
        actions.Children.Add(submit);
        form.Children.Add(actions);
        scroll.Content = form;
        Content = scroll;
        PreviewKeyDown += (_, e) => { if (e.Key == Key.Escape) Close(); };

        static void Select(ComboBox box, string tag)
        {
            foreach (var item in box.Items.OfType<ComboBoxItem>())
                if ((string?)item.Tag == tag) { box.SelectedItem = item; return; }
            if (box.Items.Count > 0) box.SelectedIndex = 0;
        }
        static List<string> SplitList(string text) => text.Split(['，', '、', ','], StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries).ToList();
    }

    private static TextBlock Label(string text) => new()
    { Text = text, Style = (Style)Application.Current.FindResource("FieldLabel"), Margin = new Thickness(0, 10, 0, 6) };
}

internal sealed class LayoutRebuildDialog : Window
{
    public int PanelCount { get; private set; } = 4;
    public string LayoutMode { get; private set; } = "dynamic";

    public LayoutRebuildDialog(Window owner, int pageNumber, int currentCount)
    {
        Owner = owner;
        Title = "重建本页版式";
        Width = 470;
        SizeToContent = SizeToContent.Height;
        WindowStartupLocation = WindowStartupLocation.CenterOwner;
        ShowInTaskbar = false;
        Background = (Brush)Application.Current.FindResource("Paper");
        var panel = new StackPanel { Margin = new Thickness(24) };
        panel.Children.Add(new TextBlock
        {
            Text = "重建本页版式", FontFamily = (FontFamily)Application.Current.FindResource("Serif"),
            FontSize = 20, FontWeight = FontWeights.SemiBold,
        });
        panel.Children.Add(new TextBlock
        {
            Text = "重建将删除本页全部格子与气泡，并从已确认剧本与原文区间重新映射；已生成候选会因分镜版本过期。这不是视觉拖拽的替代操作。",
            Style = (Style)Application.Current.FindResource("Caption"), Margin = new Thickness(0, 8, 0, 14), TextWrapping = TextWrapping.Wrap,
        });
        panel.Children.Add(new TextBlock { Text = "本页格数", Style = (Style)Application.Current.FindResource("FieldLabel") });
        var counts = new StackPanel { Orientation = Orientation.Horizontal, Margin = new Thickness(0, 6, 0, 12) };
        foreach (var count in Enumerable.Range(3, 6))
        {
            var toggle = new ToggleButton
            {
                Content = count.ToString(), Style = (Style)Application.Current.FindResource("Pill"),
                IsChecked = count == currentCount, MinWidth = 48, Margin = new Thickness(0, 0, 6, 0),
            };
            toggle.Click += (_, _) =>
            {
                PanelCount = count;
                foreach (var other in counts.Children.OfType<ToggleButton>()) other.IsChecked = ReferenceEquals(other, toggle);
            };
            counts.Children.Add(toggle);
        }
        panel.Children.Add(counts);
        panel.Children.Add(new TextBlock { Text = "版式", Style = (Style)Application.Current.FindResource("FieldLabel") });
        var modes = new StackPanel { Orientation = Orientation.Horizontal };
        var dynamicMode = new ToggleButton { Content = "动态错落", Style = (Style)Application.Current.FindResource("Pill"), IsChecked = true, MinWidth = 96, Margin = new Thickness(0, 6, 6, 6) };
        var balancedMode = new ToggleButton { Content = "均衡网格", Style = (Style)Application.Current.FindResource("Pill"), MinWidth = 96, Margin = new Thickness(0, 6, 0, 6) };
        dynamicMode.Click += (_, _) => { LayoutMode = "dynamic"; dynamicMode.IsChecked = true; balancedMode.IsChecked = false; };
        balancedMode.Click += (_, _) => { LayoutMode = "balanced"; balancedMode.IsChecked = true; dynamicMode.IsChecked = false; };
        modes.Children.Add(dynamicMode);
        modes.Children.Add(balancedMode);
        panel.Children.Add(modes);
        panel.Children.Add(new TextBlock { Text = $"当前第 {pageNumber} 页为 {currentCount} 格。", Style = (Style)Application.Current.FindResource("Micro"), Margin = new Thickness(0, 10, 0, 0) });
        var actions = new StackPanel { Orientation = Orientation.Horizontal, HorizontalAlignment = HorizontalAlignment.Right, Margin = new Thickness(0, 16, 0, 0) };
        var cancel = new Button { Content = "取消", MinWidth = 90, Margin = new Thickness(0, 0, 10, 0) };
        cancel.Click += (_, _) => Close();
        var confirm = new Button { Content = "确认重建", MinWidth = 110, Style = (Style)Application.Current.FindResource("InkButton") };
        confirm.Click += (_, _) => DialogResult = true;
        actions.Children.Add(cancel);
        actions.Children.Add(confirm);
        panel.Children.Add(actions);
        Content = panel;
        PreviewKeyDown += (_, e) => { if (e.Key == Key.Escape) Close(); };
    }
}
