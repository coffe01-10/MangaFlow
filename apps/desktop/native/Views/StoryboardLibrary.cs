using System.IO;
using System.Text.Json;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Controls.Primitives;
using System.Windows.Input;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using System.Windows.Shapes;
using System.Windows.Threading;
using MangaFlow.Native.Controls;
using MangaFlow.Native.Services;

namespace MangaFlow.Native.Views;

/// <summary>
/// Canvas library features (V02-33): layout templates, named version
/// snapshots with ghost compare, and session replay — the native counterpart
/// of web storyboard-history.ts. Same model: a CanvasState captures the
/// resolved geometry of every node (node values ARE the effective state),
/// template/snapshot application lands as ordinary undoable commands, and
/// replay frames are recorded along the command history, played back through
/// the same 170ms command transitions.
/// </summary>
public sealed partial class StoryboardView
{
    // 存储键与 web 同名（客户端本地存储：web=localStorage，原生=KeyValueStore）
    private const string TemplatesKey = "mangaflow.storyboard-templates";
    private static string SnapshotsKey(string pageId) => $"mangaflow.storyboard-snapshots.{pageId}";
    private static string AnnotationsKey(string pageId) => $"mangaflow.storyboard-annotations.{pageId}";

    // ---- 状态模型（对齐 web CanvasState：round4 归一化的有效几何）------------
    private sealed record PanelState(Rect Rect, int ZOrder);
    private sealed record CanvasState(
        Dictionary<string, PanelState> Panels,
        Dictionary<string, BubbleGeometry> Bubbles,
        Dictionary<string, Dictionary<int, SfxGeometry>> Sfx);
    private sealed record ReplayEntry(string Label, CanvasState State);
    private sealed record LayoutTemplate(string Id, string Name, bool BuiltIn, List<Rect> Cells, long CreatedAt);
    private sealed record StoryboardSnapshot(string Id, string Name, long CreatedAt, CanvasState State);

    // ---- 序列化 DTO（Rect/Point 不直接进 JSON，用紧凑 DTO）-------------------
    private sealed record RectDto(double X, double Y, double W, double H);
    private sealed record PointDto(double X, double Y);
    private sealed record TemplateDto(string Id, string Name, List<RectDto> Cells, long CreatedAt);
    private sealed record PanelStateDto(RectDto Rect, int ZOrder);
    private sealed record BubbleStateDto(RectDto Rect, double Rotation, PointDto? Anchor, PointDto? TailTarget, bool Moved);
    private sealed record SfxStateDto(double X, double Y, double Rotation, double Size, bool Moved);
    private sealed record CanvasStateDto(
        Dictionary<string, PanelStateDto> Panels,
        Dictionary<string, BubbleStateDto> Bubbles,
        Dictionary<string, Dictionary<int, SfxStateDto>> Sfx);
    private sealed record SnapshotDto(string Id, string Name, long CreatedAt, CanvasStateDto State);
    private sealed record StrokeDto(string Id, List<PointDto> Points);
    private sealed record AnnotationStroke(string Id, List<Point> Points);   // 归一化 0-1 点列

    // ---- 回放时间线：每个已应用命令后的完整状态 ------------------------------
    // pendingCommandLabel 由 CommandStack.OnPush 供标签（Push 先于应用），
    // UpdatePageSize 收尾时 RecordFrame 消费并录制；无几何变化的调用被
    // SameCanvasState 去重。切页/清栈时 timelinePageId 变化触发重基线。
    private readonly List<ReplayEntry> timeline = [];
    private string? timelinePageId, pendingCommandLabel;
    private bool replayOpen, replayPlaying;
    private int replayIndex;
    private double replaySpeed = 1;
    private CanvasState? replayReturnState;
    private DispatcherTimer? replayTimer;
    private Border replayBarElement = new() { Visibility = Visibility.Collapsed };
    private Slider replaySlider = new();
    private TextBlock replayPosition = new();
    private TextBlock replayLabel = new();
    private Button replayPlayButton = new();
    private ComboBox replaySpeedBox = new();

    // ---- 快照对比幽灵 --------------------------------------------------------
    private string? compareSnapshotId;
    private CanvasState? compareState;
    // A/B 版式对比：两份快照各标一侧（A 紫 / B 青），选择条采用其一为普通命令。
    private string? compareSideAId, compareSideBId;
    private Border compareBarElement = new() { Visibility = Visibility.Collapsed };
    private TextBlock compareBarText = new();
    private readonly List<FrameworkElement> ghostElements = [];

    private List<StoryboardSnapshot> snapshots = [];

    // ---- 手绘批注层 --------------------------------------------------------
    private bool annotating;
    private List<AnnotationStroke> annotationStrokes = [];
    private readonly List<FrameworkElement> annotationElements = [];
    private Polyline? annotationDraft;
    private List<Point>? annotationDraftNorm;

    // ============================ 状态捕获与 diff ==============================

    private CanvasState CaptureCanvasState() => new(
        panels.ToDictionary(p => p.Id, p => new PanelState(p.Rect, p.ZOrder)),
        bubbles.ToDictionary(b => b.Id, b => b.Snapshot()),
        sfxNodes.GroupBy(n => n.PanelId)
            .ToDictionary(g => g.Key, g => g.ToDictionary(n => n.Index, n => n.Snapshot())));

    private static bool SameDict<K, V>(Dictionary<K, V> a, Dictionary<K, V> b) where K : notnull =>
        a.Count == b.Count && a.All(kv => b.TryGetValue(kv.Key, out var other) && EqualityComparer<V>.Default.Equals(kv.Value, other));

    private static bool SameCanvasState(CanvasState a, CanvasState b) =>
        SameDict(a.Panels, b.Panels) && SameDict(a.Bubbles, b.Bubbles)
        && SameDict(a.Sfx, b.Sfx, (x, y) => SameDict(x, y));

    private static bool SameDict<K, V>(Dictionary<K, V> a, Dictionary<K, V> b, Func<V, V, bool> equal) where K : notnull =>
        a.Count == b.Count && a.All(kv => b.TryGetValue(kv.Key, out var other) && equal(kv.Value, other));

    /// <summary>Diff two states into one undoable command's change list —
    /// snapshot restore and (via RoundFrame) replay share this path. Nodes
    /// absent on either side keep their current value.</summary>
    private static List<GeometryChange> StateChanges(CanvasState before, CanvasState after)
    {
        var changes = new List<GeometryChange>();
        foreach (var (id, next) in after.Panels)
        {
            if (!before.Panels.TryGetValue(id, out var prev)) continue;
            if (prev.Rect != next.Rect) changes.Add(new PanelChange(id, prev.Rect, next.Rect));
            if (prev.ZOrder != next.ZOrder) changes.Add(new PanelZChange(id, prev.ZOrder, next.ZOrder));
        }
        foreach (var (id, next) in after.Bubbles)
        {
            if (before.Bubbles.TryGetValue(id, out var prev) && prev != next)
                changes.Add(new BubbleChange(id, prev, next));
        }
        foreach (var (panelId, byIndex) in after.Sfx)
        {
            foreach (var (index, next) in byIndex)
            {
                if (before.Sfx.TryGetValue(panelId, out var prevMap)
                    && prevMap.TryGetValue(index, out var prev) && prev != next)
                    changes.Add(new SfxChange(panelId, index, prev, next));
            }
        }
        return changes;
    }

    /// <summary>UpdatePageSize 收尾挂点：所有几何落地（命令/手势提交/撤销/
    /// 重载）都经过这里。回放期间不录制——回放写回节点本身不能再产帧。</summary>
    private void RecordFrame()
    {
        if (replayOpen || currentPage == null) return;
        var label = pendingCommandLabel;
        pendingCommandLabel = null;
        var state = CaptureCanvasState();
        if (timelinePageId != currentPage.Id)
        {
            timelinePageId = currentPage.Id;
            timeline.Clear();
            timeline.Add(new ReplayEntry("初始状态", state));
            return;
        }
        if (timeline.Count == 0 || !SameCanvasState(timeline[^1].State, state))
            timeline.Add(new ReplayEntry(label ?? "外部更新", state));
    }

    private void ResetTimeline()
    {
        timeline.Clear();
        timelinePageId = null;
        pendingCommandLabel = null;
    }

    // ============================ 布局模板 ===================================

    private static Rect R(double x, double y, double w, double h) => new(x, y, w, h);

    // 内置预设与 web BUILT_IN_TEMPLATES 一一对应（归一化坐标）。
    private static readonly LayoutTemplate[] BuiltInTemplates =
    [
        new("preset-yon-koma", "标准四格", true,
            [R(0.06, 0.04, 0.42, 0.44), R(0.52, 0.04, 0.42, 0.44), R(0.06, 0.52, 0.42, 0.44), R(0.52, 0.52, 0.42, 0.44)], 0),
        new("preset-three-row", "三行竖排", true,
            [R(0.08, 0.05, 0.84, 0.27), R(0.08, 0.365, 0.84, 0.27), R(0.08, 0.68, 0.84, 0.27)], 0),
        new("preset-big-two-small", "一大两小", true,
            [R(0.06, 0.04, 0.88, 0.52), R(0.06, 0.6, 0.43, 0.36), R(0.51, 0.6, 0.43, 0.36)], 0),
        new("preset-hero-side", "主格加副格", true,
            [R(0.06, 0.04, 0.6, 0.92), R(0.7, 0.04, 0.24, 0.44), R(0.7, 0.52, 0.24, 0.44)], 0),
        new("preset-two-column", "左右对开", true,
            [R(0.06, 0.06, 0.42, 0.88), R(0.52, 0.06, 0.42, 0.88)], 0),
        new("preset-six-grid", "六格网格", true,
            [R(0.06, 0.04, 0.42, 0.29), R(0.52, 0.04, 0.42, 0.29), R(0.06, 0.355, 0.42, 0.29),
             R(0.52, 0.355, 0.42, 0.29), R(0.06, 0.67, 0.42, 0.29), R(0.52, 0.67, 0.42, 0.29)], 0),
    ];

    private static readonly Rect FullPage = new(0, 0, 1, 1);

    private static Rect MapRectThrough(Rect rect, Rect from, Rect to) => new(
        to.X + (rect.X - from.X) * (to.Width / from.Width),
        to.Y + (rect.Y - from.Y) * (to.Height / from.Height),
        rect.Width * to.Width / from.Width,
        rect.Height * to.Height / from.Height);

    private static Point MapPointThrough(Point point, Rect from, Rect to) => new(
        Math.Clamp(to.X + (point.X - from.X) * (to.Width / from.Width), 0, 1),
        Math.Clamp(to.Y + (point.Y - from.Y) * (to.Height / from.Height), 0, 1));

    /// <summary>Reading-order pairing: panel[i] → cells[i]; bubbles and sound
    /// effects follow their host panel through the same affine map (web
    /// templateChanges 移植）。Extra panels/cells stay untouched.</summary>
    private static List<GeometryChange> TemplateChanges(
        List<PanelNode> ordered, List<BubbleNode> bubbleNodes, List<SfxNode> sfx, IReadOnlyList<Rect> cells)
    {
        var changes = new List<GeometryChange>();
        for (var i = 0; i < Math.Min(ordered.Count, cells.Count); i++)
        {
            var panel = ordered[i];
            var from = panel.Rect;
            if (from.Width <= 0 || from.Height <= 0) continue;
            var to = ClampInto(ClampRect(cells[i], 0.02), FullPage);
            if (to != from) changes.Add(new PanelChange(panel.Id, from, to));
            foreach (var bubble in bubbleNodes.Where(b => b.PanelId == panel.Id))
            {
                var before = bubble.Snapshot();
                var after = before with
                {
                    Rect = ClampInto(ClampRect(MapRectThrough(before.Rect, from, to), MinBubble), FullPage),
                    Anchor = before.Anchor is { } anchor ? MapPointThrough(anchor, from, to) : before.Anchor,
                    TailTarget = before.TailTarget is { } tail ? MapPointThrough(tail, from, to) : before.TailTarget,
                    Moved = true,
                };
                if (after != before) changes.Add(new BubbleChange(bubble.Id, before, after));
            }
            var scale = (to.Width / from.Width + to.Height / from.Height) / 2;
            foreach (var node in sfx.Where(n => n.PanelId == panel.Id))
            {
                var before = node.Snapshot();
                var mapped = MapPointThrough(new Point(before.X, before.Y), from, to);
                var after = before with
                {
                    X = mapped.X,
                    Y = mapped.Y,
                    Size = Math.Clamp(before.Size * scale, MinSfx, 1),
                    Moved = true,
                };
                if (after != before) changes.Add(new SfxChange(node.PanelId, node.Index, before, after));
            }
        }
        return changes;
    }

    private void RunTemplate(LayoutTemplate template)
    {
        if (replayOpen) return;
        var ordered = panels.Where(p => !p.IsPolygon).OrderBy(p => p.ReadingOrder).ToList();
        if (ordered.Count == 0) { Notice("本页没有可套用模板的矩形格"); return; }
        var changes = TemplateChanges(ordered, bubbles, sfxNodes, template.Cells);
        if (changes.Count == 0) return;
        history.Push(new GeometryCommand("套用模板", changes));
        ApplyChanges(changes, before: false);
        MarkDirty();
        RenderInspector();
        Notice($"已套用模板「{template.Name}」（{Math.Min(ordered.Count, template.Cells.Count)} 格）");
    }

    private void SaveTemplate(string name)
    {
        var cells = panels.Where(p => !p.IsPolygon).OrderBy(p => p.ReadingOrder).Select(p => p.Rect).ToList();
        if (cells.Count == 0) { Notice("本页没有可保存为模板的矩形格"); return; }
        var list = LoadUserTemplates();
        var template = new LayoutTemplate(
            Guid.NewGuid().ToString("N"),
            string.IsNullOrWhiteSpace(name) ? $"模板 {list.Count + 1}" : name,
            false, cells, DateTimeOffset.UtcNow.ToUnixTimeMilliseconds());
        list.Add(template);
        SaveUserTemplates(list);
        Notice($"已保存模板「{template.Name}」");
    }

    private void DeleteTemplate(string id)
    {
        SaveUserTemplates(LoadUserTemplates().Where(t => t.Id != id).ToList());
    }

    // ============================ 版本快照 ===================================

    private void SaveSnapshot(string name)
    {
        if (currentPage == null) return;
        var list = LoadSnapshots();   // 现读再追加：字段缓存可能落后于菜单外的保存
        var snapshot = new StoryboardSnapshot(
            Guid.NewGuid().ToString("N"),
            string.IsNullOrWhiteSpace(name) ? $"快照 {list.Count + 1}" : name,
            DateTimeOffset.UtcNow.ToUnixTimeMilliseconds(),
            CaptureCanvasState());
        list.Add(snapshot);
        WriteSnapshots(list);
        Notice($"已保存快照「{snapshot.Name}」");
    }

    private void RunSnapshotRestore(StoryboardSnapshot snapshot)
    {
        if (replayOpen) return;
        // 恢复即普通命令：撤销栈接管回滚，UpdatePageSize 接管动画与回放帧。
        var changes = StateChanges(CaptureCanvasState(), snapshot.State);
        if (changes.Count == 0) return;
        history.Push(new GeometryCommand("恢复快照", changes));
        ApplyChanges(changes, before: false);
        MarkDirty();
        RenderInspector();
        Notice($"已恢复快照「{snapshot.Name}」，可撤销");
    }

    private void DeleteSnapshot(string id)
    {
        if (compareSnapshotId == id) { compareSnapshotId = null; compareState = null; RenderGhosts(); }
        if (compareSideAId == id) compareSideAId = null;
        if (compareSideBId == id) compareSideBId = null;
        RenderGhosts();
        WriteSnapshots(LoadSnapshots().Where(s => s.Id != id).ToList());
        Notice("已删除快照");
    }

    private void ToggleCompareSnapshot(string id)
    {
        // 单份对比与 A/B 对比共用同一叠层：进入其一清空另一侧标记。
        compareSideAId = null;
        compareSideBId = null;
        compareSnapshotId = compareSnapshotId == id ? null : id;
        compareState = compareSnapshotId == null
            ? null
            : snapshots.FirstOrDefault(s => s.Id == compareSnapshotId)?.State;
        RenderGhosts();
    }

    /// <summary>把快照标到 A/B 某一侧（再点同侧取消）。有任意一侧存活即进
    /// A/B 模式：单份对比的 compareSnapshotId 被清掉，双色幽灵立刻叠上。</summary>
    private void MarkCompareSide(string id, bool sideA)
    {
        compareSnapshotId = null;
        compareState = null;
        if (sideA) compareSideAId = compareSideAId == id ? null : id;
        else compareSideBId = compareSideBId == id ? null : id;
        RenderGhosts();
    }

    /// <summary>采用某一侧快照：走普通快照恢复命令（撤销栈接管回滚），
    /// 采用后退出 A/B 对比。</summary>
    private void AdoptCompareSide(bool sideA)
    {
        var id = sideA ? compareSideAId : compareSideBId;
        var snapshot = id == null ? null : LoadSnapshots().FirstOrDefault(s => s.Id == id);
        if (snapshot == null) return;
        RunSnapshotRestore(snapshot);
        compareSideAId = null;
        compareSideBId = null;
        RenderGhosts();
        Notice($"已采用「{snapshot.Name}」版式，可撤销");
    }

    private void ExitCompareAB()
    {
        compareSideAId = null;
        compareSideBId = null;
        RenderGhosts();
    }

    /// <summary>幽灵轮廓：快照态面板/气泡以虚线叠在当前画布上（对齐 web
    /// .canvas-ghost）。单份对比为紫色；A/B 模式 A 侧紫、B 侧青。
    /// 随 UpdatePageSize 重定位，随 RenderCanvas 重建挂载。</summary>
    private void RenderGhosts()
    {
        foreach (var element in ghostElements) page.Children.Remove(element);
        ghostElements.Clear();
        var snapA = compareSideAId == null ? null : snapshots.FirstOrDefault(s => s.Id == compareSideAId);
        var snapB = compareSideBId == null ? null : snapshots.FirstOrDefault(s => s.Id == compareSideBId);
        UpdateCompareBar(snapA, snapB);
        if (page.Width <= 0) return;
        if (compareState is { } single) AddStateGhosts(single, "ghost", VioletBrush);
        if (snapA?.State is { } stateA) AddStateGhosts(stateA, "ghost:a", VioletBrush);
        if (snapB?.State is { } stateB) AddStateGhosts(stateB, "ghost:b", TealBrush);

        void AddStateGhosts(CanvasState state, string tagPrefix, Brush color)
        {
            foreach (var (id, panel) in state.Panels) AddGhost(panel.Rect, $"{tagPrefix}:panel:{id}", color);
            foreach (var (id, bubble) in state.Bubbles) AddGhost(bubble.Rect, $"{tagPrefix}:bubble:{id}", color, ellipse: true);
        }

        void AddGhost(Rect rect, string key, Brush color, bool ellipse = false)
        {
            Shape shape = ellipse ? new Ellipse() : new Rectangle();
            shape.Stroke = color;
            shape.StrokeThickness = 1.5;
            shape.StrokeDashArray = new DoubleCollection([4, 3]);
            var c = ((SolidColorBrush)color).Color;
            shape.Fill = new SolidColorBrush(Color.FromArgb(18, c.R, c.G, c.B));
            shape.IsHitTestVisible = false;
            shape.Tag = key;
            shape.Width = rect.Width * page.Width;
            shape.Height = rect.Height * page.Height;
            Canvas.SetLeft(shape, rect.X * page.Width);
            Canvas.SetTop(shape, rect.Y * page.Height);
            Panel.SetZIndex(shape, 30);
            ghostElements.Add(shape);
            page.Children.Add(shape);
        }
    }

    private static readonly SolidColorBrush VioletBrush = new(Color.FromRgb(0x7a, 0x5f, 0xb8));
    private static readonly SolidColorBrush TealBrush = new(Color.FromRgb(0x2b, 0x7a, 0x78));

    /// <summary>A/B 选择条：两侧都标了才出现（对齐 web compare-bar），叠在画布
    /// 顶部居中；回放条在底部、回放开启时 A/B 已被清场，互不遮挡。</summary>
    private void UpdateCompareBar(StoryboardSnapshot? snapA, StoryboardSnapshot? snapB)
    {
        var open = snapA != null && snapB != null;
        compareBarElement.Visibility = open ? Visibility.Visible : Visibility.Collapsed;
        if (open) compareBarText.Text = $"A {snapA!.Name}  ↔  B {snapB!.Name}";
    }

    private Border BuildCompareBar()
    {
        compareBarText = new TextBlock { FontSize = 11, FontWeight = FontWeights.SemiBold, VerticalAlignment = VerticalAlignment.Center, Margin = new Thickness(0, 0, 8, 0) };
        var panel = new StackPanel { Orientation = Orientation.Horizontal };
        panel.Children.Add(compareBarText);
        Button Make(string text, RoutedEventHandler onClick)
        {
            var button = new Button { Content = text, Style = (Style)Application.Current.FindResource("Compact"), MinHeight = 30, Margin = new Thickness(0, 0, 6, 0) };
            button.Click += onClick;
            panel.Children.Add(button);
            return button;
        }
        Make("采用 A", (_, _) => AdoptCompareSide(true));
        Make("采用 B", (_, _) => AdoptCompareSide(false));
        Make("退出对比", (_, _) => ExitCompareAB());
        return new Border
        {
            Child = panel,
            Padding = new Thickness(12, 6, 12, 6),
            Background = new SolidColorBrush(Color.FromArgb(242, 0xff, 0xfd, 0xf8)),
            BorderBrush = AssetPageUi.Brush("Ink"),
            BorderThickness = new Thickness(1),
            CornerRadius = new CornerRadius(8),
            HorizontalAlignment = HorizontalAlignment.Center,
            VerticalAlignment = VerticalAlignment.Top,
            Margin = new Thickness(0, 14, 0, 0),
            Visibility = Visibility.Collapsed,
        };
    }

    // ============================ 对开预览 ===================================
    // 跨页对开预览（roadmap 已选定 2/5）：前后页版面骨架拼在画布两侧，位置按
    // 印刷对开约定——RTL（漫画）前一页在右、后一页在左；LTR 相反。邻页分镜走
    // 既有 pages/{id}/storyboard 端点并按页缓存，纯展示不进保存载荷。

    private sealed record SpreadCell(int Order, Rect Rect);

    private readonly ToggleButton spreadButton = new() { Content = "对开", Style = (Style)Application.Current.FindResource("Pill") };
    private bool spreadOpen;
    private int spreadLoadVersion;
    private readonly Dictionary<string, List<SpreadCell>> spreadCache = new();
    private Border spreadLeftElement = new() { Visibility = Visibility.Collapsed };
    private Border spreadRightElement = new() { Visibility = Visibility.Collapsed };
    private Canvas spreadLeftSheet = new(), spreadRightSheet = new();
    private TextBlock spreadLeftCaption = new(), spreadRightCaption = new();
    private PageItem? spreadLeftItem, spreadRightItem;

    private bool ReadingRtl =>
        storyboard.ValueKind != JsonValueKind.Object
        || storyboard.Element("page").Text("reading_direction") != "ltr";

    private async Task ToggleSpread()
    {
        spreadOpen = !spreadOpen;
        spreadButton.IsChecked = spreadOpen;
        await RefreshSpreadSkeletons();
    }

    /// <summary>刷新两侧骨架：按 page_number 找前后页，各自拉取/命中缓存后渲染。
    /// 版本号防迟到覆盖（与 pageLoadVersion 同一模式）；关闭时两侧收起。</summary>
    private async Task RefreshSpreadSkeletons()
    {
        var version = ++spreadLoadVersion;
        if (!spreadOpen || currentPage == null)
        {
            RenderSpreadSide(spreadLeftElement, spreadLeftSheet, spreadLeftCaption, null, null, "");
            RenderSpreadSide(spreadRightElement, spreadRightSheet, spreadRightCaption, null, null, "");
            return;
        }
        var ordered = pages.OrderBy(p => p.PageNumber).ToList();
        var index = ordered.FindIndex(p => p.Id == currentPage.Id);
        var prev = index > 0 ? ordered[index - 1] : null;
        var next = index >= 0 && index < ordered.Count - 1 ? ordered[index + 1] : null;
        var rtl = ReadingRtl;
        var leftItem = rtl ? next : prev;
        var rightItem = rtl ? prev : next;
        var left = leftItem == null ? null : await LoadSpreadCells(leftItem);
        var right = rightItem == null ? null : await LoadSpreadCells(rightItem);
        if (version != spreadLoadVersion) return;
        spreadLeftItem = leftItem;
        spreadRightItem = rightItem;
        RenderSpreadSide(spreadLeftElement, spreadLeftSheet, spreadLeftCaption, leftItem, left, rtl ? "后一页" : "前一页");
        RenderSpreadSide(spreadRightElement, spreadRightSheet, spreadRightCaption, rightItem, right, rtl ? "前一页" : "后一页");
    }

    private async Task<List<SpreadCell>?> LoadSpreadCells(PageItem item)
    {
        if (spreadCache.TryGetValue(item.Id, out var cached)) return cached;
        try
        {
            var doc = await Api.SendAsync($"pages/{item.Id}/storyboard", cancellation: lifetime.Token);
            var cells = doc.Array("panels")
                .Select(PanelNode.From)
                .Select(node => new SpreadCell(node.ReadingOrder, node.Rect))
                .OrderBy(cell => cell.Order).ToList();
            spreadCache[item.Id] = cells;
            return cells;
        }
        catch (Exception) when (!lifetime.Token.IsCancellationRequested)
        {
            return [];   // 邻页读取失败只留空骨架，不打断当前页
        }
    }

    private void RenderSpreadSide(Border host, Canvas sheet, TextBlock caption,
        PageItem? item, List<SpreadCell>? cells, string label)
    {
        host.Visibility = spreadOpen && item != null ? Visibility.Visible : Visibility.Collapsed;
        if (!spreadOpen || item == null) return;
        caption.Text = $"P.{item.PageNumber:D3} · {label}";
        sheet.Children.Clear();
        sheet.Height = sheet.Width * pageAspect;
        var ink = AssetPageUi.Brush("Ink");
        var muted = AssetPageUi.Brush("Muted");
        foreach (var cell in cells ?? [])
        {
            var rect = new Rectangle
            {
                Stroke = ink, StrokeThickness = 1,
                Fill = new SolidColorBrush(Color.FromArgb(0x0d, 0x26, 0x20, 0x19)),
            };
            var orderText = new TextBlock
            {
                Text = cell.Order.ToString(), FontSize = 8, Foreground = muted,
                FontFamily = (FontFamily)Application.Current.FindResource("Mono"),
            };
            rect.Width = cell.Rect.Width * sheet.Width;
            rect.Height = cell.Rect.Height * sheet.Height;
            Canvas.SetLeft(rect, cell.Rect.X * sheet.Width);
            Canvas.SetTop(rect, cell.Rect.Y * sheet.Height);
            Canvas.SetLeft(orderText, cell.Rect.X * sheet.Width + 2);
            Canvas.SetTop(orderText, cell.Rect.Y * sheet.Height + 1);
            sheet.Children.Add(rect);
            sheet.Children.Add(orderText);
        }
    }

    private Border BuildSpreadSide(bool left)
    {
        var caption = new TextBlock { FontSize = 10, Foreground = AssetPageUi.Brush("Muted"), VerticalAlignment = VerticalAlignment.Center };
        var jump = new Button { Content = "跳到此页", Style = (Style)Application.Current.FindResource("Compact"), MinHeight = 26, FontSize = 10, Margin = new Thickness(8, 0, 0, 0) };
        jump.Click += async (_, _) =>
        {
            var item = left ? spreadLeftItem : spreadRightItem;
            if (item != null && await ConfirmLeaveAsync()) await SelectPageAsync(item);
        };
        var captionRow = new StackPanel { Orientation = Orientation.Horizontal };
        captionRow.Children.Add(caption);
        captionRow.Children.Add(jump);
        var sheet = new Canvas { Width = 190, Height = 190 * pageAspect, ClipToBounds = true, Background = Brushes.White };
        var stack = new StackPanel();
        stack.Children.Add(captionRow);
        stack.Children.Add(new Border
        {
            Child = sheet, BorderBrush = AssetPageUi.Brush("Ink"), BorderThickness = new Thickness(1),
            Margin = new Thickness(0, 6, 0, 0),
        });
        if (left) { spreadLeftCaption = caption; spreadLeftSheet = sheet; }
        else { spreadRightCaption = caption; spreadRightSheet = sheet; }
        return new Border
        {
            Child = stack,
            Padding = new Thickness(10),
            Background = new SolidColorBrush(Color.FromArgb(0xf2, 0xff, 0xfd, 0xf8)),
            HorizontalAlignment = left ? HorizontalAlignment.Left : HorizontalAlignment.Right,
            VerticalAlignment = VerticalAlignment.Center,
            Margin = new Thickness(16, 0, 16, 0),
            Visibility = Visibility.Collapsed,
        };
    }

    // ============================ 手绘批注层 ==================================
    // 自由笔画（圈改/箭头）逐页存 KeyValueStore，渲染为 page 上最上层
    // Polyline。纯本地标记：不进几何保存载荷、不进撤销栈与回放时间线。
    // 输入走 page 的 Preview 隧道事件——批注开启时按下即 handled，面板/气泡
    // 的拖动手势根本收不到这次按下（对齐 web annotating 时对象 interactive=false）。

    private void ToggleAnnotate()
    {
        annotating = !annotating;
        annotateButton.IsChecked = annotating;
        if (annotateBar != null) annotateBar.Visibility = annotating ? Visibility.Visible : Visibility.Collapsed;
        page.Cursor = annotating ? Cursors.Cross : Cursors.Arrow;
        Notice(annotating ? "批注模式：按住左键自由画线（圈改/箭头），笔画只存本页本地" : "退出批注模式");
    }

    private void OnAnnotateDown(object? sender, MouseButtonEventArgs e)
    {
        if (!annotating) return;
        e.Handled = true;
        var norm = ClampNorm(e.GetPosition(page));
        annotationDraftNorm = [norm];
        annotationDraft = MakeStroke();
        annotationDraft.Opacity = 0.55;
        annotationDraft.Points.Add(new Point(norm.X * page.Width, norm.Y * page.Height));
        Panel.SetZIndex(annotationDraft, 48);
        page.Children.Add(annotationDraft);
        page.CaptureMouse();
    }

    private void OnAnnotateMove(object? sender, MouseEventArgs e)
    {
        if (annotationDraft == null || annotationDraftNorm == null) return;
        e.Handled = true;
        var norm = ClampNorm(e.GetPosition(page));
        annotationDraftNorm.Add(norm);
        annotationDraft.Points.Add(new Point(norm.X * page.Width, norm.Y * page.Height));
    }

    private void OnAnnotateUp(object? sender, MouseButtonEventArgs e)
    {
        if (annotationDraft == null || annotationDraftNorm == null) return;
        e.Handled = true;
        page.ReleaseMouseCapture();
        page.Children.Remove(annotationDraft);
        annotationDraft = null;
        var points = annotationDraftNorm;
        annotationDraftNorm = null;
        if (points.Count >= 2)
        {
            annotationStrokes = [.. annotationStrokes, new AnnotationStroke(Guid.NewGuid().ToString("N"), points)];
            WriteAnnotations(annotationStrokes);
            RenderAnnotations();
        }
    }

    private Point ClampNorm(Point at) => new(
        Math.Clamp(at.X / Math.Max(1, page.Width), 0, 1),
        Math.Clamp(at.Y / Math.Max(1, page.Height), 0, 1));

    private static Polyline MakeStroke() => new()
    {
        Stroke = new SolidColorBrush(Color.FromRgb(0xC4, 0x43, 0x2A)),
        StrokeThickness = 2.5,
        StrokeLineJoin = PenLineJoin.Round,
        StrokeStartLineCap = PenLineCap.Round,
        StrokeEndLineCap = PenLineCap.Round,
        Opacity = 0.85,
        IsHitTestVisible = false,
    };

    private void RenderAnnotations()
    {
        foreach (var element in annotationElements) page.Children.Remove(element);
        annotationElements.Clear();
        if (page.Width <= 0) return;
        foreach (var stroke in annotationStrokes)
        {
            var line = MakeStroke();
            line.Tag = $"annotation:{stroke.Id}";
            foreach (var point in stroke.Points)
                line.Points.Add(new Point(point.X * page.Width, point.Y * page.Height));
            Panel.SetZIndex(line, 48);
            annotationElements.Add(line);
            page.Children.Add(line);
        }
    }

    private List<AnnotationStroke> LoadAnnotations()
    {
        if (currentPage == null) return [];
        try
        {
            var list = JsonSerializer.Deserialize<List<StrokeDto>>(KeyValueStore.Get(AnnotationsKey(currentPage.Id))) ?? [];
            return list
                .Where(dto => dto.Points is { Count: >= 2 })
                .Select(dto => new AnnotationStroke(dto.Id,
                    dto.Points.Select(p => new Point(Math.Clamp(p.X, 0, 1), Math.Clamp(p.Y, 0, 1))).ToList()))
                .ToList();
        }
        catch
        {
            return [];
        }
    }

    private void WriteAnnotations(List<AnnotationStroke> list)
    {
        if (currentPage == null) return;
        annotationStrokes = list;
        KeyValueStore.Set(AnnotationsKey(currentPage.Id),
            JsonSerializer.Serialize(list.Select(s => new StrokeDto(s.Id, s.Points.Select(p => new PointDto(p.X, p.Y)).ToList()))));
    }

    private void UndoAnnotationStroke()
    {
        if (annotationStrokes.Count == 0) return;
        WriteAnnotations(annotationStrokes.Take(annotationStrokes.Count - 1).ToList());
        RenderAnnotations();
        Notice("已撤销上一笔");
    }

    private void ClearAnnotations()
    {
        if (annotationStrokes.Count == 0) return;
        WriteAnnotations([]);
        RenderAnnotations();
        Notice("已清空本页批注");
    }

    // ============================ 制作回放 ===================================

    private Border BuildReplayBar()
    {
        var panel = new StackPanel { Orientation = Orientation.Horizontal };
        Button Make(string text, RoutedEventHandler onClick)
        {
            var button = new Button { Content = text, Style = (Style)Application.Current.FindResource("Compact"), MinHeight = 30, Margin = new Thickness(0, 0, 6, 0) };
            button.Click += onClick;
            panel.Children.Add(button);
            return button;
        }
        Make("⏮", (_, _) => ShowReplayFrame(replayIndex - 1));
        replayPlayButton = Make("⏸", (_, _) => ToggleReplayPause());
        Make("⏭", (_, _) => ShowReplayFrame(replayIndex + 1));
        replaySlider = new Slider
        {
            Width = 220, Minimum = 0, Maximum = 0, VerticalAlignment = VerticalAlignment.Center,
            IsSnapToTickEnabled = true, TickFrequency = 1, Margin = new Thickness(4, 0, 6, 0),
        };
        replaySlider.ValueChanged += (_, e) =>
        {
            if (replayOpen && (int)Math.Round(e.NewValue) != replayIndex) ShowReplayFrame((int)Math.Round(e.NewValue));
        };
        panel.Children.Add(replaySlider);
        replayPosition = new TextBlock { FontSize = 11, VerticalAlignment = VerticalAlignment.Center, MinWidth = 46, TextAlignment = TextAlignment.Center, Margin = new Thickness(0, 0, 6, 0) };
        panel.Children.Add(replayPosition);
        replayLabel = new TextBlock
        {
            FontSize = 11, FontWeight = FontWeights.SemiBold, VerticalAlignment = VerticalAlignment.Center,
            MaxWidth = 150, TextTrimming = TextTrimming.CharacterEllipsis, Margin = new Thickness(0, 0, 6, 0),
        };
        panel.Children.Add(replayLabel);
        replaySpeedBox = new ComboBox { Width = 66, Margin = new Thickness(0, 0, 6, 0), VerticalAlignment = VerticalAlignment.Center };
        foreach (var speed in new[] { 0.5, 1.0, 2.0, 4.0 })
            replaySpeedBox.Items.Add(new ComboBoxItem { Tag = speed, Content = $"×{speed:g}" });
        replaySpeedBox.SelectedIndex = 1;
        replaySpeedBox.SelectionChanged += (_, _) =>
        {
            if (replaySpeedBox.SelectedItem is ComboBoxItem { Tag: double speed }) replaySpeed = speed;
            if (replayTimer != null) replayTimer.Interval = TimeSpan.FromMilliseconds(620 / replaySpeed);
        };
        panel.Children.Add(replaySpeedBox);
        Make("导出 GIF", (_, _) => _ = ExportReplayGif());
        Make("退出回放", (_, _) => CloseReplay());
        return new Border
        {
            Child = panel,
            Padding = new Thickness(12, 6, 12, 6),
            Background = new SolidColorBrush(Color.FromArgb(242, 0xff, 0xfd, 0xf8)),
            BorderBrush = AssetPageUi.Brush("Ink"),
            BorderThickness = new Thickness(1),
            CornerRadius = new CornerRadius(8),
            HorizontalAlignment = HorizontalAlignment.Center,
            VerticalAlignment = VerticalAlignment.Bottom,
            Margin = new Thickness(0, 0, 0, 14),
            Visibility = Visibility.Collapsed,
        };
    }

    private void ToggleReplay()
    {
        if (replayOpen) { CloseReplay(); return; }
        if (timeline.Count < 2) { Notice("还没有可回放的编辑历史"); return; }
        if (Mouse.Captured != null) return;   // 手势进行中不开回放
        SelectPanels([]);
        compareSnapshotId = null;
        compareState = null;
        compareSideAId = null;
        compareSideBId = null;
        RenderGhosts();
        replayReturnState = CaptureCanvasState();
        replayOpen = true;
        replayIndex = 0;
        replayPlaying = true;
        replayPlayButton.Content = "⏸";
        replayBarElement.Visibility = Visibility.Visible;
        replaySlider.Maximum = timeline.Count - 1;
        ShowReplayFrame(0);
        EnsureReplayTimer().Start();
        Notice("回放本页制作过程（只读，退出后回到当前状态）");
    }

    private DispatcherTimer EnsureReplayTimer()
    {
        if (replayTimer == null)
        {
            replayTimer = new DispatcherTimer();
            replayTimer.Tick += (_, _) =>
            {
                if (replayIndex >= timeline.Count - 1) PauseReplay();
                else ShowReplayFrame(replayIndex + 1);
            };
        }
        replayTimer.Interval = TimeSpan.FromMilliseconds(620 / replaySpeed);
        return replayTimer;
    }

    private void PauseReplay()
    {
        replayPlaying = false;
        replayTimer?.Stop();
        replayPlayButton.Content = "▶";
    }

    private void ToggleReplayPause()
    {
        if (replayPlaying) { PauseReplay(); return; }
        if (replayIndex >= timeline.Count - 1) ShowReplayFrame(0);
        replayPlaying = true;
        replayPlayButton.Content = "⏸";
        EnsureReplayTimer().Start();
    }

    /// <summary>展示某一帧：直接写节点状态（绕过命令栈），位置/尺寸经
    /// UpdatePageSize(animate) 滑过去——与 web 的回放帧过渡同源。</summary>
    private void ShowReplayFrame(int index)
    {
        if (!replayOpen || timeline.Count == 0) return;
        replayIndex = Math.Clamp(index, 0, timeline.Count - 1);
        var frame = timeline[replayIndex].State;
        ApplyCanvasState(frame);
        UpdatePageSize(animate: true);
        if (replaySlider.Value != replayIndex) replaySlider.Value = replayIndex;
        replayPosition.Text = $"{replayIndex + 1}/{timeline.Count}";
        replayLabel.Text = timeline[replayIndex].Label;
    }

    private void ApplyCanvasState(CanvasState state)
    {
        foreach (var panel in panels)
            if (state.Panels.TryGetValue(panel.Id, out var panelState))
            {
                panel.Rect = panelState.Rect;
                panel.ZOrder = panelState.ZOrder;
            }
        foreach (var bubble in bubbles)
            if (state.Bubbles.TryGetValue(bubble.Id, out var bubbleState)) bubble.ApplySnapshot(bubbleState);
        foreach (var node in sfxNodes)
            if (state.Sfx.TryGetValue(node.PanelId, out var byIndex)
                && byIndex.TryGetValue(node.Index, out var sfxState))
                node.ApplySnapshot(sfxState);
    }

    /// <summary>制作回放导出（原生路径）：逐帧把页画布 RenderTargetBitmap 成位图，
    /// GifBitmapEncoder 逐帧写 GIF；帧延沿用回放节拍 620ms/速度（GIF 单位 1/100s），
    /// 首帧带 NETSCAPE2.0 循环扩展。导出期间暂停播放，结束回到导出前帧。</summary>
    private async Task ExportReplayGif()
    {
        if (!replayOpen || timeline.Count < 2)
        {
            Notice("还没有可回放的编辑历史");
            return;
        }
        var dialog = new Microsoft.Win32.SaveFileDialog
        {
            Filter = "GIF 动图|*.gif",
            FileName = $"分镜回放-P{currentPage?.PageNumber ?? 0:D3}.gif",
        };
        if (dialog.ShowDialog() != true) return;
        PauseReplay();
        var restoreIndex = replayIndex;
        try
        {
            await EncodeReplayGif(dialog.FileName);
            Notice($"回放已导出 {System.IO.Path.GetFileName(dialog.FileName)}");
        }
        catch (Exception error)
        {
            Notice($"回放导出失败：{error.Message}");
        }
        finally
        {
            ShowReplayFrame(restoreIndex);
        }
    }

    /// <summary>逐帧渲染整页（2× 超采样）+ GifBitmapEncoder 编码到 path。
    /// 与对话框解耦便于测试缝直写临时文件。</summary>
    private async Task EncodeReplayGif(string path)
    {
        var delay = (ushort)Math.Max(2, Math.Round(62.0 / replaySpeed));
        var frames = new List<BitmapFrame>();
        for (var i = 0; i < timeline.Count; i++)
        {
            ApplyCanvasState(timeline[i].State);
            UpdatePageSize();
            page.UpdateLayout();
            await Dispatcher.InvokeAsync(() => { }, DispatcherPriority.Render);
            var width = Math.Max(1, (int)Math.Round(page.ActualWidth > 0 ? page.ActualWidth : page.Width));
            var height = Math.Max(1, (int)Math.Round(page.ActualHeight > 0 ? page.ActualHeight : page.Height));
            var bitmap = new RenderTargetBitmap(width * 2, height * 2, 192, 192, PixelFormats.Pbgra32);
            bitmap.Render(page);
            var metadata = new BitmapMetadata("gif");
            metadata.SetQuery("/graphext/delay", delay);
            if (i == 0)
            {
                metadata.SetQuery("/appext/application", System.Text.Encoding.ASCII.GetBytes("NETSCAPE2.0"));
                metadata.SetQuery("/appext/data", new byte[] { 3, 1, 0, 0, 0 });
            }
            frames.Add(BitmapFrame.Create(bitmap, null, metadata, null));
        }
        var encoder = new GifBitmapEncoder();
        foreach (var frame in frames) encoder.Frames.Add(frame);
        await using var stream = File.Create(path);
        encoder.Save(stream);
    }

    private void CloseReplay()
    {
        if (!replayOpen) return;
        replayTimer?.Stop();
        replayTimer = null;
        replayPlaying = false;
        // 先恢复实时态再放行录制：回放帧写回不得成为时间线的新帧。
        if (replayReturnState is { } back)
        {
            ApplyCanvasState(back);
            UpdatePageSize(animate: true);
        }
        replayReturnState = null;
        replayOpen = false;
        replayBarElement.Visibility = Visibility.Collapsed;
    }

    /// <summary>换页/重建前收尾：回放与对比都是本页视图态。</summary>
    private void ExitLibraryModes()
    {
        CloseReplay();
        compareSnapshotId = null;
        compareState = null;
        compareSideAId = null;
        compareSideBId = null;
        snapshots = [];
        RenderGhosts();
    }

    // ============================ 菜单与存储 =================================

    private void RebuildLibraryMenu(ContextMenu menu)
    {
        menu.Items.Clear();
        menu.Items.Add(new MenuItem { Header = "布局模板", IsEnabled = false });
        foreach (var template in LoadTemplates())
        {
            var captured = template;
            if (template.BuiltIn)
            {
                var item = new MenuItem { Header = $"{template.Name}（{template.Cells.Count} 格）" };
                item.Click += (_, _) => RunTemplate(captured);
                menu.Items.Add(item);
            }
            else
            {
                var item = new MenuItem { Header = $"{template.Name}（{template.Cells.Count} 格）" };
                var apply = new MenuItem { Header = "套用" };
                var delete = new MenuItem { Header = "删除" };
                apply.Click += (_, _) => RunTemplate(captured);
                delete.Click += (_, _) => DeleteTemplate(captured.Id);
                item.Items.Add(apply);
                item.Items.Add(delete);
                menu.Items.Add(item);
            }
        }
        var saveTemplate = new MenuItem { Header = "存为模板…" };
        saveTemplate.Click += (_, _) =>
        {
            var name = LibraryNameDialog.Ask("存为模板", "模板名称（可留空）");
            if (name != null) SaveTemplate(name);
        };
        menu.Items.Add(saveTemplate);
        menu.Items.Add(new Separator());
        menu.Items.Add(new MenuItem { Header = "版本快照", IsEnabled = false });
        snapshots = LoadSnapshots();
        if (snapshots.Count == 0)
            menu.Items.Add(new MenuItem { Header = "还没有快照", IsEnabled = false });
        foreach (var snapshot in snapshots)
        {
            var captured = snapshot;
            var stamp = snapshot.CreatedAt > 0
                ? DateTimeOffset.FromUnixTimeMilliseconds(snapshot.CreatedAt).ToLocalTime().ToString("HH:mm")
                : "";
            var item = new MenuItem { Header = stamp.Length > 0 ? $"{snapshot.Name}  {stamp}" : snapshot.Name };
            var restore = new MenuItem { Header = "恢复" };
            var compare = new MenuItem { Header = "对比", IsCheckable = true, IsChecked = compareSnapshotId == snapshot.Id };
            var markA = new MenuItem { Header = "标为 A", IsCheckable = true, IsChecked = compareSideAId == snapshot.Id };
            var markB = new MenuItem { Header = "标为 B", IsCheckable = true, IsChecked = compareSideBId == snapshot.Id };
            var delete = new MenuItem { Header = "删除" };
            restore.Click += (_, _) => RunSnapshotRestore(captured);
            compare.Click += (_, _) => ToggleCompareSnapshot(captured.Id);
            markA.Click += (_, _) => MarkCompareSide(captured.Id, sideA: true);
            markB.Click += (_, _) => MarkCompareSide(captured.Id, sideA: false);
            delete.Click += (_, _) => DeleteSnapshot(captured.Id);
            item.Items.Add(restore);
            item.Items.Add(compare);
            item.Items.Add(markA);
            item.Items.Add(markB);
            item.Items.Add(delete);
            menu.Items.Add(item);
        }
        var saveSnapshot = new MenuItem { Header = "存快照…" };
        saveSnapshot.Click += (_, _) =>
        {
            var name = LibraryNameDialog.Ask("存快照", "快照名称（可留空）");
            if (name != null) SaveSnapshot(name);
        };
        menu.Items.Add(saveSnapshot);
    }

    private List<LayoutTemplate> LoadTemplates() => [.. BuiltInTemplates, .. LoadUserTemplates()];

    private List<LayoutTemplate> LoadUserTemplates()
    {
        try
        {
            var list = JsonSerializer.Deserialize<List<TemplateDto>>(KeyValueStore.Get(TemplatesKey)) ?? [];
            return list.Select(dto => new LayoutTemplate(
                dto.Id, dto.Name, false,
                dto.Cells.Select(c => new Rect(c.X, c.Y, c.W, c.H)).ToList(),
                dto.CreatedAt)).ToList();
        }
        catch (JsonException) { return []; }
    }

    private static void SaveUserTemplates(List<LayoutTemplate> list) =>
        KeyValueStore.Set(TemplatesKey, JsonSerializer.Serialize(list.Select(t =>
            new TemplateDto(t.Id, t.Name, t.Cells.Select(c => new RectDto(c.X, c.Y, c.Width, c.Height)).ToList(), t.CreatedAt))));

    private List<StoryboardSnapshot> LoadSnapshots()
    {
        if (currentPage == null) return [];
        try
        {
            var list = JsonSerializer.Deserialize<List<SnapshotDto>>(KeyValueStore.Get(SnapshotsKey(currentPage.Id))) ?? [];
            return list.Select(dto => new StoryboardSnapshot(dto.Id, dto.Name, dto.CreatedAt, FromDto(dto.State))).ToList();
        }
        catch (JsonException) { return []; }
    }

    private void WriteSnapshots(List<StoryboardSnapshot> list)
    {
        if (currentPage == null) return;
        KeyValueStore.Set(SnapshotsKey(currentPage.Id),
            JsonSerializer.Serialize(list.Select(s => new SnapshotDto(s.Id, s.Name, s.CreatedAt, ToDto(s.State)))));
        snapshots = list;
    }

    private static RectDto ToDto(Rect r) => new(r.X, r.Y, r.Width, r.Height);
    private static PointDto? ToDto(Point? p) => p is { } v ? new PointDto(v.X, v.Y) : null;

    private static CanvasStateDto ToDto(CanvasState state) => new(
        state.Panels.ToDictionary(kv => kv.Key, kv => new PanelStateDto(ToDto(kv.Value.Rect), kv.Value.ZOrder)),
        state.Bubbles.ToDictionary(kv => kv.Key, kv => new BubbleStateDto(
            ToDto(kv.Value.Rect), kv.Value.Rotation, ToDto(kv.Value.Anchor), ToDto(kv.Value.TailTarget), kv.Value.Moved)),
        state.Sfx.ToDictionary(kv => kv.Key, kv => kv.Value.ToDictionary(
            e => e.Key, e => new SfxStateDto(e.Value.X, e.Value.Y, e.Value.Rotation, e.Value.Size, e.Value.Moved))));

    private static CanvasState FromDto(CanvasStateDto dto) => new(
        dto.Panels.ToDictionary(kv => kv.Key, kv => new PanelState(
            new Rect(kv.Value.Rect.X, kv.Value.Rect.Y, kv.Value.Rect.W, kv.Value.Rect.H), kv.Value.ZOrder)),
        dto.Bubbles.ToDictionary(kv => kv.Key, kv => new BubbleGeometry(
            new Rect(kv.Value.Rect.X, kv.Value.Rect.Y, kv.Value.Rect.W, kv.Value.Rect.H),
            kv.Value.Rotation,
            kv.Value.Anchor is { } a ? new Point(a.X, a.Y) : null,
            kv.Value.TailTarget is { } t ? new Point(t.X, t.Y) : null,
            kv.Value.Moved)),
        dto.Sfx.ToDictionary(kv => kv.Key, kv => kv.Value.ToDictionary(
            e => e.Key, e => new SfxGeometry(e.Value.X, e.Value.Y, e.Value.Rotation, e.Value.Size, e.Value.Moved))));

    // ============================ 测试缝 ======================================
    // 与既有 *ForTest 同一约定：驱动生产方法，只把入口参数化。

    internal int TimelineCountForTest => timeline.Count;
    internal bool ReplayOpenForTest => replayOpen;
    internal int ReplayIndexForTest => replayIndex;
    internal string? ReplayLabelForTest => timeline.Count > 0 && replayIndex < timeline.Count ? timeline[replayIndex].Label : null;
    internal void ToggleReplayForTest() => ToggleReplay();
    internal void ReplaySeekForTest(int index) => ShowReplayFrame(index);
    internal void CloseReplayForTest() => CloseReplay();
    /// <summary>导出到指定文件（绕过 SaveFileDialog 的测试缝）：回放必须开着，
    /// 结束回到导出前帧。</summary>
    internal async Task ExportReplayGifForTest(string path)
    {
        var restoreIndex = replayIndex;
        PauseReplay();
        try { await EncodeReplayGif(path); }
        finally { ShowReplayFrame(restoreIndex); }
    }
    internal int GhostCountForTest => ghostElements.Count;
    internal Task ToggleSpreadForTest() => ToggleSpread();
    internal bool SpreadOpenForTest => spreadOpen;
    internal bool SpreadLeftVisibleForTest => spreadLeftElement.Visibility == Visibility.Visible;
    internal bool SpreadRightVisibleForTest => spreadRightElement.Visibility == Visibility.Visible;
    internal string SpreadLeftCaptionForTest => spreadLeftCaption.Text;
    internal string SpreadRightCaptionForTest => spreadRightCaption.Text;
    internal int SpreadLeftCellCountForTest => spreadLeftSheet.Children.OfType<Rectangle>().Count();
    internal int SpreadRightCellCountForTest => spreadRightSheet.Children.OfType<Rectangle>().Count();
    internal int SnapshotCountForTest => LoadSnapshots().Count;
    internal string? SnapshotNameForTest(int index) => LoadSnapshots().ElementAtOrDefault(index)?.Name;
    internal void SaveSnapshotForTest(string name) => SaveSnapshot(name);
    internal void RestoreSnapshotForTest(int index)
    {
        if (LoadSnapshots().ElementAtOrDefault(index) is { } snapshot) RunSnapshotRestore(snapshot);
    }
    internal void ToggleCompareForTest(int index)
    {
        snapshots = LoadSnapshots();
        if (snapshots.ElementAtOrDefault(index) is { } snapshot) ToggleCompareSnapshot(snapshot.Id);
    }
    internal void MarkCompareForTest(int index, bool sideA)
    {
        snapshots = LoadSnapshots();
        if (snapshots.ElementAtOrDefault(index) is { } snapshot) MarkCompareSide(snapshot.Id, sideA);
    }
    internal void AdoptCompareForTest(bool sideA) => AdoptCompareSide(sideA);
    internal void ExitCompareABForTest() => ExitCompareAB();
    internal bool CompareBarOpenForTest => compareBarElement.Visibility == Visibility.Visible;
    internal int GhostTaggedCountForTest(string prefix) =>
        ghostElements.Count(el => el.Tag is string tag && tag.StartsWith(prefix, StringComparison.Ordinal));
    internal void DeleteSnapshotForTest(int index)
    {
        if (LoadSnapshots().ElementAtOrDefault(index) is { } snapshot) DeleteSnapshot(snapshot.Id);
    }
    internal int TemplateCountForTest => LoadUserTemplates().Count;
    internal void SaveTemplateForTest(string name) => SaveTemplate(name);
    internal void ApplyTemplateForTest(int index)
    {
        if (LoadTemplates().ElementAtOrDefault(index) is { } template) RunTemplate(template);
    }
    internal bool AnnotatingForTest => annotating;
    internal int AnnotationCountForTest => annotationStrokes.Count;
    internal int StoredAnnotationCountForTest => LoadAnnotations().Count;
    internal void ToggleAnnotateForTest() => ToggleAnnotate();
    /// <summary>提交一笔（绕过鼠标捕获路径，走同一持久化与渲染管线）。</summary>
    internal void StrokeForTest(params Point[] normalized)
    {
        if (normalized.Length < 2) return;
        annotationStrokes = [.. annotationStrokes, new AnnotationStroke(Guid.NewGuid().ToString("N"), normalized.ToList())];
        WriteAnnotations(annotationStrokes);
        RenderAnnotations();
    }
    internal void UndoAnnotationForTest() => UndoAnnotationStroke();
    internal void ClearAnnotationsForTest() => ClearAnnotations();
}

/// <summary>单行名称输入对话框（模板/快照命名共用）。</summary>
internal sealed class LibraryNameDialog : Window
{
    private readonly TextBox input;

    private LibraryNameDialog(Window? owner, string title, string placeholder)
    {
        Owner = owner;
        Title = title;
        Width = 340;
        SizeToContent = SizeToContent.Height;
        WindowStartupLocation = owner == null ? WindowStartupLocation.CenterScreen : WindowStartupLocation.CenterOwner;
        ShowInTaskbar = false;
        Background = (Brush)Application.Current.FindResource("Paper");
        var stack = new StackPanel { Margin = new Thickness(18) };
        input = new TextBox { MinHeight = 30, Margin = new Thickness(0, 0, 0, 14) };
        System.Windows.Automation.AutomationProperties.SetName(input, placeholder);
        stack.Children.Add(input);
        var row = new StackPanel { Orientation = Orientation.Horizontal, HorizontalAlignment = HorizontalAlignment.Right };
        var cancel = new Button { Content = "取消", MinWidth = 80, Margin = new Thickness(0, 0, 8, 0) };
        cancel.Click += (_, _) => Close();
        var ok = new Button { Content = "确定", MinWidth = 96, Style = (Style)Application.Current.FindResource("InkButton") };
        ok.Click += (_, _) => { DialogResult = true; };
        row.Children.Add(cancel);
        row.Children.Add(ok);
        stack.Children.Add(row);
        Content = stack;
        input.Focus();
        PreviewKeyDown += (_, e) => { if (e.Key == Key.Escape) Close(); };
    }

    /// <summary>返回 null 表示取消；空字符串合法（调用方给默认名）。</summary>
    public static string? Ask(string title, string placeholder)
    {
        var owner = Application.Current?.Windows.OfType<Window>().FirstOrDefault(w => w.IsActive)
            ?? Application.Current?.MainWindow;
        var dialog = new LibraryNameDialog(owner, title, placeholder);
        return dialog.ShowDialog() == true ? dialog.input.Text.Trim() : null;
    }
}
