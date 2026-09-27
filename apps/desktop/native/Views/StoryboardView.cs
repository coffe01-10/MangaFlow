using System.Net.Http;
using System.Text.Json;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Controls.Primitives;
using System.Windows.Input;
using System.Windows.Media;
using System.Windows.Media.Animation;
using System.Windows.Shapes;
using MangaFlow.Native.Controls;
using MangaFlow.Native.Services;

namespace MangaFlow.Native.Views;

/// <summary>
/// NUI-4: visual storyboard canvas. Panels and bubbles live in normalized 0–1 page
/// coordinates; gestures move FrameworkElements directly (zero re-render during drag)
/// and commit into an undo stack on release. Save is one atomic PUT with request_id.
/// </summary>
public sealed partial class StoryboardView : WorkspaceView
{
    private const double BasePageWidth = 640;
    private const double MinSize = 0.03, MinBubble = 0.02, MinSfx = 0.01;
    // 吸附阈值与 web 同为 6px（归一化由页宽折算）；旋转吸附 15°，角度域 (-360,360]。
    private const double RotationSnapDeg = 15, MaxRotation = 360;
    private static readonly double[] PageGuides = [0, 0.5, 1];
    private static readonly int[] GridSteps = [5, 10, 20];

    private readonly ComboBox chapterSelector = Selector("章节选择", 240);
    private readonly StackPanel pageBar = new() { Orientation = Orientation.Vertical };
    private readonly Canvas page = new() { Background = Brushes.White };
    private readonly Border pageHost = new();
    private readonly ScrollViewer viewport = new() { Background = new SolidColorBrush(Color.FromRgb(0xD8, 0xD2, 0xC6)), VerticalScrollBarVisibility = ScrollBarVisibility.Auto, HorizontalScrollBarVisibility = ScrollBarVisibility.Auto };
    private readonly StackPanel inspector = new();
    private readonly TextBlock statusLine = new() { Style = (Style)Application.Current.FindResource("Micro") };
    private readonly ToggleButton snapButton = new() { Content = "吸附", Style = (Style)Application.Current.FindResource("Pill"), IsChecked = true };
    private readonly ToggleButton orderButton = new() { Content = "阅读序", Style = (Style)Application.Current.FindResource("Pill"), IsChecked = true };
    private readonly Button saveButton = new() { Content = "保存本页", IsEnabled = false, Style = (Style)Application.Current.FindResource("InkButton") };
    private readonly Button undoButton = new() { Content = "撤销", IsEnabled = false, Style = (Style)Application.Current.FindResource("Compact") };
    private readonly Button redoButton = new() { Content = "重做", IsEnabled = false, Style = (Style)Application.Current.FindResource("Compact") };
    private readonly TextBlock zoomLabel = new() { Text = "100%", VerticalAlignment = VerticalAlignment.Center, Margin = new Thickness(8, 0, 8, 0) };
    private readonly Border conflictBar = new();
    // 网格（对齐 web toolbar 的 grid 开关 + gridStep 下拉）：叠层线与吸附目标
    // 共用同一份归一化坐标；网格开启才把线并入吸附目标。
    private readonly ToggleButton gridButton = new() { Content = "网格", Style = (Style)Application.Current.FindResource("Pill") };
    private readonly ComboBox gridStepBox = new() { MinHeight = 38, MinWidth = 78, Visibility = Visibility.Collapsed };
    private readonly ToggleButton annotateButton = new() { Content = "批注", Style = (Style)Application.Current.FindResource("Pill") };
    private int gridStepMm = 10;
    private readonly List<double> gridX = [], gridY = [];
    private readonly List<(double At, bool Vertical, Line Element)> gridLineElements = [];
    // 对齐/分布工具组（对齐 web toolbar-align）：可移动选中格 ≥2 才显示，
    // 等距分布需要 ≥3。
    private WrapPanel? alignBar;
    private WrapPanel? annotateBar;
    private readonly List<Button> distributeButtons = [];
    // 实时尺寸标签（对齐 web canvas-size-label）：手势期间跟随对象右下角
    // 显示 W×H mm / 旋转角 / 拟声词字号，松手隐藏。
    private readonly Border sizeLabel = new()
    {
        Tag = "size-label", IsHitTestVisible = false, Visibility = Visibility.Collapsed,
        Background = new SolidColorBrush(Color.FromRgb(0x15, 0x15, 0x12)),
        CornerRadius = new CornerRadius(3), Padding = new Thickness(7, 2, 7, 2),
        Child = new TextBlock { Foreground = Brushes.White, FontSize = 11 },
    };
    private Point sizeLabelAt;
    // 气泡手柄（对齐 web transform-handles kind="bubble"）：四角缩放 + 旋转 +
    // 可选锚点/尾巴终点；仅气泡选中时挂载，位置随 rotation 绕中心公转。
    private readonly List<(string Name, Border Element)> bubbleHandles = [];
    private readonly List<SfxNode> sfxNodes = [];
    private SfxNode? selectedSfx;
    // 多选格集合（对齐 web selection.ids）：有序，末位为主格；气泡/拟声词
    // 选中时收缩为宿主格单元素，面板手势只认 selectedBubble==null 状态。
    private readonly List<string> selectedIds = [];
    // Ctrl+C 复制出的几何：Rect（面板）/ (Rect,Rotation)（气泡）/ SfxGeometry。
    private object? copiedGeometry;
    private string lastNotice = "";
    // 对齐 web StoryboardToolbar 的两组开关：出血框/安全区默认关闭，页缺 canvas
    // 字段时禁用；专注模式对齐 focus-mode CSS（隐藏页面条）；重算按钮对齐
    // storyboard-status 行的「从本页重新计算」。
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
        // 批注笔画走 Preview 隧道事件：批注开启时按下即 handled，面板/气泡
        // 的拖动处理器根本收不到这次按下（对齐 web annotating 时对象失焦）。
        page.PreviewMouseLeftButtonDown += OnAnnotateDown;
        page.PreviewMouseMove += OnAnnotateMove;
        page.PreviewMouseLeftButtonUp += OnAnnotateUp;
    }

    // 画布可能尚未挂进视觉树（如无头回归检查），此时 Focus 无效，跳过即可。
    private void FocusCanvas()
    {
        if (page.IsLoaded) page.Focus();
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


    public override async void Activate(WorkspaceContext context)
    {
        base.Activate(context);
        // 作用域守卫（net10 迟到失败窗口）：钉住本次激活的 CTS 实例。跨项目切换
        // （Deactivate→Activate 续新 CTS）之后，上一轮激活的三章请求迟到失败若
        // 无守卫会画进新项目；成功续体同理不得把旧项目数据写进字段。
        var requestLifetime = lifetime;
        try
        {
            var chapterRows = await Api.SendAsync($"projects/{ProjectId}/chapters", cancellation: lifetime.Token);
            var characterRows = await Api.SendAsync($"projects/{ProjectId}/characters", cancellation: lifetime.Token);
            var outfitRows = await Api.SendAsync($"projects/{ProjectId}/outfits", cancellation: lifetime.Token);
            if (lifetime.Token.IsCancellationRequested || !ReferenceEquals(requestLifetime, lifetime)) return;
            chapters = chapterRows.EnumerateArray().Select(ChapterItem.From).ToList();
            characters = characterRows.EnumerateArray().Select(CharacterItem.From).ToList();
            outfits = outfitRows.EnumerateArray().Select(OutfitItem.From).ToList();
            chapterSelector.Items.Clear();
            foreach (var chapter in chapters)
                chapterSelector.Items.Add(new ComboBoxItem { Tag = chapter.Id, Content = $"第 {chapter.Ordinal} 章 · {chapter.Title}" });
            if (chapters.Count == 0)
            {
                // R2D-02：无章节项目必须连同上一项目的画布/页签状态一起清空，
                // 否则面包屑已切到新项目、画布仍显示旧项目的分镜页——页签点击、
                // 拖动格子、保存本页都会读写旧项目的数据（跨项目污染）。
                chapterId = "";
                pages = [];
                currentPage = null;
                panels = [];
                bubbles.Clear();
                sfxNodes.Clear();
                storyboard = default;
                dialogueDrafts.Clear();
                history.Clear();
                ResetTimeline();
                geometryRequest = null;
                dirty = false;
                bubblesDeleted = false;
                UpdateStructureBar();
                RenderPageBar();
                page.Children.Clear();
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
            if (!ReferenceEquals(requestLifetime, lifetime) || lifetime.Token.IsCancellationRequested) return;
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
        // 作用域守卫（net10 迟到失败窗口）：以发起时的 CTS 实例钉住请求所属的
        // 视图作用域——跨项目切换时 Deactivate→Activate 会续新 CTS，仅靠版本号
        // 管不住「新激活尚未发起加载」的空档；旧项目的迟到失败一律不渲染。
        var requestLifetime = lifetime;
        try
        {
            var rows = await Api.SendAsync($"chapters/{chapterId}/pages", cancellation: lifetime.Token);
            if (requestVersion != pagesLoadVersion || lifetime.Token.IsCancellationRequested
                || !ReferenceEquals(requestLifetime, lifetime)) return;
            pages = rows.EnumerateArray().Select(PageItem.From).ToList();
            UpdateStructureBar();
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
            // 迟到的失败同属已被取代的请求：不得把较新请求已落下的页面列表盖成错误卡；
            // 作用域已更换/取消的失败（跨项目切换空档）同样不得渲染。
            if (requestVersion != pagesLoadVersion || !ReferenceEquals(requestLifetime, lifetime)
                || lifetime.Token.IsCancellationRequested) return;
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
                        new TextBlock { Text = $"P.{item.PageNumber:D3}", FontFamily = (FontFamily)FindResource("Serif"), FontSize = 12 },
                        new TextBlock { Text = $"{item.PanelCount} 格", FontSize = 11, FontWeight = FontWeights.Bold, Margin = new Thickness(0, 5, 0, 4) },
                        new TextBlock { Text = item.ContinuityStatus == "NEEDS_REVIEW" ? "待复查" : item.StateLabel, FontSize = 12 },
                    },
                },
                Style = (Style)Application.Current.FindResource("Pill"),
                IsChecked = currentPage?.Id == item.Id, Margin = new Thickness(0, 0, 0, 5), MinWidth = 76, MinHeight = 76, Padding = new Thickness(8),
            };
            var captured = item;
            chip.Click += async (_, _) =>
            {
                if (currentPage?.Id == captured.Id) { chip.IsChecked = true; return; }
                if (!await ConfirmLeaveAsync()) { chip.IsChecked = currentPage?.Id == captured.Id; return; }
                await SelectPageAsync(captured);
            };
            pageBar.Children.Add(chip);
        }
        RefreshPageSelector(); UpdatePageSummary();
    }

    private async Task SelectPageAsync(PageItem item, bool preserveDrafts = false)
    {
        // 页面加载先收尾视图态：回放/快照对比不跨页存活（对齐 web 的
        // 页切换清 compareSnapshotId 与 timeline；同页 preserve 重载同理）。
        ExitLibraryModes();
        var previous = currentPage;
        // 单调请求代号（与 ScriptView 的 scriptLoadVersion 同一模式）：快速连点
        // 两页时，迟到的旧响应既不得渲染旧分镜，也不得改写 currentPage 与页号记忆。
        var requestVersion = ++pageLoadVersion;
        // preserveDrafts（叙事保存后的同页重载）：几何草稿、撤销栈、选中态先快照，
        // 新服务端数据落地后叠回去（对齐 web 的 invalidateQueries refetch + drafts
        // overlay：叙事 CRUD 会升 panel.version 与页栅栏，锚点必须刷新，但画布上
        // 未保存的几何不允许被这次重载吞掉）。
        var panelRectDrafts = preserveDrafts ? panels.ToDictionary(p => p.Id, p => p.Rect) : null;
        var bubbleDraftSnapshot = preserveDrafts ? bubbles.ToDictionary(b => b.Id, b => b.Snapshot()) : null;
        var sfxDraftSnapshot = preserveDrafts ? sfxNodes.ToDictionary(n => (n.PanelId, n.Index), n => n.Snapshot()) : null;
        var selectedPanelIdsDraft = preserveDrafts ? selectedIds.ToList() : null;
        var selectedBubbleId = preserveDrafts ? selectedBubble?.Id : null;
        var selectedSfxKeyDraft = preserveDrafts && selectedSfx is { } sfx ? (sfx.PanelId, sfx.Index) : ((string, int)?)null;
        // 作用域守卫（net10 迟到失败窗口，LoadPagesAsync 同款）：跨项目切换的空档里
        // 旧项目的整页分镜迟到失败不得画进新项目。
        var requestLifetime = lifetime;
        try
        {
            // 迟到的旧响应先用局部变量承接，守卫通过后再写字段：无条件赋值会在
            // 快速连点两页时把旧页分镜短暂污染进字段（P3-6 同类竞态）。
            var fresh = await Api.SendAsync($"pages/{item.Id}/storyboard", cancellation: lifetime.Token);
            if (requestVersion != pageLoadVersion || lifetime.Token.IsCancellationRequested
                || !ReferenceEquals(requestLifetime, lifetime)) return;
            storyboard = fresh;
            // 拉取成功后才切换页签状态与记住的页号；页版本以服务端为准刷新，
            // 冲突恢复（放弃并重新加载）后的重试才有新锚点，否则永远撞同一个 409
            var serverVersion = storyboard.Element("page").Number("storyboard_version");
            currentPage = serverVersion > 0 && serverVersion != item.StoryboardVersion ? item with { StoryboardVersion = serverVersion } : item;
            KeyValueStore.Set("storyboard:page:" + ProjectId, item.Id);
            // 批注笔画按页本地留存：换页后重读新页的 KeyValueStore 键。
            annotationStrokes = LoadAnnotations();
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
            RebuildSfx();
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
                selectedSfx = null;
                selectedIds.Clear();
                conflictBar.Visibility = Visibility.Collapsed;   // 新数据落地即冲突解除
                MarkDirty();
            }
            else
            {
                foreach (var panel in panels)
                    if (panelRectDrafts!.TryGetValue(panel.Id, out var rect)) panel.Rect = rect;
                foreach (var bubble in bubbles)
                    if (bubbleDraftSnapshot!.TryGetValue(bubble.Id, out var draft)) bubble.ApplySnapshot(draft);
                foreach (var node in sfxNodes)
                    if (sfxDraftSnapshot!.TryGetValue((node.PanelId, node.Index), out var draft)) node.ApplySnapshot(draft);
                // 选中集必须先清再还原：不清就叠加会留下重复 id（panel-1,panel-2
                // 变成 p1,p2,p1,p2），层序/微调的 selectedIds 语义随之失真。
                selectedIds.Clear();
                if (selectedPanelIdsDraft != null) selectedIds.AddRange(selectedPanelIdsDraft.Where(id => panels.Any(p => p.Id == id)));
                selected = panels.FirstOrDefault(p => selectedIds.Count > 0 && p.Id == selectedIds[^1]);
                selectedBubble = bubbles.FirstOrDefault(b => b.Id == selectedBubbleId);
                selectedSfx = sfxNodes.FirstOrDefault(n => selectedSfxKeyDraft is { } key && n.PanelId == key.Item1 && n.Index == key.Item2);
                // 节点全部重建过，选中高亮要在新元素上重挂（RenderCanvas 只重建手柄）
                foreach (var panel in panels) panel.SetSelected(selectedIds.Contains(panel.Id));
                foreach (var bubble in bubbles) bubble.SetSelected(bubble == selectedBubble);
                foreach (var node in sfxNodes) node.SetSelected(node == selectedSfx);
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
            if (spreadOpen) await RefreshSpreadSkeletons();   // 对开骨架随新页重排邻页
            if (fitPending && viewport.ActualWidth > 100 && viewport.ActualHeight > 100) { fitPending = false; FitViewport(); }
            ApplyOutfitFocus();
        }
         catch (OperationCanceledException) { }
        catch (Exception error) when (error is not OperationCanceledException)
        {
            // 迟到的失败同样属于已被取代的请求：不得回滚较新请求已经落下的选中页签；
            // 作用域已更换/取消的失败（跨项目切换空档）同样不得渲染。
            if (requestVersion != pageLoadVersion || !ReferenceEquals(requestLifetime, lifetime)
                || lifetime.Token.IsCancellationRequested) return;
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

    private void RebuildSfx()
    {
        sfxNodes.Clear();
        foreach (var panel in panels)
        {
            var index = 0;
            foreach (var entry in panel.SoundEffects)
            {
                if (SfxNode.From(entry, panel, index++) is { } node)
                {
                    node.HitArea.MouseLeftButtonDown += (s, e) => BeginSfxMove(s, e, node);
                    node.RotateHandle.MouseLeftButtonDown += (s, e) => BeginSfxHandle(s, e, node, "rotate");
                    node.ScaleHandle.MouseLeftButtonDown += (s, e) => BeginSfxHandle(s, e, node, "scale");
                    sfxNodes.Add(node);
                }
            }
        }
    }

    // animate=true 走命令动效（web 命令 transition）：只有离散命令的提交点传，
    // zoom/fit/手势直写/页面重建一律瞬写（对齐 web .is-gesturing 压零过渡）。
    private void UpdatePageSize(bool animate = false)
    {
        var width = BasePageWidth * zoom;
        var height = width * pageAspect;
        page.Width = width;
        page.Height = height;
        pageHost.Width = width + 2;
        pageHost.Height = height + 2;
        foreach (var panel in panels) panel.ApplyPosition(page, animate);
        foreach (var bubble in bubbles) bubble.ApplyPosition(page, animate);
        foreach (var node in sfxNodes) node.ApplyPosition(page, animate);
        PositionResizeHandles(ActiveHandleRect(), animate);
        PositionBubbleHandles(animate);
        UpdateOverlaySizes();
        RenderGhosts();
        RenderAnnotations();
        // 回放帧录制（V02-33）：所有几何落地都汇聚到 UpdatePageSize，
        // 在这里统一录时间线；回放期间的写回被 RecordFrame 自身门禁拦掉。
        RecordFrame();
    }

    private void RenderCanvas()
    {
        // 事件处理器已在节点创建处挂接一次，这里只重组 Children
        page.Children.Clear();
        guideLines.Clear();
        fadingGuides.Clear();
        guideSignatures.Clear();
        foreach (var panel in panels) page.Children.Add(panel.Element);
        foreach (var bubble in bubbles) page.Children.Add(bubble.Element);
        foreach (var node in sfxNodes) page.Children.Add(node.Element);
        RenderGridOverlay();
        page.Children.Add(sizeLabel);
        RenderOrderBadges();
        RenderGuidesOverlay();
        RenderResizeHandles();
        RenderGhosts();
        RenderAnnotations();
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
                Background = new SolidColorBrush(Color.FromRgb(0x15, 0x15, 0x12)), HorizontalAlignment = HorizontalAlignment.Right,
                Padding = new Thickness(5, 2, 5, 2),
                Child = new TextBlock { Text = $"格 {panel.ReadingOrder:D2}", FontSize = 10, Foreground = Brushes.White },
            };
            // Bind to the rendered panel so zoom, drag, resize and keyboard movement
            // cannot leave badges at their previous canvas coordinates.
            var anchor = new Grid { Tag = "order-badge", IsHitTestVisible = false };
            anchor.Children.Add(badge);
            anchor.SetBinding(WidthProperty, new System.Windows.Data.Binding("Width") { Source = panel.Element });
            anchor.SetBinding(Canvas.LeftProperty, new System.Windows.Data.Binding { Source = panel.Element, Path = new PropertyPath("(0)", Canvas.LeftProperty) });
            anchor.SetBinding(Canvas.TopProperty, new System.Windows.Data.Binding { Source = panel.Element, Path = new PropertyPath("(0)", Canvas.TopProperty) });
            Panel.SetZIndex(anchor, 45);
            page.Children.Add(anchor);
        }
    }

    // ============ Gesture engine: direct element mutation, commit on release ============
    private PanelGesture? panelGesture;
    private readonly List<Line> guideLines = [];
    // 淡出中的参考线（对齐 web .canvas-guide-line.leaving）：ClearGuides 把线挪进
    // 这里做 140ms 淡出，同签名的新参考线可以原地复活，避免吸附点闪断。
    private readonly List<Line> fadingGuides = [];
    private readonly Dictionary<Line, string> guideSignatures = [];

    // ============ 画布命令动效（对齐 web .canvas-page 的 170ms ease-out transition）============
    // 离散几何命令（对齐/分布/同尺寸/粘贴/数字输入/方向键/撤销重做）的落位用
    // DoubleAnimation 滑到目标；手势直写路径 animate=false，先 BeginAnimation(null)
    // 摘掉在途动画，保证拖动总能立即接管。层序变更用透明度脉冲（对齐 web z-flash）。
    private static readonly TimeSpan CommandMotionSpan = TimeSpan.FromMilliseconds(170);
    private static readonly TimeSpan GuideFadeSpan = TimeSpan.FromMilliseconds(140);
    private static readonly CubicEase CommandMotionEase = new() { EasingMode = EasingMode.EaseOut };

    private static DoubleAnimation CommandAnimation(double to) =>
        new(to, new Duration(CommandMotionSpan)) { EasingFunction = CommandMotionEase };

    private static void AnimateOrSet(IAnimatable element, DependencyProperty property, double to, bool animate)
    {
        if (animate)
        {
            element.BeginAnimation(property, CommandAnimation(to));
            return;
        }
        element.BeginAnimation(property, null);
        ((DependencyObject)element).SetValue(property, to);
    }

    // 旋转角动画：命令路径在既有 RotateTransform 上滑 Angle；瞬写路径整体替换
    // transform（顺带摘掉在途动画，落回 web 的 transform: rotate(deg) 语义）。
    private static void ApplyRotation(FrameworkElement element, double angle, bool animate)
    {
        if (!animate)
        {
            element.RenderTransform = Math.Abs(angle) > 1e-9 ? new RotateTransform(angle) : null;
            return;
        }
        if (element.RenderTransform is not RotateTransform rot)
        {
            rot = new RotateTransform(0);
            element.RenderTransform = rot;
        }
        rot.BeginAnimation(RotateTransform.AngleProperty, CommandAnimation(angle));
    }

    // 层序脉冲（对齐 web .canvas-panel.z-flash 关键帧）：透明度 1→.45→1。
    private static void PulseZIndex(IAnimatable element)
    {
        var anim = new DoubleAnimationUsingKeyFrames { Duration = new Duration(TimeSpan.FromMilliseconds(240)) };
        anim.KeyFrames.Add(new LinearDoubleKeyFrame(0.45, KeyTime.FromPercent(0.4)));
        anim.KeyFrames.Add(new LinearDoubleKeyFrame(1, KeyTime.FromPercent(1)));
        element.BeginAnimation(UIElement.OpacityProperty, anim);
    }

    // 统一的手势捕获骨架（对齐 web pointermove→commitGesture 的一次性提交）：
    // moved 里只做元素级改画、参考线与尺寸标签；up/LostCapture 由 commit 一次入栈。
    private void CaptureGesture(FrameworkElement element, MouseButtonEventArgs e, Action<Point> onMove, Action onCommit)
    {
        element.CaptureMouse();
        MouseEventHandler moved = (_, me) => onMove(ToNormalized(me.GetPosition(page)));
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
            onCommit();
            ClearGuides();
            HideSizeLabel();
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

    private void BeginPanelDrag(object sender, MouseButtonEventArgs e, PanelNode panel)
    {
        if (replayOpen || e.ChangedButton != MouseButton.Left) return;
        // Shift+按下 = 加选/保留多选（对齐 web startPanelMove 的 additive）：
        // 已选中的格保持整组拖动；无修饰键回到单选。
        var additive = Keyboard.Modifiers == ModifierKeys.Shift && selectedBubble == null && selectedSfx == null;
        var ids = additive && selectedIds.Count > 0
            ? (selectedIds.Contains(panel.Id) ? new List<string>(selectedIds) : [.. selectedIds, panel.Id])
            : [panel.Id];
        SelectPanels(ids);
        // 多边形格只读：保持选中但不进移动组（web 排除 isPolygonPanel 同款）。
        var movable = panels.Where(item => ids.Contains(item.Id) && !item.IsPolygon).ToList();
        if (movable.Count == 0) { e.Handled = true; return; }
        var start = ToNormalized(e.GetPosition(page));
        panelGesture = new PanelGesture(movable);
        var element = (FrameworkElement)sender;
        CaptureGesture(element, e,
            onMove: pointer =>
            {
                if (panelGesture == null) return;
                panelGesture.Offset = new Point(pointer.X - start.X, pointer.Y - start.Y);
                var movedRects = panelGesture.Compute(this);
                foreach (var target in movable) target.SetRectDirect(movedRects[target.Id], page);
                // 手柄跟随拖动实时移动（对齐 web 手势期的 paintHandles）
                PositionResizeHandles(ActiveHandleRect());
                PositionBubbleHandles();
            },
            onCommit: () =>
            {
                panelGesture?.Commit(this);
                panelGesture = null;
            });
    }

    private void BeginBubbleDrag(object sender, MouseButtonEventArgs e, BubbleNode bubble)
    {
        if (replayOpen || e.ChangedButton != MouseButton.Left) return;
        SelectBubble(bubble);
        var start = ToNormalized(e.GetPosition(page));
        var origin = bubble.Snapshot();
        var host = panels.FirstOrDefault(p => p.Id == bubble.PanelId);
        var element = (FrameworkElement)sender;
        CaptureGesture(element, e,
            onMove: pointer =>
            {
                var next = new Rect(origin.Rect.X + pointer.X - start.X, origin.Rect.Y + pointer.Y - start.Y, origin.Rect.Width, origin.Rect.Height);
                if (host != null) next = ClampInto(next, host.Rect);
                bubble.SetGeometryDirect(origin with { Rect = next, Moved = true }, page);
                PositionBubbleHandles();
                ShowSizeLabel(RectSizeLabel(next), next.Right, next.Bottom);
            },
            onCommit: () =>
            {
                var final = bubble.Snapshot();
                if (host != null && !Covers(host.Rect, final.Rect))
                {
                    // 气泡不出格（bubbles never leave their panel）：连旋转/锚点一起回弹。
                    bubble.ApplySnapshot(origin);
                    bubble.ApplyPosition(page);
                }
                else if (final.Rect != origin.Rect)
                {
                    history.Push(new GeometryCommand("拖动气泡", [new BubbleChange(bubble.Id, origin, final)]));
                    MarkDirty();
                }
                RenderInspector();
            });
    }

    // ============ 气泡手柄：缩放 / 旋转 / 锚点·尾巴 ============
    // （对齐 web startBubbleResize / startBubbleRotate / startBubblePoint）

    private void BeginBubbleResize(object sender, MouseButtonEventArgs e, BubbleNode bubble, string handle)
    {
        if (e.ChangedButton != MouseButton.Left) return;
        var origin = bubble.Snapshot();
        var host = panels.FirstOrDefault(p => p.Id == bubble.PanelId);
        var ratioLock = Keyboard.Modifiers == ModifierKeys.Shift;
        var fromCenter = Keyboard.Modifiers == ModifierKeys.Alt;
        var element = (FrameworkElement)sender;
        CaptureGesture(element, e,
            onMove: pointer =>
            {
                var next = ApplyResize(origin.Rect, handle, pointer, ratioLock, fromCenter, MinBubble);
                if (host != null) next = ClampInto(next, host.Rect);
                bubble.SetGeometryDirect(origin with { Rect = next, Moved = true }, page);
                PositionBubbleHandles();
                ShowSizeLabel(RectSizeLabel(next), next.Right, next.Bottom);
            },
            onCommit: () => CommitBubbleGeometry(bubble, origin, "缩放气泡"));
    }

    private void BeginBubbleRotate(object sender, MouseButtonEventArgs e, BubbleNode bubble)
    {
        if (e.ChangedButton != MouseButton.Left) return;
        var origin = bubble.Snapshot();
        var center = new Point(origin.Rect.X + origin.Rect.Width / 2, origin.Rect.Y + origin.Rect.Height / 2);
        var aspect = page.Height / Math.Max(1, page.Width);
        var startPointer = ToNormalized(e.GetPosition(page));
        // web startBubbleRotate：按下角度与存角之差做零点，移动中保持同偏差。
        var startAngle = AngleBetween(center, startPointer, aspect) - origin.Rotation;
        // web 同款：15° 吸附由手势启动时的 Shift 状态锁定，不按移动中的实时修饰键。
        var snap = Keyboard.Modifiers == ModifierKeys.Shift;
        var element = (FrameworkElement)sender;
        CaptureGesture(element, e,
            onMove: pointer =>
            {
                var rotation = NormalizeRotation(origin.Rotation + AngleBetween(center, pointer, aspect) - startAngle);
                if (snap) rotation = NormalizeRotation(Math.Round(rotation / RotationSnapDeg) * RotationSnapDeg);
                bubble.SetGeometryDirect(origin with { Rotation = rotation, Moved = true }, page);
                PositionBubbleHandles();
                ShowSizeLabel($"{rotation:F0}°", origin.Rect.Right, origin.Rect.Y);
            },
            onCommit: () => CommitBubbleGeometry(bubble, origin, "旋转气泡"));
    }

    private void BeginBubblePoint(object sender, MouseButtonEventArgs e, BubbleNode bubble, string kind)
    {
        if (e.ChangedButton != MouseButton.Left) return;
        var origin = bubble.Snapshot();
        var element = (FrameworkElement)sender;
        CaptureGesture(element, e,
            onMove: pointer =>
            {
                var point = new Point(Math.Clamp(pointer.X, 0, 1), Math.Clamp(pointer.Y, 0, 1));
                bubble.SetGeometryDirect(kind == "anchor"
                    ? origin with { Anchor = point, Moved = true }
                    : origin with { TailTarget = point, Moved = true }, page);
                PositionBubbleHandles();
            },
            onCommit: () => CommitBubbleGeometry(bubble, origin, "调整气泡尾巴"));
    }

    private void CommitBubbleGeometry(BubbleNode bubble, BubbleGeometry origin, string label)
    {
        var final = bubble.Snapshot();
        if (final != origin)
        {
            history.Push(new GeometryCommand(label, [new BubbleChange(bubble.Id, origin, final)]));
            MarkDirty();
        }
        RenderInspector();
    }

    // ============ 拟声词手势（对齐 web startSfxMove / startSfxRotate / startSfxScale）============

    private void BeginSfxMove(object sender, MouseButtonEventArgs e, SfxNode node)
    {
        if (replayOpen || e.ChangedButton != MouseButton.Left) return;
        SelectSfx(node);
        var start = ToNormalized(e.GetPosition(page));
        var origin = node.Snapshot();
        var element = (FrameworkElement)sender;
        CaptureGesture(element, e,
            onMove: pointer =>
            {
                node.SetGeometryDirect(origin with
                {
                    X = Math.Clamp(origin.X + pointer.X - start.X, 0, 1),
                    Y = Math.Clamp(origin.Y + pointer.Y - start.Y, 0, 1),
                    Moved = true,
                }, page);
                ShowSizeLabel($"{node.Size * canvasHeightMm:F1} mm", node.X, node.Y);
            },
            onCommit: () => CommitSfxGeometry(node, origin, "移动拟声词"));
    }

    private void BeginSfxHandle(object sender, MouseButtonEventArgs e, SfxNode node, string kind)
    {
        if (replayOpen || e.ChangedButton != MouseButton.Left) return;
        var origin = node.Snapshot();
        var center = new Point(node.X, node.Y);
        var aspect = page.Height / Math.Max(1, page.Width);
        var startPointer = ToNormalized(e.GetPosition(page));
        var startAngle = AngleBetween(center, startPointer, aspect) - origin.Rotation;
        var snap = Keyboard.Modifiers == ModifierKeys.Shift;
        var startDistance = Math.Max(0.01, Math.Sqrt(Math.Pow(startPointer.X - center.X, 2) + Math.Pow((startPointer.Y - center.Y) * aspect, 2)));
        var element = (FrameworkElement)sender;
        CaptureGesture(element, e,
            onMove: pointer =>
            {
                if (kind == "rotate")
                {
                    var rotation = NormalizeRotation(origin.Rotation + AngleBetween(center, pointer, aspect) - startAngle);
                    if (snap) rotation = NormalizeRotation(Math.Round(rotation / RotationSnapDeg) * RotationSnapDeg);
                    node.SetGeometryDirect(origin with { Rotation = rotation, Moved = true }, page);
                    ShowSizeLabel($"{rotation:F0}°", node.X, node.Y);
                }
                else
                {
                    // web startSfxScale：size = origin.size ×（当前指针距 / 起始指针距），
                    // 纵横比修正后按页高归一化，clamp 到 [0.01, 0.5]。
                    var distance = Math.Max(0.01, Math.Sqrt(Math.Pow(pointer.X - center.X, 2) + Math.Pow((pointer.Y - center.Y) * aspect, 2)));
                    var size = Math.Clamp(origin.Size * distance / startDistance, MinSfx, 0.5);
                    node.SetGeometryDirect(origin with { Size = size, Moved = true }, page);
                    ShowSizeLabel($"{size * canvasHeightMm:F1} mm", node.X, node.Y);
                }
            },
            onCommit: () => CommitSfxGeometry(node, origin, kind == "rotate" ? "旋转拟声词" : "缩放拟声词"));
    }

    private void CommitSfxGeometry(SfxNode node, SfxGeometry origin, string label)
    {
        var final = node.Snapshot();
        if (final != origin)
        {
            history.Push(new GeometryCommand(label, [new SfxChange(node.PanelId, node.Index, origin, final)]));
            MarkDirty();
        }
        RenderInspector();
    }

    // ============ 多选组缩放（对齐 web startGroupResize → scaleRectWithBounds）============

    private void BeginGroupHandleDrag(object sender, MouseButtonEventArgs e, string handle)
    {
        if (replayOpen || e.ChangedButton != MouseButton.Left) return;
        var movable = MovableSelected();
        if (movable.Count < 2 || BoundingBox(movable.Select(p => p.Rect)) is not { } originBox) return;
        var origins = movable.ToDictionary(p => p.Id, p => p.Rect);
        var ratioLock = Keyboard.Modifiers == ModifierKeys.Shift;
        var element = (FrameworkElement)sender;
        CaptureGesture(element, e,
            onMove: pointer =>
            {
                var resized = ComputeGroupBox(originBox, handle, pointer, ratioLock);
                foreach (var (id, origin) in origins)
                    if (panels.FirstOrDefault(p => p.Id == id) is { } target)
                        target.SetRectDirect(ScaleRectWithBounds(origin, originBox, resized, MinSize), page);
                PositionResizeHandles(resized);
                ShowSizeLabel(RectSizeLabel(resized), resized.Right, resized.Bottom);
            },
            onCommit: () =>
            {
                var changes = new List<GeometryChange>();
                foreach (var (id, origin) in origins)
                    if (panels.FirstOrDefault(p => p.Id == id) is { } target && target.Rect != origin)
                        changes.Add(new PanelChange(id, origin, target.Rect));
                if (changes.Count > 0)
                {
                    history.Push(new GeometryCommand("缩放多格", changes));
                    MarkDirty();
                }
                UpdatePageSize();
                RenderInspector();
            });
    }

    // 组 bounding box 自身的 resize：ApplyResize + 吸附（对齐 web 的
    // snapRect(resized, targets)——吸附移动的是整个 bbox 原点）。
    private Rect ComputeGroupBox(Rect originBox, string handle, Point pointer, bool ratioLock)
    {
        var resized = ApplyResize(originBox, handle, pointer, ratioLock, false, MinSize);
        if (snapButton.IsChecked != true) return resized;
        var xs = new List<double>(); var ys = new List<double>();
        CollectSnapTargets(null, xs, ys, excludeAllSelected: true);
        var threshold = SnapThresholdPx();
        var bestX = BestSnapDelta([resized.X, resized.X + resized.Width / 2, resized.Right], xs, resized.X);
        var bestY = BestSnapDelta([resized.Y, resized.Y + resized.Height / 2, resized.Bottom], ys, resized.Y);
        ClearGuides();
        if (Math.Abs(bestX.Delta) <= threshold) ShowGuide(bestX.Guide, true);
        if (Math.Abs(bestY.Delta) <= threshold) ShowGuide(bestY.Guide, false);
        var x = Math.Abs(bestX.Delta) <= threshold ? Math.Clamp(resized.X + bestX.Delta, 0, 1 - resized.Width) : resized.X;
        var y = Math.Abs(bestY.Delta) <= threshold ? Math.Clamp(resized.Y + bestY.Delta, 0, 1 - resized.Height) : resized.Y;
        return new Rect(x, y, resized.Width, resized.Height);
    }

    // web scaleRectWithBounds 的直译：成员格相对组 bbox 等比映射到新 bbox，
    // 结果各自 clampRect 到页内与最小尺寸。
    private static Rect ScaleRectWithBounds(Rect origin, Rect from, Rect to, double minSize)
    {
        if (from.Width <= 0 || from.Height <= 0) return origin;
        var scaleX = to.Width / from.Width;
        var scaleY = to.Height / from.Height;
        return ClampRect(new Rect(
            to.X + (origin.X - from.X) * scaleX,
            to.Y + (origin.Y - from.Y) * scaleY,
            origin.Width * scaleX,
            origin.Height * scaleY), minSize);
    }

    private static Rect? BoundingBox(IEnumerable<Rect> rects)
    {
        var list = rects.ToList();
        if (list.Count == 0) return null;
        var x = list.Min(r => r.X); var y = list.Min(r => r.Y);
        return new Rect(x, y, list.Max(r => r.Right) - x, list.Max(r => r.Bottom) - y);
    }

    // web rotatePointAround / angleBetween：角度在纵横比修正的页空间里计算。
    private static double NormalizeRotation(double degrees)
    {
        var normalized = degrees % MaxRotation;
        if (normalized <= -MaxRotation) normalized += MaxRotation;
        if (normalized > MaxRotation) normalized -= MaxRotation;
        return normalized;
    }

    private static Point RotatePointAround(Point point, Point center, double degrees, double aspect)
    {
        if (Math.Abs(degrees) < 1e-9) return point;
        var radians = degrees * Math.PI / 180;
        var dx = point.X - center.X;
        var dy = (point.Y - center.Y) * aspect;
        var cos = Math.Cos(radians);
        var sin = Math.Sin(radians);
        return new Point(
            center.X + dx * cos - dy * sin,
            center.Y + (dx * sin + dy * cos) / Math.Max(0.0001, aspect));
    }

    private static double AngleBetween(Point center, Point point, double aspect) =>
        Math.Atan2((point.Y - center.Y) * aspect, point.X - center.X) * 180 / Math.PI;

    private static bool Covers(Rect host, Rect inner) =>
        inner.X >= host.X - 0.0005 && inner.Y >= host.Y - 0.0005 &&
        inner.Right <= host.Right + 0.0005 && inner.Bottom <= host.Bottom + 0.0005;

    private static Rect ClampInto(Rect rect, Rect bounds) => new(
        Math.Clamp(rect.X, bounds.X, Math.Max(bounds.X, bounds.Right - rect.Width)),
        Math.Clamp(rect.Y, bounds.Y, Math.Max(bounds.Y, bounds.Bottom - rect.Height)),
        Math.Min(rect.Width, bounds.Width), Math.Min(rect.Height, bounds.Height));

    private Point ToNormalized(Point point) => new(point.X / Math.Max(1, page.Width), point.Y / Math.Max(1, page.Height));

    // 参考线淡出（对齐 web .leaving）：不立刻移除，140ms 透明度归零后再摘除；
    // Tag 改为 leaving，测试缝/复用逻辑都不再把它当活动参考线。
    private void ClearGuides()
    {
        foreach (var line in guideLines)
        {
            var fading = line;
            fading.Tag = "leaving";
            fadingGuides.Add(fading);
            var anim = new DoubleAnimation(0, new Duration(GuideFadeSpan));
            anim.Completed += (_, _) =>
            {
                page.Children.Remove(fading);
                fadingGuides.Remove(fading);
                guideSignatures.Remove(fading);
            };
            fading.BeginAnimation(UIElement.OpacityProperty, anim);
        }
        guideLines.Clear();
    }

    // gap=true 画等间距参考线（web .canvas-guide-line.gap 的 #c05a9e 虚线）。
    // 同签名（轴/类别/位置 F4）的淡出中线原地复活（对齐 web 的复用策略）：
    // 吸附稳定时线不闪断，真正消失才淡出。
    private void ShowGuide(double at, bool vertical, bool gap = false)
    {
        var signature = $"{(vertical ? "v" : "h")}:{(gap ? "gap" : "edge")}:{at:F4}";
        var line = fadingGuides.FirstOrDefault(item => guideSignatures.TryGetValue(item, out var s) && s == signature);
        if (line != null)
        {
            fadingGuides.Remove(line);
            line.Tag = gap ? "guide-gap" : "guide";
            line.BeginAnimation(UIElement.OpacityProperty, null);
            line.Opacity = 1;
            if (line.Parent != page) page.Children.Add(line);
        }
        else
        {
            line = new Line
            {
                Stroke = new SolidColorBrush(gap ? Color.FromRgb(0xC0, 0x5A, 0x9E) : Color.FromRgb(0x2B, 0xA6, 0xA0)),
                StrokeThickness = 1.5, StrokeDashArray = new DoubleCollection { 4, 3 },
                IsHitTestVisible = false,
                Tag = gap ? "guide-gap" : "guide",
                Opacity = 0,
            };
            line.BeginAnimation(UIElement.OpacityProperty, new DoubleAnimation(1, new Duration(GuideFadeSpan)));
            Panel.SetZIndex(line, 30);
            page.Children.Add(line);
        }
        guideSignatures[line] = signature;
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
        guideLines.Add(line);
    }

    // ============ 面板缩放手柄（对齐 web transform-handles + geometry.applyResize）============

    // web 面板手柄全集：四角 + 四边；单选一个矩形格挂格手柄，多选 ≥2 挂组
    // bounding box 手柄，气泡选中换成气泡手柄（对齐 web TransformHandles 分支）。
    private static readonly string[] PanelHandleNames = ["nw", "n", "ne", "e", "se", "s", "sw", "w"];
    private bool handlesAreGroup;

    private void RenderResizeHandles()
    {
        foreach (var (_, element) in resizeHandles) page.Children.Remove(element);
        resizeHandles.Clear();
        foreach (var (_, element) in bubbleHandles) page.Children.Remove(element);
        bubbleHandles.Clear();
        handlesAreGroup = false;
        if (selectedSfx != null) return;   // 拟声词手柄挂在节点自身
        if (selectedBubble is { } bubble) { RenderBubbleHandles(bubble); return; }
        if (selectedIds.Count == 1 && selected is { IsPolygon: false } single)
            BuildHandleSet(single.Rect, group: false);
        else if (selectedIds.Count >= 2 && MovableSelected() is { Count: >= 2 } movable
            && BoundingBox(movable.Select(p => p.Rect)) is { } box)
            BuildHandleSet(box, group: true);
    }

    private void BuildHandleSet(Rect rect, bool group)
    {
        handlesAreGroup = group;
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
                Tag = (group ? "group-handle:" : "resize-handle:") + name,
            };
            var captured = name;
            handle.MouseLeftButtonDown += group
                ? (s, e) => BeginGroupHandleDrag(s, e, captured)
                : (s, e) => BeginHandleDrag(s, e, selected!, captured);
            Panel.SetZIndex(handle, 50);
            page.Children.Add(handle);
            resizeHandles.Add((name, handle));
        }
        PositionResizeHandles(rect);
    }

    // 气泡手柄集（对齐 web transform-handles kind="bubble"）：四角缩放 + rotate +
    // 仅当服务端形状带 anchor/tail_target 时才挂对应点手柄。
    private void RenderBubbleHandles(BubbleNode bubble)
    {
        foreach (var name in new[] { "nw", "ne", "se", "sw" })
        {
            var handle = MakeBubbleHandle(name, HandleCursor(name), "bubble-handle:");
            var captured = name;
            handle.MouseLeftButtonDown += (s, e) => BeginBubbleResize(s, e, bubble, captured);
            bubbleHandles.Add((name, handle));
        }
        var rotate = MakeBubbleHandle("rotate", Cursors.Hand, "bubble-handle:");
        rotate.Background = new SolidColorBrush(Color.FromRgb(0x3A, 0x7A, 0x98));
        rotate.MouseLeftButtonDown += (s, e) => BeginBubbleRotate(s, e, bubble);
        bubbleHandles.Add(("rotate", rotate));
        if (bubble.Anchor != null)
        {
            var anchor = MakeBubbleHandle("anchor", Cursors.Hand, "bubble-handle:");
            anchor.Background = new SolidColorBrush(Color.FromRgb(0x33, 0x30, 0x2A));
            anchor.MouseLeftButtonDown += (s, e) => BeginBubblePoint(s, e, bubble, "anchor");
            bubbleHandles.Add(("anchor", anchor));
        }
        if (bubble.TailTarget != null)
        {
            var tail = MakeBubbleHandle("tail", Cursors.Cross, "bubble-handle:");
            tail.Background = new SolidColorBrush(Color.FromRgb(0x33, 0x30, 0x2A));
            tail.MouseLeftButtonDown += (s, e) => BeginBubblePoint(s, e, bubble, "tail");
            bubbleHandles.Add(("tail", tail));
        }
        PositionBubbleHandles();
    }

    private static Border MakeBubbleHandle(string name, Cursor cursor, string tagPrefix) => new()
    {
        Width = 14, Height = 14,
        Background = (Brush)Application.Current.FindResource("Accent"),
        BorderBrush = Brushes.White,
        BorderThickness = new Thickness(2),
        CornerRadius = new CornerRadius(999),
        Cursor = cursor,
        Tag = tagPrefix + name,
    };

    private void PositionBubbleHandles(bool animate = false)
    {
        if (selectedBubble is not { } bubble) return;
        var center = new Point(bubble.Rect.X + bubble.Rect.Width / 2, bubble.Rect.Y + bubble.Rect.Height / 2);
        var aspect = page.Height / Math.Max(1, page.Width);
        foreach (var (name, element) in bubbleHandles)
        {
            // 手柄锚点随气泡 rotation 绕中心公转（对齐 web anchorPosition 的旋转分支）
            var anchor = RotatePointAround(BubbleHandleAnchor(bubble, name), center, bubble.Rotation, aspect);
            AnimateOrSet(element, Canvas.LeftProperty, anchor.X * page.Width - element.Width / 2, animate);
            AnimateOrSet(element, Canvas.TopProperty, anchor.Y * page.Height - element.Height / 2, animate);
            Panel.SetZIndex(element, 50);
            if (element.Parent != page) page.Children.Add(element);
        }
    }

    private static Point BubbleHandleAnchor(BubbleNode bubble, string name)
    {
        var rect = bubble.Rect;
        return name switch
        {
            // web anchorPosition rotate：居中上方 0.045 归一化，贴近页顶时收回页内。
            "rotate" => new Point(rect.X + rect.Width / 2, Math.Max(0, rect.Y - 0.045)),
            "anchor" => bubble.Anchor ?? new Point(rect.X + rect.Width / 2, rect.Bottom),
            "tail" => bubble.TailTarget ?? new Point(rect.Right, rect.Bottom),
            _ => new Point(name.Contains('w') ? rect.X : rect.Right, name.Contains('n') ? rect.Y : rect.Bottom),
        };
    }

    // 当前手柄组跟随的矩形：单选=选中格；多选=可移动选中格的 bounding box。
    private Rect? ActiveHandleRect()
    {
        if (selectedBubble != null || selectedSfx != null) return null;
        if (selectedIds.Count == 1)
            return panels.FirstOrDefault(p => p.Id == selectedIds[0] && !p.IsPolygon)?.Rect;
        if (selectedIds.Count >= 2 && MovableSelected() is { Count: >= 2 } movable)
            return BoundingBox(movable.Select(p => p.Rect));
        return null;
    }

    private static Cursor HandleCursor(string name) => name switch
    {
        "n" or "s" => Cursors.SizeNS,
        "e" or "w" => Cursors.SizeWE,
        "ne" or "sw" => Cursors.SizeNESW,
        _ => Cursors.SizeNWSE,
    };

    private void PositionResizeHandles(Rect? rect, bool animate = false)
    {
        if (rect is not { } value) return;
        foreach (var (name, element) in resizeHandles)
        {
            var anchor = HandleAnchor(value, name);
            AnimateOrSet(element, Canvas.LeftProperty, anchor.X * page.Width - element.Width / 2, animate);
            AnimateOrSet(element, Canvas.TopProperty, anchor.Y * page.Height - element.Height / 2, animate);
        }
    }

    // 手柄锚点（对齐 web anchorPosition）：w 取左边、e 取右边，否则水平中点；
    // n 取上边、s 取下边，否则垂直中点。边手柄落在对边中点上。
    private static Point HandleAnchor(Rect rect, string name) => new(
        name.Contains('w') ? rect.X : name.Contains('e') ? rect.Right : rect.X + rect.Width / 2,
        name.Contains('n') ? rect.Y : name.Contains('s') ? rect.Bottom : rect.Y + rect.Height / 2);

    private void BeginHandleDrag(object sender, MouseButtonEventArgs e, PanelNode panel, string handle)
    {
        if (replayOpen || e.ChangedButton != MouseButton.Left) return;
        // 手柄只在 selected==panel 时挂载（RenderResizeHandles 的前置条件），
        // 这里不得再走 SelectPanel：它会重建手柄元素，把正要捕获鼠标的 sender
        // 从视觉树摘下来，捕获随即失效。
        if (selected != panel) SelectPanel(panel);
        var origin = panel.Rect;
        var element = (FrameworkElement)sender;
        CaptureGesture(element, e,
            onMove: pointer =>
            {
                var next = ComputeResized(origin, handle, pointer, panel, Keyboard.Modifiers);
                panel.SetRectDirect(next, page);
                PositionResizeHandles(next);
                ShowSizeLabel(RectSizeLabel(next), next.Right, next.Bottom);
            },
            onCommit: () =>
            {
                CommitResize(panel, origin);
                RenderInspector();
            });
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
        var threshold = SnapThresholdPx();
        var xTargets = new List<double>();
        var yTargets = new List<double>();
        CollectSnapTargets(self, xTargets, yTargets);
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
        PositionGridLines();
        PositionSizeLabel();
    }

    // ============ 网格叠层（对齐 web gridLinesFor + canvas-grid）============
    // 叠层线与吸附目标共用同一份归一化坐标：物理间距 stepMm 沿宽/高铺满一页，
    // 位置 round4；网格开启才把线并入吸附目标。页缺 canvas 字段时按回落的
    // defaultCanvas 尺寸计算（与 web canvas ?? defaultCanvas 同源）。
    private void RenderGridOverlay()
    {
        foreach (var (_, _, element) in gridLineElements) page.Children.Remove(element);
        gridLineElements.Clear();
        gridX.Clear();
        gridY.Clear();
        if (gridButton.IsChecked != true || canvasWidthMm <= 0 || canvasHeightMm <= 0)
        {
            gridStepBox.Visibility = Visibility.Collapsed;
            return;
        }
        gridStepBox.Visibility = Visibility.Visible;
        for (var pos = (double)gridStepMm; pos < canvasWidthMm - 1e-6; pos += gridStepMm)
            gridX.Add(Math.Round(pos / canvasWidthMm, 4));
        for (var pos = (double)gridStepMm; pos < canvasHeightMm - 1e-6; pos += gridStepMm)
            gridY.Add(Math.Round(pos / canvasHeightMm, 4));
        foreach (var at in gridX) AddGridLine(at, vertical: true);
        foreach (var at in gridY) AddGridLine(at, vertical: false);
        PositionGridLines();
    }

    private void AddGridLine(double at, bool vertical)
    {
        var line = new Line
        {
            Stroke = new SolidColorBrush(Color.FromArgb(0x38, 0x44, 0x3F, 0x38)),
            StrokeThickness = 1, IsHitTestVisible = false,
        };
        Panel.SetZIndex(line, 4);
        gridLineElements.Add((at, vertical, line));
        page.Children.Add(line);
    }

    private void PositionGridLines()
    {
        foreach (var (at, vertical, line) in gridLineElements)
            if (vertical) { line.X1 = line.X2 = at * page.Width; line.Y1 = 0; line.Y2 = page.Height; }
            else { line.Y1 = line.Y2 = at * page.Height; line.X1 = 0; line.X2 = page.Width; }
    }

    // ============ 实时尺寸标签（对齐 web canvas-size-label）============
    // 手势期间跟随对象右下角（web 偏移 +8px/+6px），越界翻转到左上侧；
    // 松手隐藏。内容：矩形 W×H mm / 旋转角 / 拟声词字号。
    private void ShowSizeLabel(string text, double normalizedX, double normalizedY)
    {
        ((TextBlock)sizeLabel.Child).Text = text;
        sizeLabelAt = new Point(normalizedX, normalizedY);
        sizeLabel.Visibility = Visibility.Visible;
        PositionSizeLabel();
    }

    private void PositionSizeLabel()
    {
        if (sizeLabel.Visibility != Visibility.Visible) return;
        sizeLabel.Measure(new Size(double.PositiveInfinity, double.PositiveInfinity));
        var size = sizeLabel.DesiredSize;
        var x = sizeLabelAt.X * page.Width + 8;
        var y = sizeLabelAt.Y * page.Height + 6;
        if (x + size.Width > page.Width) x = sizeLabelAt.X * page.Width - size.Width - 8;
        if (y + size.Height > page.Height) y = sizeLabelAt.Y * page.Height - size.Height - 6;
        Canvas.SetLeft(sizeLabel, Math.Max(0, x));
        Canvas.SetTop(sizeLabel, Math.Max(0, y));
    }

    private void HideSizeLabel() => sizeLabel.Visibility = Visibility.Collapsed;

    private string RectSizeLabel(Rect rect) =>
        $"{rect.Width * canvasWidthMm:F1} × {rect.Height * canvasHeightMm:F1} mm";

    // ============ 吸附目标与等间距参考线（对齐 web snapTargets + equalGapSnap）============
    // 目标集 = 页参考线 + 其他格的边/中线 + （网格开启时的）网格线。
    // excludeAllSelected 用于组缩放/等距场景：其余选中格也在动，不能作吸附目标。
    private void CollectSnapTargets(PanelNode? exclude, List<double> xs, List<double> ys, bool excludeAllSelected = false)
    {
        xs.AddRange(PageGuides);
        ys.AddRange(PageGuides);
        foreach (var other in panels)
        {
            if (excludeAllSelected ? selectedIds.Contains(other.Id) : ReferenceEquals(other, exclude)) continue;
            xs.Add(other.Rect.X); xs.Add(other.Rect.X + other.Rect.Width / 2); xs.Add(other.Rect.Right);
            ys.Add(other.Rect.Y); ys.Add(other.Rect.Y + other.Rect.Height / 2); ys.Add(other.Rect.Bottom);
        }
        if (gridButton.IsChecked == true) { xs.AddRange(gridX); ys.AddRange(gridY); }
    }

    // web equalGapSnap 的单轴直译：左边界的右侧间距与右边界的左侧间距相等时，
    // 把格推到两邻格正中；返回位移，命中的邻边作为 gap 参考线画出。
    private double EqualGapDelta(Rect rect, List<Rect> others, bool vertical, List<(double At, bool Vertical)> guides)
    {
        var delta = 0.0;
        if (vertical)
        {
            var leftEdges = others.Where(o => o.Right <= rect.X + SnapThresholdPx()).ToList();
            var rightEdges = others.Where(o => o.X >= rect.Right - SnapThresholdPx()).ToList();
            foreach (var left in leftEdges)
                foreach (var right in rightEdges)
                {
                    var candidate = (left.Right + right.X - rect.Width) / 2;
                    var next = candidate - rect.X;
                    if (Math.Abs(next) <= SnapThresholdPx() && Math.Abs(next) > 1e-9)
                    {
                        delta = next;
                        guides.Add((Math.Round(left.Right, 4), true));
                        guides.Add((Math.Round(right.X, 4), true));
                    }
                }
        }
        else
        {
            var topEdges = others.Where(o => o.Bottom <= rect.Y + SnapThresholdPx()).ToList();
            var bottomEdges = others.Where(o => o.Y >= rect.Bottom - SnapThresholdPx()).ToList();
            foreach (var top in topEdges)
                foreach (var bottom in bottomEdges)
                {
                    var candidate = (top.Bottom + bottom.Y - rect.Height) / 2;
                    var next = candidate - rect.Y;
                    if (Math.Abs(next) <= SnapThresholdPx() && Math.Abs(next) > 1e-9)
                    {
                        delta = next;
                        guides.Add((Math.Round(top.Bottom, 4), false));
                        guides.Add((Math.Round(bottom.Y, 4), false));
                    }
                }
        }
        return delta;
    }

    private double SnapThresholdPx() => 6 / Math.Max(1, page.Width);

    // ============ 选择模型（对齐 web selection { ids, bubble, sfx }）============
    // selectedIds 有序，末位是主格（对齐时的参照格）；气泡/拟声词选中时收缩为
    // 宿主格单元素。selected/selectedBubble 保持原语义不变。
    private void SelectPanel(PanelNode? panel) => SelectPanels(panel == null ? [] : [panel.Id]);

    private void SelectPanels(IReadOnlyList<string> ids)
    {
        selectedIds.Clear();
        selectedIds.AddRange(ids.Where(id => panels.Any(p => p.Id == id)));
        foreach (var candidate in panels) candidate.SetSelected(selectedIds.Contains(candidate.Id));
        foreach (var bubble in bubbles) bubble.SetSelected(false);
        foreach (var node in sfxNodes) node.SetSelected(false);
        selected = panels.FirstOrDefault(p => selectedIds.Count > 0 && p.Id == selectedIds[^1]);
        selectedBubble = null;
        selectedSfx = null;
        FocusCanvas();   // 选中即聚焦画布：键盘快捷键（Tab/方向键/Delete）随之可用
        RenderResizeHandles();
        UpdateAlignBar();
        RenderInspector();
    }

    private void SelectBubble(BubbleNode bubble)
    {
        selectedIds.Clear();
        selectedIds.Add(bubble.PanelId);
        foreach (var candidate in panels) candidate.SetSelected(candidate.Id == bubble.PanelId);
        foreach (var bubbleNode in bubbles) bubbleNode.SetSelected(bubbleNode == bubble);
        foreach (var node in sfxNodes) node.SetSelected(false);
        selected = panels.FirstOrDefault(p => p.Id == bubble.PanelId);
        selectedBubble = bubble;
        selectedSfx = null;
        FocusCanvas();
        // 气泡选中时挂气泡手柄而非面板手柄（对齐 web TransformHandles 分支）
        RenderResizeHandles();
        UpdateAlignBar();
        RenderInspector();
    }

    private void SelectSfx(SfxNode node)
    {
        selectedIds.Clear();
        selectedIds.Add(node.PanelId);
        foreach (var candidate in panels) candidate.SetSelected(candidate.Id == node.PanelId);
        foreach (var bubble in bubbles) bubble.SetSelected(false);
        foreach (var other in sfxNodes) other.SetSelected(other == node);
        selected = panels.FirstOrDefault(p => p.Id == node.PanelId);
        selectedBubble = null;
        selectedSfx = node;
        FocusCanvas();
        RenderResizeHandles();
        UpdateAlignBar();
        RenderInspector();
    }

    // 可移动选中格（对齐 web movableIds → panels.filter(!isPolygonPanel)）。
    private List<PanelNode> MovableSelected() =>
        panels.Where(p => selectedIds.Contains(p.Id) && !p.IsPolygon).ToList();

    // 对齐 web toolbar-align 的可见性：≥2 个可移动选中格才显示整组，
    // 等距分布需要 ≥3。
    private void UpdateAlignBar()
    {
        if (alignBar == null) return;
        var count = (selectedBubble == null && selectedSfx == null) ? MovableSelected().Count : 0;
        alignBar.Visibility = count >= 2 ? Visibility.Visible : Visibility.Collapsed;
        foreach (var button in distributeButtons)
            button.Visibility = count >= 3 ? Visibility.Visible : Visibility.Collapsed;
    }

    // ============ 精准编辑命令（对齐 web alignRects/distributeRects/sameSizeRects/
    //              reorderPanels/geometry clipboard）============

    // 对齐：非主格向选中组 bounding box 的对应边/中线收拢；round4 消浮点尾差。
    private void RunAlign(string mode)
    {
        if (replayOpen) return;
        var targets = MovableSelected();
        if (targets.Count < 2 || BoundingBox(targets.Select(p => p.Rect)) is not { } box) return;
        var changes = new List<GeometryChange>();
        foreach (var panel in targets)
        {
            var rect = panel.Rect;
            var after = mode switch
            {
                "left" => rect with { X = box.X },
                "centerX" => rect with { X = Math.Round(box.X + (box.Width - rect.Width) / 2, 4) },
                "right" => rect with { X = Math.Round(box.Right - rect.Width, 4) },
                "top" => rect with { Y = box.Y },
                "middleY" => rect with { Y = Math.Round(box.Y + (box.Height - rect.Height) / 2, 4) },
                "bottom" => rect with { Y = Math.Round(box.Bottom - rect.Height, 4) },
                _ => rect,
            };
            if (after != rect) changes.Add(new PanelChange(panel.Id, rect, after));
        }
        CommitPanelChanges("对齐格子", changes);
    }

    // 分布（≥3，web distributeRects 直译）：按轴位置排序，最外两格不动，
    // 相邻格间距相等——gap = (span - totalSize)/(n-1)，中间格顺次落位。
    private void RunDistribute(string axis)
    {
        if (replayOpen) return;
        var targets = MovableSelected();
        if (targets.Count < 3) return;
        var horizontal = axis == "x";
        var sorted = targets.OrderBy(p => horizontal ? p.Rect.X : p.Rect.Y).ToList();
        var first = sorted[0].Rect;
        var last = sorted[^1].Rect;
        var span = (horizontal ? last.Right : last.Bottom) - (horizontal ? first.X : first.Y);
        var totalSize = sorted.Sum(p => horizontal ? p.Rect.Width : p.Rect.Height);
        var gap = (span - totalSize) / (sorted.Count - 1);
        var changes = new List<GeometryChange>();
        var cursor = (horizontal ? first.Right : first.Bottom) + gap;
        for (var i = 1; i < sorted.Count - 1; i++)
        {
            var rect = sorted[i].Rect;
            var after = horizontal
                ? rect with { X = Math.Round(cursor, 4) }
                : rect with { Y = Math.Round(cursor, 4) };
            cursor += (horizontal ? rect.Width : rect.Height) + gap;
            if (after != rect) changes.Add(new PanelChange(sorted[i].Id, rect, after));
        }
        CommitPanelChanges(horizontal ? "水平等距" : "垂直等距", changes);
    }

    // 同尺寸：向首个选中的可移动格对齐（web sameSizeRects 的 ids[0] 参照——
    // ids 是点击序，selectedIds 同序），溢出页界时 clampRect 回挪原点。
    private void RunSameSize(string mode)
    {
        if (replayOpen) return;
        var targets = selectedIds
            .Select(id => panels.FirstOrDefault(p => p.Id == id))
            .Where(p => p is { IsPolygon: false }).Cast<PanelNode>().ToList();
        if (targets.Count < 2) return;
        var reference = targets[0].Rect;
        var changes = new List<GeometryChange>();
        foreach (var panel in targets.Skip(1))
        {
            var rect = panel.Rect;
            var width = mode is "width" or "size" ? reference.Width : rect.Width;
            var height = mode is "height" or "size" ? reference.Height : rect.Height;
            var after = ClampRect(new Rect(rect.X, rect.Y, width, height), MinSize);
            after = new Rect(Math.Round(after.X, 4), Math.Round(after.Y, 4), Math.Round(after.Width, 4), Math.Round(after.Height, 4));
            if (after != rect) changes.Add(new PanelChange(panel.Id, rect, after));
        }
        CommitPanelChanges(mode switch { "width" => "同宽", "height" => "同高", _ => "同大小" }, changes);
    }

    private void CommitPanelChanges(string label, List<GeometryChange> changes)
    {
        if (changes.Count == 0) return;
        history.Push(new GeometryCommand(label, changes));
        ApplyChanges(changes, before: false);
        MarkDirty();
        RenderInspector();
    }

    // 层序（web zOrderChanges 的直译）：up/down 与最近邻交换档位，top → maxZ+1，
    // bottom → minZ-1（minZ-1<1 时其余格整体上移一档、目标落 1，z_order 恒 ≥1）。
    // 元数据变更与几何同栈撤销，随整页 PUT 的 geometry.z_order 落库；
    // 目标格 = 单选格或检查器当前格（对齐 web targetId 取值）。
    private void RunZOrder(string op)
    {
        if (replayOpen) return;
        var targetId = selectedIds.Count == 1 ? selectedIds[0] : selected?.Id;
        if (targetId == null || panels.FirstOrDefault(p => p.Id == targetId) is not { } target) return;
        var zOrders = panels.ToDictionary(p => p.Id, p => p.ZOrder);
        var current = zOrders[targetId];
        var ids = zOrders.Keys.ToList();
        var maxZ = ids.Max(id => zOrders[id]);
        var minZ = ids.Min(id => zOrders[id]);
        var changes = new List<(string Id, int Before, int After)>();
        switch (op)
        {
            case "top":
                if (!(current == maxZ && ids.Count(id => zOrders[id] == maxZ) == 1))
                    changes.Add((targetId, current, maxZ + 1));
                break;
            case "bottom":
                if (minZ - 1 >= 1) changes.Add((targetId, current, minZ - 1));
                else
                {
                    foreach (var id in ids.Where(id => id != targetId))
                        changes.Add((id, zOrders[id], zOrders[id] + 1));
                    changes.Add((targetId, current, 1));
                }
                break;
            default:
                var neighbour = op == "up"
                    ? ids.Where(id => id != targetId && zOrders[id] > current).OrderBy(id => zOrders[id]).FirstOrDefault()
                    : ids.Where(id => id != targetId && zOrders[id] < current).OrderByDescending(id => zOrders[id]).FirstOrDefault();
                if (neighbour != null)
                {
                    changes.Add((targetId, current, zOrders[neighbour]));
                    changes.Add((neighbour, zOrders[neighbour], current));
                }
                break;
        }
        if (changes.Count == 0) return;
        history.Push(new GeometryCommand("调整图层",
            changes.Select(c => (GeometryChange)new PanelZChange(c.Id, c.Before, c.After)).ToList()));
        foreach (var (id, _, after) in changes)
            if (panels.FirstOrDefault(p => p.Id == id) is { } panel)
            {
                panel.ZOrder = after;
                PulseZIndex(panel.Element);
            }
        UpdatePageSize();
        MarkDirty();
        RenderInspector();
    }

    // ============ 几何剪贴板（对齐 web copyGeometry/pasteGeometry + Ctrl+C/V）============
    // 跨型别粘贴按 web 语义降维：面板/气泡共享 rect（气泡附带 rotation）；
    // 拟声词只取中心点与旋转；面板不吸收拟声词的 size。
    private sealed record CopiedBubble(Rect Rect, double Rotation);

    private void CopySelectionGeometry()
    {
        if (replayOpen) return;
        if (selectedSfx is { } sfx)
        {
            var g = sfx.Snapshot();
            copiedGeometry = new SfxGeometry(g.X, g.Y, g.Rotation, g.Size, true);
            Notice($"复制几何：{sfx.Text}");
            return;
        }
        if (selectedBubble is { } bubble)
        {
            copiedGeometry = new CopiedBubble(bubble.Rect, bubble.Rotation);
            Notice("复制几何：气泡");
            return;
        }
        if (selectedIds.Count == 1 && panels.FirstOrDefault(p => p.Id == selectedIds[0]) is { IsPolygon: false } panel)
        {
            copiedGeometry = panel.Rect;
            Notice("复制几何：本页");
        }
    }

    private bool PasteSelectionGeometry()
    {
        if (replayOpen) return false;
        if (!AnySelection()) { Notice("先选中一个格再按 Ctrl+C 复制几何"); return false; }
        if (copiedGeometry == null) return false;
        if (selectedSfx is { } sfx)
        {
            var origin = sfx.Snapshot();
            SfxGeometry after = copiedGeometry switch
            {
                SfxGeometry g => g with { Moved = true },
                CopiedBubble b => origin with { X = b.Rect.X + b.Rect.Width / 2, Y = b.Rect.Y + b.Rect.Height / 2, Rotation = b.Rotation, Moved = true },
                Rect r => origin with { X = r.X + r.Width / 2, Y = r.Y + r.Height / 2, Moved = true },
                _ => origin,
            };
            if (after != origin)
            {
                history.Push(new GeometryCommand("粘贴几何", [new SfxChange(sfx.PanelId, sfx.Index, origin, after)]));
                sfx.ApplySnapshot(after);
                sfx.ApplyPosition(page, animate: true);
                MarkDirty();
                RenderInspector();
            }
            return true;
        }
        if (selectedBubble is { } bubble)
        {
            var origin = bubble.Snapshot();
            BubbleGeometry? after = copiedGeometry switch
            {
                CopiedBubble b => origin with { Rect = ClampRect(b.Rect, MinBubble), Rotation = b.Rotation, Moved = true },
                Rect r => origin with { Rect = ClampRect(r, MinBubble), Moved = true },
                _ => null,
            };
            if (after is { } applied && applied != origin)
            {
                history.Push(new GeometryCommand("粘贴几何", [new BubbleChange(bubble.Id, origin, applied)]));
                bubble.ApplySnapshot(applied);
                bubble.ApplyPosition(page, animate: true);
                PositionBubbleHandles(animate: true);
                MarkDirty();
                RenderInspector();
            }
            return true;
        }
        if (copiedGeometry is Rect || copiedGeometry is CopiedBubble)
        {
            var pasted = copiedGeometry is CopiedBubble b2 ? b2.Rect : (Rect)copiedGeometry!;
            var changes = new List<GeometryChange>();
            foreach (var panel in MovableSelected())
            {
                var after = ClampRect(pasted, MinSize);
                if (after != panel.Rect) changes.Add(new PanelChange(panel.Id, panel.Rect, after));
            }
            CommitPanelChanges("粘贴几何", changes);
            return true;
        }
        return false;
    }

    private void Notice(string text)
    {
        lastNotice = text;
        if (Context != null) State.Status = text;
        UpdateStatus(text);
    }

    // ============ 数字几何提交（对齐 web commitRect/commitBubbleRect/commitRotation）============

    private void CommitPanelRect(PanelNode panel, Rect after)
    {
        if (replayOpen) return;
        // clamp 下沉到提交点（对齐 web commitRect 内部的 clampRect）：
        // 数字字段已夹过，测试缝/将来其他调用点走同一防线。
        after = ClampRect(after, MinSize);
        if (after == panel.Rect) return;
        history.Push(new GeometryCommand("输入几何", [new PanelChange(panel.Id, panel.Rect, after)]));
        panel.Rect = after;
        UpdatePageSize(animate: true);
        MarkDirty();
        RenderInspector();
    }

    private void CommitBubbleRectField(BubbleNode bubble, Rect after)
    {
        if (replayOpen) return;
        var origin = bubble.Snapshot();
        var next = origin with { Rect = ClampRect(after, MinBubble), Moved = true };
        if (next == origin) return;
        history.Push(new GeometryCommand("输入几何", [new BubbleChange(bubble.Id, origin, next)]));
        bubble.ApplySnapshot(next);
        UpdatePageSize(animate: true);
        MarkDirty();
        RenderInspector();
    }

    private void CommitBubbleRotationField(BubbleNode bubble, double rotation)
    {
        if (replayOpen) return;
        var origin = bubble.Snapshot();
        // 数字输入用 web commitRotation 的 clamp（±360 + round4），非手势的取模归一化
        var next = origin with { Rotation = Math.Round(Math.Clamp(rotation, -MaxRotation, MaxRotation), 4), Moved = true };
        if (next == origin) return;
        history.Push(new GeometryCommand("输入几何", [new BubbleChange(bubble.Id, origin, next)]));
        bubble.ApplySnapshot(next);
        UpdatePageSize(animate: true);
        MarkDirty();
        RenderInspector();
    }

    // ============ Undo / redo ============
    private void Undo()
    {
        if (replayOpen || !history.CanUndo) return;
        var command = history.Undo();
        pendingCommandLabel = $"撤销：{command.Label}";
        ApplyChanges(command.Changes, before: true);
        MarkDirty();
        RenderCanvas();
        RenderInspector();
    }

    private void Redo()
    {
        if (replayOpen || !history.CanRedo) return;
        var command = history.Redo();
        pendingCommandLabel = $"重做：{command.Label}";
        ApplyChanges(command.Changes, before: false);
        MarkDirty();
        RenderCanvas();
        RenderInspector();
    }

    // 回跳到历史树任意节点（对齐 web jumpToHistory）：先沿撤销段应用 Before
    // 上行到公共祖先，再沿重做段应用 After 到目标；一次批量落位，回放
    // 时间线只录一帧。
    private void JumpToHistory(CommandStack.Node? target)
    {
        if (replayOpen || target == history.Active) return;
        var (undos, redos) = history.PlanTo(target);
        if (undos.Count + redos.Count == 0) { history.SetActive(target); return; }
        pendingCommandLabel = $"回跳：{(redos.Count > 0 ? redos[^1].Label : "基线（初始状态）")}";
        foreach (var command in undos) ApplyChanges(command.Changes, before: true);
        foreach (var command in redos) ApplyChanges(command.Changes, before: false);
        history.SetActive(target);
        MarkDirty();
        RenderCanvas();
        RenderInspector();
        Notice($"已回跳到「{(redos.Count > 0 ? redos[^1].Label : "基线（初始状态）")}」");
    }

    /// <summary>历史树菜单（轻量版：基线 + 全树按深度缩进平铺，分支节点标
    /// ↳，当前节点打勾）——对齐 web HistoryTree 弹层。</summary>
    private void OpenHistoryMenu(FrameworkElement anchor)
    {
        var menu = new ContextMenu();
        var baseline = new MenuItem
        {
            Header = "基线（初始状态）",
            IsChecked = history.Active == null,
            FontWeight = history.Active == null ? FontWeights.Bold : FontWeights.Normal,
        };
        baseline.Click += (_, _) => JumpToHistory(null);
        menu.Items.Add(baseline);
        var seq = 0;
        void AddChildren(CommandStack.Node? parent, int depth)
        {
            foreach (var node in history.ChildrenOf(parent))
            {
                seq += 1;
                var captured = node;
                var onPath = history.IsOnPath(node);
                var indent = new string('　', depth);
                var item = new MenuItem
                {
                    Header = $"{indent}{(onPath ? $"{seq}." : "↳")} {node.Command.Label}",
                    IsChecked = node == history.Active,
                    FontWeight = node == history.Active ? FontWeights.Bold : FontWeights.Normal,
                    FontStyle = onPath ? FontStyles.Normal : FontStyles.Italic,
                };
                item.Click += (_, _) => JumpToHistory(captured);
                menu.Items.Add(item);
                AddChildren(node, depth + 1);
            }
        }
        AddChildren(null, 0);
        menu.PlacementTarget = anchor;
        menu.Placement = PlacementMode.Bottom;
        menu.IsOpen = true;
    }

    private void ApplyChanges(List<GeometryChange> changes, bool before)
    {
        foreach (var change in changes)
        {
            switch (change)
            {
                case PanelChange panelChange:
                    if (panels.FirstOrDefault(p => p.Id == panelChange.Id) is { } panel)
                        panel.Rect = before ? panelChange.Before : panelChange.After;
                    break;
                case PanelZChange zChange:
                    if (panels.FirstOrDefault(p => p.Id == zChange.Id) is { } zPanel)
                    {
                        zPanel.ZOrder = before ? zChange.Before : zChange.After;
                        PulseZIndex(zPanel.Element);
                    }
                    break;
                case BubbleChange bubbleChange:
                    if (bubbles.FirstOrDefault(b => b.Id == bubbleChange.Id) is { } bubble)
                        bubble.ApplySnapshot(before ? bubbleChange.Before : bubbleChange.After);
                    break;
                case SfxChange sfxChange:
                    if (sfxNodes.FirstOrDefault(n => n.PanelId == sfxChange.PanelId && n.Index == sfxChange.Index) is { } node)
                        node.ApplySnapshot(before ? sfxChange.Before : sfxChange.After);
                    break;
            }
        }
        UpdatePageSize(animate: true);
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

    private void UpdateStatus(string label)
    {
        undoButton.IsEnabled = history.CanUndo;
        redoButton.IsEnabled = history.CanRedo;
        statusLine.Text = label.Contains("当前 V") ? label : $"{label} · 当前 V{currentPage?.StoryboardVersion ?? 0}";
        var failed = label.Contains("失败") || label.Contains("冲突");
        var changed = failed || dirty || saving || narrativeBusy;
        statusLine.Foreground = AssetPageUi.Brush(failed ? "Danger" : changed ? "Ink" : "Success");
        saveState.Background = AssetPageUi.Brush(failed ? "WarningBg" : changed ? "Surface" : "SuccessBg");
        saveState.BorderBrush = AssetPageUi.Brush(failed ? "Danger" : changed ? "LineDark" : "Success");
        UpdatePageSummary();
    }

    // ============ Inspector ============
    private void RenderInspector()
    {
        inspector.Children.Clear();
        if (currentPage == null) return;
        inspector.Children.Add(InspectorHeading($"P.{currentPage.PageNumber:D3}" + (selected == null ? "" : $" / PANEL {selected.ReadingOrder:D2}"), "分镜导演台",
            selected == null ? null : Kit.Act("编辑本格", async (_, _) => { if (selected is { } target) await EditPanel(target); }, "Compact")));
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
        var card = new StackPanel();
        card.Children.Add(Kit.FieldLabel("几何"));
        if (panel.IsPolygon)
            card.Children.Add(Kit.Caption("此格为多边形，画布暂不支持拖拽与缩放；矩形调整手柄已禁用。"));
        else
            card.Children.Add(BuildGeometryFields("几何", () => panel.Rect, MinSize, after => CommitPanelRect(panel, after)));
        card.Children.Add(BuildLayerOps());
        card.Children.Add(Kit.Caption($"阅读序 {panel.ReadingOrder} · 绘制层 Z{panel.ZOrder}" + (panel.Bleed ? " · 出血格" : "") + (panel.Borderless ? " · 无边框" : "")));
        var source = storyboard.Array("panels").FirstOrDefault(p => p.Text("id") == panel.Id);
        ComboBox Combo(IReadOnlyDictionary<string, string> labels, string current)
        {
            var box = new ComboBox { Margin = new Thickness(0, 0, 0, 8) };
            foreach (var (key, label) in labels) box.Items.Add(new ComboBoxItem { Tag = key, Content = label });
            SelectCombo(box, current);
            return box;
        }
        var shot = Combo(Labels.ShotType, panel.ShotType);
        System.Windows.Automation.AutomationProperties.SetName(shot, "景别");
        var angle = Combo(Labels.CameraAngle, panel.CameraAngle);
        System.Windows.Automation.AutomationProperties.SetName(angle, "镜头角度");
        var scriptAction = new TextBox { Text = panel.ScriptAction, AcceptsReturn = true, MinHeight = 48, Margin = new Thickness(0, 0, 0, 8) };
        System.Windows.Automation.AutomationProperties.SetName(scriptAction, "动作与表演");
        var background = new TextBox { Text = source.Text("background"), AcceptsReturn = true, MinHeight = 40, Margin = new Thickness(0, 0, 0, 8) };
        System.Windows.Automation.AutomationProperties.SetName(background, "背景");
        var props = new TextBox { Text = string.Join("、", source.Array("props").Select(p => p.ToString())), Margin = new Thickness(0, 0, 0, 8) };
        System.Windows.Automation.AutomationProperties.SetName(props, "场景道具");
        var bleed = new CheckBox { Content = "出血格", IsChecked = panel.Bleed, Margin = new Thickness(0, 4, 0, 0) };
        var borderless = new CheckBox { Content = "无边框", IsChecked = panel.Borderless, Margin = new Thickness(0, 0, 0, 8) };
        card.Children.Add(Kit.FieldLabel("景别"));
        card.Children.Add(shot);
        card.Children.Add(Kit.FieldLabel("镜头角度"));
        card.Children.Add(angle);
        card.Children.Add(Kit.FieldLabel("动作与表演"));
        card.Children.Add(scriptAction);
        card.Children.Add(Kit.FieldLabel("背景"));
        card.Children.Add(background);
        card.Children.Add(Kit.FieldLabel("场景道具（用逗号分隔）"));
        card.Children.Add(props);
        card.Children.Add(bleed);
        card.Children.Add(borderless);
        var panelSaveBusy = false;
        var savePanel = Kit.Act("保存本格", async (sender, _) =>
        {
            if (panelSaveBusy) return;
            panelSaveBusy = true;
            var button = (Button)sender!;
            button.IsEnabled = false;
            var actions = source.Element("actions").ValueKind == JsonValueKind.Object
                ? source.Element("actions").EnumerateObject().ToDictionary(p => p.Name, p => (object?)p.Value.Clone())
                : new Dictionary<string, object?>();
            actions["script_action"] = scriptAction.Text;
            var payload = new Dictionary<string, object?>
            {
                ["version"] = source.Number("version"),
                ["shot_type"] = (shot.SelectedItem as ComboBoxItem)?.Tag as string ?? panel.ShotType,
                ["camera_angle"] = (angle.SelectedItem as ComboBoxItem)?.Tag as string ?? panel.CameraAngle,
                ["actions"] = actions,
                ["background"] = background.Text,
                ["props"] = props.Text.Split('、', ',', '，').Select(p => p.Trim()).Where(p => p.Length > 0).ToList(),
                ["bleed"] = bleed.IsChecked == true,
                ["borderless"] = borderless.IsChecked == true,
            };
            try
            {
                await Api.SendAsync($"panels/{panel.Id}", HttpMethod.Patch, payload, cancellation: lifetime.Token);
                if (currentPage != null) await SelectPageAsync(currentPage, preserveDrafts: true);
            }
            catch (OperationCanceledException) { }
            catch (Exception error) when (error is not OperationCanceledException)
            {
                MessageBox.Show(Host, error.Message, "保存本格未完成", MessageBoxButton.OK, MessageBoxImage.Warning);
            }
            finally { panelSaveBusy = false; button.IsEnabled = true; }
        }, "InkButton");
        savePanel.Margin = new Thickness(0, 4, 0, 8);
        card.Children.Add(savePanel);
        var presence = source.Element("character_presence");
        var cast = new WrapPanel { Margin = new Thickness(0, 10, 0, 0) };
        var members = presence.ValueKind == JsonValueKind.Object
            ? presence.EnumerateObject().Select(p => (Id: p.Name, State: p.Value.GetString() ?? ""))
            : source.Array("characters").Select(p => (Id: p.ToString(), State: "VISIBLE"));
        foreach (var member in members.Where(m => m.State != "NONE"))
            cast.Children.Add(new Border { BorderBrush = AssetPageUi.Brush("Line"), BorderThickness = new Thickness(1), Padding = new Thickness(6, 4, 6, 4), Margin = new Thickness(0, 0, 5, 5),
                Child = new TextBlock { FontSize = 10, Text = $"{characters.FirstOrDefault(c => c.Id == member.Id)?.PrimaryName ?? "未知角色"} · {(member.State == "VISIBLE" ? "实际出镜" : member.State == "OFFSCREEN" ? "画外" : "提及")}" } });
        card.Children.Add(cast);
        inspector.Children.Add(InspectorSection(card));
        var lettering = new StackPanel();
        BuildDialogueEditor(lettering, panel);
        inspector.Children.Add(InspectorSection(lettering));
        // 气泡/拟声词选中时追加各自的几何区（对齐 web bubbleFields 块与拟声词读数）
        if (selectedBubble is { } bubbleSel) inspector.Children.Add(InspectorSection(BuildBubbleGeometryCard(bubbleSel)));
        if (selectedSfx is { } sfxSel) inspector.Children.Add(InspectorSection(BuildSfxCard(sfxSel)));
    }

    // 气泡几何卡（对齐 web bubbleFields 的「气泡几何」块）：X/Y/宽/高 + 角度，
    // mm/% 单位与面板几何同源。
    private StackPanel BuildBubbleGeometryCard(BubbleNode bubble)
    {
        var card = new StackPanel();
        card.Children.Add(Kit.FieldLabel("气泡几何"));
        card.Children.Add(BuildGeometryFields("气泡几何", () => bubble.Rect, MinBubble,
            after => CommitBubbleRectField(bubble, after),
            () => bubble.Rotation, deg => CommitBubbleRotationField(bubble, deg)));
        return card;
    }

    // 拟声词读数卡（web 检查器无独立拟声词表单，画布上直拖/转/缩；这里给只读
    // 定位信息与操作提示，数值与 SizeLabel 同源）。
    private StackPanel BuildSfxCard(SfxNode node)
    {
        var card = new StackPanel();
        card.Children.Add(Kit.FieldLabel("拟声词"));
        card.Children.Add(new TextBlock
        {
            Text = $"{node.Text} · 位置 X {node.X:P1} Y {node.Y:P1} · 角度 {node.Rotation:F0}° · 大小 {node.Size * canvasHeightMm:F1}mm",
            FontSize = 13, FontWeight = FontWeights.Bold, TextWrapping = TextWrapping.Wrap, Margin = new Thickness(0, 6, 0, 6),
        });
        card.Children.Add(Kit.Caption("画布上拖动移动位置，Shift+旋转手柄按 15° 吸附，缩放手柄调字号；随本页几何一并保存。"));
        return card;
    }

    // 数字几何区（对齐 web GeometryFields）：mm/% 双单位切换，各字段独立草稿，
    // Enter/失焦提交、Esc 放弃草稿；AutomationProperties.Name 与 web aria-label
    // 同构（"X（mm）"/"角度（°）"），便于回归检查按可访问名定位。
    private FrameworkElement BuildGeometryFields(string namePrefix, Func<Rect> read, double minSize,
        Action<Rect> commitRect, Func<double>? readRotation = null, Action<double>? commitRotation = null)
    {
        var host = new StackPanel { Margin = new Thickness(0, 6, 0, 8) };
        var inMm = true;
        var fields = new (string Label, Func<Rect, double> Get, Func<Rect, double, Rect> Set, bool Horizontal)[]
        {
            ("X", r => r.X, (r, v) => new Rect(v, r.Y, r.Width, r.Height), true),
            ("Y", r => r.Y, (r, v) => new Rect(r.X, v, r.Width, r.Height), false),
            ("宽", r => r.Width, (r, v) => new Rect(r.X, r.Y, v, r.Height), true),
            ("高", r => r.Height, (r, v) => new Rect(r.X, r.Y, r.Width, v), false),
        };
        var boxes = new List<TextBox>();
        TextBox? rotationBox = null;
        double AxisMm(bool horizontal) => horizontal ? canvasWidthMm : canvasHeightMm;
        string Display(Func<Rect, double> get, bool horizontal)
        {
            var value = get(read());
            return (inMm ? value * AxisMm(horizontal) : value * 100).ToString("F1");
        }
        void Reseed()
        {
            for (var i = 0; i < boxes.Count; i++) boxes[i].Text = Display(fields[i].Get, fields[i].Horizontal);
            if (rotationBox != null && readRotation != null)
                rotationBox.Text = readRotation().ToString("F0");
        }
        void Commit(TextBox box, Func<Rect, double, Rect> set, bool horizontal)
        {
            if (!double.TryParse(box.Text, out var parsed)) { Reseed(); return; }
            var normalized = inMm && AxisMm(horizontal) > 0 ? parsed / AxisMm(horizontal) : parsed / 100;
            var rect = read();
            var after = ClampRect(set(rect, normalized), minSize);
            if (after != rect) commitRect(after);
            else Reseed();
        }
        // 单位切换（对齐 web geometry-unit-toggle）
        var unitRow = new StackPanel { Orientation = Orientation.Horizontal, Margin = new Thickness(0, 0, 0, 6) };
        var mmToggle = new ToggleButton { Content = "mm", IsChecked = true, MinWidth = 44, MinHeight = 28, Style = (Style)Application.Current.FindResource("Pill") };
        var pctToggle = new ToggleButton { Content = "%", MinWidth = 44, MinHeight = 28, Margin = new Thickness(4, 0, 0, 0), Style = (Style)Application.Current.FindResource("Pill") };
        mmToggle.Click += (_, _) => { inMm = true; mmToggle.IsChecked = true; pctToggle.IsChecked = false; Reseed(); };
        pctToggle.Click += (_, _) => { inMm = false; pctToggle.IsChecked = true; mmToggle.IsChecked = false; Reseed(); };
        unitRow.Children.Add(mmToggle);
        unitRow.Children.Add(pctToggle);
        host.Children.Add(unitRow);
        var grid = new WrapPanel();
        foreach (var field in fields)
        {
            var row = new StackPanel { Orientation = Orientation.Horizontal, Margin = new Thickness(0, 0, 10, 6), VerticalAlignment = VerticalAlignment.Center };
            row.Children.Add(new TextBlock { Text = field.Label, Width = 18, Foreground = AssetPageUi.Brush("Muted"), FontSize = 11, VerticalAlignment = VerticalAlignment.Center });
            var box = new TextBox { Width = 68, MinHeight = 30, Tag = namePrefix };
            var captured = field;
            System.Windows.Automation.AutomationProperties.SetName(box, $"{field.Label}（mm）");
            box.GotFocus += (_, _) => System.Windows.Automation.AutomationProperties.SetName(box, $"{captured.Label}（{(inMm ? "mm" : "%")}）");
            box.LostFocus += (_, _) => Commit(box, captured.Set, captured.Horizontal);
            box.KeyDown += (_, ke) =>
            {
                if (ke.Key == Key.Enter) { Commit(box, captured.Set, captured.Horizontal); Keyboard.ClearFocus(); ke.Handled = true; }
                else if (ke.Key == Key.Escape) { Reseed(); Keyboard.ClearFocus(); ke.Handled = true; }
            };
            boxes.Add(box);
            row.Children.Add(box);
            grid.Children.Add(row);
        }
        if (readRotation != null && commitRotation != null)
        {
            var row = new StackPanel { Orientation = Orientation.Horizontal, Margin = new Thickness(0, 0, 10, 6), VerticalAlignment = VerticalAlignment.Center };
            row.Children.Add(new TextBlock { Text = "角度", Width = 30, Foreground = AssetPageUi.Brush("Muted"), FontSize = 11, VerticalAlignment = VerticalAlignment.Center });
            var box = new TextBox { Width = 68, MinHeight = 30, Tag = namePrefix };
            System.Windows.Automation.AutomationProperties.SetName(box, "角度（°）");
            box.LostFocus += (_, _) =>
            {
                if (double.TryParse(box.Text, out var deg)) commitRotation(deg);
                else Reseed();
            };
            box.KeyDown += (_, ke) =>
            {
                if (ke.Key == Key.Enter && double.TryParse(box.Text, out var deg)) { commitRotation(deg); Keyboard.ClearFocus(); ke.Handled = true; }
                else if (ke.Key == Key.Escape) { Reseed(); Keyboard.ClearFocus(); ke.Handled = true; }
            };
            rotationBox = box;
            row.Children.Add(box);
            grid.Children.Add(row);
        }
        host.Children.Add(grid);
        Reseed();
        return host;
    }

    // 图层顺序组（对齐 web panel-layer-ops：上移一层/下移一层/置顶/置底）
    private WrapPanel BuildLayerOps()
    {
        var row = new WrapPanel { Margin = new Thickness(0, 0, 0, 4) };
        foreach (var (label, op) in new[] { ("上移一层", "up"), ("下移一层", "down"), ("置顶", "top"), ("置底", "bottom") })
        {
            var button = Kit.Act(label, (_, _) => RunZOrder(op), "Compact");
            button.MinHeight = 28;
            button.Margin = new Thickness(0, 0, 4, 4);
            row.Children.Add(button);
        }
        return row;
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
        var head = new WrapPanel();
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
        ConfigureDesk(ActualWidth > 0 ? ActualWidth : 1100, true);
    }

    private async Task DeleteBubbleAsync(BubbleNode bubble)
    {
        // 测试缝：headless 回归检查用无模态实现替换删除确认（LeaveConfirmOverride
        // 同一模式）；生产路径为 null。
        var confirmed = DeleteConfirmOverride is { } prompt
            ? prompt()
            : new ConfirmDialog(Host, "删除气泡", "删除这个文字气泡？", "删除", danger: true).ShowDialog() == true;
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
        if (replayOpen || currentPage == null) return;
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
                new { panel_count = count, layout_mode = mode, storyboard_version = pageAtRequest.StoryboardVersion },
                cancellation: lifetime.Token);
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
        // 回放中禁止保存：节点此刻是帧投影而非工作几何，PUT 会把投影落库。
        if (replayOpen) { Notice("退出回放后再保存本页"); return; }
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
                && prior.StackIndex == history.Depth
                && prior.BubbleCount == bubbles.Count
                ? prior.Id
                : Guid.NewGuid();
            geometryRequest = (requestId, history.Depth, bubbles.Count);
            var payload = BuildPayload(requestId);
            // 拟声词几何走面板叙事 PATCH（panel.version），先于整页 PUT 逐格提交
            // （对齐 web saveGeometry 的 sfxPatches：对象条目打底 + Moved 覆写；
            // 未动条目按 {text} 规范化随包回传）。PATCH 升格 panel.version，
            // 成功后整页重载会带上新锚点。
            var sfxPanels = sfxNodes.Where(n => n.Moved).Select(n => n.PanelId).Distinct().ToList();
            foreach (var panelId in sfxPanels)
            {
                var host = panels.FirstOrDefault(p => p.Id == panelId);
                if (host == null) continue;
                // 数组下标即锚：合并包按原 sound_effects 数组序（含未渲染的空文本位）补齐
                var full = new List<object?>();
                for (var i = 0; i < host.SoundEffects.Count; i++)
                {
                    var node = sfxNodes.FirstOrDefault(n => n.PanelId == panelId && n.Index == i);
                    full.Add(node != null ? node.BuildMergedEntry() : SfxBaseEntry(host.SoundEffects[i]));
                }
                await Api.SendAsync($"panels/{panelId}", HttpMethod.Patch,
                    new Dictionary<string, object?> { ["version"] = host.Version, ["sound_effects"] = full },
                    cancellation: lifetime.Token);
            }
            var response = await Api.SendAsync($"pages/{pageAtRequest.Id}/storyboard-geometry", HttpMethod.Put, payload, cancellation: lifetime.Token);
            history.Clear();
            geometryRequest = null;   // 草稿已落库，重试身份随之作废
            bubblesDeleted = false;
            dirty = NarrativeDirty;   // 几何已落库；叙事草稿仍在册时保持脏（离开保护，web 同源）
            var version = response.Element("page").Number("storyboard_version");
            var staleCount = response.Number("candidate_count");
            UpdateStatus($"已保存 · 当前 V{version}");
            State.Status = $"分镜已保存 · V{version} · 将使 {staleCount} 个候选过期";
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
                // 对齐 web buildGeometryPayload：polygon 格原样透传存储几何、仅覆写
                // z_order（层序命令可改 polygon 格）；矩形格以当前 bounds 重建并带
                // 上 rotation 与 z_order（旧版「未动则透传 geometry」会让层序/旋转
                // 草稿永远落不了库——payload 草案钉死这个回归）。
                geometry = (object?)(panel.IsPolygon && panel.StoredGeometry.ValueKind == JsonValueKind.Object
                    ? PolygonGeometryPayload(panel)
                    : new
                    {
                        type = "rect",
                        rect = Round4(panel.Rect),
                        polygon = (double[]?)null,
                        rotation = Math.Round(panel.Rotation, 4),
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

    // polygon 格的保存包（对齐 web `{...stored, z_order: meta ?? stored.z_order}`）
    private static Dictionary<string, object?> PolygonGeometryPayload(PanelNode panel)
    {
        var payload = JsonSerializer.Deserialize<Dictionary<string, object?>>(panel.StoredGeometry.GetRawText()) ?? [];
        payload["z_order"] = panel.ZOrder;
        return payload;
    }

    // 未渲染/未动的拟声词条目基底（对齐 web merged 的 `string→{text}`、对象→原样展开）
    private static Dictionary<string, object?> SfxBaseEntry(JsonElement entry) =>
        entry.ValueKind == JsonValueKind.Object
            ? JsonSerializer.Deserialize<Dictionary<string, object?>>(entry.GetRawText()) ?? []
            : new Dictionary<string, object?> { ["text"] = entry.ToString() };

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
        // 回放只读：几何快捷键（Delete/方向键/Ctrl+C·V·Z·Y/Tab 轮换）全部拦下。
        if (replayOpen || currentPage == null) return;
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
            case Key.C when Keyboard.Modifiers == ModifierKeys.Control:
                CopySelectionGeometry();
                e.Handled = true;
                break;
            case Key.V when Keyboard.Modifiers == ModifierKeys.Control:
                if (PasteSelectionGeometry() || !AnySelection()) e.Handled = true;
                break;
            case Key.Escape:
                if (AnySelection()) { SelectPanels([]); e.Handled = true; }
                break;
            case Key.Tab:
                if (panels.Count > 0)
                {
                    var index = selected == null ? 0 : (panels.IndexOf(selected) + (Keyboard.Modifiers == ModifierKeys.Shift ? -1 : 1) + panels.Count) % panels.Count;
                    SelectPanel(panels[index]);
                    e.Handled = true;
                }
                break;
            case Key.Return or Key.Enter:
                // 画布提示文案承诺「回车打开属性」（检查器按钮是同一能力的鼠标
                // 入口；dc901b01 曾把按钮丢成 null 使编辑对话框整体不可达）。
                // 气泡选中时 selected 是其所属格，回车同样打开该格属性。
                if (selected is { } panelToEdit) { _ = EditPanel(panelToEdit); e.Handled = true; }
                break;
            case Key.Left or Key.Right or Key.Up or Key.Down:
                var offset = e.Key switch
                {
                    Key.Left => new Point(-step, 0), Key.Right => new Point(step, 0),
                    Key.Up => new Point(0, -step), _ => new Point(0, step),
                };
                if (selectedBubble != null)
                {
                    var origin = selectedBubble.Snapshot();
                    var next = new Rect(origin.Rect.X + offset.X, origin.Rect.Y + offset.Y, origin.Rect.Width, origin.Rect.Height);
                    var host = panels.FirstOrDefault(p => p.Id == selectedBubble.PanelId);
                    if (host != null) next = ClampInto(next, host.Rect);
                    var after = origin with { Rect = next, Moved = true };
                    history.Push(new GeometryCommand("方向键微调", [new BubbleChange(selectedBubble.Id, origin, after)]));
                    selectedBubble.ApplySnapshot(after);
                    selectedBubble.ApplyPosition(page, animate: true);
                    PositionBubbleHandles(animate: true);
                    MarkDirty();
                    RenderInspector();
                    e.Handled = true;
                }
                else if (selectedSfx != null)
                {
                    var origin = selectedSfx.Snapshot();
                    var after = origin with
                    {
                        X = Math.Clamp(origin.X + offset.X, 0, 1),
                        Y = Math.Clamp(origin.Y + offset.Y, 0, 1),
                        Moved = true,
                    };
                    history.Push(new GeometryCommand("方向键微调", [new SfxChange(selectedSfx.PanelId, selectedSfx.Index, origin, after)]));
                    selectedSfx.ApplySnapshot(after);
                    selectedSfx.ApplyPosition(page, animate: true);
                    MarkDirty();
                    RenderInspector();
                    e.Handled = true;
                }
                else if (selectedIds.Count > 0)
                {
                    // 多选微调（对齐 web keyboardNudge：组内可移动格同向位移一条命令）
                    var changes = new List<GeometryChange>();
                    foreach (var target in MovableSelected())
                    {
                        var next = ClampMove(new Rect(target.Rect.X + offset.X, target.Rect.Y + offset.Y, target.Rect.Width, target.Rect.Height));
                        if (next != target.Rect)
                        {
                            changes.Add(new PanelChange(target.Id, target.Rect, next));
                            target.Rect = next;
                            target.ApplyPosition(page, animate: true);
                        }
                    }
                    if (changes.Count > 0)
                    {
                        history.Push(new GeometryCommand("方向键微调", changes));
                        PositionResizeHandles(ActiveHandleRect(), animate: true);
                        MarkDirty();
                        RenderInspector();
                    }
                    e.Handled = true;
                }
                break;
        }
    }

    private bool AnySelection() => selectedIds.Count > 0 || selectedBubble != null || selectedSfx != null;

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
        fitToViewport = false;
        zoom = Math.Clamp(value, 0.25, 4);
        zoomLabel.Text = $"{(int)Math.Round(zoom * 100)}%";
        UpdatePageSize();
    }

    private void FitViewport()
    {
        var width = Math.Clamp(Math.Min((viewport.ActualWidth - 72) / BasePageWidth, (viewport.ActualHeight - 72) / (BasePageWidth * pageAspect)), 0.25, 4);
        SetZoom(width);
        fitToViewport = true;
    }

    public override Task<bool> ConfirmLeaveAsync()
    {
        // 测试缝：headless 检查替换模态确认；生产路径为 null
        if (LeaveConfirmOverride is { } prompt) return prompt();
        if (!dirty) return Task.FromResult(true);
        var result = new ConfirmDialog(Host, "离开确认", "分镜画布有未保存的几何草稿，离开将丢失。确定离开吗？", "离开").ShowDialog();
        if (result != true) return Task.FromResult(false);
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
    internal int HistoryDepthForTest => history.Depth;
    internal int HistoryNodeCountForTest => history.Nodes.Count;
    /// <summary>回跳到节点（index 为推入序号）或基线（index < 0）。</summary>
    internal void JumpToHistoryForTest(int index) =>
        JumpToHistory(index < 0 ? null : history.Nodes.ElementAtOrDefault(index));
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

    // ── 精准编辑测试缝：全部走生产提交函数（Run*/Commit*/手势 Compute+Commit），
    // 只是入口参数化免真实鼠标设备——与 ResizeViaHandleForTest 同一约定。
    internal IReadOnlyList<string> SelectedPanelIdsForTest => selectedIds.ToList();
    internal int SfxCountForTest => sfxNodes.Count;
    internal string? SelectedSfxTextForTest => selectedSfx?.Text;
    internal int BubbleHandleCountForTest => bubbleHandles.Count;
    internal int GridLineCountForTest => gridLineElements.Count;
    internal bool GroupHandlesForTest => handlesAreGroup;
    internal int GuideCountForTest => page.Children.OfType<Line>().Count(l => l.Tag as string == "guide");
    internal int GapGuideCountForTest => page.Children.OfType<Line>().Count(l => l.Tag as string == "guide-gap");
    internal string LastNoticeForTest => lastNotice;
    internal bool GridVisibleForTest => gridButton.IsChecked == true;
    internal int PanelZOrderForTest(int index) => panels.ElementAtOrDefault(index)?.ZOrder ?? -1;
    internal double PanelRotationForTest(int index) => panels.ElementAtOrDefault(index)?.Rotation ?? double.NaN;
    internal (Rect Rect, double Rotation, Point? Anchor, Point? Tail)? BubbleGeometryForTest(int index) =>
        bubbles.ElementAtOrDefault(index) is { } b ? (b.Rect, b.Rotation, b.Anchor, b.TailTarget) : null;
    internal (double X, double Y, double Rotation, double Size)? SfxGeometryForTest(int index) =>
        sfxNodes.ElementAtOrDefault(index) is { } n ? (n.X, n.Y, n.Rotation, n.Size) : null;

    internal void SelectPanelsForTest(params int[] indexes) =>
        SelectPanels(indexes.Where(i => i >= 0 && i < panels.Count).Select(i => panels[i].Id).ToList());
    internal void SelectSfxForTest(int index)
    {
        if (sfxNodes.ElementAtOrDefault(index) is { } node) SelectSfx(node);
    }
    internal void SetGridForTest(bool visible, int? stepMm = null)
    {
        gridButton.IsChecked = visible;
        if (stepMm is { } step) gridStepMm = step;
        RenderGridOverlay();
    }

    internal void AlignForTest(string mode) => RunAlign(mode);
    internal void DistributeForTest(string axis) => RunDistribute(axis);
    internal void SameSizeForTest(string mode) => RunSameSize(mode);
    internal void ZOrderForTest(string op) => RunZOrder(op);
    internal void CopyGeometryForTest() => CopySelectionGeometry();
    internal bool PasteGeometryForTest() => PasteSelectionGeometry();
    internal void CommitPanelRectForTest(int index, Rect after)
    {
        if (panels.ElementAtOrDefault(index) is { } panel) CommitPanelRect(panel, after);
    }
    internal void CommitBubbleRectForTest(int index, Rect after)
    {
        if (bubbles.ElementAtOrDefault(index) is { } bubble) CommitBubbleRectField(bubble, after);
    }
    internal void CommitBubbleRotationForTest(int index, double degrees)
    {
        if (bubbles.ElementAtOrDefault(index) is { } bubble) CommitBubbleRotationField(bubble, degrees);
    }

    // 命令动效断言缝：元素当前的视觉左缘（动画在途时≠模型落位）与基础值
    // （动画叠层时=旧位，瞬写时=新位）。配合 Rect 模型断言可判定动画叠层/接管。
    // 动画断言缝：GetValueSource.IsAnimated 在属性系统未结算前不可靠，公开可
    // 用的确定性判定是 HasAnimatedProperties（只在「该元素无其他动画」的干净上
    // 下文使用）+ GetAnimationBaseValue（动画叠层时基础值仍停在旧位）。
    internal double PageWidthForTest => page.Width;
    internal double? PanelElementLeftForTest(int index) =>
        panels.ElementAtOrDefault(index) is { } panel ? Canvas.GetLeft(panel.Element) : null;
    internal double? PanelElementBaseLeftForTest(int index) =>
        panels.ElementAtOrDefault(index) is { } panel ? (double)panel.Element.GetAnimationBaseValue(Canvas.LeftProperty) : null;
    internal bool PanelAnimatingForTest(int index) =>
        panels.ElementAtOrDefault(index) is { } panel && panel.Element.HasAnimatedProperties;
    internal int ActiveGuideCountForTest() => guideLines.Count;
    internal int FadingGuideCountForTest() => fadingGuides.Count;

    // 多选拖动（生产 PanelGesture 的 Compute+Commit，注入归一化位移）；
    // keepGuides=true 时保留提交时画的参考线，供检查等距/吸附参考线。
    internal void DragSelectionForTest(double dx, double dy, bool keepGuides = false)
    {
        var movable = MovableSelected();
        if (movable.Count == 0) return;
        var gesture = new PanelGesture(movable) { Offset = new Point(dx, dy) };
        var moved = gesture.Compute(this);
        foreach (var target in movable) target.SetRectDirect(moved[target.Id], page);
        gesture.Commit(this);
        if (!keepGuides) { ClearGuides(); HideSizeLabel(); }
        PositionResizeHandles(ActiveHandleRect());
    }

    // 组缩放（生产 ComputeGroupBox + ScaleRectWithBounds + 同一条提交命令）
    internal void GroupResizeForTest(string handle, Point pointerNormalized, ModifierKeys modifiers = ModifierKeys.None)
    {
        var movable = MovableSelected();
        if (movable.Count < 2 || BoundingBox(movable.Select(p => p.Rect)) is not { } originBox) return;
        var origins = movable.ToDictionary(p => p.Id, p => p.Rect);
        var nextBox = ComputeGroupBox(originBox, handle, pointerNormalized, modifiers == ModifierKeys.Shift);
        var changes = new List<GeometryChange>();
        foreach (var panel in movable)
        {
            var after = ClampRect(ScaleRectWithBounds(origins[panel.Id], originBox, nextBox, MinSize), MinSize);
            if (after != origins[panel.Id]) changes.Add(new PanelChange(panel.Id, origins[panel.Id], after));
            panel.SetRectDirect(after, page);
        }
        if (changes.Count > 0)
        {
            history.Push(new GeometryCommand("缩放多格", changes));
            MarkDirty();
            RenderInspector();
        }
        PositionResizeHandles(nextBox);
        ClearGuides();
        HideSizeLabel();
    }

    // 气泡移动/旋转（生产 CommitBubbleGeometry 提交链）
    internal void MoveBubbleForTest(int index, double dx, double dy)
    {
        if (bubbles.ElementAtOrDefault(index) is not { } bubble) return;
        SelectBubble(bubble);
        var origin = bubble.Snapshot();
        var next = new Rect(origin.Rect.X + dx, origin.Rect.Y + dy, origin.Rect.Width, origin.Rect.Height);
        if (panels.FirstOrDefault(p => p.Id == bubble.PanelId) is { } host) next = ClampInto(next, host.Rect);
        bubble.SetGeometryDirect(origin with { Rect = next, Moved = true }, page);
        CommitBubbleGeometry(bubble, origin, "拖动气泡");
        PositionBubbleHandles();
    }

    internal void RotateBubbleForTest(int index, double degrees)
    {
        if (bubbles.ElementAtOrDefault(index) is not { } bubble) return;
        SelectBubble(bubble);
        var origin = bubble.Snapshot();
        bubble.SetGeometryDirect(origin with { Rotation = NormalizeRotation(degrees), Moved = true }, page);
        CommitBubbleGeometry(bubble, origin, "旋转气泡");
        PositionBubbleHandles();
    }

    // 拟声词三手势（生产 CommitSfxGeometry 提交链）
    internal void MoveSfxForTest(int index, double dx, double dy)
    {
        if (sfxNodes.ElementAtOrDefault(index) is not { } node) return;
        SelectSfx(node);
        var origin = node.Snapshot();
        node.SetGeometryDirect(origin with
        {
            X = Math.Clamp(origin.X + dx, 0, 1), Y = Math.Clamp(origin.Y + dy, 0, 1), Moved = true,
        }, page);
        CommitSfxGeometry(node, origin, "移动拟声词");
    }

    internal void ScaleSfxForTest(int index, double sizeDelta)
    {
        if (sfxNodes.ElementAtOrDefault(index) is not { } node) return;
        SelectSfx(node);
        var origin = node.Snapshot();
        node.SetGeometryDirect(origin with { Size = Math.Clamp(origin.Size + sizeDelta, MinSfx, 0.2), Moved = true }, page);
        CommitSfxGeometry(node, origin, "缩放拟声词");
    }

    internal void RotateSfxForTest(int index, double degrees)
    {
        if (sfxNodes.ElementAtOrDefault(index) is not { } node) return;
        SelectSfx(node);
        var origin = node.Snapshot();
        node.SetGeometryDirect(origin with { Rotation = NormalizeRotation(degrees), Moved = true }, page);
        CommitSfxGeometry(node, origin, "旋转拟声词");
    }

    // ============ Geometry node model ============
    private sealed class PanelNode
    {
        public required string Id { get; init; }
        public Rect Rect { get; set; }
        public int ReadingOrder { get; init; }
        // z_order 可被层序命令改写并随 geometry.z_order 落库（web panelMetaDrafts 同源）
        public int ZOrder { get; set; }
        // 面板 rotation 只透传（web meta.rotation 同款：画布暂无面板旋转手柄）
        public double Rotation { get; init; }
        public int Version { get; init; }
        // PanelRead 没有 per-panel action 字段；「动作与表演」实际存放在 actions.script_action
        public string ScriptAction { get; init; } = "";
        public string ShotType { get; init; } = "";
        public string CameraAngle { get; init; } = "";
        public bool Bleed { get; init; }
        public bool Borderless { get; init; }
        public List<JsonElement> Dialogues { get; init; } = [];
        // 拟声词原始条目（字符串或带 x/y/rotation/size 的对象；对齐 web panel.sound_effects）
        public List<JsonElement> SoundEffects { get; init; } = [];
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
                SoundEffects = row.Array("sound_effects"),
                Rotation = geometry.Decimal("rotation"),
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

        public void ApplyPosition(Canvas canvas, bool animate = false)
        {
            AnimateOrSet(Element, Canvas.LeftProperty, Rect.X * canvas.Width, animate);
            AnimateOrSet(Element, Canvas.TopProperty, Rect.Y * canvas.Height, animate);
            AnimateOrSet(Element, WidthProperty, Math.Max(4, Rect.Width * canvas.Width), animate);
            AnimateOrSet(Element, HeightProperty, Math.Max(4, Rect.Height * canvas.Height), animate);
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
        // web BubbleGeometryShape 的其余几何字段：rotation（度）、anchor（尾巴锚点
        // 归一化坐标）、tail_target（终点）；只在存储形状带字段时挂对应手柄。
        public double Rotation { get; set; }
        public Point? Anchor { get; set; }
        public Point? TailTarget { get; set; }
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
            Point? ReadPoint(string name)
            {
                var point = shape.Element(name);
                return point.ValueKind == JsonValueKind.Object
                    ? new Point(point.Decimal("x"), point.Decimal("y"))
                    : null;
            }
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
                Rotation = shape.Decimal("rotation"),
                Anchor = ReadPoint("anchor"),
                TailTarget = ReadPoint("tail_target"),
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

        public void ApplyPosition(Canvas canvas, bool animate = false)
        {
            AnimateOrSet(Element, Canvas.LeftProperty, Rect.X * canvas.Width, animate);
            AnimateOrSet(Element, Canvas.TopProperty, Rect.Y * canvas.Height, animate);
            AnimateOrSet(Element, WidthProperty, Math.Max(12, Rect.Width * canvas.Width), animate);
            AnimateOrSet(Element, HeightProperty, Math.Max(12, Rect.Height * canvas.Height), animate);
            Panel.SetZIndex(Element, 20);
            // 旋转只改视觉不改布局（对齐 web transform: rotate(deg) 绕中心）
            Element.RenderTransformOrigin = new Point(0.5, 0.5);
            ApplyRotation(Element, Rotation, animate);
        }

        public void SetRectDirect(Rect rect, Canvas canvas)
        {
            Rect = rect;
            Moved = true;
            ApplyPosition(canvas);
        }

        // 几何快照/回滚（撤销栈的 BubbleChange 用）：Rect+Rotation+Anchor+TailTarget+Moved
        public BubbleGeometry Snapshot() => new(Rect, Rotation, Anchor, TailTarget, Moved);

        public void ApplySnapshot(BubbleGeometry geometry)
        {
            Rect = geometry.Rect;
            Rotation = geometry.Rotation;
            Anchor = geometry.Anchor;
            TailTarget = geometry.TailTarget;
            Moved = geometry.Moved;
        }

        public void SetGeometryDirect(BubbleGeometry geometry, Canvas canvas)
        {
            ApplySnapshot(geometry);
            ApplyPosition(canvas);
        }

        // 提交时以服务端存储形状为底，更新 rect/rotation/anchor/tail_target 并剥离
        // 服务端专有键；移动过的气泡丢弃旧的 text_region（服务端要求它位于新 rect 内）。
        public object BuildPayloadBubble()
        {
            if (StoredBubble.ValueKind != JsonValueKind.Object)
                return new { type = Shape, rect = Round4(Rect), rotation = Rotation };
            var payload = JsonSerializer.Deserialize<Dictionary<string, object?>>(StoredBubble.GetRawText()) ?? [];
            var storedRect = StoredBubble.Element("rect");
            var moved = Math.Round(storedRect.Decimal("x"), 4) != Math.Round(Rect.X, 4)
                || Math.Round(storedRect.Decimal("y"), 4) != Math.Round(Rect.Y, 4)
                || Math.Round(storedRect.Decimal("width"), 4) != Math.Round(Rect.Width, 4)
                || Math.Round(storedRect.Decimal("height"), 4) != Math.Round(Rect.Height, 4);
            payload.Remove("mapped_from_legacy");
            payload["rect"] = Round4(Rect);
            payload["rotation"] = Math.Round(Rotation, 4);
            if (Anchor is { } anchor)
                payload["anchor"] = new Dictionary<string, double> { ["x"] = Math.Round(anchor.X, 4), ["y"] = Math.Round(anchor.Y, 4) };
            else payload.Remove("anchor");
            if (TailTarget is { } tail)
                payload["tail_target"] = new Dictionary<string, double> { ["x"] = Math.Round(tail.X, 4), ["y"] = Math.Round(tail.Y, 4) };
            else payload.Remove("tail_target");
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

    // 拟声词画布节点（对齐 web sfx-node + contract §12）：x/y 是归一化中心点，
    // rotation 度、size 归一化字号（相对页高）。字符串条目无几何字段，首次
    // 拖动才落结构化几何（Moved）——保存时经面板叙事 PATCH 回写。
    private sealed class SfxNode
    {
        public required string PanelId { get; init; }
        public required int Index { get; init; }      // sound_effects 数组下标（PATCH 锚定用）
        public required string Text { get; init; }
        public JsonElement StoredEntry { get; init; } // 字符串条目为 default
        public double X { get; set; }
        public double Y { get; set; }
        public double Rotation { get; set; }
        public double Size { get; set; }
        public bool Moved { get; set; }
        public required Grid Element { get; init; }   // 0×0 定位壳：子元素以中心对齐锚定点
        public FrameworkElement HitArea { get; private set; } = null!;
        private TextBlock label = null!;
        private Border dash = null!;
        private Border rotateHandle = null!;
        private Border scaleHandle = null!;
        private double fontPx = 12;

        public static SfxNode? From(JsonElement entry, PanelNode panel, int index)
        {
            var isObject = entry.ValueKind == JsonValueKind.Object;
            var text = isObject ? entry.Text("text") : entry.ToString();
            if (text.Length == 0) return null;
            // 缺省落位（对齐 web defaultSfxPosition）：按所属格内网格排布
            var fallbackX = Math.Clamp(panel.Rect.X + panel.Rect.Width * (0.5 + (index % 3 - 1) * 0.2), 0, 1);
            var fallbackY = Math.Clamp(panel.Rect.Y + panel.Rect.Height * (0.3 + index / 3 * 0.2), 0, 1);
            var size = isObject && entry.Decimal("size") > 0 ? entry.Decimal("size") : 0.05;
            var node = new SfxNode
            {
                PanelId = panel.Id,
                Index = index,
                Text = text,
                StoredEntry = isObject ? entry : default,
                X = isObject && entry.Element("x").ValueKind == JsonValueKind.Number ? entry.Decimal("x") : fallbackX,
                Y = isObject && entry.Element("y").ValueKind == JsonValueKind.Number ? entry.Decimal("y") : fallbackY,
                Rotation = isObject ? entry.Decimal("rotation") : 0,
                Size = size,
                // Moved = 本会话手势过的草稿标记（对齐 web sfxDrafts）：存储几何
                // 本身不算草稿——PATCH 脏判定只看 Moved，撤销回存储态即脱草稿。
                Moved = false,
                Element = new Grid { Width = 0, Height = 0, RenderTransformOrigin = new Point(0.5, 0.5) },
            };
            var cell = new Grid { HorizontalAlignment = HorizontalAlignment.Center, VerticalAlignment = VerticalAlignment.Center };
            var dash = new Border
            {
                BorderBrush = new SolidColorBrush(Color.FromRgb(0xC0, 0x5A, 0x44)),
                BorderThickness = new Thickness(1.5), CornerRadius = new CornerRadius(2),
                Visibility = Visibility.Collapsed, IsHitTestVisible = false,
            };
            node.label = new TextBlock
            {
                Text = text, FontWeight = FontWeights.Black, Margin = new Thickness(3),
                Foreground = new SolidColorBrush(Color.FromRgb(0x26, 0x20, 0x19)),
                // web .canvas-sfx-text 的白色描边近似
                Effect = new System.Windows.Media.Effects.DropShadowEffect { Color = Colors.White, BlurRadius = 1, ShadowDepth = 0, Opacity = 0.9 },
            };
            cell.Children.Add(dash);
            cell.Children.Add(node.label);
            node.Element.Children.Add(cell);
            node.HitArea = cell;
            cell.Cursor = Cursors.Hand;
            node.dash = dash;
            node.rotateHandle = node.MakeHandle("#3A7A98", "rotate");
            node.scaleHandle = node.MakeHandle(null, "scale");
            return node;
        }

        private Border MakeHandle(string? color, string kind)
        {
            var handle = new Border
            {
                Width = 14, Height = 14, CornerRadius = new CornerRadius(999),
                Background = color != null ? new SolidColorBrush((Color)ColorConverter.ConvertFromString(color)) : (Brush)Application.Current.FindResource("Accent"),
                BorderBrush = Brushes.White, BorderThickness = new Thickness(2),
                Visibility = Visibility.Collapsed, Tag = "sfx-handle:" + kind,
                HorizontalAlignment = HorizontalAlignment.Center, VerticalAlignment = VerticalAlignment.Center,
                Cursor = kind == "rotate" ? Cursors.Hand : Cursors.SizeNWSE,
                IsHitTestVisible = false,
            };
            Element.Children.Add(handle);
            return handle;
        }

        // 手柄需要视图侧手势：把事件转发挂接点暴露出来（保持节点私有构造干净）
        public Border RotateHandle => rotateHandle;
        public Border ScaleHandle => scaleHandle;

        public SfxGeometry Snapshot() => new(X, Y, Rotation, Size, Moved);

        public void ApplySnapshot(SfxGeometry geometry)
        {
            X = geometry.X; Y = geometry.Y; Rotation = geometry.Rotation; Size = geometry.Size; Moved = geometry.Moved;
        }

        public void SetGeometryDirect(SfxGeometry geometry, Canvas canvas)
        {
            ApplySnapshot(geometry);
            ApplyPosition(canvas);
        }

        public void ApplyPosition(Canvas canvas, bool animate = false)
        {
            AnimateOrSet(Element, Canvas.LeftProperty, X * canvas.Width, animate);
            AnimateOrSet(Element, Canvas.TopProperty, Y * canvas.Height, animate);
            fontPx = Math.Max(Size * canvas.Height, 6);
            AnimateOrSet(label, TextBlock.FontSizeProperty, fontPx, animate);
            ApplyRotation(Element, Rotation, animate);
            // 手柄在自身（已旋转的）坐标系内偏移，与 web 的 left/top em 定位同源：
            // rotate 在中心上方 1.6em、scale 在右下 (1.2em, 0.9em)。
            rotateHandle.RenderTransform = new TranslateTransform(0, -1.6 * fontPx);
            scaleHandle.RenderTransform = new TranslateTransform(1.2 * fontPx, 0.9 * fontPx);
            Panel.SetZIndex(Element, 22);
        }

        public void SetSelected(bool isSelected)
        {
            dash.Visibility = isSelected ? Visibility.Visible : Visibility.Collapsed;
            foreach (var handle in new[] { rotateHandle, scaleHandle })
            {
                handle.Visibility = isSelected ? Visibility.Visible : Visibility.Collapsed;
                handle.IsHitTestVisible = isSelected;
            }
        }

        // 保存时的单条目合并（对齐 web sfxPatches 的 base/draft 合并）：对象条目
        // 原样打底；字符串条目升格为 { text }。只有手势过（Moved）的条目才覆写
        // 几何四字段——未动条目保持存储内容，不凭空虚增 x/y。
        public Dictionary<string, object?> BuildMergedEntry()
        {
            var payload = StoredEntry.ValueKind == JsonValueKind.Object
                ? JsonSerializer.Deserialize<Dictionary<string, object?>>(StoredEntry.GetRawText()) ?? []
                : new Dictionary<string, object?> { ["text"] = Text };
            if (Moved)
            {
                payload["x"] = Math.Round(X, 4);
                payload["y"] = Math.Round(Y, 4);
                payload["rotation"] = Math.Round(Rotation, 4);
                payload["size"] = Math.Round(Size, 4);
            }
            return payload;
        }
    }

    // 多选移动手势（对齐 web computeResult("move-panels")）：先按 Offset 平移
    // 全部成员，吸附只作用于主格（Group[0]），其余成员跟随同一修正量；
    // 等间距吸附只服务单格移动（多选时组内格互为干扰，web ids.length==1 同款）。
    private sealed class PanelGesture(List<PanelNode> group)
    {
        public List<PanelNode> Group { get; } = group;
        public Dictionary<string, Rect> Origins { get; } = group.ToDictionary(g => g.Id, g => g.Rect);
        public Point Offset { get; set; }

        // 计算最终落位并顺带画参考线/尺寸标签（moved 与 Commit 共用同一份结果，
        // 保证画布上所画即所存）。
        public Dictionary<string, Rect> Compute(StoryboardView view)
        {
            var primary = Group[0];
            var clampedPrimary = view.ClampMove(new Rect(
                Origins[primary.Id].X + Offset.X, Origins[primary.Id].Y + Offset.Y,
                Origins[primary.Id].Width, Origins[primary.Id].Height));
            var fix = new Point(clampedPrimary.X - (Origins[primary.Id].X + Offset.X),
                clampedPrimary.Y - (Origins[primary.Id].Y + Offset.Y));
            var gapGuides = new List<(double At, bool Vertical)>();
            if (view.snapButton.IsChecked == true)
            {
                var xs = new List<double>(); var ys = new List<double>();
                // 边吸附只排除主格自身（对齐 web otherPanelRects([primaryId])）：
                // 组内其他成员仍在原位，作吸附目标保住旧测试语义。
                view.CollectSnapTargets(primary, xs, ys);
                var bestX = BestDelta([clampedPrimary.X, clampedPrimary.X + clampedPrimary.Width / 2, clampedPrimary.Right], xs, clampedPrimary.X);
                var bestY = BestDelta([clampedPrimary.Y, clampedPrimary.Y + clampedPrimary.Height / 2, clampedPrimary.Bottom], ys, clampedPrimary.Y);
                var threshold = view.SnapThresholdPx();
                var snappedX = Math.Abs(bestX.Delta) <= threshold;
                var snappedY = Math.Abs(bestY.Delta) <= threshold;
                var snapped = new Rect(clampedPrimary.X + (snappedX ? bestX.Delta : 0),
                    clampedPrimary.Y + (snappedY ? bestY.Delta : 0), clampedPrimary.Width, clampedPrimary.Height);
                var gapX = 0.0; var gapY = 0.0;
                if (Group.Count == 1)
                {
                    var others = view.panels.Where(p => !ReferenceEquals(p, primary)).Select(p => p.Rect).ToList();
                    if (!snappedX) gapX = view.EqualGapDelta(snapped, others, vertical: true, gapGuides);
                    if (!snappedY) gapY = view.EqualGapDelta(snapped, others, vertical: false, gapGuides);
                }
                if (snappedX) fix.X += bestX.Delta; else fix.X += gapX;
                if (snappedY) fix.Y += bestY.Delta; else fix.Y += gapY;
                view.ClearGuides();
                if (snappedX) view.ShowGuide(bestX.Guide, true);
                if (snappedY) view.ShowGuide(bestY.Guide, false);
                foreach (var (at, vertical) in gapGuides) view.ShowGuide(at, vertical, gap: true);
            }
            // 落位保持全精度（web move 分支不 round）——浮点尾差在 Commit 用
            // 1e-9 epsilon 消掉，不向用户几何里写量化值。
            var result = Group.ToDictionary(p => p.Id, p => view.ClampMove(new Rect(
                Origins[p.Id].X + Offset.X + fix.X, Origins[p.Id].Y + Offset.Y + fix.Y,
                Origins[p.Id].Width, Origins[p.Id].Height)));
            var labelRect = Group.Count > 1 && BoundingBox(result.Values) is { } box ? box : result[primary.Id];
            view.ShowSizeLabel(view.RectSizeLabel(labelRect), labelRect.Right, labelRect.Bottom);
            return result;
        }

        public void Commit(StoryboardView view)
        {
            var result = Compute(view);
            var changes = new List<GeometryChange>();
            foreach (var target in Group)
            {
                var before = Origins[target.Id];
                var next = result[target.Id];
                // 吸附/等距修正的浮点尾差（~1e-17）会让「吸回原位」误判成有变化；
                // epsilon 比较只挡亚像素级幽灵位移，真实手势最小步长 >> 1e-9。
                if (!NearlyEqual(next, before))
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
            view.RenderInspector();
        }

        private static bool NearlyEqual(Rect a, Rect b) =>
            Math.Abs(a.X - b.X) < 1e-9 && Math.Abs(a.Y - b.Y) < 1e-9
            && Math.Abs(a.Width - b.Width) < 1e-9 && Math.Abs(a.Height - b.Height) < 1e-9;

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

    // 移动 clamp（对齐 web translateRect 的页边界约束，但带上宽高感知——web
    // translateRect 只夹 x/y 原点，本实现沿用原生既有的右/下界保护）。
    private Rect ClampMove(Rect rect) => new(
        Math.Clamp(rect.X, 0, 1 - Math.Min(rect.Width, 1)),
        Math.Clamp(rect.Y, 0, 1 - Math.Min(rect.Height, 1)),
        rect.Width, rect.Height);

    private sealed record GeometryCommand(string Label, List<GeometryChange> Changes);
    private abstract record GeometryChange(string Id);
    private sealed record PanelChange(string Id, Rect Before, Rect After) : GeometryChange(Id);
    // 气泡几何快照（对齐 web bubble.shape 的 rect+rotation+anchor+tail_target 四元组；
    // Moved 记录是否已定位——撤销回到未定位态时 payload 重新发 null）。
    private sealed record BubbleGeometry(Rect Rect, double Rotation, Point? Anchor, Point? TailTarget, bool Moved);
    private sealed record BubbleChange(string Id, BubbleGeometry Before, BubbleGeometry After) : GeometryChange(Id);
    private sealed record PanelZChange(string Id, int Before, int After) : GeometryChange(Id);
    // 拟声词快照（对齐 web sfxGeometryPatch 的 x/y/rotation/size 四元组 + Moved 定位语义）
    private sealed record SfxGeometry(double X, double Y, double Rotation, double Size, bool Moved);
    private sealed record SfxChange(string PanelId, int Index, SfxGeometry Before, SfxGeometry After) : GeometryChange(PanelId);

    // 对白草稿（对齐 web DialogueDraft 的四个字段；保存载荷 = 草稿 + panel_version）
    private sealed record DialogueDraft(string TargetText, string? SpeakerCharacterId, string TextDirection, bool RewriteForbidden)
    {
        public static DialogueDraft From(JsonElement dialogue) => new(
            dialogue.Text("target_text"),
            dialogue.TextOrNull("speaker_character_id"),
            dialogue.Text("text_direction") == "horizontal" ? "horizontal" : "vertical",
            dialogue.Element("rewrite_forbidden").ValueKind != JsonValueKind.False);   // 缺省视为锁定
    }

    // 历史是追加式节点树而非线性栈：撤销后再编辑向被撤销的节点追加一个子
    // 节点（分叉），旧的重做尾巴不被截断，任何节点都能回跳。LastChild 记
    // 录每个节点上次离开的方向，重做沿用户来时的分支走（对齐 web
    // command-stack.ts 的 nodes/activeId/lastChildId）。
    private sealed class CommandStack
    {
        internal sealed class Node
        {
            public required GeometryCommand Command { get; init; }
            public required Node? Parent { get; init; }
            public Node? LastChild { get; set; }
        }

        private readonly List<Node> nodes = [];
        private Node? active;
        private Node? rootLastChild;

        public Node? Active => active;
        /// <summary>推入顺序的全量节点（历史树与测试缝共用）。</summary>
        public IReadOnlyList<Node> Nodes => nodes;
        public int Depth => PathFrom(active).Count;
        public bool CanUndo => active != null;
        public bool CanRedo => ChildOf(active) != null;

        // V02-33 回放帧标签：Push 先于应用发生，标签挂起到 UpdatePageSize
        // 落点后由 RecordFrame 消费；空变更（Changes.Count==0）不推不录。
        public Action<GeometryCommand>? OnPush;

        public void Push(GeometryCommand command)
        {
            if (command.Changes.Count == 0) return;
            var node = new Node { Command = command, Parent = active };
            if (active != null) active.LastChild = node;
            else rootLastChild = node;
            nodes.Add(node);
            active = node;
            OnPush?.Invoke(command);
        }

        public GeometryCommand Undo()
        {
            var node = active!;
            if (node.Parent != null) node.Parent.LastChild = node;
            else rootLastChild = node;
            active = node.Parent;
            return node.Command;
        }

        public GeometryCommand Redo()
        {
            var node = ChildOf(active)!;
            active = node;
            return node.Command;
        }

        public void Clear()
        {
            nodes.Clear();
            active = null;
            rootLastChild = null;
        }

        private Node? ChildOf(Node? parent) =>
            (parent != null ? parent.LastChild : rootLastChild)
            ?? nodes.LastOrDefault(n => n.Parent == parent);

        /// <summary>节点是否在「基线 → 当前活动节点」的主链上（历史树区分
        /// 主链节点与 ↳ 分支节点的依据）。</summary>
        public bool IsOnPath(Node node)
        {
            for (var cur = active; cur != null; cur = cur.Parent)
                if (cur == node) return true;
            return false;
        }

        public List<Node> ChildrenOf(Node? parent) => nodes.Where(n => n.Parent == parent).ToList();

        private static List<Node> PathFrom(Node? tip)
        {
            var path = new List<Node>();
            for (var cur = tip; cur != null; cur = cur.Parent) path.Insert(0, cur);
            return path;
        }

        /// <summary>回跳计划：undos 段从活动点上行到公共祖先（应用 Before），
        /// redos 段下行到目标（应用 After）；执行后 SetActive 落定。</summary>
        public (List<GeometryCommand> Undos, List<GeometryCommand> Redos) PlanTo(Node? target)
        {
            var activeChain = PathFrom(active);
            var targetChain = PathFrom(target);
            var shared = 0;
            while (shared < Math.Min(activeChain.Count, targetChain.Count)
                && activeChain[shared] == targetChain[shared]) shared++;
            foreach (var node in activeChain.Skip(shared))
                if (node.Parent != null) node.Parent.LastChild = node;
                else rootLastChild = node;
            foreach (var node in targetChain.Skip(shared))
                if (node.Parent != null) node.Parent.LastChild = node;
                else rootLastChild = node;
            return (activeChain.Skip(shared).Reverse().Select(n => n.Command).ToList(),
                targetChain.Skip(shared).Select(n => n.Command).ToList());
        }

        public void SetActive(Node? target) => active = target;
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
        // 拟声词几何锚定基线（对齐 web anchorSoundEffects）：文本列表重建时未变
        // 条目保留原 x/y/rotation/size，改动条目按同索引位置沿用几何。
        var storedSfx = new List<JsonElement>();

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
                    ["sound_effects"] = AnchorSoundEffects(SplitList(soundEffects.Text), storedSfx),
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
            // 结构化条目只显示 text 字段（Strings 会把对象序列化成 JSON 原文）
            soundEffects.Text = string.Join("、", row.Array("sound_effects").Select(e => e.ValueKind == JsonValueKind.Object ? e.Text("text") : e.ToString()));
            storedSfx = row.Array("sound_effects");
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

    // web anchorSoundEffects 的直译（几何按文本锚定，internal 供离线回归直接驱动）：
    // 1) 未变文本从左到右精确匹配消耗；2) 改名/新增回退同索引位置的原对象
    //    沿用几何；3) 其余落成裸 {text}（旧字符串条目一并规范化为对象）。
    internal static List<Dictionary<string, object?>> AnchorSoundEffects(List<string> texts, List<JsonElement> previous)
    {
        var available = previous.Select((entry, index) => (entry, index, used: false))
            .Where(slot => slot.entry.ValueKind == JsonValueKind.Object).ToList();
        var matched = texts.Select(text =>
        {
            var hitIndex = available.FindIndex(slot => !slot.used && slot.entry.Text("text") == text);
            if (hitIndex < 0) return (Dictionary<string, object?>?)null;
            var hit = available[hitIndex];
            available[hitIndex] = hit with { used = true };
            var payload = JsonSerializer.Deserialize<Dictionary<string, object?>>(hit.entry.GetRawText()) ?? [];
            payload["text"] = text;
            return payload;
        }).ToList();
        return matched.Select((entry, index) =>
        {
            if (entry != null) return entry;
            var slotIndex = available.FindIndex(slot => slot.index == index && !slot.used);
            if (slotIndex >= 0)
            {
                var positional = available[slotIndex];
                available[slotIndex] = positional with { used = true };
                var payload = JsonSerializer.Deserialize<Dictionary<string, object?>>(positional.entry.GetRawText()) ?? [];
                payload["text"] = texts[index];
                return payload;
            }
            return new Dictionary<string, object?> { ["text"] = texts[index] };
        }).ToList();
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
