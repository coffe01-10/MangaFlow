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
    private readonly Border conflictBar = new();
    // 对齐 web StoryboardToolbar 的两组开关：出血框/安全区默认关闭，页缺 canvas
    // 字段时禁用；专注模式对齐 focus-mode CSS（隐藏页面条）；重算按钮对齐
    // storyboard-status 行的「从本页重新计算」（桌面无独立 status 行，放工具栏）。
    private readonly ToggleButton bleedButton = new() { Content = "出血框", Style = (Style)Application.Current.FindResource("Pill") };
    private readonly ToggleButton safeButton = new() { Content = "安全区", Style = (Style)Application.Current.FindResource("Pill") };
    private readonly ToggleButton focusButton = new() { Content = "专注模式", Style = (Style)Application.Current.FindResource("Pill") };
    private readonly Button replanButton = new() { Content = "从本页重新计算", Style = (Style)Application.Current.FindResource("Compact") };

    // 页画布尺寸（对齐 web defaultCanvas：缺字段回落 182×257mm、出血 3mm、安全 5mm）
    private bool canvasKnown;
    private double canvasWidthMm = 182, canvasHeightMm = 257, bleedMm = 3, safeMm = 5;
    // 出血/安全区叠加元素（对齐 web GuidesOverlay：出血框画在页外、安全区内缩）
    private Border? bleedRing, safeFrame;
    // 选中格的 8 向缩放手柄（对齐 web transform-handles：nw/n/ne/e/se/s/sw/w，
    // 仅恰好单选一个矩形格时挂载；多边形格与气泡选中不显示面板手柄）
    private readonly List<(string Name, Border Element)> resizeHandles = [];
    // 叙事草稿（对齐 web dialogueDrafts/newDialogue）：键为对白 id。任何存活对白
    // 的在册草稿、或新增气泡草稿打开，都参与脏判定（离开保护）；保存成功且
    // 无更新输入时才摘除。保存本页按钮只看几何草稿（web canSave 同源）。
    private readonly Dictionary<string, DialogueDraft> dialogueDrafts = [];
    private DialogueDraft? newDialogue;
    private bool narrativeBusy, replanning;
    // 测试缝：headless 回归检查用无模态的实现替换离开确认（AssetsView 的
    // OutfitDraftPrompt 同一模式）；生产路径为 null。
    internal Func<Task<bool>>? LeaveConfirmOverride;
    // 测试缝：headless 回归检查用无模态实现替换气泡删除确认；生产路径为 null。
    internal Func<bool>? DeleteConfirmOverride;

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
    // 对齐 web storyboard-editor 的 geometryRequestRef：记录上一次几何保存的
    // { request_id, 撤销栈 index, 气泡数 }。网络超时后服务端可能已提交事务
    // （幂等元组随成功事务持久化，见 storyboard_geometry.save_storyboard_geometry），
    // 同一草稿状态重试必须重放同一 request_id；栈或气泡集合变过才换新 id。
    // 气泡删除不在撤销栈里，bubbles.Count 是栈 index 之外必要的草稿指纹。
    private (Guid Id, int StackIndex, int BubbleCount)? geometryRequest;
    private bool saving, dirty, bubblesDeleted;
    private int pageLoadVersion;
    // #429-3: LoadPagesAsync 的章节页列表读取序号（与 pageLoadVersion 分工：
    // 那个只守 SelectPageAsync 的整页分镜读取，这个守页列表与页签重渲）。
    private int pagesLoadVersion;
    private PanelNode? selected;
    private BubbleNode? selectedBubble;

    public StoryboardView()
    {
        BuildLayout();
        // 键盘纪律（对齐 WorkflowView 的模式）：快捷键处理器挂画布元素，不挂整个
        // View。PreviewKeyDown 是隧道事件，挂在 View 上会让检查器对白编辑器的
        // TextBox 里按 Backspace/Delete 触发气泡删除确认、方向键变成面板微移、
        // Tab 被无条件劫持（#339）；挂在 page 上后，画布子树之外的焦点（检查器、
        // 工具栏）产生的按键根本不会路由进 OnCanvasKey。
        page.Focusable = true;
        page.PreviewKeyDown += OnCanvasKey;
        Focusable = true;
        page.MouseLeftButtonDown += (_, e) =>
        {
            if (e.OriginalSource is Canvas)
            {
                SelectPanel(null);
                FocusCanvas();
                e.Handled = true;
            }
        };
    }

    // 画布可能尚未挂进视觉树（如无头回归检查），此时 Focus 无效，跳过即可。
    private void FocusCanvas()
    {
        if (page.IsLoaded) page.Focus();
    }

    private void BuildLayout()
    {
        var root = new Grid { Margin = new Thickness(4, 0, 24, 24) };
        root.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
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
        // 409 冲突条（对齐 web storyboard-editor 的冲突横幅 + discardDraft）：
        // 几何保存撞车时保留草稿，并给出「放弃草稿并重新加载」的恢复出口。
        BuildConflictBar();
        Grid.SetRow(conflictBar, 2);
        root.Children.Add(conflictBar);
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
        Grid.SetRow(split, 3);
        Content = root;
    }

    private void BuildConflictBar()
    {
        conflictBar.BorderBrush = (Brush)Application.Current.FindResource("Danger");
        conflictBar.BorderThickness = new Thickness(0, 0, 0, 2);
        conflictBar.Padding = new Thickness(14, 9, 14, 9);
        conflictBar.Margin = new Thickness(0, 0, 0, 12);
        conflictBar.Visibility = Visibility.Collapsed;
        var row = new StackPanel { Orientation = Orientation.Horizontal };
        row.Children.Add(new TextBlock
        {
            Text = "分镜格已在别处更新，画布草稿已保留",
            FontSize = 12.5, VerticalAlignment = VerticalAlignment.Center, TextWrapping = TextWrapping.Wrap,
        });
        var discard = new Button { Content = "放弃草稿并重新加载", Style = (Style)Application.Current.FindResource("InkButton"), Margin = new Thickness(12, 0, 0, 0) };
        discard.Click += async (_, _) => await DiscardDraftAndReloadAsync();
        row.Children.Add(discard);
        conflictBar.Child = row;
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
        replanButton.Click += async (_, _) => await ReplanFromPageAsync();
        replanButton.Margin = new Thickness(6, 0, 6, 0);
        right.Children.Add(replanButton);
        focusButton.Click += (_, _) => ApplyFocusMode();
        focusButton.Margin = new Thickness(0, 0, 6, 0);
        right.Children.Add(focusButton);
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
        orderButton.Margin = new Thickness(0, 0, 6, 0);
        orderButton.Click += (_, _) => { RenderOrderBadges(); };
        left.Children.Add(orderButton);
        bleedButton.Click += (_, _) => RenderGuidesOverlay();
        bleedButton.Margin = new Thickness(0, 0, 6, 0);
        left.Children.Add(bleedButton);
        safeButton.Click += (_, _) => RenderGuidesOverlay();
        safeButton.Margin = new Thickness(0, 0, 6, 0);
        left.Children.Add(safeButton);
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
            var target = chapters.FirstOrDefault(c => c.Id == KeyValueStore.Get("workspace:chapter:" + ProjectId)) ?? chapters.FirstOrDefault(c => c.Id == chapterId) ?? chapters[0];
            // 激活不是章节切换：离开分镜区时 MainWindow 已运行过离开确认，这里必须
            // 先赋 chapterId 再拨选择器，让 SelectionChanged 的确认/加载旁路保持
            // 静默，由 Activate 自己恰好加载一次（旧顺序在用户拒绝切换时仍会覆盖
            // 拒绝加载目标章节，在同意时则重复加载两遍）。
            chapterId = target.Id;
            SelectChapter(target.Id);
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

    /// <summary>
    /// ?page= deep link (web storyboard route from routeForBlocker): locate a page that
    /// may live in another chapter. MainWindow stores the id in KeyValueStore first, so
    /// after the chapter switch LoadPagesAsync picks it up; this method only resolves the
    /// owning chapter (GET pages/{id} → chapter_id) when the page is not already loaded.
    /// ?character= (MISSING_OUTFIT_ASSIGNMENT carries the target character) additionally
    /// focuses the first VISIBLE panel of that character without an outfit, mirroring the
    /// web storyboard-editor focusCharacterId effect.
    /// </summary>
    internal async Task LocatePageAsync(string pageId, string? focusCharacterId = null)
    {
        if (lifetime.IsCancellationRequested) return;
        // Queue before any early return so the focus survives "already on that page".
        pendingOutfitCharacterId = string.IsNullOrEmpty(focusCharacterId) ? null : focusCharacterId;
        if (pages.FirstOrDefault(p => p.Id == pageId) is { } current && currentPage?.Id == pageId)
        {
            ApplyOutfitFocus();
            return;
        }
        if (pages.Any(p => p.Id == pageId))
        {
            await LoadPagesAsync();
            return;
        }
        try
        {
            var row = await Api.SendAsync($"pages/{pageId}", cancellation: lifetime.Token);
            if (lifetime.IsCancellationRequested) return;
            var owner = row.Text("chapter_id");
            if (owner.Length == 0 || owner == chapterId) { await LoadPagesAsync(); return; }
            if (!await ConfirmLeaveAsync()) return;
            // Assign before moving the selector: SelectionChanged must not double-load
            // (Activate uses the same silence trick).
            chapterId = owner;
            SelectChapter(owner);
            await LoadPagesAsync();
        }
        catch (OperationCanceledException) { }
        catch (Exception error) when (error is not OperationCanceledException)
        {
            inspector.Children.Add(Kit.Caption($"深链页面定位失败：{error.Message}"));
        }
    }

    // ?character=&edit=outfit deep link target: web selects the first panel where the
    // character is VISIBLE (character_presence, with the characters array fallback)
    // and has no outfit bound, then points the user at the outfit picker. The pending
    // id is consumed once; SelectPageAsync applies it after the located page renders.
    private string? pendingOutfitCharacterId;

    private void ApplyOutfitFocus()
    {
        if (pendingOutfitCharacterId is not { } characterId) return;
        foreach (var row in storyboard.Array("panels"))
        {
            var presence = row.Element("character_presence");
            var visible = presence.ValueKind == JsonValueKind.Object
                && presence.EnumerateObject().Any(p => p.Name == characterId
                    && p.Value.ValueKind == JsonValueKind.String && p.Value.GetString() == "VISIBLE")
                || row.Array("characters").Any(c => c.ValueKind == JsonValueKind.String && c.GetString() == characterId);
            if (!visible) continue;
            var outfits = row.Element("outfits");
            if (outfits.ValueKind == JsonValueKind.Object
                && outfits.EnumerateObject().Any(p => p.Name == characterId && p.Value.ToString().Length > 0))
                continue;
            pendingOutfitCharacterId = null;
            if (panels.FirstOrDefault(p => p.Id == row.Text("id")) is { } target)
            {
                SelectPanel(target);
                UpdateStatus("已定位到缺少服装的出镜格，请在人物下方选择服装并保存本格分镜");
            }
            return;
        }
    }

    private async Task LoadPagesAsync()
    {
        // #429-3: 页列表读取序号（ScriptView.scriptLoadVersion / SelectPageAsync 的
        // pageLoadVersion 同款）：pageLoadVersion 只覆盖整页分镜读取，本方法此前没有
        // 守卫——A 章的 pages 响应慢于 B 切换落地时，旧响应会把 pages/画布翻回 A 而
        // 选择器仍显示 B，用户在错误的章节视图上编辑。非最新请求的响应整份丢弃。
        var requestVersion = ++pagesLoadVersion;
        try
        {
            var rows = await Api.SendAsync($"chapters/{chapterId}/pages", cancellation: lifetime.Token);
            if (requestVersion != pagesLoadVersion || lifetime.Token.IsCancellationRequested) return;
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
            // 迟到的失败同属已被取代的请求：不得把较新请求已落下的页面列表盖成错误卡
            if (requestVersion != pagesLoadVersion) return;
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

    private async Task SelectPageAsync(PageItem item, bool preserveDrafts = false)
    {
        var previous = currentPage;
        // 单调请求代号（与 ScriptView 的 scriptLoadVersion 同一模式）：快速连点
        // 两页时，迟到的旧响应既不得渲染旧分镜，也不得改写 currentPage 与页号记忆。
        var requestVersion = ++pageLoadVersion;
        // preserveDrafts（叙事保存后的同页重载）：几何草稿、撤销栈、选中态先快照，
        // 新服务端数据落地后叠回去（对齐 web 的 invalidateQueries refetch + drafts
        // overlay：叙事 CRUD 会升 panel.version 与页栅栏，锚点必须刷新，但画布上
        // 未保存的几何不允许被这次重载吞掉）。
        var panelRectDrafts = preserveDrafts ? panels.ToDictionary(p => p.Id, p => p.Rect) : null;
        var bubbleDraftSnapshot = preserveDrafts ? bubbles.ToDictionary(b => b.Id, b => (b.Rect, b.Moved)) : null;
        var selectedPanelId = preserveDrafts ? selected?.Id : null;
        var selectedBubbleId = preserveDrafts ? selectedBubble?.Id : null;
        try
        {
            // 迟到的旧响应先用局部变量承接，守卫通过后再写字段：无条件赋值会在
            // 快速连点两页时把旧页分镜短暂污染进字段（P3-6 同类竞态）。
            var fresh = await Api.SendAsync($"pages/{item.Id}/storyboard", cancellation: lifetime.Token);
            if (requestVersion != pageLoadVersion || lifetime.Token.IsCancellationRequested) return;
            storyboard = fresh;
            // 拉取成功后才切换页签状态与记住的页号；页版本以服务端为准刷新，
            // 冲突恢复（放弃并重新加载）后的重试才有新锚点，否则永远撞同一个 409
            var serverVersion = storyboard.Element("page").Number("storyboard_version");
            currentPage = serverVersion > 0 && serverVersion != item.StoryboardVersion ? item with { StoryboardVersion = serverVersion } : item;
            KeyValueStore.Set("storyboard:page:" + ProjectId, item.Id);
            var canvasInfo = storyboard.Element("page").Element("canvas");
            // 对齐 web：canvasKnown = Boolean(page.canvas)（只看字段存在）；
            // 尺寸回落 defaultCanvas 的 182×257 / 出血 3mm / 安全 5mm。
            canvasKnown = canvasInfo.ValueKind == JsonValueKind.Object;
            double widthMm = canvasInfo.Decimal("width_mm") is var w && w > 0 ? w : 182;
            double heightMm = canvasInfo.Decimal("height_mm") is var h && h > 0 ? h : 257;
            canvasWidthMm = widthMm;
            canvasHeightMm = heightMm;
            bleedMm = canvasInfo.Decimal("bleed_mm", 3);
            safeMm = canvasInfo.Decimal("safe_mm", 5);
            bleedButton.IsEnabled = canvasKnown;
            safeButton.IsEnabled = canvasKnown;
            var missingCanvas = "本页缺少画布尺寸字段，出血框与安全区暂不可用。";
            bleedButton.ToolTip = canvasKnown ? null : missingCanvas;
            safeButton.ToolTip = canvasKnown ? null : missingCanvas;
            pageAspect = heightMm / widthMm;
            panels = storyboard.Array("panels").Select(PanelNode.From).ToList();
            foreach (var panel in panels)
                panel.Element.MouseLeftButtonDown += (s, e) => BeginPanelDrag(s, e, panel);
            RebuildBubbles();
            if (!preserveDrafts)
            {
                history.Clear();
                geometryRequest = null;   // 新页新草稿：旧页的 request_id 不得跨页复用（对齐 web switchPage→clearGeometryDrafts）
                bubblesDeleted = false;
                dirty = false;
                // 换页即弃叙事草稿：草稿属于上一页的对白，留着会把旧台词泄漏进
                // 新页编辑器（对齐 web switchPage 的 setDialogueDrafts({})）。
                dialogueDrafts.Clear();
                newDialogue = null;
                // 选中态不跨页残留：残留的旧页 selectedBubble 会让新页上按 Delete
                // 真删旧页气泡（服务端 DELETE），inspector 也须按新页数据重渲。
                selected = null;
                selectedBubble = null;
                conflictBar.Visibility = Visibility.Collapsed;   // 新数据落地即冲突解除
                UpdateStatus("已保存");
            }
            else
            {
                foreach (var panel in panels)
                    if (panelRectDrafts!.TryGetValue(panel.Id, out var rect)) panel.Rect = rect;
                foreach (var bubble in bubbles)
                    if (bubbleDraftSnapshot!.TryGetValue(bubble.Id, out var draft)) { bubble.Rect = draft.Rect; bubble.Moved = draft.Moved; }
                selected = panels.FirstOrDefault(p => p.Id == selectedPanelId);
                selectedBubble = bubbles.FirstOrDefault(b => b.Id == selectedBubbleId);
                MarkDirty();   // 撤销栈/气泡删除/叙事草稿照旧参与脏判定（不因重载清零）
                // #371：按重载后的真实状态重估冲突条。preserve 重载已把服务器锚点
                // 换成最新（上方 currentPage 的页栅栏、重建 panels/bubbles 的
                // panel.version），重试保存即可成功——409 的前提（锚点过期）不复
                // 存在，横幅文案「分镜格已在别处更新」对新画布不再成立，保留只会
                // 把用户引向「放弃草稿并重新加载」这一破坏性出口；未保存草稿的存
                // 在仍由脏判定/离开确认表达。读取失败走下面的 catch，storyboard
                // 字段未换新锚点，横幅在那里保持原状。
                conflictBar.Visibility = Visibility.Collapsed;
            }
            RenderPageBar();
            RenderCanvas();
            RenderInspector();
            UpdatePageSize();
            ApplyOutfitFocus();
        }
         catch (OperationCanceledException) { }
        catch (Exception error) when (error is not OperationCanceledException)
        {
            // 迟到的失败同样属于已被取代的请求：不得回滚较新请求已经落下的选中页签
            if (requestVersion != pageLoadVersion) return;
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
        PositionResizeHandles(selected?.Rect);
        UpdateOverlaySizes();
    }

    private void RenderCanvas()
    {
        // 事件处理器已在节点创建处挂接一次，这里只重组 Children
        page.Children.Clear();
        foreach (var panel in panels) page.Children.Add(panel.Element);
        foreach (var bubble in bubbles) page.Children.Add(bubble.Element);
        RenderOrderBadges();
        RenderGuidesOverlay();
        RenderResizeHandles();
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
            // 选中格的手柄跟随拖动实时移动（对齐 web 手势期的 paintHandles）
            if (selected != null) PositionResizeHandles(selected.Rect);
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

    // ============ 面板缩放手柄（对齐 web transform-handles + geometry.applyResize）============

    // web 面板手柄全集：四角 + 四边；只在「恰好单选一个矩形格」时挂载。
    private static readonly string[] PanelHandleNames = ["nw", "n", "ne", "e", "se", "s", "sw", "w"];

    private void RenderResizeHandles()
    {
        foreach (var (_, element) in resizeHandles) page.Children.Remove(element);
        resizeHandles.Clear();
        if (selected is not { } panel || selectedBubble != null || panel.IsPolygon) return;
        foreach (var name in PanelHandleNames)
        {
            var handle = new Border
            {
                Width = 14,
                Height = 14,
                Background = (Brush)Application.Current.FindResource("Accent"),
                BorderBrush = Brushes.White,
                BorderThickness = new Thickness(2),
                CornerRadius = new CornerRadius(name.Length == 2 ? 3 : 999),
                Cursor = HandleCursor(name),
                Tag = "resize-handle:" + name,
            };
            var captured = name;
            handle.MouseLeftButtonDown += (s, e) => BeginHandleDrag(s, e, panel, captured);
            Panel.SetZIndex(handle, 50);
            page.Children.Add(handle);
            resizeHandles.Add((name, handle));
        }
        PositionResizeHandles(panel.Rect);
    }

    private static Cursor HandleCursor(string name) => name switch
    {
        "n" or "s" => Cursors.SizeNS,
        "e" or "w" => Cursors.SizeWE,
        "ne" or "sw" => Cursors.SizeNESW,
        _ => Cursors.SizeNWSE,
    };

    private void PositionResizeHandles(Rect? rect)
    {
        if (rect is not { } value) return;
        foreach (var (name, element) in resizeHandles)
        {
            var anchor = HandleAnchor(value, name);
            Canvas.SetLeft(element, anchor.X * page.Width - element.Width / 2);
            Canvas.SetTop(element, anchor.Y * page.Height - element.Height / 2);
        }
    }

    // 手柄锚点（对齐 web anchorPosition）：w 取左边、e 取右边，否则水平中点；
    // n 取上边、s 取下边，否则垂直中点。边手柄落在对边中点上。
    private static Point HandleAnchor(Rect rect, string name) => new(
        name.Contains('w') ? rect.X : name.Contains('e') ? rect.Right : rect.X + rect.Width / 2,
        name.Contains('n') ? rect.Y : name.Contains('s') ? rect.Bottom : rect.Y + rect.Height / 2);

    private void BeginHandleDrag(object sender, MouseButtonEventArgs e, PanelNode panel, string handle)
    {
        if (e.ChangedButton != MouseButton.Left) return;
        // 手柄只在 selected==panel 时挂载（RenderResizeHandles 的前置条件），
        // 这里不得再走 SelectPanel：它会重建手柄元素，把正要捕获鼠标的 sender
        // 从视觉树摘下来，捕获随即失效。
        if (selected != panel) SelectPanel(panel);
        var origin = panel.Rect;
        var element = (FrameworkElement)sender;
        element.CaptureMouse();
        MouseEventHandler moved = (_, me) =>
        {
            var next = ComputeResized(origin, handle, ToNormalized(me.GetPosition(page)), panel, Keyboard.Modifiers);
            panel.SetRectDirect(next, page);
            PositionResizeHandles(next);
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
            CommitResize(panel, origin);
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

    // web resize 手势的几何核心：applyResize（最小尺寸 MinSize=0.03 / Shift 锁
    // 宽高比 / Alt 从中心对称缩放）+ 可选吸附（阈值 6px 折算归一化，对齐
    // SNAP_THRESHOLD_PX；目标线 = 页参考线 + 其余格的边/中线）。
    // 修饰键由调用方传入：生产路径读 Keyboard.Modifiers，测试访问器显式给
    // ModifierKeys.None——headless 检查绝不能读取宿主机物理键盘状态
    // （曾因残留的 Shift 按下状态让 se 缩放走进宽高比锁分支随机飘红）。
    private Rect ComputeResized(Rect origin, string handle, Point pointer, PanelNode self, ModifierKeys modifiers)
    {
        var resized = ApplyResize(origin, handle, pointer,
            modifiers == ModifierKeys.Shift, modifiers == ModifierKeys.Alt, MinSize);
        if (snapButton.IsChecked != true) return resized;
        var threshold = 6 / Math.Max(1, page.Width);
        var xTargets = new List<double>(PageGuides);
        var yTargets = new List<double>(PageGuides);
        foreach (var other in panels.Where(p => !ReferenceEquals(p, self)))
        {
            xTargets.Add(other.Rect.X); xTargets.Add(other.Rect.X + other.Rect.Width / 2); xTargets.Add(other.Rect.Right);
            yTargets.Add(other.Rect.Y); yTargets.Add(other.Rect.Y + other.Rect.Height / 2); yTargets.Add(other.Rect.Bottom);
        }
        var bestX = BestSnapDelta([resized.X, resized.X + resized.Width / 2, resized.Right], xTargets, resized.X);
        var bestY = BestSnapDelta([resized.Y, resized.Y + resized.Height / 2, resized.Bottom], yTargets, resized.Y);
        ClearGuides();
        // 吸附闸门必须取绝对值（web geometry.snapRect 用 Math.abs(delta) > threshold 跳过）：
        // 带符号比较会让所有负向 delta（向左/上吸附）无条件通过，把格子拽到远处参考线。
        if (Math.Abs(bestX.Delta) <= threshold) ShowGuide(bestX.Guide, true);
        if (Math.Abs(bestY.Delta) <= threshold) ShowGuide(bestY.Guide, false);
        var x = Math.Abs(bestX.Delta) <= threshold ? Math.Clamp(resized.X + bestX.Delta, 0, 1 - resized.Width) : resized.X;
        var y = Math.Abs(bestY.Delta) <= threshold ? Math.Clamp(resized.Y + bestY.Delta, 0, 1 - resized.Height) : resized.Y;
        return new Rect(x, y, resized.Width, resized.Height);
    }

    private void CommitResize(PanelNode panel, Rect origin)
    {
        var final = panel.Rect;
        if (final == origin) return;
        history.Push(new GeometryCommand("缩放格子", [new PanelChange(panel.Id, origin, final)]));
        MarkDirty();
    }

    // web geometry.applyResize 的直译：指针位置改写命中的边，先最小尺寸约束、
    // 再整体 clamp 进页内；指针越过对边时宽高为 0，由最小尺寸分支兜底。
    internal static Rect ApplyResize(Rect origin, string handle, Point pointer, bool ratioLock, bool fromCenter, double minSize)
    {
        var east = handle.Contains('e');
        var west = handle.Contains('w');
        var south = handle.Contains('s');
        var north = handle.Contains('n');
        double left = origin.X, top = origin.Y, right = origin.Right, bottom = origin.Bottom;
        if (east) right = Clamp01(pointer.X);
        if (west) left = Clamp01(pointer.X);
        if (south) bottom = Clamp01(pointer.Y);
        if (north) top = Clamp01(pointer.Y);
        if (fromCenter)
        {
            if (east || west)
            {
                var center = origin.X + origin.Width / 2;
                var offset = Math.Min(Math.Abs(pointer.X - center), Math.Min(center, 1 - center));
                left = center - offset;
                right = center + offset;
            }
            if (south || north)
            {
                var center = origin.Y + origin.Height / 2;
                var offset = Math.Min(Math.Abs(pointer.Y - center), Math.Min(center, 1 - center));
                top = center - offset;
                bottom = center + offset;
            }
        }
        var width = Math.Max(right - left, 0);
        var height = Math.Max(bottom - top, 0);
        if (ratioLock && origin.Width > 0 && origin.Height > 0)
        {
            var ratio = origin.Width / origin.Height;
            if (width / height > ratio) width = height * ratio;
            else height = width / ratio;
            if (east) right = left + width;
            else left = right - width;
            if (south) bottom = top + height;
            else top = bottom - height;
        }
        if (width < minSize)
        {
            if (west && !east) left = Math.Max(right - minSize, 0);
            else right = Math.Min(left + minSize, 1);
            width = Math.Min(minSize, 1);
        }
        if (height < minSize)
        {
            if (north && !south) top = Math.Max(bottom - minSize, 0);
            else bottom = Math.Min(top + minSize, 1);
            height = Math.Min(minSize, 1);
        }
        return ClampRect(new Rect(Math.Min(left, right), Math.Min(top, bottom), width, height), minSize);
    }

    private static double Clamp01(double value) => Math.Min(1, Math.Max(0, value));

    // 缩放吸附的最近参考线（与 PanelGesture.BestDelta 同一算法；嵌套类的
    // private 成员对包含类不可见，这里独立成一份）
    private static (double Delta, double Guide) BestSnapDelta(double[] edges, List<double> targets, double current)
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

    // web geometry.clampRect 的直译（resize 专用；移动路径沿用 PanelGesture 的 SnapRect）
    private static Rect ClampRect(Rect rect, double minSize)
    {
        var width = Math.Min(Math.Max(rect.Width, minSize), 1);
        var height = Math.Min(Math.Max(rect.Height, minSize), 1);
        return new Rect(
            Clamp01(Math.Min(rect.X, 1 - width)),
            Clamp01(Math.Min(rect.Y, 1 - height)),
            width, height);
    }

    // ============ 出血/安全区叠加（对齐 web GuidesOverlay + defaultCanvas）============

    private void RenderGuidesOverlay()
    {
        if (bleedRing != null) { page.Children.Remove(bleedRing); bleedRing = null; }
        if (safeFrame != null) { page.Children.Remove(safeFrame); safeFrame = null; }
        if (canvasKnown && canvasWidthMm > 0 && canvasHeightMm > 0)
        {
            if (bleedButton.IsChecked == true && bleedMm > 0)
            {
                bleedRing = new Border
                {
                    BorderBrush = new SolidColorBrush(Color.FromRgb(0x9A, 0x65, 0x34)),
                    Background = new SolidColorBrush(Color.FromArgb(0x0D, 0x9A, 0x65, 0x34)),
                    BorderThickness = new Thickness(1),
                    IsHitTestVisible = false,   // 参考线不挡格子点击
                    Tag = "bleed-ring",
                };
                Panel.SetZIndex(bleedRing, 28);
                page.Children.Add(bleedRing);
            }
            if (safeButton.IsChecked == true && safeMm > 0)
            {
                safeFrame = new Border
                {
                    BorderBrush = new SolidColorBrush(Color.FromRgb(0x2B, 0xA6, 0xA0)),
                    Background = new SolidColorBrush(Color.FromArgb(0x0A, 0x2B, 0xA6, 0xA0)),
                    BorderThickness = new Thickness(1),
                    IsHitTestVisible = false,
                    Tag = "safe-rect",
                };
                Panel.SetZIndex(safeFrame, 28);
                page.Children.Add(safeFrame);
            }
        }
        UpdateOverlaySizes();
    }

    // 出血框向外扩张 bleed_mm/页宽 的比例（web insetStyle(invert:true)）；
    // 安全区从页边内缩 safe_mm 比例（insetStyle(invert:false)）。
    private void UpdateOverlaySizes()
    {
        if (bleedRing is { } ring)
        {
            var x = bleedMm / canvasWidthMm;
            var y = bleedMm / canvasHeightMm;
            Canvas.SetLeft(ring, -x * page.Width);
            Canvas.SetTop(ring, -y * page.Height);
            ring.Width = page.Width * (1 + 2 * x);
            ring.Height = page.Height * (1 + 2 * y);
        }
        if (safeFrame is { } frame)
        {
            var x = safeMm / canvasWidthMm;
            var y = safeMm / canvasHeightMm;
            Canvas.SetLeft(frame, x * page.Width);
            Canvas.SetTop(frame, y * page.Height);
            frame.Width = page.Width * (1 - 2 * x);
            frame.Height = page.Height * (1 - 2 * y);
        }
    }

    private void SelectPanel(PanelNode? panel)
    {
        foreach (var candidate in panels) candidate.SetSelected(candidate == panel);
        foreach (var bubble in bubbles) bubble.SetSelected(false);
        selected = panel;
        selectedBubble = null;
        FocusCanvas();   // 选中即聚焦画布：键盘快捷键（Tab/方向键/Delete）随之可用
        RenderResizeHandles();
        RenderInspector();
    }

    private void SelectBubble(BubbleNode bubble)
    {
        foreach (var candidate in panels) candidate.SetSelected(candidate.Id == bubble.PanelId);
        foreach (var bubbleNode in bubbles) bubbleNode.SetSelected(bubbleNode == bubble);
        selected = panels.FirstOrDefault(p => p.Id == bubble.PanelId);
        selectedBubble = bubble;
        FocusCanvas();
        // 气泡选中不挂面板缩放手柄（对齐 web：气泡选中时 TransformHandles 换成
        // 气泡手柄；桌面未实现气泡缩放，先清空面板手柄）
        RenderResizeHandles();
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

    // 叙事草稿脏判定（对齐 web dirty 公式的 narrative 部分）：任何存活对白的
    // 在册草稿（web：draft 存在即脏，即使内容被改回）、或新增气泡草稿打开。
    private bool NarrativeDirty =>
        newDialogue != null || dialogueDrafts.Keys.Any(id => bubbles.Any(bubble => bubble.Id == id));

    private void MarkDirty()
    {
        // 脏判定必须覆盖撤销栈之外的真实草稿：气泡删除虽已即时提交服务端，
        // 但整页几何快照（面板 bounds + 气泡集合）已偏离最近一次保存/加载的
        // 版本，在下一次整页保存或页面重载完成前不得回到「已保存」；
        // 叙事草稿参与离开保护，但不点亮「保存本页」（web canSave 只看撤销栈）。
        dirty = history.CanUndo || bubblesDeleted || NarrativeDirty;
        saveButton.IsEnabled = (history.CanUndo || bubblesDeleted) && !saving;
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
        if (selected is not { } panel)
        {
            // 所属格已不存在（别处删除）但气泡残留选中：只保留最小只读卡兜底
            if (selectedBubble is { } orphan)
            {
                var fallback = new StackPanel();
                fallback.Children.Add(new TextBlock { Text = $"气泡 · {orphan.TargetText}", FontWeight = FontWeights.Bold, TextWrapping = TextWrapping.Wrap });
                var remove = Kit.Act("删除气泡", async (_, _) => await DeleteBubbleAsync(orphan), "CompactDanger");
                remove.Margin = new Thickness(0, 8, 0, 0);
                fallback.Children.Add(remove);
                inspector.Children.Add(new Border { Style = (Style)Application.Current.FindResource("Card"), Padding = new Thickness(14), Child = fallback });
            }
            return;
        }
        var card = new StackPanel { Margin = new Thickness(0, 10, 0, 0) };
        card.Children.Add(new TextBlock { Text = $"PANEL {panel.ReadingOrder:D2} · 分镜导演台", Style = (Style)Application.Current.FindResource("SectionIndex") });
        card.Children.Add(new TextBlock
        {
            Text = $"X {panel.Rect.X:P1} · Y {panel.Rect.Y:P1} · 宽 {panel.Rect.Width:P1} · 高 {panel.Rect.Height:P1}",
            FontFamily = (FontFamily)Application.Current.FindResource("Mono"), FontSize = 12, Margin = new Thickness(0, 6, 0, 4),
        });
        card.Children.Add(new TextBlock { Text = panel.ScriptAction.Length > 0 ? panel.ScriptAction : "待补充", TextWrapping = TextWrapping.Wrap, FontWeight = FontWeights.Bold });
        // 气泡选中时检查器仍钉在其所属格（对齐 web inspectorPanel），对应气泡卡高亮
        BuildDialogueEditor(card, panel);
        var edit = Kit.Act("编辑本格", async (_, _) => await EditPanel(panel), "Compact");
        edit.Margin = new Thickness(0, 10, 0, 0);
        card.Children.Add(edit);
        inspector.Children.Add(new Border { Style = (Style)Application.Current.FindResource("Card"), Padding = new Thickness(14), Child = card });
    }

    // 对白编辑区（对齐 web panel-inspector 的 LETTERING 段 + dialogue-card）：
    // 每条对白一张可编辑卡（文字/说话人/排字方向/锁定），底部可开一张新增卡。
    private void BuildDialogueEditor(StackPanel card, PanelNode panel)
    {
        card.Children.Add(new TextBlock
        {
            Text = $"LETTERING · 文字与气泡 · {panel.Dialogues.Count} 个气泡",
            Style = (Style)Application.Current.FindResource("SectionIndex"), Margin = new Thickness(0, 10, 0, 0),
        });
        var addNew = Kit.Act("新增气泡", (_, _) =>
        {
            // 对齐 web newDialogue 种子：空文本 / 无说话人 / 竖排 / 锁定文字
            newDialogue = new DialogueDraft("", null, "vertical", true);
            MarkDirty();
            RenderInspector();
        }, "Compact");
        addNew.IsEnabled = newDialogue == null;   // 同时只开一张新增卡（web 同款禁用）
        addNew.Margin = new Thickness(0, 8, 0, 0);
        card.Children.Add(addNew);
        if (newDialogue != null) card.Children.Add(BuildDialogueCard(panel, null, panel.Dialogues.Count + 1));
        var index = 0;
        foreach (var dialogue in panel.Dialogues)
            card.Children.Add(BuildDialogueCard(panel, dialogue, ++index));
    }

    private Border BuildDialogueCard(PanelNode panel, JsonElement? dialogue, int index)
    {
        var dialogueId = dialogue?.Text("id") ?? "";
        var isNew = dialogueId.Length == 0;
        DialogueDraft Seed() => dialogue is { ValueKind: JsonValueKind.Object } row ? DialogueDraft.From(row)
            : newDialogue ?? new DialogueDraft("", null, "vertical", true);
        var draft = !isNew && dialogueDrafts.TryGetValue(dialogueId, out var stored) ? stored : Seed();
        var highlighted = !isNew && selectedBubble?.Id == dialogueId;

        var body = new StackPanel();
        var head = new StackPanel { Orientation = Orientation.Horizontal };
        head.Children.Add(new TextBlock
        {
            Text = isNew ? $"新增文字气球 · BALLOON {index:D2}" : $"气泡 {index:D2} · BALLOON {index:D2}",
            FontWeight = FontWeights.Bold, VerticalAlignment = VerticalAlignment.Center,
        });
        var count = new TextBlock
        {
            Text = $"{draft.TargetText.Trim().Length} 字",
            Style = (Style)Application.Current.FindResource("Micro"), Margin = new Thickness(10, 0, 0, 0), VerticalAlignment = VerticalAlignment.Center,
        };
        head.Children.Add(count);
        if (!isNew)
        {
            var remove = Kit.Act("删除", async (_, _) =>
            {
                if (bubbles.FirstOrDefault(b => b.Id == dialogueId) is { } bubble) await DeleteBubbleAsync(bubble);
            }, "CompactDanger");
            remove.Margin = new Thickness(10, 0, 0, 0);
            head.Children.Add(remove);
        }
        body.Children.Add(head);

        var textLabel = Kit.FieldLabel("文字内容");
        textLabel.Margin = new Thickness(0, 8, 0, 4);
        body.Children.Add(textLabel);
        var text = new TextBox { AcceptsReturn = true, TextWrapping = TextWrapping.Wrap, MinHeight = 64, Text = draft.TargetText };
        System.Windows.Automation.AutomationProperties.SetName(text, isNew ? "新增气泡文字" : $"气泡 {index} 文字");
        body.Children.Add(text);

        var speakerLabel = Kit.FieldLabel("说话人");
        speakerLabel.Margin = new Thickness(0, 8, 0, 4);
        body.Children.Add(speakerLabel);
        var speaker = new ComboBox();
        System.Windows.Automation.AutomationProperties.SetName(speaker, isNew ? "新增气泡说话人" : $"气泡 {index} 说话人");
        speaker.Items.Add(new ComboBoxItem { Tag = "", Content = "旁白 / 无说话人" });
        foreach (var character in characters)
            speaker.Items.Add(new ComboBoxItem { Tag = character.Id, Content = character.PrimaryName });
        SelectCombo(speaker, draft.SpeakerCharacterId ?? "");
        body.Children.Add(speaker);

        // 排字方向（对齐 web dialogue-card 的竖排/横排切换对）
        var directionRow = new StackPanel { Orientation = Orientation.Horizontal, Margin = new Thickness(0, 8, 0, 0) };
        var vertical = new ToggleButton { Content = "竖排", Style = (Style)Application.Current.FindResource("Pill"), IsChecked = draft.TextDirection != "horizontal", MinWidth = 64 };
        var horizontal = new ToggleButton { Content = "横排", Style = (Style)Application.Current.FindResource("Pill"), IsChecked = draft.TextDirection == "horizontal", MinWidth = 64, Margin = new Thickness(6, 0, 0, 0) };
        directionRow.Children.Add(vertical);
        directionRow.Children.Add(horizontal);
        body.Children.Add(directionRow);

        var locked = new CheckBox { Content = "锁定文字（生图时禁止改写）", IsChecked = draft.RewriteForbidden, Margin = new Thickness(0, 8, 0, 0) };
        body.Children.Add(locked);

        var save = new Button
        {
            Content = isNew ? "新增气泡" : "保存更改",
            Style = (Style)Application.Current.FindResource("InkButton"),
            MinWidth = 110, Margin = new Thickness(0, 10, 0, 0),
            // busy 期间不靠初始值禁用（重载会重建按钮，saving/narrativeBusy 的
            // 复位时点晚于重建就会卡在禁用态）；点击处理里有 narrativeBusy/saving
            // 双守卫，几何 PUT 或叙事保存在途时提交不会重复发出。
            IsEnabled = !string.IsNullOrWhiteSpace(text.Text),
        };
        body.Children.Add(save);

        // 草稿写回（对齐 web onDialogueDraftChange/onNewDialogueChange）：输入即入册，
        // 选中态变化或整页重载后仍能从册中复活；任何在册草稿都点亮脏判定。
        DialogueDraft Current() => !isNew && dialogueDrafts.TryGetValue(dialogueId, out var value) ? value
            : isNew && newDialogue != null ? newDialogue
            : Seed();
        void Store(DialogueDraft next)
        {
            if (isNew) newDialogue = next;
            else dialogueDrafts[dialogueId] = next;
            count.Text = $"{next.TargetText.Trim().Length} 字";
            save.IsEnabled = !string.IsNullOrWhiteSpace(next.TargetText);
            MarkDirty();
        }
        text.TextChanged += (_, _) => Store(Current() with { TargetText = text.Text });
        // 事件挂接种子之后：程序化选中不算用户改动
        speaker.SelectionChanged += (_, _) => Store(Current() with
        {
            SpeakerCharacterId = (speaker.SelectedItem as ComboBoxItem)?.Tag as string is { Length: > 0 } id ? id : null,
        });
        vertical.Click += (_, _) => { vertical.IsChecked = true; horizontal.IsChecked = false; Store(Current() with { TextDirection = "vertical" }); };
        horizontal.Click += (_, _) => { horizontal.IsChecked = true; vertical.IsChecked = false; Store(Current() with { TextDirection = "horizontal" }); };
        locked.Checked += (_, _) => Store(Current() with { RewriteForbidden = true });
        locked.Unchecked += (_, _) => Store(Current() with { RewriteForbidden = false });

        if (isNew)
            save.Click += async (_, _) => await AddDialogueAsync(panel, Current());
        else
            save.Click += async (_, _) => await SaveDialogueAsync(panel, dialogueId, Current());

        var container = new Border
        {
            Style = (Style)Application.Current.FindResource("Card"),
            Padding = new Thickness(12),
            Margin = new Thickness(0, 8, 0, 0),
            Child = body,
            Tag = highlighted ? "dialogue-card:selected" : "dialogue-card",
        };
        if (highlighted)
        {
            container.BorderBrush = (Brush)Application.Current.FindResource("Accent");
            container.BorderThickness = new Thickness(2);
        }
        return container;
    }

    private static void SelectCombo(ComboBox box, string tag)
    {
        foreach (var item in box.Items.OfType<ComboBoxItem>())
            if ((string?)item.Tag == tag) { box.SelectedItem = item; return; }
        if (box.Items.Count > 0) box.SelectedIndex = 0;
    }

    private async Task EditPanel(PanelNode panel)
    {
        var pageAtRequest = currentPage;
        if (pageAtRequest == null) return;
        try
        {
            var fresh = await Api.SendAsync($"pages/{pageAtRequest.Id}/storyboard", cancellation: lifetime.Token);
            var row = fresh.Array("panels").FirstOrDefault(p => p.Text("id") == panel.Id);
            if (row.ValueKind != JsonValueKind.Object) throw new InvalidOperationException("找不到该分镜格");
            // PATCH 在对话框关闭决策之前执行：409 时不关对话框、就地显示冲突与
            // 「放弃并重新加载」（对齐 web 编辑表单保持打开 + 冲突横幅的行为），
            // 用户输入不再随 DialogResult=true 一起丢失。
            var dialog = new PanelEditDialog(Host, row, characters, outfits,
                async payload =>
                {
                    try
                    {
                        await Api.SendAsync($"panels/{panel.Id}", HttpMethod.Patch, payload, cancellation: lifetime.Token);
                        return (PanelEditDialog.PanelSaveOutcome.Saved, "");
                    }
                    catch (OperationCanceledException) { return (PanelEditDialog.PanelSaveOutcome.Cancelled, ""); }
                    catch (Exception error) when (error is not OperationCanceledException)
                    {
                        return IsConflict(error)
                            ? (PanelEditDialog.PanelSaveOutcome.Conflict, "")
                            : (PanelEditDialog.PanelSaveOutcome.Failed, error.Message);
                    }
                },
                async () =>
                {
                    try
                    {
                        var snapshot = await Api.SendAsync($"pages/{pageAtRequest.Id}/storyboard", cancellation: lifetime.Token);
                        var next = snapshot.Array("panels").FirstOrDefault(p => p.Text("id") == panel.Id);
                        if (next.ValueKind != JsonValueKind.Object) return null;
                        return next;
                    }
                    catch (Exception) { return null; }
                });
            if (dialog.ShowDialog() != true) return;
            Cache.Invalidate("storyboard:" + pageAtRequest.Id, "pages:" + chapterId);
            // 保存/对话框在途期间用户可能已切页：晚到的刷新不得把画布拉回旧页。
            // preserve：本格 PATCH 后的重载不吞画布几何草稿与检查器里的对白草稿
            //（对齐 web savePanel.onSuccess → refresh 只换服务端数据）。
            if (currentPage?.Id == pageAtRequest.Id) await SelectPageAsync(pageAtRequest, preserveDrafts: true);
        }
         catch (OperationCanceledException) { }
        catch (Exception error) when (error is not OperationCanceledException)
        {
            MessageBox.Show(Host, error.Message, "保存本格未完成", MessageBoxButton.OK, MessageBoxImage.Warning);
        }
    }

    // ============ 对白叙事保存（对齐 web saveDialogue/addDialogue 的单对象 CRUD）============

    // 叙事保存/删除后的同页重载：刷新 panel.version 与页栅栏锚点（服务端在叙事
    // 写入时会升格两者），同时让几何草稿、撤销栈、选中态与叙事草稿原样存活
    //（对齐 web invalidateQueries refetch + drafts overlay）。
    private async Task ReloadPagePreservingDraftsAsync()
    {
        if (currentPage is not { } page) return;
        await SelectPageAsync(page, preserveDrafts: true);
    }

    private async Task SaveDialogueAsync(PanelNode panel, string dialogueId, DialogueDraft submitted)
    {
        var pageAtRequest = currentPage;
        if (pageAtRequest == null || narrativeBusy || saving) return;
        narrativeBusy = true;
        try
        {
            // PATCH 载荷对齐 web updateDialogue：panel_version（所属格的乐观锁）+
            // dialogue-card 的四个草稿字段全量回传。
            await Api.SendAsync($"dialogues/{dialogueId}", HttpMethod.Patch, new
            {
                panel_version = panel.Version,
                target_text = submitted.TargetText,
                speaker_character_id = submitted.SpeakerCharacterId,
                text_direction = submitted.TextDirection,
                rewrite_forbidden = submitted.RewriteForbidden,
            }, cancellation: lifetime.Token);
            // 先解除 busy 再触发重载：preserve 重载会重建检查器与保存按钮，
            // 若在 busy 中重建，按钮的初始 IsEnabled 会卡在禁用态。
            narrativeBusy = false;
            Cache.Invalidate("storyboard:" + pageAtRequest.Id, "pages:" + chapterId);
            // 在途保存期间继续敲入的文本不能被这次成功静默丢弃（对齐 web）：
            // 草稿仍等于提交内容时才摘除，否则保留新输入（编辑器保持脏）。
            if (dialogueDrafts.TryGetValue(dialogueId, out var latest) && latest == submitted)
                dialogueDrafts.Remove(dialogueId);
            conflictBar.Visibility = Visibility.Collapsed;   // 成功即解除冲突态
            UpdateStatus($"已保存 · 当前 V{pageAtRequest.StoryboardVersion + 1}");
            State.Status = "对白已保存";
            // PATCH 在途期间用户可能已切页：晚到的刷新不得把画布拉回旧页
            if (currentPage?.Id == pageAtRequest.Id) await ReloadPagePreservingDraftsAsync();
        }
        catch (OperationCanceledException) { }
        catch (Exception error) when (error is not OperationCanceledException)
        {
            UpdateStatus("保存失败");
            if (IsConflict(error))
                // 409：保留输入 + 冲突条（对齐 web 冲突横幅 + discardReload）；版本
                // 拒绝早退不落库，恢复出口是「放弃草稿并重新加载」。
                conflictBar.Visibility = Visibility.Visible;
            else
                MessageBox.Show(Host, error.Message, "保存更改未完成", MessageBoxButton.OK, MessageBoxImage.Warning);
        }
        finally { narrativeBusy = false; }
    }

    private async Task AddDialogueAsync(PanelNode panel, DialogueDraft submitted)
    {
        var pageAtRequest = currentPage;
        if (pageAtRequest == null || narrativeBusy || saving || string.IsNullOrWhiteSpace(submitted.TargetText)) return;
        narrativeBusy = true;
        try
        {
            // POST 载荷对齐 web createDialogue：panel_version 乐观锁 + 新气泡种子字段。
            await Api.SendAsync($"panels/{panel.Id}/dialogues", HttpMethod.Post, new
            {
                panel_version = panel.Version,
                target_text = submitted.TargetText,
                speaker_character_id = submitted.SpeakerCharacterId,
                text_direction = submitted.TextDirection,
                rewrite_forbidden = submitted.RewriteForbidden,
            }, cancellation: lifetime.Token);
            // 同上：先解除 busy 再触发会重建检查器的重载
            narrativeBusy = false;
            Cache.Invalidate("storyboard:" + pageAtRequest.Id, "pages:" + chapterId);
            // 提交后又有输入则保留卡片（对齐 web addDialogue.onSuccess 的比较逻辑）
            if (newDialogue == submitted) newDialogue = null;
            conflictBar.Visibility = Visibility.Collapsed;
            UpdateStatus($"已保存 · 当前 V{pageAtRequest.StoryboardVersion + 1}");
            State.Status = "气泡已新增";
            if (currentPage?.Id == pageAtRequest.Id) await ReloadPagePreservingDraftsAsync();
        }
        catch (OperationCanceledException) { }
        catch (Exception error) when (error is not OperationCanceledException)
        {
            UpdateStatus("保存失败");
            if (IsConflict(error))
                conflictBar.Visibility = Visibility.Visible;
            else
                MessageBox.Show(Host, error.Message, "新增气泡未完成", MessageBoxButton.OK, MessageBoxImage.Warning);
        }
        finally { narrativeBusy = false; }
    }

    // ============ 从本页重新计算（对齐 web onReplan → api.planChapter）============

    private async Task ReplanFromPageAsync()
    {
        if (currentPage == null || replanning) return;
        // 重算会整段重建从本页起的分镜：未保存草稿先确认再丢弃（网页端无此守卫，
        // 桌面按任务要求补上；拒绝即中止，不发出任何请求）。
        if (!await ConfirmLeaveAsync()) return;
        var pageAtRequest = currentPage;
        var chapterAtRequest = chapterId;
        replanning = true;
        replanButton.IsEnabled = false;
        replanButton.Content = "重新计算中";
        try
        {
            // 端点与语义对齐 web api.planChapter：POST /chapters/{id}/plan，
            // replace_existing=true + from_page_number=当前页号（从该页起重排，
            // 之前的页保持不变）。
            var result = await Api.SendAsync($"chapters/{chapterAtRequest}/plan", HttpMethod.Post,
                new { replace_existing = true, from_page_number = pageAtRequest.PageNumber }, cancellation: lifetime.Token);
            Cache.Invalidate("pages:" + chapterAtRequest, "storyboard:" + pageAtRequest.Id, "workbench:", "library:" + ProjectId);
            State.Status = $"已从第 {pageAtRequest.PageNumber} 页重新计算分页";
            // 重算在途期间用户可能已切页/切章：晚到的重载不得把画布拉回旧上下文
            if (chapterId == chapterAtRequest && (currentPage?.Id == pageAtRequest.Id || currentPage == null))
            {
                // plan 重建的页面带新 id：按页号找回同页的新 id 并记住，重载后落回本页
                var nextId = result.Array("pages").FirstOrDefault(p => p.Number("page_number") == pageAtRequest.PageNumber).Text("id");
                if (nextId.Length > 0) KeyValueStore.Set("storyboard:page:" + ProjectId, nextId);
                await LoadPagesAsync();
                UpdateStatus("已重新计算");
            }
        }
        catch (OperationCanceledException) { }
        catch (Exception error) when (error is not OperationCanceledException)
        {
            UpdateStatus("重新计算失败");
            MessageBox.Show(Host, error.Message, "重新计算未完成", MessageBoxButton.OK, MessageBoxImage.Warning);
        }
        finally
        {
            replanning = false;
            replanButton.IsEnabled = true;
            replanButton.Content = "从本页重新计算";
        }
    }

    // 专注模式（对齐 web focus-mode CSS：隐藏 page-strip 与 status 行；桌面没有
    // 独立 status 行——其内容在工具栏 statusLine/重算按钮里，保持可见）。
    private void ApplyFocusMode()
    {
        pageBar.Visibility = focusButton.IsChecked == true ? Visibility.Collapsed : Visibility.Visible;
    }

    private async Task DeleteBubbleAsync(BubbleNode bubble)
    {
        // 测试缝：headless 回归检查用无模态实现替换删除确认（LeaveConfirmOverride
        // 同一模式）；生产路径为 null。
        var confirmed = DeleteConfirmOverride is { } prompt
            ? prompt()
            : MessageBox.Show(Host, "删除这个文字气泡？", "删除气泡", MessageBoxButton.YesNo, MessageBoxImage.Question) == MessageBoxResult.Yes;
        if (!confirmed) return;
        var pageAtRequest = currentPage;
        if (pageAtRequest == null) return;
        try
        {
            await Api.SendOptionalAsync($"dialogues/{bubble.Id}", HttpMethod.Delete,
                new { panel_version = bubble.PanelVersion }, lifetime.Token);
            bubbles.Remove(bubble);
            page.Children.Remove(bubble.Element);
            dialogueDrafts.Remove(bubble.Id);   // 孤儿草稿不得让编辑器永久脏（对齐 web removeDialogue.onSuccess）
            Cache.Invalidate("storyboard:" + pageAtRequest.Id, "pages:" + chapterId);
            bubblesDeleted = true;   // 删除不在撤销栈里：脏状态必须显式记下这笔草稿
            MarkDirty();
            RenderInspector();
            RenderOrderBadges();
            // 删除会升格 panel.version 与页栅栏：整页重载刷新锚点，几何草稿与
            // 撤销栈随 preserve 重载存活（对齐 web removeDialogue → refetch）。
            if (currentPage?.Id == pageAtRequest.Id) await ReloadPagePreservingDraftsAsync();
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
        var pageAtRequest = currentPage;
        if (pageAtRequest == null) return;
        try
        {
            await Api.SendAsync($"pages/{pageAtRequest.Id}/layout", HttpMethod.Patch,
                new { panel_count = count, layout_mode = mode }, cancellation: lifetime.Token);
            history.Clear();
            geometryRequest = null;   // 整页重排后旧草稿指纹全部作废
            bubblesDeleted = false;
            dirty = false;
            // PATCH 在途期间用户可能已切页：晚到的刷新不得把画布拉回重建前的旧页。
            if (currentPage?.Id == pageAtRequest.Id) await SelectPageAsync(pageAtRequest);
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
        var pageAtRequest = currentPage;
        saving = true;
        saveButton.IsEnabled = false;
        saveButton.Content = "保存中";
        try
        {
            // request_id 复用（对齐 web buildGeometryPayload 的 geometryRequestRef）：
            // 网络超时后服务端可能已提交事务，重放同 request_id + 同载荷会幂等
            // 命中已持久化的命令元组并返回既有结果；撤销栈前进/回退或气泡删除
            // （删除不入栈，气泡数是草稿指纹的一部分）之后才换新 id，避免同 id
            // 撞不同内容被服务端以 409 拒绝。409 是版本拒绝（早退回滚不落库），
            // 恢复走冲突条的「放弃草稿并重新加载」，与本复用互不影响。
            var requestId = geometryRequest is { } prior
                && prior.StackIndex == history.Index
                && prior.BubbleCount == bubbles.Count
                ? prior.Id
                : Guid.NewGuid();
            geometryRequest = (requestId, history.Index, bubbles.Count);
            var payload = BuildPayload(requestId);
            var response = await Api.SendAsync($"pages/{pageAtRequest.Id}/storyboard-geometry", HttpMethod.Put, payload, cancellation: lifetime.Token);
            history.Clear();
            geometryRequest = null;   // 草稿已落库，重试身份随之作废
            bubblesDeleted = false;
            dirty = NarrativeDirty;   // 几何已落库；叙事草稿仍在册时保持脏（离开保护，web 同源）
            var version = response.Element("page").Number("storyboard_version");
            var staleCount = response.Number("candidate_count");
            UpdateStatus($"已保存 · 当前 V{version}");
            State.Status = $"分镜已保存 · V{version} · 将使 {staleCount} 个候选过期";
            Cache.Invalidate("pages:" + chapterId, "workbench:", "library:" + ProjectId);
            // PUT 在途期间用户可能已切页：晚到的刷新不得把画布拉回旧页
            // （storyboard 字段与整页重载一起跳过，避免旧页数据污染新页）。
            if (currentPage?.Id == pageAtRequest.Id)
            {
                storyboard = response;
                await SelectPageAsync(pageAtRequest);
            }
        }
        catch (OperationCanceledException) { }
        catch (Exception error) when (error is not OperationCanceledException)
        {
            UpdateStatus("保存失败");
            if (IsConflict(error))
            {
                // 409：保留草稿并亮出冲突条（对齐 web 的冲突横幅 + 放弃重载）。
                // 版本拒绝早退不落库，重试沿用同一 request_id 仍是干净的版本检查；
                // 版本锚点要等整页重载后才会更新。
                conflictBar.Visibility = Visibility.Visible;
            }
            else
            {
                MessageBox.Show(Host, error.Message + "\n\n画布草稿已保留，可重试保存或放弃草稿。", "保存未完成", MessageBoxButton.OK, MessageBoxImage.Warning);
            }
        }
        finally
        {
            saving = false;
            saveButton.Content = "保存本页";
            // 保存本页只提交几何：按钮只看几何草稿（成功路径 history 已清空）
            saveButton.IsEnabled = history.CanUndo || bubblesDeleted;
        }
    }

    // ApiClient 不透出状态码；它给所有 409 统一加的中文前缀是唯一可识别标记
    // （与 Services/ApiClient.cs 的 Conflict 分支保持同步的契约）。
    private static bool IsConflict(Exception error) =>
        error.Message.StartsWith("数据已变化或操作条件不满足", StringComparison.Ordinal);

    // 对齐 web 的 discardDraft：清空本地几何草稿（面板 bounds、气泡、撤销栈）
    // 后整页重载，用服务端的新 storyboard_version 重新起锚，打破 409 循环。
    // 叙事草稿同样丢弃（web #155：草稿基于旧版本，重载后保留只会重新武装
    // 同一个 409 循环——这个恢复按钮存在的意义就是打破它）。
    private async Task DiscardDraftAndReloadAsync()
    {
        conflictBar.Visibility = Visibility.Collapsed;
        history.Clear();
        geometryRequest = null;   // 弃稿即弃用重试身份（对齐 web discardDraft→clearGeometryDrafts）
        bubblesDeleted = false;
        dirty = false;
        dialogueDrafts.Clear();
        newDialogue = null;
        selected = null;
        selectedBubble = null;
        bubbles.Clear();
        page.Children.Clear();
        if (currentPage != null) await SelectPageAsync(currentPage);
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
        // 第二道防线：焦点在文本编辑控件（检查器 TextBox / ComboBox）时按键属于
        // 文本编辑（退格删字、方向键移光标、Tab 焦点遍历），画布不得劫持。处理器
        // 挂在 page 上已隔离画布之外的控件，这里再挡住画布子树内将来可能出现的
        // 文本输入元素（#339）。
        if (Keyboard.FocusedElement is TextBoxBase or ComboBox) return;
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
        // 测试缝：headless 检查替换模态确认；生产路径为 null
        if (LeaveConfirmOverride is { } prompt) return prompt();
        if (!dirty) return Task.FromResult(true);
        var result = MessageBox.Show(Host, "分镜画布有未保存的几何草稿，离开将丢失。确定离开吗？",
            "离开确认", MessageBoxButton.YesNo, MessageBoxImage.Question);
        if (result != MessageBoxResult.Yes) return Task.FromResult(false);
        // 同意离开即弃稿：每个 true 调用点都会继续用新状态覆盖画布，但 dirty
        // 原样保留会让同一次弃稿在再次激活/切换章节时重复弹窗。
        dirty = false;
        return Task.FromResult(true);
    }

    public override Task RefreshAsync()
    {
        // F5/刷新对齐 web 的 refetch 语义（#341）：重读服务端锚点（页栅栏、
        // panel.version、canvas 字段）但保留画布几何草稿、撤销栈与对白草稿——
        // 刷新不得变成静默弃稿。显式弃稿的出口仍是离开确认与冲突条的
        // 「放弃草稿并重新加载」。
        if (currentPage != null) _ = SelectPageAsync(currentPage, preserveDrafts: true);
        return Task.CompletedTask;
    }

    /// <summary>
    /// #428: 重连（重新连接 → ConnectAsync → OpenProjectAsync 同项目分支）在用户
    /// 拒绝弃稿时的保真激活。重连已 Dispose 旧 ApiClient，必须重绑新上下文，但
    /// 不能走 Activate 的整链重载——LoadPagesAsync→SelectPageAsync（preserveDrafts
    /// 默认 false）会清掉几何草稿、撤销栈与叙事草稿。脏判定与 ConfirmLeaveAsync
    /// 同源（dirty）；用户仍可用 F5（RefreshAsync 的 preserve 重载）刷新服务端锚点。
    /// </summary>
    internal void ActivatePreservingDrafts(WorkspaceContext context)
    {
        if (!dirty)
        {
            Activate(context);
            return;
        }
        base.Activate(context);
    }

    // ============ Test seams（headless 回归检查专用；不承载生产行为）============
    // PanelNode/BubbleNode 是私有嵌套类型，反射拿不到强类型；这些只读访问器与
    // 手势驱动器让检查直接走到与真实鼠标路径相同的 ComputeResized/CommitResize。

    internal int PanelCountForTest => panels.Count;
    internal int BubbleCountForTest => bubbles.Count;
    internal Rect PanelRectForTest(int index) => panels.ElementAtOrDefault(index)?.Rect ?? Rect.Empty;
    internal bool CanUndoForTest => history.CanUndo;
    internal bool NarrativeDirtyForTest => NarrativeDirty;
    internal int HandleCountForTest => resizeHandles.Count;
    internal string? SelectedPanelIdForTest => selected?.Id;
    internal string? SelectedBubbleIdForTest => selectedBubble?.Id;

    internal void SelectPanelForTest(int index) => SelectPanel(panels.ElementAtOrDefault(index));
    internal void SelectBubbleForTest(int index)
    {
        if (bubbles.ElementAtOrDefault(index) is { } bubble) SelectBubble(bubble);
    }

    // 驱动一次完整缩放（= BeginHandleDrag 的 moved + Finish，减去真实鼠标设备）：
    // 同一几何核心 ComputeResized + CommitResize，测试即覆盖生产逻辑。
    // 修饰键显式传入（默认 None）：headless 检查不得依赖宿主机键盘状态；
    // 需要验证 Shift/Alt 分支时由检查点名传入。
    internal void ResizeViaHandleForTest(int index, string handle, Point pointerNormalized, ModifierKeys modifiers = ModifierKeys.None)
    {
        if (panels.ElementAtOrDefault(index) is not { } panel) return;
        var origin = panel.Rect;
        var next = ComputeResized(origin, handle, pointerNormalized, panel, modifiers);
        panel.SetRectDirect(next, page);
        PositionResizeHandles(next);
        CommitResize(panel, origin);
        ClearGuides();
    }

    // ============ Geometry node model ============
    private sealed class PanelNode
    {
        public required string Id { get; init; }
        public Rect Rect { get; set; }
        public int ReadingOrder { get; init; }
        public int ZOrder { get; init; }
        public int Version { get; init; }
        // PanelRead 没有 per-panel action 字段；「动作与表演」实际存放在 actions.script_action
        public string ScriptAction { get; init; } = "";
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
                ScriptAction = row.Element("actions").Text("script_action"),
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

        // 多边形格（对齐 web isPolygonPanel）：画布只读——可选中，但不挂
        // 缩放手柄（web 同样不允许 bounds 被改写）。
        public bool IsPolygon => StoredGeometry.Text("type") == "polygon";

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
            // 同 ComputeResized：吸附闸门取绝对值，负向 delta 不得无条件通过。
            if (Math.Abs(bestX.Delta) <= SnapThreshold && Math.Abs(bestX.Delta) < Math.Abs(bestY.Delta)) view.ShowGuide(bestX.Guide, true);
            if (Math.Abs(bestY.Delta) <= SnapThreshold && Math.Abs(bestY.Delta) < Math.Abs(bestX.Delta)) view.ShowGuide(bestY.Guide, false);
            var moved = new Rect(clamped.X + (Math.Abs(bestX.Delta) <= SnapThreshold ? bestX.Delta : 0), clamped.Y + (Math.Abs(bestY.Delta) <= SnapThreshold ? bestY.Delta : 0), clamped.Width, clamped.Height);
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

    // 对白草稿（对齐 web DialogueDraft 的四个字段；保存载荷 = 草稿 + panel_version）
    private sealed record DialogueDraft(string TargetText, string? SpeakerCharacterId, string TextDirection, bool RewriteForbidden)
    {
        public static DialogueDraft From(JsonElement dialogue) => new(
            dialogue.Text("target_text"),
            dialogue.TextOrNull("speaker_character_id"),
            dialogue.Text("text_direction") == "horizontal" ? "horizontal" : "vertical",
            dialogue.Element("rewrite_forbidden").ValueKind != JsonValueKind.False);   // 缺省视为锁定
    }

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

/// <summary>Panel narrative edit dialog: shot, camera, cast presence, per-cast direction, outfits, flags.</summary>
internal sealed class PanelEditDialog : Window
{
    public object? Result { get; private set; }

    // PATCH 结果分类：Saved 关闭对话框；Conflict 保持打开并给出冲突恢复出口；
    // Failed 弹 MessageBox 后允许就地重试；Cancelled 静默（视图已被离开/停用）。
    internal enum PanelSaveOutcome { Saved, Conflict, Failed, Cancelled }

    public PanelEditDialog(Window owner, JsonElement panel, List<CharacterItem> characters, List<OutfitItem> outfits,
        Func<Dictionary<string, object?>, Task<(PanelSaveOutcome Outcome, string Message)>> save,
        Func<Task<JsonElement?>> reloadRow)
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

        // 行基线（含 version）与全部种子值都由 Seed 统一落位：初始打开与 409 后的
        // 「放弃并重新加载」走同一条重置路径，新 version 直接成为下次提交的锚点。
        var current = panel;
        var shot = new ComboBox();
        foreach (var (key, label) in Labels.ShotType) shot.Items.Add(new ComboBoxItem { Tag = key, Content = label });
        var angle = new ComboBox();
        foreach (var (key, label) in Labels.CameraAngle) angle.Items.Add(new ComboBoxItem { Tag = key, Content = label });
        var storedHeight = "";
        var height = new ComboBox();
        foreach (var (key, label) in Labels.CameraHeight) height.Items.Add(new ComboBoxItem { Tag = key, Content = label });
        var heightTouched = false;
        var storedActions = new Dictionary<string, string>();
        var storedScriptAction = "";
        var scriptAction = new TextBox { AcceptsReturn = true, MinHeight = 60 };
        var background = new TextBox { AcceptsReturn = true, MinHeight = 48 };
        var props = new TextBox();
        var soundEffects = new TextBox();

        form.Children.Add(Label("景别"));
        form.Children.Add(shot);
        form.Children.Add(Label("镜头角度"));
        form.Children.Add(angle);
        form.Children.Add(Label("机位高度"));
        form.Children.Add(height);
        form.Children.Add(Label("动作与表演"));
        form.Children.Add(scriptAction);
        form.Children.Add(Label("背景"));
        form.Children.Add(background);
        form.Children.Add(Label("场景道具（用逗号分隔）"));
        form.Children.Add(props);
        form.Children.Add(Label("拟声词（用逗号分隔）"));
        form.Children.Add(soundEffects);
        form.Children.Add(Label("人物状态"));
        form.Children.Add(Kit.Caption("只有“实际出镜”会要求人物与服装参考；画外音和被提及人物不会阻塞生图。"));
        // 服务端字段是 character_presence（PanelRead），旧代码读的 presence 不存在，
        // 导致每次打开都全员回落 NONE、保存时把既有出场状态清空。「NONE」只是
        // 客户端语义（服务端枚举没有该值），保存时全量回传非 NONE 项，
        // 未改动的保存即等价于原快照。
        var storedPresence = default(JsonElement);
        var presence = new Dictionary<string, ComboBox>();
        var expressionBoxes = new Dictionary<string, TextBox>();
        var outfitBoxes = new Dictionary<string, ComboBox>();
        var directionHost = new StackPanel();
        var storedExpressions = new Dictionary<string, string>();
        var storedOutfits = new Dictionary<string, string>();
        foreach (var character in characters)
        {
            var row = new StackPanel { Orientation = Orientation.Horizontal, Margin = new Thickness(0, 4, 0, 4) };
            row.Children.Add(new TextBlock { Text = character.PrimaryName, Width = 100, VerticalAlignment = VerticalAlignment.Center });
            var box = new ComboBox { Width = 180 };
            foreach (var (key, label) in Labels.CharacterPresence)
                box.Items.Add(new ComboBoxItem { Tag = key, Content = label });
            presence[character.Id] = box;
            box.SelectionChanged += (_, _) => RebuildDirectionRows();
            row.Children.Add(box);
            form.Children.Add(row);
        }
        form.Children.Add(directionHost);
        var bleed = new CheckBox { Content = "出血格", Margin = new Thickness(0, 8, 0, 0) };
        var borderless = new CheckBox { Content = "无边框", Margin = new Thickness(0, 0, 0, 12) };
        form.Children.Add(bleed);
        form.Children.Add(borderless);

        var error = new TextBlock { Foreground = (Brush)Application.Current.FindResource("Danger"), TextWrapping = TextWrapping.Wrap, VerticalAlignment = VerticalAlignment.Center };
        // 409 冲突专属出口（对齐 web 的冲突横幅 + discardReload）：重拉该格最新
        // 数据，以新 version 重置对话框基线（重新 From 种子值），用户可基于新
        // 基线就地重试保存。
        var reload = new Button
        {
            Content = "放弃并重新加载",
            Style = (Style)Application.Current.FindResource("InkButton"),
            Visibility = Visibility.Collapsed,
            Margin = new Thickness(12, 0, 0, 0),
        };
        reload.Click += async (_, _) =>
        {
            error.Text = "";
            reload.IsEnabled = false;
            var fresh = await reloadRow();
            if (!IsLoaded) return;   // 对话框已关闭：迟到的结果无处安放
            reload.IsEnabled = true;
            if (fresh is not { ValueKind: JsonValueKind.Object } row)
            {
                error.Text = "重新加载失败：读取不到该分镜格的最新数据，请稍后重试。";
                return;
            }
            Seed(row);
        };
        var errorRow = new StackPanel { Orientation = Orientation.Horizontal, Margin = new Thickness(0, 6, 0, 6) };
        errorRow.Children.Add(error);
        errorRow.Children.Add(reload);
        form.Children.Add(errorRow);
        var actions = new StackPanel { Orientation = Orientation.Horizontal, HorizontalAlignment = HorizontalAlignment.Right };
        var cancel = new Button { Content = "取消", MinWidth = 96, Margin = new Thickness(0, 0, 10, 0) };
        cancel.Click += (_, _) => Close();
        var submit = new Button { Content = "保存本格分镜", MinWidth = 130, Style = (Style)Application.Current.FindResource("InkButton") };
        submit.Click += async (_, _) =>
        {
            error.Text = "";
            reload.Visibility = Visibility.Collapsed;
            submit.IsEnabled = false;
            try
            {
                var presencePayload = new Dictionary<string, string>();
                foreach (var entry in presence)
                    if ((entry.Value.SelectedItem as ComboBoxItem)?.Tag as string is { } state && state != "NONE")
                        presencePayload[entry.Key] = state;
                var payload = new Dictionary<string, object?>
                {
                    ["version"] = current.Number("version"),
                    ["shot_type"] = (shot.SelectedItem as ComboBoxItem)?.Tag,
                    ["camera_angle"] = (angle.SelectedItem as ComboBoxItem)?.Tag,
                    ["background"] = background.Text,
                    ["props"] = SplitList(props.Text),
                    ["sound_effects"] = SplitList(soundEffects.Text),
                    ["character_presence"] = presencePayload,
                    ["bleed"] = bleed.IsChecked == true,
                    ["borderless"] = borderless.IsChecked == true,
                };
                // 新增字段仅在改动时提交：拿空默认值覆盖服务端已有内容比不提交更糟。
                // 机位高度基线是映射后的 overhead；服务端旧 top_down 未被用户改动
                // 时就不提交（读侧映射保证显示不失真），改动才提交新词表键。
                if (heightTouched && (height.SelectedItem as ComboBoxItem)?.Tag as string is { Length: > 0 } heightTag && heightTag != storedHeight)
                    payload["camera_height"] = heightTag;
                if (scriptAction.Text != storedScriptAction)
                    // actions 是整包字典（含 source_text 等服务端键）：以存量打底只覆写 script_action
                    payload["actions"] = new Dictionary<string, string>(storedActions) { ["script_action"] = scriptAction.Text };
                var expressionPayload = expressionBoxes.ToDictionary(entry => entry.Key, entry => entry.Value.Text);
                if (!SameMap(expressionPayload, storedExpressions)) payload["expressions"] = expressionPayload;
                var outfitPayload = new Dictionary<string, string>();
                foreach (var entry in outfitBoxes)
                    if ((entry.Value.SelectedItem as ComboBoxItem)?.Tag as string is { Length: > 0 } outfitId)
                        outfitPayload[entry.Key] = outfitId;
                if (!SameMap(outfitPayload, storedOutfits)) payload["outfits"] = outfitPayload;
                Result = payload;
                // 关闭决策后置到 PATCH 之后：409 时保持对话框打开、用户输入保留
                var outcome = await save(payload);
                if (!IsLoaded) return;   // 对话框已随取消关闭：迟到的结果无处安放
                if (outcome.Outcome == PanelSaveOutcome.Saved) { DialogResult = true; return; }
                submit.IsEnabled = true;
                if (outcome.Outcome == PanelSaveOutcome.Conflict)
                {
                    error.Text = "分镜格已被更新，无法用旧版本保存。";
                    reload.Visibility = Visibility.Visible;
                }
                else if (outcome.Outcome == PanelSaveOutcome.Failed)
                {
                    // 非 409 维持 MessageBox 提示，对话框不关闭、可直接重试
                    MessageBox.Show(this, outcome.Message, "保存本格未完成", MessageBoxButton.OK, MessageBoxImage.Warning);
                }
            }
            catch (Exception reason)
            {
                if (!IsLoaded) return;
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

        // 行基线与全部表单种子的唯一落位点：初始打开与「放弃并重新加载」共用。
        void Seed(JsonElement row)
        {
            current = row;
            Select(shot, row.Text("shot_type"));
            Select(angle, row.Text("camera_angle"));
            // web 词表键是 overhead（顶视机位）；服务端历史值 top_down 做读侧映射，
            // 种子与提交统一走 overhead，避免旧值落不中词表而显示失真
            var rawHeight = row.Text("camera_height");
            storedHeight = rawHeight == "top_down" ? "overhead" : rawHeight;
            Select(height, storedHeight);
            heightTouched = false;
            storedActions = row.StringMap("actions");
            storedScriptAction = storedActions.TryGetValue("script_action", out var seeded) ? seeded : "";
            scriptAction.Text = storedScriptAction;
            background.Text = row.Text("background");
            props.Text = string.Join("、", row.Strings("props"));
            soundEffects.Text = string.Join("、", row.Strings("sound_effects"));
            storedPresence = row.Element("character_presence");
            storedExpressions = row.StringMap("expressions");
            storedOutfits = row.StringMap("outfits");
            // 表情/服装行按新基线重建：清掉旧输入态，改由存量种子重新落位
            expressionBoxes.Clear();
            outfitBoxes.Clear();
            foreach (var character in characters)
                Select(presence[character.Id], storedPresence.Text(character.Id, "NONE") is { Length: > 0 } state ? state : "NONE");
            RebuildDirectionRows();
            bleed.IsChecked = row.Flag("bleed");
            borderless.IsChecked = row.Flag("borderless");
            error.Text = "";
            reload.Visibility = Visibility.Collapsed;
        }

        // 初始种子落位后再挂事件：程序化选中不算用户改动，机位高度未改动就不提交；
        // 重载路径里 Seed 会在 Select 之后把 heightTouched 复位。
        Seed(panel);
        height.SelectionChanged += (_, _) => heightTouched = true;

        void RebuildDirectionRows()
        {
            // 表情/服装只属于「实际出镜」的角色（与 web 一致）：切回不出镜即随行丢弃，
            // 已输入的表情在重建时保留，不做静默清空。
            var typed = expressionBoxes.ToDictionary(entry => entry.Key, entry => entry.Value.Text);
            expressionBoxes.Clear();
            outfitBoxes.Clear();
            directionHost.Children.Clear();
            foreach (var character in characters)
            {
                if ((presence[character.Id].SelectedItem as ComboBoxItem)?.Tag as string != "VISIBLE") continue;
                var card = new StackPanel { Margin = new Thickness(0, 6, 0, 6) };
                card.Children.Add(new TextBlock { Text = character.PrimaryName, FontWeight = FontWeights.SemiBold });
                var expressionRow = new StackPanel { Orientation = Orientation.Horizontal, Margin = new Thickness(0, 4, 0, 0) };
                expressionRow.Children.Add(new TextBlock { Text = "表情", Width = 64, VerticalAlignment = VerticalAlignment.Center });
                var expression = new TextBox
                {
                    Width = 220,
                    Text = typed.TryGetValue(character.Id, out var edited) ? edited
                        : storedExpressions.TryGetValue(character.Id, out var seeded) ? seeded : "",
                };
                expressionBoxes[character.Id] = expression;
                expressionRow.Children.Add(expression);
                card.Children.Add(expressionRow);
                var options = outfits.Where(option => option.CharacterId == character.Id).ToList();
                if (options.Count > 0)
                {
                    var outfitRow = new StackPanel { Orientation = Orientation.Horizontal, Margin = new Thickness(0, 4, 0, 0) };
                    outfitRow.Children.Add(new TextBlock { Text = "服装", Width = 64, VerticalAlignment = VerticalAlignment.Center });
                    var outfit = new ComboBox { Width = 220 };
                    outfit.Items.Add(new ComboBoxItem { Tag = "", Content = "沿用场景服装" });
                    foreach (var option in options)
                        outfit.Items.Add(new ComboBoxItem { Tag = option.Id, Content = option.Name });
                    Select(outfit, storedOutfits.TryGetValue(character.Id, out var outfitId) ? outfitId : "");
                    outfitBoxes[character.Id] = outfit;
                    outfitRow.Children.Add(outfit);
                    card.Children.Add(outfitRow);
                }
                directionHost.Children.Add(card);
            }
        }

        static void Select(ComboBox box, string tag)
        {
            foreach (var item in box.Items.OfType<ComboBoxItem>())
                if ((string?)item.Tag == tag) { box.SelectedItem = item; return; }
            if (box.Items.Count > 0) box.SelectedIndex = 0;
        }
        static List<string> SplitList(string text) => text.Split(['，', '、', ','], StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries).ToList();
        static bool SameMap(Dictionary<string, string> left, Dictionary<string, string> right)
        {
            if (left.Count != right.Count) return false;
            foreach (var (key, value) in left)
                if (!right.TryGetValue(key, out var other) || other != value) return false;
            return true;
        }
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
