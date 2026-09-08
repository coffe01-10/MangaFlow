using System.Net.Http;
using System.IO;
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
/// NUI-4: workflow DAG studio. Nodes drag on a dark canvas with bezier edges;
/// drafts autosave debounced (800ms) with optimistic version, runs poll at 3s.
/// </summary>
public sealed class WorkflowView : WorkspaceView
{
    private const double NodeWidth = 224;
    private readonly ComboBox workflowSelector = Selector("选择工作流", 250);
    private readonly Canvas canvas = new() { Background = new SolidColorBrush(Color.FromRgb(0x17, 0x1A, 0x18)) };
    private readonly ScrollViewer canvasScroll = new();
    private readonly StackPanel library = new();
    private readonly StackPanel inspector = new();
    private readonly StackPanel runMonitor = new();
    private readonly TextBlock statusLine = new() { Foreground = new SolidColorBrush(Color.FromRgb(0xE9, 0xE6, 0xDD)) };
    private readonly ComboBox scopeType = new() { Width = 110 };
    private readonly ComboBox scopeTarget = new() { Width = 170 };
    private readonly List<WorkflowNode> nodes = [];
    private readonly List<WorkflowEdge> edges = [];
    private readonly Dictionary<string, System.Windows.Shapes.Path> edgePaths = new();
    private List<JsonElement> nodeTypes = [];
    private List<JsonElement> workflows = [];
    private List<ChapterItem> chapters = [];
    private List<PageItem> scopePages = [];
    private JsonElement current;
    private string workflowId = "";
    private int version;
    private WorkflowNode? selected;
    private readonly Dictionary<string, (double X, double Y)> draftPositions = new();
    private System.Timers.Timer? autosave;
    private int generation;
    private bool dragging;
    private double scale = 0.75;

    public WorkflowView()
    {
        var root = new Grid();
        root.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });   // topbar
        root.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });   // status
        root.RowDefinitions.Add(new RowDefinition { Height = new GridLength(1, GridUnitType.Star) });
        root.Children.Add(BuildTopBar());
        var status = BuildStatusBar();
        Grid.SetRow(status, 1);
        root.Children.Add(status);
        var split = new Grid();
        split.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(238) });
        split.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        split.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(286) });
        Grid.SetRow(split, 2);
        root.Children.Add(split);

        var libraryHost = new DockPanel { Background = new SolidColorBrush(Color.FromRgb(0x20, 0x24, 0x21)) };
        var libraryHeader = new Border
        {
            Padding = new Thickness(12, 14, 12, 12),
            BorderBrush = new SolidColorBrush(Color.FromRgb(0x38, 0x3D, 0x39)),
            BorderThickness = new Thickness(0, 0, 0, 1),
            Child = new StackPanel
            {
                Children =
                {
                    new TextBlock { Text = "NODE LIBRARY", FontSize = 10, FontWeight = FontWeights.Bold, Foreground = new SolidColorBrush(Color.FromRgb(0xA4, 0xAD, 0xA7)) },
                    new TextBlock { Text = "节点库", FontSize = 15, FontWeight = FontWeights.Bold, Foreground = new SolidColorBrush(Color.FromRgb(0xE9, 0xE6, 0xDD)) },
                },
            },
        };
        DockPanel.SetDock(libraryHeader, Dock.Top);
        libraryHost.Children.Add(libraryHeader);
        var libraryScroll = new ScrollViewer { VerticalScrollBarVisibility = ScrollBarVisibility.Auto, Content = library, Padding = new Thickness(10) };
        libraryHost.Children.Add(libraryScroll);
        Grid.SetColumn(libraryHost, 0);
        split.Children.Add(libraryHost);

        canvasScroll.Content = canvas;
        canvasScroll.Background = new SolidColorBrush(Color.FromRgb(0x17, 0x1A, 0x18));
        canvasScroll.VerticalScrollBarVisibility = ScrollBarVisibility.Auto;
        canvasScroll.HorizontalScrollBarVisibility = ScrollBarVisibility.Auto;
        Grid.SetColumn(canvasScroll, 1);
        split.Children.Add(canvasScroll);

        var inspectorHost = new DockPanel { Background = new SolidColorBrush(Color.FromRgb(0x20, 0x24, 0x21)) };
        var inspectorHeader = new Border
        {
            Padding = new Thickness(12, 14, 12, 12),
            BorderBrush = new SolidColorBrush(Color.FromRgb(0x38, 0x3D, 0x39)),
            BorderThickness = new Thickness(0, 0, 0, 1),
            Child = new StackPanel
            {
                Children =
                {
                    new TextBlock { Text = "INSPECTOR", FontSize = 10, FontWeight = FontWeights.Bold, Foreground = new SolidColorBrush(Color.FromRgb(0xA4, 0xAD, 0xA7)) },
                    new TextBlock { Text = "属性面板", FontSize = 15, FontWeight = FontWeights.Bold, Foreground = new SolidColorBrush(Color.FromRgb(0xE9, 0xE6, 0xDD)) },
                },
            },
        };
        DockPanel.SetDock(inspectorHeader, Dock.Top);
        inspectorHost.Children.Add(inspectorHeader);
        var inspectorScroll = new ScrollViewer { VerticalScrollBarVisibility = ScrollBarVisibility.Auto, Content = inspector, Padding = new Thickness(12) };
        inspectorHost.Children.Add(inspectorScroll);
        Grid.SetColumn(inspectorHost, 2);
        split.Children.Add(inspectorHost);

        var runner = BuildRunner();
        DockPanel.SetDock(runner, Dock.Bottom);
        var shell = new DockPanel();
        shell.Children.Add(runner);
        shell.Children.Add(root);
        Content = shell;
        canvas.PreviewMouseWheel += (sender, e) =>
        {
            if (Keyboard.Modifiers == ModifierKeys.Control)
            {
                scale = Math.Clamp(scale * (e.Delta > 0 ? 1.1 : 1 / 1.1), 0.2, 1.6);
                ApplyView();
                e.Handled = true;
            }
        };
    }

    private FrameworkElement BuildTopBar()
    {
        var bar = new Border
        {
            Background = new SolidColorBrush(Color.FromRgb(0xEF, 0xED, 0xE5)),
            Padding = new Thickness(16, 10, 16, 10),
            BorderBrush = new SolidColorBrush(Color.FromRgb(0x38, 0x3D, 0x39)),
            BorderThickness = new Thickness(0, 0, 0, 1),
        };
        var dock = new DockPanel();
        var actions = new StackPanel { Orientation = Orientation.Horizontal };
        actions.Children.Add(Kit.Act("保存", async (_, _) => await SaveNowAsync(), "Compact"));
        var validate = Kit.Act("校验", async (_, _) => await ValidateAsync(), "Compact");
        validate.Margin = new Thickness(6, 0, 0, 0);
        actions.Children.Add(validate);
        var publish = Kit.Act("发布", async (_, _) => await PublishAsync(), "CompactInk");
        publish.Margin = new Thickness(6, 0, 0, 0);
        actions.Children.Add(publish);
        var export = Kit.Act("导出", (_, _) => ExportGraph(), "Compact");
        export.Margin = new Thickness(6, 0, 0, 0);
        actions.Children.Add(export);
        var import = Kit.Act("导入", async (_, _) => await ImportGraphAsync(), "Compact");
        import.Margin = new Thickness(6, 0, 0, 0);
        actions.Children.Add(import);
        DockPanel.SetDock(actions, Dock.Right);
        dock.Children.Add(actions);
        workflowSelector.SelectionChanged += async (_, _) =>
        {
            if (workflowSelector.SelectedItem is ComboBoxItem { Tag: string id } && id != workflowId)
            {
                await SaveNowAsync();
                workflowId = id;
                await LoadWorkflowAsync();
            }
        };
        dock.Children.Add(workflowSelector);
        bar.Child = dock;
        return bar;
    }

    private FrameworkElement BuildStatusBar()
    {
        var bar = new Border
        {
            Background = new SolidColorBrush(Color.FromRgb(0x20, 0x24, 0x21)),
            Padding = new Thickness(16, 8, 16, 8),
            BorderBrush = new SolidColorBrush(Color.FromRgb(0x38, 0x3D, 0x39)),
            BorderThickness = new Thickness(0, 0, 0, 1),
        };
        var dock = new DockPanel();
        var canvasTools = new StackPanel { Orientation = Orientation.Horizontal };
        canvasTools.Children.Add(Kit.Act("撤销", (_, _) => Undo(), "Compact"));
        var redo = Kit.Act("重做", (_, _) => Redo(), "Compact");
        redo.Margin = new Thickness(6, 0, 0, 0);
        canvasTools.Children.Add(redo);
        var auto = Kit.Act("自动布局", (_, _) => AutoLayout(), "Compact");
        auto.Margin = new Thickness(6, 0, 0, 0);
        canvasTools.Children.Add(auto);
        var copy = Kit.Act("复制", (_, _) => DuplicateSelected(), "Compact");
        copy.Margin = new Thickness(6, 0, 0, 0);
        canvasTools.Children.Add(copy);
        var remove = Kit.Act("删除", (_, _) => DeleteSelected(), "Compact");
        remove.Margin = new Thickness(6, 0, 0, 0);
        canvasTools.Children.Add(remove);
        var fit = Kit.Act("查看全图", (_, _) => FitView(), "Compact");
        fit.Margin = new Thickness(6, 0, 0, 0);
        canvasTools.Children.Add(fit);
        DockPanel.SetDock(canvasTools, Dock.Left);
        dock.Children.Add(canvasTools);
        statusLine.HorizontalAlignment = HorizontalAlignment.Right;
        dock.Children.Add(statusLine);
        bar.Child = dock;
        return bar;
    }

    private FrameworkElement BuildRunner()
    {
        var bar = new Border
        {
            Background = new SolidColorBrush(Color.FromRgb(0x11, 0x13, 0x11)),
            BorderBrush = new SolidColorBrush(Color.FromRgb(0x38, 0x3D, 0x39)),
            BorderThickness = new Thickness(0, 1, 0, 0),
            Padding = new Thickness(16, 9, 16, 9),
        };
        var dock = new DockPanel();
        var actions = new StackPanel { Orientation = Orientation.Horizontal };
        var runNode = Kit.Act("运行节点", async (_, _) => await RunAsync([SelectedId()], [SelectedId()]), "Compact");
        var runFrom = Kit.Act("从这里运行", async (_, _) => await RunAsync([SelectedId()], []), "Compact");
        runFrom.Margin = new Thickness(6, 0, 0, 0);
        var runAll = Kit.Act("运行工作流", async (_, _) => await RunAsync([], []), "CompactInk");
        runAll.Margin = new Thickness(6, 0, 0, 0);
        actions.Children.Add(runNode);
        actions.Children.Add(runFrom);
        actions.Children.Add(runAll);
        DockPanel.SetDock(actions, Dock.Right);
        dock.Children.Add(actions);
        scopeType.Items.Add(new ComboBoxItem { Tag = "CHAPTER", Content = "章节" });
        scopeType.Items.Add(new ComboBoxItem { Tag = "PAGE", Content = "页面" });
        scopeType.SelectedIndex = 0;
        scopeType.SelectionChanged += async (_, _) => await LoadScopeTargetsAsync();
        var scopeRow = new StackPanel { Orientation = Orientation.Horizontal };
        var label = new TextBlock { Text = "运行范围  ", VerticalAlignment = VerticalAlignment.Center, Foreground = new SolidColorBrush(Color.FromRgb(0xE9, 0xE6, 0xDD)), FontSize = 12 };
        scopeRow.Children.Add(label);
        scopeRow.Children.Add(scopeType);
        scopeTarget.Margin = new Thickness(8, 0, 0, 0);
        scopeRow.Children.Add(scopeTarget);
        runMonitor.Margin = new Thickness(18, 0, 0, 0);
        scopeRow.Children.Add(runMonitor);
        dock.Children.Add(scopeRow);
        bar.Child = dock;
        return bar;
    }

    public override async void Activate(WorkspaceContext context)
    {
        base.Activate(context);
        try
        {
            var typesTask = Api.SendAsync("workflow-node-types", cancellation: lifetime.Token);
            var workflowsTask = Api.SendAsync($"projects/{ProjectId}/workflows", cancellation: lifetime.Token);
            var chaptersTask = Api.SendAsync($"projects/{ProjectId}/chapters", cancellation: lifetime.Token);
            await Task.WhenAll(typesTask, workflowsTask, chaptersTask);
            if (lifetime.Token.IsCancellationRequested) return;
            nodeTypes = (await typesTask).EnumerateArray().ToList();
            workflows = (await workflowsTask).EnumerateArray().ToList();
            chapters = (await chaptersTask).EnumerateArray().Select(ChapterItem.From).ToList();
            RenderLibrary();
            workflowSelector.Items.Clear();
            foreach (var workflow in workflows)
                workflowSelector.Items.Add(new ComboBoxItem { Tag = workflow.Text("id"), Content = workflow.Text("name") });
            if (workflows.Count == 0)
            {
                var created = await Api.SendAsync($"projects/{ProjectId}/workflows", HttpMethod.Post,
                    new { name = "单页生产流程", template = "manga_default", description = "" }, cancellation: lifetime.Token);
                workflows = [created];
                workflowSelector.Items.Add(new ComboBoxItem { Tag = created.Text("id"), Content = created.Text("name") });
            }
            var targetId = workflowId;
            var target = workflows.FirstOrDefault(w => w.Text("id") == targetId);
            if (target.ValueKind != JsonValueKind.Object) target = workflows[0];
            foreach (var item in workflowSelector.Items.OfType<ComboBoxItem>())
                if ((string?)item.Tag == target.Text("id")) { workflowSelector.SelectedItem = item; break; }
            workflowId = target.Text("id");
            await LoadWorkflowAsync();
            await LoadScopeTargetsAsync();
        }
        catch (Exception error) when (error is not OperationCanceledException)
        {
            statusLine.Text = $"无法载入项目工作流：{error.Message}";
        }
    }

    private async Task LoadScopeTargetsAsync()
    {
        // SelectionChanged 的 async 链路：未捕获异常会崩进程
        try
        {
            scopeTarget.Items.Clear();
            var isPageScope = (scopeType.SelectedItem as ComboBoxItem)?.Tag as string == "PAGE";
            if (isPageScope)
            {
                var chapter = chapters.FirstOrDefault();
                if (chapter != null)
                {
                    var rows = await Api.SendAsync($"chapters/{chapter.Id}/pages", cancellation: lifetime.Token);
                    scopePages = rows.EnumerateArray().Select(PageItem.From).ToList();
                    foreach (var page in scopePages)
                        scopeTarget.Items.Add(new ComboBoxItem { Tag = page.Id, Content = $"第 {page.PageNumber} 页" });
                }
            }
            else
            {
                foreach (var chapter in chapters)
                    scopeTarget.Items.Add(new ComboBoxItem { Tag = chapter.Id, Content = $"第 {chapter.Ordinal} 章 · {chapter.Title}" });
            }
            if (scopeTarget.Items.Count > 0) scopeTarget.SelectedIndex = 0;
        }
        catch (Exception error) when (error is not OperationCanceledException)
        {
            scopeTarget.SelectedItem = null;
            statusLine.Text = $"运行范围读取失败：{error.Message}";
        }
    }

    private void RenderLibrary()
    {
        library.Children.Clear();
        string? currentCategory = null;
        foreach (var type in nodeTypes)
        {
            var category = type.Text("category");
            if (category != currentCategory)
            {
                currentCategory = category;
                var label = new TextBlock
                {
                    Text = category switch { "INPUT" => "输入", "AGENT" => "智能处理", "CONTROL" => "控制", "OUTPUT" => "生成与输出", _ => category },
                    FontSize = 11, FontWeight = FontWeights.Bold, Margin = new Thickness(2, 10, 0, 4),
                    Foreground = new SolidColorBrush(Color.FromRgb(0xA4, 0xAD, 0xA7)),
                };
                library.Children.Add(label);
            }
            var button = new Button
            {
                Content = new StackPanel
                {
                    Children =
                    {
                        new TextBlock { Text = type.Text("display_name"), FontWeight = FontWeights.Bold, FontSize = 12.5, Foreground = new SolidColorBrush(Color.FromRgb(0xE9, 0xE6, 0xDD)), TextWrapping = TextWrapping.Wrap },
                        new TextBlock { Text = type.Text("description"), FontSize = 11, Foreground = new SolidColorBrush(Color.FromRgb(0xA4, 0xAD, 0xA7)), TextWrapping = TextWrapping.Wrap },
                    },
                },
                HorizontalContentAlignment = HorizontalAlignment.Left,
                Background = new SolidColorBrush(Color.FromRgb(0x24, 0x2A, 0x27)),
                BorderBrush = new SolidColorBrush(Color.FromRgb(0x52, 0x60, 0x5A)),
                Foreground = Brushes.White,
                Margin = new Thickness(0, 0, 0, 6),
                Padding = new Thickness(10, 8, 10, 8),
            };
            button.Click += (_, _) => AddNode(type);
            library.Children.Add(button);
        }
    }

    private async Task LoadWorkflowAsync()
    {
        try
        {
            current = await Api.SendAsync($"workflows/{workflowId}", cancellation: lifetime.Token);
            if (lifetime.Token.IsCancellationRequested) return;
            version = current.Number("version");
            nodes.Clear();
            edges.Clear();
            draftPositions.Clear();
            var graph = current.Element("draft_graph");
            foreach (var row in graph.Array("nodes"))
            {
                var node = WorkflowNode.From(row);
                nodes.Add(node);
                node.Element.MouseLeftButtonDown += (s, e) => BeginNodeDrag(s, e, node);
            }
            foreach (var row in graph.Array("edges"))
                edges.Add(new WorkflowEdge(
                    row.Text("source_node"), row.Text("source_port"),
                    row.Text("target_node"), row.Text("target_port")));
            history.Clear();
            historyIndex = 0;
            PushHistory(Snapshot("基线"));   // 基线快照：首个命令可一步撤销
            RenderCanvas();
            RenderInspector();
            UpdateStatus("已保存");
            _ = LoadRunsAsync();
        }
        catch (Exception error) when (error is not OperationCanceledException)
        {
            statusLine.Text = $"工作流载入失败：{error.Message}";
        }
    }

    private void RenderCanvas()
    {
        canvas.Children.Clear();
        edgePaths.Clear();
        foreach (var edge in edges)
        {
            var from = nodes.FirstOrDefault(n => n.Id == edge.Source);
            var to = nodes.FirstOrDefault(n => n.Id == edge.Target);
            if (from == null || to == null) continue;
            var curve = new System.Windows.Shapes.Path
            {
                Data = EdgeGeometry(from, to),
                Stroke = new SolidColorBrush(Color.FromRgb(0x77, 0x84, 0x7C)),
                StrokeThickness = 2,
            };
            edgePaths[EdgeKey(edge)] = curve;
            canvas.Children.Add(curve);
        }
        foreach (var node in nodes)
        {
            Canvas.SetLeft(node.Element, node.Position.X);
            Canvas.SetTop(node.Element, node.Position.Y);
            canvas.Children.Add(node.Element);
        }
        if (nodes.Count == 0)
        {
            var hint = new TextBlock
            {
                Text = "画布为空。从左侧节点库添加节点，或创建工作流时选择模板。",
                Foreground = new SolidColorBrush(Color.FromRgb(0xA4, 0xAD, 0xA7)),
                FontSize = 13,
            };
            Canvas.SetLeft(hint, 40);
            Canvas.SetTop(hint, 40);
            canvas.Children.Add(hint);
        }
        ApplyView();
    }

    private void ApplyView()
    {
        canvas.RenderTransform = new ScaleTransform(scale, scale);
        canvas.Width = Math.Max(1200, nodes.Count * 285 / Math.Max(0.2, scale));
        canvas.Height = Math.Max(700, 700 / Math.Max(0.2, scale));
    }

    private void FitView()
    {
        if (nodes.Count == 0) return;
        var minX = nodes.Min(n => n.Position.X);
        var maxX = nodes.Max(n => n.Position.X + NodeWidth);
        var minY = nodes.Min(n => n.Position.Y);
        var maxY = nodes.Max(n => n.Position.Y + 140);
        scale = Math.Clamp(Math.Min(
            (canvasScroll.ViewportWidth - 80) / Math.Max(200, maxX - minX),
            (canvasScroll.ViewportHeight - 80) / Math.Max(200, maxY - minY)), 0.2, 1.6);
        ApplyView();
        canvasScroll.ScrollToHorizontalOffset(Math.Max(0, minX * scale - 40));
        canvasScroll.ScrollToVerticalOffset(Math.Max(0, minY * scale - 40));
    }

    private void AddNode(JsonElement type)
    {
        var nodeType = type.Text("type");
        var id = $"{nodeType.Replace('.', '-')}-{Guid.NewGuid().ToString()[..8]}";
        var position = (X: 320 + nodes.Count * 24, Y: 120 + nodes.Count * 18);
        var node = WorkflowNode.Create(id, nodeType, type.Text("display_name"), position, type);
        nodes.Add(node);
        node.Element.MouseLeftButtonDown += (s, e) => BeginNodeDrag(s, e, node);
        PushHistory(Snapshot("添加节点"));
        Select(node);
        ScheduleSave();
        RenderCanvas();
    }

    private void Select(WorkflowNode node)
    {
        foreach (var other in nodes) other.SetSelected(other == node);
        selected = node;
        RenderInspector();
    }

    private void BeginNodeDrag(object sender, MouseButtonEventArgs e, WorkflowNode node)
    {
        if (e.ChangedButton != MouseButton.Left) return;
        Select(node);
        var element = (FrameworkElement)sender;
        var origin = e.GetPosition(canvas);
        var nodeOrigin = node.Position;
        element.CaptureMouse();
        dragging = true;
        MouseEventHandler moved = (_, me) =>
        {
            // GetPosition(canvas) already inverse-applies the canvas RenderTransform,
            // so the delta is in unscaled node coordinates — dividing by scale again
            // would make the node trail the cursor at zoom != 1.
            var current = me.GetPosition(canvas);
            var x = Math.Max(0, nodeOrigin.X + (current.X - origin.X));
            var y = Math.Max(0, nodeOrigin.Y + (current.Y - origin.Y));
            node.Position = (x, y);
            Canvas.SetLeft(node.Element, x);
            Canvas.SetTop(node.Element, y);
            RedrawEdgesFor(node);
        };
        MouseButtonEventHandler up = null!;
        MouseEventHandler lost = null!;
        var committed = false;   // up 与 LostMouseCapture 都会触发时只提交一次
        void Finish()
        {
            dragging = false;
            Mouse.RemoveMouseMoveHandler(element, moved);
            Mouse.RemoveMouseUpHandler(element, up);
            Mouse.RemoveLostMouseCaptureHandler(element, lost);
            if (committed || node.Position == nodeOrigin) return;
            committed = true;
            PushHistory(Snapshot("拖动节点"));
            ScheduleSave();
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

    // 拖拽移动时只更新与该节点相连边的 Path Data，避免全量重绘
    private void RedrawEdgesFor(WorkflowNode node)
    {
        foreach (var edge in edges)
        {
            if (edge.Source != node.Id && edge.Target != node.Id) continue;
            if (!edgePaths.TryGetValue(EdgeKey(edge), out var path)) continue;
            var from = nodes.FirstOrDefault(n => n.Id == edge.Source);
            var to = nodes.FirstOrDefault(n => n.Id == edge.Target);
            if (from == null || to == null) continue;
            path.Data = EdgeGeometry(from, to);
        }
    }

    private static string EdgeKey(WorkflowEdge edge) => $"{edge.Source}:{edge.SourcePort}-{edge.Target}:{edge.TargetPort}";

    private static PathGeometry EdgeGeometry(WorkflowNode from, WorkflowNode to)
    {
        var start = new Point(from.Position.X + NodeWidth, from.Position.Y + 64);
        var end = new Point(to.Position.X, to.Position.Y + 64);
        return new PathGeometry([new PathFigure(start,
        [
            new BezierSegment(new Point(start.X + 60, start.Y), new Point(end.X - 60, end.Y), end, true),
        ], false)]);
    }

    private void DeleteSelected()
    {
        if (selected == null) return;
        edges.RemoveAll(e => e.Source == selected.Id || e.Target == selected.Id);
        nodes.Remove(selected);
        selected = null;
        PushHistory(Snapshot("删除节点"));
        ScheduleSave();
        RenderCanvas();
        RenderInspector();
    }

    private void DuplicateSelected()
    {
        if (selected == null) return;
        var clone = WorkflowNode.Create(
            $"{selected.Type.Replace('.', '-')}-{Guid.NewGuid().ToString()[..8]}",
            selected.Type, selected.Name + " 副本", (selected.Position.X + 44, selected.Position.Y + 44), nodeTypes.FirstOrDefault(t => t.Text("type") == selected.Type));
        clone.Config = selected.Config;
        nodes.Add(clone);
        clone.Element.MouseLeftButtonDown += (s, e) => BeginNodeDrag(s, e, clone);
        PushHistory(Snapshot("复制节点"));
        Select(clone);
        ScheduleSave();
        RenderCanvas();
    }

    private void AutoLayout()
    {
        var lane = new Dictionary<string, int>();
        var index = 0;
        foreach (var node in nodes)
        {
            var tone = node.Tone;
            lane.TryAdd(tone, 0);
            node.Position = (80 + index * 285, 110 + lane[tone] * 170);
            lane[tone]++;
            index++;
        }
        PushHistory(Snapshot("自动布局"));
        ScheduleSave();
        RenderCanvas();
    }

    // ============ Undo (snapshot-based) ============
    private readonly List<string> history = [];
    private int historyIndex;

    private string Snapshot(string label) => JsonSerializer.Serialize(new
    {
        label,
        nodes = nodes.Select(n => new Dictionary<string, object?>
        {
            ["id"] = n.Id, ["type"] = n.Type, ["name"] = n.Name,
            ["x"] = n.Position.X, ["y"] = n.Position.Y, ["config"] = n.Config,
        }).ToList(),
        // 与 BuildGraph 相同的 snake_case 结构，保证 Restore 能按 source_node 等字段读回
        edges = edges.Select(e => new Dictionary<string, object?>
        {
            ["id"] = $"{e.Source}:{e.SourcePort}-{e.Target}:{e.TargetPort}",
            ["source_node"] = e.Source, ["source_port"] = e.SourcePort,
            ["target_node"] = e.Target, ["target_port"] = e.TargetPort,
        }).ToList(),
    });

    private void Undo()
    {
        if (historyIndex <= 1) return;   // history[0] 是载入基线，到基线即无可撤销
        Restore(history[--historyIndex - 1]);
        ScheduleSave();
    }

    private void Redo()
    {
        if (historyIndex >= history.Count) return;
        Restore(history[historyIndex++]);
        ScheduleSave();
    }

    private void Restore(string snapshotJson)
    {
        try
        {
            using var document = JsonDocument.Parse(snapshotJson);
            var snapshot = document.RootElement;
            nodes.Clear();
            edges.Clear();
            foreach (var row in snapshot.Array("nodes"))
            {
                var type = row.Text("type");
                var definition = nodeTypes.FirstOrDefault(t => t.Text("type") == type);
                if (definition.ValueKind != JsonValueKind.Object) continue;
                // 快照不含端口定义，用节点库类型定义补齐 inputs/outputs
                var node = WorkflowNode.Create(row.Text("id"), type, row.Text("name"), (row.Decimal("x"), row.Decimal("y")), definition);
                if (row.Element("config").ValueKind == JsonValueKind.Object)
                    node.Config = JsonSerializer.Deserialize<Dictionary<string, object?>>(row.Element("config").GetRawText()) ?? [];
                nodes.Add(node);
                node.Element.MouseLeftButtonDown += (s, e) => BeginNodeDrag(s, e, node);
            }
            foreach (var row in snapshot.Array("edges"))
                edges.Add(new WorkflowEdge(row.Text("source_node"), row.Text("source_port"), row.Text("target_node"), row.Text("target_port")));
            selected = null;
            RenderCanvas();
            RenderInspector();
        }
        catch (JsonException) { }
    }

    private string SelectedId() => selected?.Id ?? "";

    private void PushHistory(string snapshot)
    {
        if (historyIndex < history.Count) history.RemoveRange(historyIndex, history.Count - historyIndex);
        history.Add(snapshot);
        if (history.Count > 40) history.RemoveAt(0);
        historyIndex = history.Count;
    }

    // ============ Autosave (800ms debounce, serialized, version-carried) ============
    private void ScheduleSave()
    {
        generation++;
        UpdateStatus("待保存");
        autosave?.Stop();
        if (dragging) return;
        var timer = new System.Timers.Timer(800) { AutoReset = false };
        timer.Elapsed += (_, _) => Dispatcher.BeginInvoke(async () =>
        {
            timer.Stop();   // 回调只停自己的计时器，不碰可能已被替换的 autosave 字段
            await SaveNowAsync();
        });
        autosave = timer;
        timer.Start();
    }

    // 保存队列尾。所有调用点都在 UI 线程上，读写无需加锁；导航离开时也要
    // await 它，否则 Deactivate 取消令牌会腰斩在途 PATCH。
    private Task saveChain = Task.CompletedTask;

    private Task SaveNowAsync()
    {
        var previous = saveChain;
        var run = SaveAfterAsync(previous);
        saveChain = run;
        return run;

        async Task SaveAfterAsync(Task before)
        {
            try { await before; } catch (Exception) { }   // 前一次失败不阻塞本次
            await SaveNowCoreAsync();
        }
    }

    private async Task SaveNowCoreAsync()
    {
        if (workflowId.Length == 0) return;
        var generationAtSave = generation;
        UpdateStatus("保存中");
        try
        {
            var payload = BuildGraph();
            var saved = await Api.SendAsync($"workflows/{workflowId}", HttpMethod.Patch,
                new { version, draft_graph = payload }, cancellation: lifetime.Token);
            if (lifetime.Token.IsCancellationRequested) return;
            version = saved.Number("version");
            current = saved;
            if (generationAtSave == generation) UpdateStatus($"已保存 · 草稿 V{saved.Number("draft_version")}");
            else await SaveNowCoreAsync();   // new edits landed while saving（同槽递归，避免自我等待）
        }
        catch (OperationCanceledException)
        {
            // 导航/关闭取消在途保存：视图即将离场，吞掉令牌异常防止逃逸到 dispatcher
            UpdateStatus("保存已取消");
        }
        catch (Exception error)
        {
            UpdateStatus("保存失败");
            statusLine.Text = $"保存失败：{error.Message.Split('\n')[0]}";
        }
    }

    private Dictionary<string, object?> BuildGraph() => new()
    {
        ["schema_version"] = 2,
        ["nodes"] = nodes.Select(n => new Dictionary<string, object?>
        {
            ["id"] = n.Id, ["type"] = n.Type, ["name"] = n.Name,
            ["position"] = new Dictionary<string, object> { ["x"] = n.Position.X, ["y"] = n.Position.Y },
            ["inputs"] = n.Inputs, ["outputs"] = n.Outputs, ["config"] = n.Config,
        }).ToList(),
        ["edges"] = edges.Select(e => new Dictionary<string, object?>
        {
            ["id"] = $"{e.Source}:{e.SourcePort}-{e.Target}:{e.TargetPort}",
            ["source_node"] = e.Source, ["source_port"] = e.SourcePort,
            ["target_node"] = e.Target, ["target_port"] = e.TargetPort,
        }).ToList(),
    };

    private async Task ValidateAsync()
    {
        await SaveNowAsync();
        try
        {
            var report = await Api.SendAsync($"workflows/{workflowId}/validate", HttpMethod.Post, cancellation: lifetime.Token);
            var issues = report.Array("issues");
            var errors = issues.Count(i => i.Text("severity") == "ERROR");
            var warnings = issues.Count - errors;
            statusLine.Text = $"校验完成：{errors} 项错误 · {warnings} 项警告";
            if (issues.Count > 0)
                MessageBox.Show(Host, string.Join("\n", issues.Select(i => $"[{i.Text("severity")}] {i.Text("message")}")), "校验问题");
        }
        catch (Exception error) when (error is not OperationCanceledException)
        {
            MessageBox.Show(Host, error.Message, "校验失败", MessageBoxButton.OK, MessageBoxImage.Warning);
        }
    }

    private async Task PublishAsync()
    {
        await SaveNowAsync();
        try
        {
            await Api.SendAsync($"workflows/{workflowId}/publish", HttpMethod.Post, cancellation: lifetime.Token);
            statusLine.Text = "已发布不可变版本";
            State.Status = "工作流版本已发布";
        }
        catch (Exception error) when (error is not OperationCanceledException)
        {
            MessageBox.Show(Host, error.Message, "发布失败", MessageBoxButton.OK, MessageBoxImage.Warning);
        }
    }

    private void ExportGraph()
    {
        var dialog = new Microsoft.Win32.SaveFileDialog
        {
            Title = "导出工作流", Filter = "工作流 JSON|*.json", FileName = $"{current.Text("name", "workflow")}.json",
        };
        if (dialog.ShowDialog(Host) != true) return;
        try
        {
            var payload = new Dictionary<string, object?>
            {
                ["schema"] = "mangaflow.workflow.v2",
                ["name"] = current.Text("name"),
                ["description"] = current.Text("description"),
                ["graph"] = BuildGraph(),
            };
            File.WriteAllText(dialog.FileName, JsonSerializer.Serialize(payload, new JsonSerializerOptions { WriteIndented = true }));
            statusLine.Text = "工作流已导出";
        }
        catch (Exception error)
        {
            MessageBox.Show(Host, error.Message, "导出未完成", MessageBoxButton.OK, MessageBoxImage.Warning);
        }
    }

    private async Task ImportGraphAsync()
    {
        var dialog = new Microsoft.Win32.OpenFileDialog { Title = "导入工作流", Filter = "工作流 JSON|*.json" };
        if (dialog.ShowDialog(Host) != true) return;
        try
        {
            using var document = JsonDocument.Parse(await File.ReadAllTextAsync(dialog.FileName, lifetime.Token));
            var root = document.RootElement;
            var graph = root.Element("graph");
            if (root.Text("schema") != "mangaflow.workflow.v2" || graph.Number("schema_version") != 2 || graph.Array("nodes").Count == 0)
                throw new InvalidOperationException("导入失败：文件不是 schema_version 2 的工作流图谱（缺少 nodes / edges 或版本不符）。");
            var name = root.Text("name").Length > 0 ? root.Text("name") : System.IO.Path.GetFileNameWithoutExtension(dialog.FileName);
            var imported = await Api.SendAsync($"projects/{ProjectId}/workflows/import", HttpMethod.Post,
                new { name, description = root.Text("description"), graph = JsonSerializer.Deserialize<Dictionary<string, object?>>(graph.GetRawText()) },
                cancellation: lifetime.Token);
            workflowId = imported.Text("id");
            var refreshed = Context!;
            async void Reload() => Activate(refreshed);
            Reload();
        }
        catch (Exception error) when (error is not OperationCanceledException)
        {
            MessageBox.Show(Host, error.Message, "导入失败", MessageBoxButton.OK, MessageBoxImage.Warning);
        }
    }

    private async Task RunAsync(string[] startNodes, string[] stopNodes)
    {
        if (scopeTarget.SelectedItem is not ComboBoxItem { Tag: string scopeId } || scopeId.Length == 0)
        {
            MessageBox.Show(Host, "请先选择章节或页面运行范围", "运行工作流");
            return;
        }
        try
        {
            var scope = (scopeType.SelectedItem as ComboBoxItem)?.Tag as string ?? "CHAPTER";
            await Api.SendAsync($"workflows/{workflowId}/runs", HttpMethod.Post, new
            {
                scope_type = scope, scope_id = scopeId,
                start_node_ids = startNodes.Where(s => s.Length > 0).ToList(),
                stop_node_ids = stopNodes.Where(s => s.Length > 0).ToList(),
            }, cancellation: lifetime.Token);
            statusLine.Text = "运行已启动";
            await LoadRunsAsync();
        }
        catch (Exception error) when (error is not OperationCanceledException)
        {
            MessageBox.Show(Host, error.Message, "运行未启动", MessageBoxButton.OK, MessageBoxImage.Warning);
        }
    }

    private JsonElement latestRun;
    private bool runActive;

    private async Task LoadRunsAsync()
    {
        try
        {
            var runs = await Api.SendAsync($"workflows/{workflowId}/runs", cancellation: lifetime.Token);
            if (lifetime.Token.IsCancellationRequested) return;
            var list = runs.EnumerateArray().ToList();
            if (list.Count == 0) return;
            latestRun = list[0];
            runActive = latestRun.Text("status") == "RUNNING";
            var nodeRuns = latestRun.Array("node_runs");
            var done = nodeRuns.Count(n => n.Text("status") == "COMPLETED");
            runMonitor.Children.Clear();
            var summary = new TextBlock
            {
                Text = $"运行 {Labels.Map(Labels.WorkflowRunStatus, latestRun.Text("status"))} · {done}/{nodeRuns.Count}",
                Foreground = new SolidColorBrush(Color.FromRgb(0xE9, 0xE6, 0xDD)), FontSize = 12, VerticalAlignment = VerticalAlignment.Center,
            };
            runMonitor.Children.Add(summary);
            foreach (var node in nodes)
            {
                var run = nodeRuns.FirstOrDefault(n => n.Text("node_id") == node.Id);
                node.SetRunStatus(run.ValueKind == JsonValueKind.Object
                    ? Labels.Map(Labels.WorkflowRunStatus, run.Text("status")) : null);
            }
            if (runActive && latestRun.Text("status") == "RUNNING")
            {
                var cancel = Kit.Act("取消", async (_, _) =>
                {
                    try
                    {
                        await Api.SendAsync($"workflow-runs/{latestRun.Text("id")}/cancel", HttpMethod.Post);
                        await LoadRunsAsync();
                    }
                    catch (Exception error) { MessageBox.Show(Host, error.Message, "取消失败"); }
                }, "Compact");
                cancel.Margin = new Thickness(12, 0, 0, 0);
                runMonitor.Children.Add(cancel);
            }
        }
        catch (Exception error) when (error is not OperationCanceledException)
        {
            _ = error;
        }
    }

    private void UpdateStatus(string label)
    {
        var published = current.Text("published_version_id").Length > 0;
        statusLine.Text = $"{label} · 草稿 V{current.Number("draft_version")} · 已发布 {(published ? "版本就绪" : "尚未发布")}";
    }

    private void RenderInspector()
    {
        inspector.Children.Clear();
        if (selected == null)
        {
            inspector.Children.Add(new TextBlock
            {
                Text = "从这里开始", Foreground = new SolidColorBrush(Color.FromRgb(0xE9, 0xE6, 0xDD)),
                FontWeight = FontWeights.Bold, FontSize = 15,
            });
            foreach (var step in new[]
                     {
                         "选择节点查看配置", "拖动端口建立连线", "校验草稿并修复问题", "发布不可变版本", "选择范围后运行",
                     })
                inspector.Children.Add(new TextBlock
                {
                    Text = step, Foreground = new SolidColorBrush(Color.FromRgb(0xA4, 0xAD, 0xA7)),
                    FontSize = 12, Margin = new Thickness(0, 8, 0, 0),
                });
            return;
        }
        var node = selected;
        var name = new TextBox { Text = node.Name };
        name.TextChanged += (_, _) => { node.Name = name.Text; ScheduleSave(); };
        var timeout = new TextBox { Text = node.ConfigElement.TryGetProperty("timeout_seconds", out var t) && t.ValueKind == JsonValueKind.Number ? t.GetInt32().ToString() : "900", Width = 130 };
        var retries = new TextBox { Text = node.ConfigElement.TryGetProperty("max_attempts", out var r) && r.ValueKind == JsonValueKind.Number ? r.GetInt32().ToString() : "3", Width = 130 };
        var prompt = new TextBox
        {
            AcceptsReturn = true, MinHeight = 70,
            Text = node.ConfigElement.TryGetProperty("prompt_template", out var p) && p.ValueKind == JsonValueKind.String ? p.GetString() : "",
        };
        prompt.TextChanged += (_, _) => { node.SetConfig("prompt_template", prompt.Text.Length == 0 ? null : prompt.Text); ScheduleSave(); };
        var notes = new TextBox
        {
            AcceptsReturn = true, MinHeight = 44,
            Text = node.ConfigElement.TryGetProperty("notes", out var n) && n.ValueKind == JsonValueKind.String ? n.GetString() : "",
        };
        notes.TextChanged += (_, _) => { node.SetConfig("notes", notes.Text.Length == 0 ? null : notes.Text); ScheduleSave(); };

        inspector.Children.Add(DarkLabel("节点名称"));
        inspector.Children.Add(name);
        inspector.Children.Add(DarkLabel("节点类型"));
        inspector.Children.Add(new TextBlock { Text = node.Type, Foreground = new SolidColorBrush(Color.FromRgb(0xA4, 0xAD, 0xA7)), FontFamily = (FontFamily)Application.Current.FindResource("Mono"), FontSize = 12 });
        timeout.TextChanged += (_, _) => { if (int.TryParse(timeout.Text, out var v)) { node.SetConfig("timeout_seconds", v); ScheduleSave(); } };
        retries.TextChanged += (_, _) => { if (int.TryParse(retries.Text, out var v)) { node.SetConfig("max_attempts", v); ScheduleSave(); } };
        inspector.Children.Add(DarkLabel("超时（秒）"));
        inspector.Children.Add(timeout);
        inspector.Children.Add(DarkLabel("重试次数"));
        inspector.Children.Add(retries);
        inspector.Children.Add(DarkLabel("提示词"));
        inspector.Children.Add(prompt);
        inspector.Children.Add(DarkLabel("备注"));
        inspector.Children.Add(notes);
    }

    private static TextBlock DarkLabel(string text) => new()
    {
        Text = text, FontSize = 11, FontWeight = FontWeights.Bold,
        Foreground = new SolidColorBrush(Color.FromRgb(0xA4, 0xAD, 0xA7)), Margin = new Thickness(0, 12, 0, 4),
    };

    public override void PollTick()
    {
        if (runActive) _ = LoadRunsAsync();
    }

    public override Task RefreshAsync()
    {
        _ = LoadRunsAsync();
        return Task.CompletedTask;
    }

    public override async Task<bool> ConfirmLeaveAsync()
    {
        // Flush the debounced draft AND await any PATCH already in flight: returning
        // first would let Deactivate() cancel the lifetime token mid-request.
        autosave?.Stop();
        if (autosave is { Enabled: true }) await SaveNowAsync();
        else await saveChain;
        return true;
    }

    // ============ Node visual model ============
    private sealed class WorkflowNode
    {
        public required string Id { get; init; }
        public required string Type { get; init; }
        public string Name;
        public (double X, double Y) Position;
        public JsonElement Inputs { get; init; }
        public JsonElement Outputs { get; init; }
        public Dictionary<string, object?> Config { get; set; } = [];
        public required Border Element { get; init; }
        private TextBlock? statusBadge;

        public JsonElement ConfigElement
        {
            get
            {
                var json = JsonSerializer.Serialize(Config);
                return JsonDocument.Parse(json).RootElement.Clone();
            }
        }

        public string Tone => Type.StartsWith("source.") ? "input"
            : Type.StartsWith("control.") ? "control"
            : Type.StartsWith("generator.") || Type.StartsWith("output.") ? "output"
            : Type.StartsWith("quality.") ? "quality"
            : "agent";

        private static Color ToneColor(string tone) => tone switch
        {
            "input" => Color.FromRgb(0x39, 0x7B, 0x68),
            "agent" => Color.FromRgb(0x32, 0x6B, 0x91),
            "control" => Color.FromRgb(0xB7, 0x7C, 0x26),
            "output" => Color.FromRgb(0xB9, 0x47, 0x35),
            _ => Color.FromRgb(0x78, 0x62, 0xA4),
        };

        public static WorkflowNode Create(string id, string type, string name, (double X, double Y) position, JsonElement typeDefinition)
        {
            var node = new WorkflowNode
            {
                Id = id,
                Type = type,
                Name = name,
                Position = position,
                Inputs = typeDefinition.Element("inputs"),
                Outputs = typeDefinition.Element("outputs"),
                Element = new Border { Width = NodeWidth },
            };
            node.Config = new Dictionary<string, object?>
            {
                ["model_alias"] = type.StartsWith("agent.") || type.StartsWith("quality.") || type.StartsWith("director.") ? "auto" : null,
                ["prompt_template"] = "",
                ["system_instruction"] = "",
                ["temperature"] = 0.2,
                ["timeout_seconds"] = 900,
                ["max_attempts"] = 3,
                ["concurrency"] = 1,
                ["resolution"] = type == "generator.page" ? "1K" : null,
                ["locked"] = false,
                ["notes"] = "",
                ["condition"] = new Dictionary<string, object?>(),
                ["requires_approval"] = type is "generator.page" or "control.approval",
            };
            node.BuildVisual();
            return node;
        }

        public static WorkflowNode From(JsonElement row)
        {
            var position = row.Element("position");
            var node = new WorkflowNode
            {
                Id = row.Text("id"),
                Type = row.Text("type"),
                Name = row.Text("name"),
                Position = (position.Decimal("x"), position.Decimal("y")),
                Inputs = row.Element("inputs"),
                Outputs = row.Element("outputs"),
                Element = new Border { Width = NodeWidth },
            };
            node.Config = row.Element("config").ValueKind == JsonValueKind.Object
                ? JsonSerializer.Deserialize<Dictionary<string, object?>>(row.Element("config").GetRawText()) ?? []
                : [];
            node.BuildVisual();
            return node;
        }

        public void SetConfig(string key, object? value) => Config[key] = value;

        private void BuildVisual()
        {
            var tone = Tone;
            var color = ToneColor(tone);
            var header = new Border
            {
                Background = new SolidColorBrush(Color.FromArgb(0x22, 0x00, 0x00, 0x00)),
                BorderBrush = new SolidColorBrush(Color.FromRgb(0x3C, 0x46, 0x41)),
                BorderThickness = new Thickness(0, 0, 0, 1),
                Padding = new Thickness(9, 4, 9, 4),
                Child = new DockPanel(),
            };
            statusBadge = new TextBlock
            {
                Text = "DRAFT", FontSize = 10, Foreground = new SolidColorBrush(Color.FromRgb(0x9A, 0xA3, 0x9D)),
                FontFamily = (FontFamily)Application.Current.FindResource("Mono"),
            };
            var headerDock = (DockPanel)header.Child;
            DockPanel.SetDock(statusBadge, Dock.Right);
            headerDock.Children.Add(statusBadge);
            headerDock.Children.Add(new TextBlock
            {
                Text = Type, FontSize = 10, Foreground = new SolidColorBrush(Color.FromRgb(0xA4, 0xAD, 0xA7)),
                FontFamily = (FontFamily)Application.Current.FindResource("Mono"),
            });
            var title = new TextBlock
            {
                Text = Name, FontSize = 13.5, FontWeight = FontWeights.Bold,
                Foreground = new SolidColorBrush(Color.FromRgb(0xE9, 0xE6, 0xDD)),
                FontFamily = (FontFamily)Application.Current.FindResource("Serif"),
                Margin = new Thickness(9, 8, 9, 8), TextWrapping = TextWrapping.Wrap,
            };
            var ports = new Grid { Margin = new Thickness(0, 0, 0, 8), MinHeight = 40 };
            ports.ColumnDefinitions.Add(new ColumnDefinition());
            ports.ColumnDefinitions.Add(new ColumnDefinition());
            var inputs = new StackPanel { Margin = new Thickness(9, 0, 4, 0) };
            foreach (var input in Inputs.EnumerateArray())
                inputs.Children.Add(new TextBlock
                {
                    Text = $"{input.Text("label")}  {input.Text("data_type")}", FontSize = 10,
                    Foreground = new SolidColorBrush(Color.FromRgb(0xA4, 0xAD, 0xA7)),
                });
            var outputs = new StackPanel { Margin = new Thickness(4, 0, 9, 0) };
            foreach (var output in Outputs.EnumerateArray())
                outputs.Children.Add(new TextBlock
                {
                    Text = $"{output.Text("label")}  {output.Text("data_type")}", FontSize = 10,
                    Foreground = new SolidColorBrush(Color.FromRgb(0xA4, 0xAD, 0xA7)), HorizontalAlignment = HorizontalAlignment.Right,
                });
            Grid.SetColumn(inputs, 0);
            Grid.SetColumn(outputs, 1);
            ports.Children.Add(inputs);
            ports.Children.Add(outputs);
            var content = new StackPanel();
            content.Children.Add(header);
            content.Children.Add(title);
            content.Children.Add(ports);
            Element.Background = new SolidColorBrush(Color.FromRgb(0x24, 0x2A, 0x27));
            Element.BorderBrush = new SolidColorBrush(Color.FromRgb(0x52, 0x60, 0x5A));
            Element.BorderThickness = new Thickness(1);
            Element.Child = content;
            var topBar = new Border
            {
                Height = 3, Background = new SolidColorBrush(color),
                HorizontalAlignment = HorizontalAlignment.Stretch, VerticalAlignment = VerticalAlignment.Top,
            };
            Element.Loaded += (_, _) =>
            {
                var grid = Element.Child as StackPanel;
                grid?.Children.Insert(0, topBar);
            };
        }

        public void SetSelected(bool isSelected)
        {
            Element.BorderBrush = isSelected
                ? new SolidColorBrush(Color.FromRgb(0xE7, 0xE2, 0xD7))
                : new SolidColorBrush(Color.FromRgb(0x52, 0x60, 0x5A));
            Element.BorderThickness = isSelected ? new Thickness(2) : new Thickness(1);
        }

        public void SetRunStatus(string? status)
        {
            if (statusBadge != null) statusBadge.Text = status ?? "DRAFT";
        }
    }

    private sealed record WorkflowEdge(string Source, string SourcePort, string Target, string TargetPort);
}
