using System.Globalization;
using System.Net.Http;
using System.IO;
using System.Text.Json;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Controls.Primitives;
using System.Windows.Input;
using System.Windows.Media;
using System.Windows.Shapes;
using System.Windows.Threading;
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
    // 端口锚点固定公式（首行中心 ≈ 节点顶部 70px、行距 25px）：只在布局完成前的
    // 初帧作回退使用（WorkflowNode.AnchorCache 为空时）。布局完成后锚点改用
    // TranslatePoint 实测的端口圆点位置（见 MeasureAnchors）——标题 TextWrapping
    // 换行会把端口区下推约 19px/行，纯公式会让连线端点脱离端口圆点（web 端
    // React Flow 从 Handle 实测 DOM 位置画边）。
    private const double PortRowTop = 70;
    private const double PortRowStep = 25;
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
    // 每条边两份 Path：可视层（2px 实线）+ 命中层（14px 透明加宽，解决细线难点中）
    private readonly Dictionary<string, (System.Windows.Shapes.Path Curve, System.Windows.Shapes.Path Hit)> edgePaths = new();
    private List<JsonElement> nodeTypes = [];
    private List<JsonElement> workflows = [];
    private List<JsonElement> textModels = [];    // /models 里 TEXT+structured_text 的模型（网页 textModels）
    private List<JsonElement> imageModels = [];   // /models 里 IMAGE+image_edit 的模型（审批时选择）
    private List<JsonElement> runRows = [];       // 运行历史：GET workflows/{id}/runs 原序（created_at 倒序）
    private List<ChapterItem> chapters = [];
    private List<PageItem> scopePages = [];
    private JsonElement current;
    private string workflowId = "";
    // #427: 画布当前图归属的工作流 id（version/current 与它同组提交）。快速切换时
    // workflowId 先行指向新选择，画布仍属旧工作流直到载入提交——PATCH 必须按画布
    // 归属配对 (graph, id)，绝不能按"当前 workflowId"发请求（否则 A 的图会写进 B）。
    private string canvasWorkflowId = "";
    // #427: 载入请求令牌（ScriptView.scriptLoadVersion 同款）：A→B→C 快速切换时
    // GET 响应可能乱序返回，迟到响应不得把画布翻回旧工作流。
    private int workflowLoadVersion;
    private int version;
    private WorkflowNode? selected;
    private string? selectedEdgeKey;
    private Button? removeButton;                    // P2-2: 状态栏删除按钮，按选中状态禁用
    private readonly Dictionary<string, (double X, double Y)> draftPositions = new();
    private System.Timers.Timer? autosave;
    private int generation;
    private bool dragging;
    private double scale = 0.75;
    private System.Windows.Shapes.Path? pendingWire;   // 连线拖拽中的虚线预览
    private Action? cancelWire;                        // Escape 取消进行中的连线拖拽
    private readonly StackPanel runHistory = new();    // 属性面板下方的运行历史列表（轮询刷新不重建属性面板）
    private readonly StackPanel approvalQueue = new(); // 页脚审批队列（网页 footer 的 WAITING_APPROVAL 行）
    private string drawModel = "";                     // 审批时选择的图片模型（网页 drawModel 状态）
    private string drawResolution = "1K";              // 审批清晰度（网页 drawResolution 状态）
    private bool approving;                            // 审批动作防重入（网页 approveNode.isPending 禁用）
    private int runsLoading;                           // LoadRunsAsync 防重入（3s 轮询上一轮未返回时跳过）

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
        // 运行历史与属性面板同列（网页把版本列表这类辅助列表放进属性面板 aside）。
        // 刻意分成两个面板：轮询刷新运行历史时不重建属性面板的输入框，编辑中的
        // 文本不会被 3s 轮询打断（网页 runs 轮询也只更新节点角标与审批行）。
        var historyHost = new DockPanel { Background = new SolidColorBrush(Color.FromRgb(0x20, 0x24, 0x21)) };
        var historyHeader = new Border
        {
            Padding = new Thickness(12, 12, 12, 10),
            BorderBrush = new SolidColorBrush(Color.FromRgb(0x38, 0x3D, 0x39)),
            BorderThickness = new Thickness(0, 1, 0, 0),
            Child = new StackPanel
            {
                Children =
                {
                    new TextBlock { Text = "RUN HISTORY", FontSize = 10, FontWeight = FontWeights.Bold, Foreground = new SolidColorBrush(Color.FromRgb(0xA4, 0xAD, 0xA7)) },
                    new TextBlock { Text = "运行历史", FontSize = 13, FontWeight = FontWeights.Bold, Foreground = new SolidColorBrush(Color.FromRgb(0xE9, 0xE6, 0xDD)) },
                },
            },
        };
        DockPanel.SetDock(historyHeader, Dock.Top);
        historyHost.Children.Add(historyHeader);
        var historyScroll = new ScrollViewer { VerticalScrollBarVisibility = ScrollBarVisibility.Auto, Content = runHistory, Padding = new Thickness(12) };
        historyHost.Children.Add(historyScroll);
        var rightColumn = new Grid();
        rightColumn.RowDefinitions.Add(new RowDefinition { Height = new GridLength(1, GridUnitType.Star) });
        rightColumn.RowDefinitions.Add(new RowDefinition { Height = new GridLength(232) });
        Grid.SetRow(inspectorHost, 0);
        Grid.SetRow(historyHost, 1);
        rightColumn.Children.Add(inspectorHost);
        rightColumn.Children.Add(historyHost);
        Grid.SetColumn(rightColumn, 2);
        split.Children.Add(rightColumn);

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
        // 键盘纪律：Delete/Backspace/Escape 只在画布持有键盘焦点时生效。属性面板的
        // TextBox 不在画布视觉树下，输入时事件不会路由到画布，不会误删（对齐 web
        // 的 deleteKeyCode 只作用于 React Flow 选区）。
        canvas.Focusable = true;
        canvas.PreviewKeyDown += OnCanvasKeyDown;
        // 点空白画布 = 清空节点/连线选中并聚焦（节点、端口、连线处理器都会吞掉
        // 自己的点击，这里只剩空白区域的事件）
        canvas.MouseLeftButtonDown += (_, _) =>
        {
            ClearSelection();
            FocusCanvas();
        };
    }

    private void OnCanvasKeyDown(object sender, KeyEventArgs e)
    {
        if (e.Key == Key.Escape)
        {
            // 取消进行中的连线拖拽；没有拖拽时不吞按键
            var active = cancelWire != null;
            cancelWire?.Invoke();
            if (active) e.Handled = true;
            return;
        }
        if (e.Key is not (Key.Delete or Key.Back)) return;
        if (selectedEdgeKey != null)
        {
            DeleteSelectedEdge();
            e.Handled = true;
        }
        else if (selected != null)
        {
            DeleteSelected();   // web 的 deleteKeyCode 同样作用于选中的节点
            e.Handled = true;
        }
    }

    // 画布可能尚未挂进视觉树（如无头回归检查），此时 Focus 无效，跳过即可
    private void FocusCanvas()
    {
        if (canvas.IsLoaded) canvas.Focus();
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
                // #342 补齐（与 ConfirmLeaveAsync 同款纪律）：先停掉武装中的防抖
                // 计时器再 flush。否则 flush 响应在途时计时器到期，回调的身份检查
                // 读到的仍是旧 workflowId/version（切换还没越过 await），会把同一份
                // 草稿按同版本号 PATCH 第二次（生产环境表现为伪 409）。
                autosave?.Stop();
                // #427: 在任何 await 之前提交新 id。旧顺序把 workflowId = id 放在
                // await SaveNowAsync() 之后：处理器悬挂在 flush 期间发生第二次切换，
                // 后一个处理器读到旧 workflowId、其排进保存链的 flush（target 为空）
                // 轮到执行时按"当前 workflowId"发 PATCH——此刻 id 已被前一个处理器
                // 改成 B、画布却还是 A 的图 → A 的图 PATCH 进 workflows/B（两个新
                // 工作流版本恰好相同时后端 CAS 也拦不住）。离场 flush 必须点名离开
                // 的工作流，保存核按画布归属配对 (graph, id)（SaveNowCoreAsync）。
                var leaving = workflowId;
                workflowId = id;
                await SaveNowAsync(leaving);
                await LoadWorkflowAsync(id);
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
        // P2-2: 删除按钮统一分派（选中连线删连线，否则选中节点删节点），且无
        // 选中对象时禁用——对齐 web 端 deleteKeyCode 只作用于唯一选中对象 +
        // 工具栏 disabled 语义，不再出现点了静默无效。
        removeButton = Kit.Act("删除", (_, _) => DeleteSelection(), "Compact");
        removeButton.Margin = new Thickness(6, 0, 0, 0);
        canvasTools.Children.Add(removeButton);
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
        // 审批队列挂在页脚（网页 footer 的 WAITING_APPROVAL 行）：有等待确认的
        // 节点时出现模型/清晰度选择与「确认继续」，为空时不占高度。
        var content = new StackPanel();
        content.Children.Add(dock);
        approvalQueue.Margin = new Thickness(0, 4, 0, 0);
        content.Children.Add(approvalQueue);
        bar.Child = content;
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
            await LoadModelsAsync();
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
            // #427: 先提交 id 再拨选择器（ScriptView/StoryboardView 的 Activate 同款
            // 纪律）：SelectionChanged 处理器的 id != workflowId 检查保持静默，由
            // Activate 自己恰好载入一次。旧顺序先拨 SelectedItem（处理器先行载入
            // 一次）再自行载入——每次激活双倍 GET，迟到的第一份纯属浪费。
            workflowId = target.Text("id");
            foreach (var item in workflowSelector.Items.OfType<ComboBoxItem>())
                if ((string?)item.Tag == workflowId) { workflowSelector.SelectedItem = item; break; }
            await LoadWorkflowAsync(workflowId);
            await LoadScopeTargetsAsync();
        }
        catch (OperationCanceledException) { }
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
         catch (OperationCanceledException) { }
        catch (Exception error) when (error is not OperationCanceledException)
        {
            scopeTarget.SelectedItem = null;
            statusLine.Text = $"运行范围读取失败：{error.Message}";
        }
    }

    // 模型目录独立加载（网页的 models 查询也是独立 staleTime 查询）：失败只让
    // 检查器下拉退化为 auto 选项、审批选不到模型，不阻断工作流载入。
    private async Task LoadModelsAsync()
    {
        try
        {
            var rows = await Api.SendAsync("models", cancellation: lifetime.Token);
            if (lifetime.Token.IsCancellationRequested) return;
            textModels = rows.EnumerateArray()
                .Where(m => m.Text("model_type") == "TEXT" && HasOperation(m, "structured_text")).ToList();
            imageModels = rows.EnumerateArray()
                .Where(m => m.Text("model_type") == "IMAGE" && HasOperation(m, "image_edit")).ToList();
        }
        catch (OperationCanceledException) { }
        catch (Exception error) when (error is not OperationCanceledException)
        {
            textModels = [];
            imageModels = [];
            statusLine.Text = $"模型目录读取失败：{error.Message.Split('\n')[0]}";
        }
    }

    private static bool HasOperation(JsonElement model, string operation) =>
        model.Array("operations").Any(item => item.ToString() == operation);

    // 对齐网页 selectedTextModels：TEXT+structured_text 模型里保留「已启用且显示」
    // 或当前绑定别名的模型（creatorVisibleModels：隐藏模型不失效已绑定值），
    // quality.inspect 节点还要求 multimodal_analysis 能力。
    private IEnumerable<JsonElement> VisibleTextModels(WorkflowNode node, string currentAlias)
    {
        foreach (var model in textModels)
        {
            if (node.Type == "quality.inspect" && !HasOperation(model, "multimodal_analysis")) continue;
            if (model.Flag("enabled") && model.Flag("display_enabled")
                || model.Text("logical_alias") == currentAlias)
                yield return model;
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

    private async Task LoadWorkflowAsync(string? targetId = null)
    {
        var requestedId = targetId ?? workflowId;
        // #427: 载入请求序号（ScriptView.scriptLoadVersion 同款）：A→B→C 快速切换时
        // 两个 GET 的响应可能乱序返回，迟到响应若照常落地会把画布翻回旧工作流，而
        // workflowId 已指向新工作流——下一次编辑的 ScheduleSave 就会把旧图 PATCH 进
        // 新工作流（版本恰好相等时后端 CAS 也拦不住）。非最新请求的响应整份丢弃。
        var requestVersion = ++workflowLoadVersion;
        try
        {
            var loaded = await Api.SendAsync($"workflows/{requestedId}", cancellation: lifetime.Token);
            if (requestVersion != workflowLoadVersion || lifetime.Token.IsCancellationRequested) return;
            // 提交块：画布归属、当前选择、版本号与 current 同组落地（都在 UI 线程，
            // 中间无 await），三者永不描述不同的工作流。
            current = loaded;
            workflowId = requestedId;
            canvasWorkflowId = requestedId;
            version = current.Number("version");
            nodes.Clear();
            edges.Clear();
            draftPositions.Clear();
            var graph = current.Element("draft_graph");
            foreach (var row in graph.Array("nodes"))
            {
                var node = WorkflowNode.From(row);
                nodes.Add(node);
                AttachNodeHandlers(node);
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
         catch (OperationCanceledException) { }
        catch (Exception error) when (error is not OperationCanceledException)
        {
            statusLine.Text = $"工作流载入失败：{error.Message}";
        }
    }

    private void RenderCanvas()
    {
        canvas.Children.Clear();
        edgePaths.Clear();
        // 选中键可能指向已被 Undo/删节点移除的边，重建前先收敛掉悬空引用
        if (selectedEdgeKey != null && edges.All(e => EdgeKey(e) != selectedEdgeKey)) selectedEdgeKey = null;
        foreach (var edge in edges)
        {
            var from = nodes.FirstOrDefault(n => n.Id == edge.Source);
            var to = nodes.FirstOrDefault(n => n.Id == edge.Target);
            if (from == null || to == null) continue;
            var geometry = EdgeGeometry(from, edge.SourcePort, to, edge.TargetPort);
            if (geometry == null) continue;   // 端口 id 漂移（P3b）：宁可少画不可画错
            var curve = new System.Windows.Shapes.Path
            {
                Data = geometry,
                Stroke = new SolidColorBrush(Color.FromRgb(0x77, 0x84, 0x7C)),
                StrokeThickness = 2,
                IsHitTestVisible = false,   // 点击交给下方加宽的命中层
            };
            // 命中层：透明加宽描边，解决 2px 细线几乎点不中的问题；Tag 携带边模型
            var hit = new System.Windows.Shapes.Path
            {
                Data = geometry,
                Stroke = Brushes.Transparent,
                StrokeThickness = 14,
                Cursor = Cursors.Hand,
                Tag = edge,
            };
            hit.MouseLeftButtonDown += (_, me) =>
            {
                me.Handled = true;   // 不冒泡成空白画布点击
                SelectEdge(edge);
            };
            var key = EdgeKey(edge);
            if (key == selectedEdgeKey) ApplyEdgeSelection(curve, true);
            edgePaths[key] = (curve, hit);
            canvas.Children.Add(curve);
            canvas.Children.Add(hit);
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
        RefreshDeleteButton();   // 重建会收敛悬空的 selectedEdgeKey，按钮态随之刷新
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
        AttachNodeHandlers(node);
        PushHistory(Snapshot("添加节点"));
        Select(node);
        ScheduleSave();
        RenderCanvas();
    }

    private void Select(WorkflowNode node)
    {
        foreach (var other in nodes) other.SetSelected(other == node);
        selected = node;
        ClearEdgeSelection();
        RenderInspector();
    }

    // 连线与节点互斥选中（web 端同一时刻只有一个选中对象驱动 deleteKeyCode）
    private void SelectEdge(WorkflowEdge edge)
    {
        if (selected != null)
        {
            selected.SetSelected(false);
            selected = null;
            RenderInspector();
        }
        var key = EdgeKey(edge);
        if (selectedEdgeKey != key)
        {
            if (selectedEdgeKey != null && edgePaths.TryGetValue(selectedEdgeKey, out var previous))
                ApplyEdgeSelection(previous.Curve, false);
            selectedEdgeKey = key;
        }
        if (edgePaths.TryGetValue(key, out var current)) ApplyEdgeSelection(current.Curve, true);
        RefreshDeleteButton();   // 选中了连线：删除按钮必须可用
        FocusCanvas();   // Delete 删除连线要求键盘焦点在画布，而不是上一次点过的输入框
    }

    private void ClearSelection()
    {
        foreach (var other in nodes) other.SetSelected(false);
        selected = null;
        ClearEdgeSelection();
        RenderInspector();
    }

    private void ClearEdgeSelection()
    {
        if (selectedEdgeKey != null)
        {
            if (edgePaths.TryGetValue(selectedEdgeKey, out var path)) ApplyEdgeSelection(path.Curve, false);
            selectedEdgeKey = null;
        }
        RefreshDeleteButton();   // 选中状态变化（含节点选中覆盖连线选中的路径）都刷新按钮
    }

    private static void ApplyEdgeSelection(System.Windows.Shapes.Path curve, bool isSelected)
    {
        curve.Stroke = new SolidColorBrush(isSelected
            ? Color.FromRgb(0xE7, 0xE2, 0xD7)   // 与节点选中描边同色，视觉语义一致
            : Color.FromRgb(0x77, 0x84, 0x7C));
        curve.StrokeThickness = isSelected ? 3 : 2;
    }

    // 节点入列/恢复后的统一接线：整体拖拽 + 输出端口发起连线 + 锚点实测回调
    private void AttachNodeHandlers(WorkflowNode node)
    {
        node.Element.MouseLeftButtonDown += (s, e) => BeginNodeDrag(s, e, node);
        // P1-1: 布局实测的端口锚点变化（标题换行推挤端口区等）→ 只重画与该
        // 节点相连的边，锚点缓存见 WorkflowNode.MeasureAnchors。
        node.AnchorsChanged = () => RedrawEdgesFor(node);
        foreach (var port in node.Ports)
            if (port.IsOutput)
                port.Element.MouseLeftButtonDown += (s, e) => BeginWireDrag(s, e, port);
            else
                // P3c: 输入端口是连线落点而非拖拽把手——阻断按下冒泡，点击输入
                // 端口不再拖动整个节点（对齐 web Handle 的 mousedown 语义）。
                // 落点解析走 Mouse.DirectlyOver（纯命中测试），不受 Handled 影响。
                port.Element.MouseLeftButtonDown += (_, e) => e.Handled = true;
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

    // ============ 连线拖拽（输出端口 → 输入端口） ============
    // 从输出端口按下即捕获鼠标，画一条虚线预览跟随光标；松开时解析落点：
    // 命中输入端口且校验通过则建边，否则（含空白处）静默取消——与 web 的
    // onConnect/isValidConnection 语义一致，非法落点不弹窗、不留半成品。
    private void BeginWireDrag(object sender, MouseButtonEventArgs e, PortSite source)
    {
        if (e.ChangedButton != MouseButton.Left) return;
        e.Handled = true;   // 端口点击不再冒泡成节点拖拽
        FocusCanvas();      // Escape 取消依赖画布持有键盘焦点
        if (PortAnchor(source.Node, source.Id, true) is not { } anchor)
            return;   // 端口定义已漂移（公式回退也找不到该端口）：不启动连线
        var element = (FrameworkElement)sender;
        element.CaptureMouse();
        pendingWire = new System.Windows.Shapes.Path
        {
            Stroke = new SolidColorBrush(Color.FromRgb(0x77, 0x84, 0x7C)),
            StrokeThickness = 2,
            StrokeDashArray = [4, 3],   // 虚线 = 未提交的预览，与实线正式边区分
            IsHitTestVisible = false,   // 预览线绝不能挡住落点端口的命中
        };
        // P3a: 预览虚线插在边层之后、节点层之前（RenderCanvas 先加边对再加节点，
        // children 前段恰好是 2×边数的边层），不再浮在节点上方。
        canvas.Children.Insert(Math.Min(edgePaths.Count * 2, canvas.Children.Count), pendingWire);
        Mouse.OverrideCursor = Cursors.Cross;
        MouseEventHandler moved = (_, me) =>
        {
            // GetPosition(canvas) 与 Canvas.SetLeft 同一坐标系（文件内节点拖拽同款约定）
            if (pendingWire != null) pendingWire.Data = BezierWire(anchor, me.GetPosition(canvas));
        };
        MouseButtonEventHandler up = null!;
        MouseEventHandler lost = null!;
        var finished = false;   // up 释放捕获会再触发 lost，只结算一次
        void Finish(bool commit)
        {
            if (finished) return;
            finished = true;
            Mouse.RemoveMouseMoveHandler(element, moved);
            Mouse.RemoveMouseUpHandler(element, up);
            Mouse.RemoveLostMouseCaptureHandler(element, lost);
            cancelWire = null;
            if (pendingWire != null)
            {
                canvas.Children.Remove(pendingWire);
                pendingWire = null;
            }
            Mouse.OverrideCursor = null;
            element.ReleaseMouseCapture();
            if (commit) TryConnect(source, PortUnderCursor());
        }
        up = (_, _) => Finish(true);
        lost = (_, _) => Finish(false);   // 失去捕获（切窗等）按取消处理
        cancelWire = () => Finish(false);
        Mouse.AddMouseMoveHandler(element, moved);
        Mouse.AddMouseUpHandler(element, up);
        Mouse.AddLostMouseCaptureHandler(element, lost);
    }

    // 解析光标下的输入端口：鼠标捕获不影响 Mouse.DirectlyOver，预览线已关闭
    // 命中测试，所以 DirectlyOver 就是真实的落点元素；沿视觉树上溯找端口行。
    private PortSite? PortUnderCursor()
    {
        for (var visual = Mouse.DirectlyOver as DependencyObject; visual != null; visual = VisualTreeHelper.GetParent(visual))
            if (visual is FrameworkElement { Tag: PortSite port } && !port.IsOutput)
                return port;
        return null;   // 空白画布/节点本体/输出端口 → 取消
    }

    // ============ 连线契约（纯函数，供 TryConnect 与无 UI 回归检查共用） ============
    // 对齐 web workflow-studio 的 validConnection+connect 与后端 catalog._edge：
    // ① 两端 data_type 必须相同；② 禁自连（同节点）；③ 同端口对（四元组）判重
    // ——确定性边 id 下重复连接会造出同 id 两条边，后端直接 422；④ 四元组任一
    // 为空视为无效（对应 web connect 对 sourceHandle/targetHandle 的真值检查）。
    internal static string EdgeId(string sourceNode, string sourcePort, string targetNode, string targetPort) =>
        $"{sourceNode}:{sourcePort}-{targetNode}:{targetPort}";

    internal static bool CanConnect(
        string sourceNode, string sourcePort, string sourceDataType,
        string targetNode, string targetPort, string targetDataType,
        IEnumerable<(string SourceNode, string SourcePort, string TargetNode, string TargetPort)> existingEdges)
    {
        if (sourceNode.Length == 0 || sourcePort.Length == 0 || targetNode.Length == 0 || targetPort.Length == 0) return false;
        if (sourceNode == targetNode) return false;
        if (!string.Equals(sourceDataType, targetDataType, StringComparison.Ordinal)) return false;
        foreach (var edge in existingEdges)
            if (edge.SourceNode == sourceNode && edge.SourcePort == sourcePort
                && edge.TargetNode == targetNode && edge.TargetPort == targetPort)
                return false;
        return true;
    }

    // 建边规则经 CanConnect 纯函数执行（契约细节见上方注释）。
    private bool TryConnect(PortSite source, PortSite? target)
    {
        if (target == null) return false;
        if (!CanConnect(source.Node.Id, source.Id, source.DataType,
                target.Node.Id, target.Id, target.DataType,
                edges.Select(e => (e.Source, e.SourcePort, e.Target, e.TargetPort))))
            return false;
        edges.Add(new WorkflowEdge(source.Node.Id, source.Id, target.Node.Id, target.Id));
        PushHistory(Snapshot("建立连线"));
        ScheduleSave();   // 与节点操作同一条 脏标记 → 防抖 → 版本化 PATCH 保存链路
        RenderCanvas();
        return true;
    }

    // 只删连线本身，两端节点原样保留（web applyEdgeChanges remove 的语义）
    private void DeleteSelectedEdge()
    {
        if (selectedEdgeKey == null) return;
        edges.RemoveAll(e => EdgeKey(e) == selectedEdgeKey);
        selectedEdgeKey = null;
        PushHistory(Snapshot("删除连线"));
        ScheduleSave();
        RenderCanvas();
    }

    // P2-2: 状态栏「删除」统一分派——选中连线删连线，否则选中节点删节点，
    // 与键盘 Delete 的分派顺序一致（连线优先，节点连线互斥选中）。
    private void DeleteSelection()
    {
        if (selectedEdgeKey != null) DeleteSelectedEdge();
        else DeleteSelected();
    }

    // 无任何选中对象时删除按钮禁用（对齐 web 工具栏 disabled 语义）
    private void RefreshDeleteButton()
    {
        if (removeButton != null) removeButton.IsEnabled = selectedEdgeKey != null || selected != null;
    }

    // 拖拽移动时只更新与该节点相连边的 Path Data，避免全量重绘
    private void RedrawEdgesFor(WorkflowNode node)
    {
        foreach (var edge in edges)
        {
            if (edge.Source != node.Id && edge.Target != node.Id) continue;
            if (!edgePaths.TryGetValue(EdgeKey(edge), out var pair)) continue;
            var from = nodes.FirstOrDefault(n => n.Id == edge.Source);
            var to = nodes.FirstOrDefault(n => n.Id == edge.Target);
            if (from == null || to == null) continue;
            var geometry = EdgeGeometry(from, edge.SourcePort, to, edge.TargetPort);
            if (geometry == null) continue;   // 端口 id 漂移（P3b）：保留原样不更新错位几何
            pair.Curve.Data = geometry;
            pair.Hit.Data = geometry;   // 命中层与可视层共用同一份几何
        }
    }

    private static string EdgeKey(WorkflowEdge edge) => EdgeId(edge.Source, edge.SourcePort, edge.Target, edge.TargetPort);

    // 端口圆心即连线端点。P1-1: 优先读布局后实测的锚点缓存（标题换行会把端口
    // 区推下约 19px/行，公式必然脱锚）；缓存缺失（初帧/未布局，如无头检查）回退
    // 固定公式；端口 id 漂移（两边公式都找不到该端口）返回 null，调用方跳过
    // 该边（P3b：宁可少画不可画错）。
    private static Point? PortAnchor(WorkflowNode node, string portId, bool isOutput)
    {
        if (node.AnchorCache != null && node.AnchorCache.TryGetValue(PortSite.Key(portId, isOutput), out var measured))
            return new Point(node.Position.X + measured.X, node.Position.Y + measured.Y);
        var port = node.Ports.FirstOrDefault(p => p.Id == portId && p.IsOutput == isOutput);
        if (port == null) return null;
        return new Point(
            isOutput ? node.Position.X + NodeWidth - 6 : node.Position.X + 6,
            node.Position.Y + PortRowTop + port.Index * PortRowStep);
    }

    private static PathGeometry BezierWire(Point start, Point end) => new([new PathFigure(start,
        [
            new BezierSegment(new Point(start.X + 60, start.Y), new Point(end.X - 60, end.Y), end, true),
        ], false)]);

    private static PathGeometry? EdgeGeometry(WorkflowNode from, string fromPort, WorkflowNode to, string toPort)
    {
        if (PortAnchor(from, fromPort, true) is not { } start) return null;
        if (PortAnchor(to, toPort, false) is not { } end) return null;
        return BezierWire(start, end);
    }

    private void DeleteSelected()
    {
        if (selected == null) return;
        edges.RemoveAll(e => e.Source == selected.Id || e.Target == selected.Id);
        selectedEdgeKey = null;   // 挂在被删节点上的连线一并移除，选中键随之失效
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
        // #340: 深拷 config。引用赋值会让克隆上的检查器编辑（SetConfig 就地写
        // Config[key]）静默改写原节点，并被自动保存把两个节点 PATCH 成同一份配置。
        clone.Config = DeepCopyConfig(selected.Config);
        nodes.Add(clone);
        AttachNodeHandlers(clone);
        PushHistory(Snapshot("复制节点"));
        Select(clone);
        ScheduleSave();
        RenderCanvas();
    }

    // #340/#344: 复制节点时的 config 深拷。字典级浅拷不够：condition 等嵌套可变值
    // （Create/检查器合并写回存 Dictionary<string,object?>，服务端载入形态是
    // JsonElement）仍会共享同一对象，克隆编辑条件同样串写原节点，所以逐层递归。
    // 标量（string/double/bool）不可变，原样保留。
    private static Dictionary<string, object?> DeepCopyConfig(Dictionary<string, object?> source)
    {
        var copy = new Dictionary<string, object?>(source.Count);
        foreach (var (key, value) in source)
            copy[key] = value switch
            {
                Dictionary<string, object?> nested => DeepCopyConfig(nested),
                List<object?> list => list.Select(item => item is Dictionary<string, object?> inner ? DeepCopyConfig(inner) : item).ToList(),
                JsonElement { ValueKind: not JsonValueKind.Undefined } element => element.Clone(),
                _ => value,
            };
        return copy;
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

    // #344: 快照在 push 时即序列化成字符串（值捕获，不是引用捕获）。这个不变式
    // 依赖 DuplicateSelected 的 config 深拷：若节点间共享可变字典，克隆编辑之后
    // 推入的快照会把被污染的原节点 config 一并冻结，undo/redo 重放时把克隆的
    // 最后编辑写回原节点。
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
            ["id"] = EdgeId(e.Source, e.SourcePort, e.Target, e.TargetPort),
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
                AttachNodeHandlers(node);
            }
            foreach (var row in snapshot.Array("edges"))
                edges.Add(new WorkflowEdge(row.Text("source_node"), row.Text("source_port"), row.Text("target_node"), row.Text("target_port")));
            selected = null;
            selectedEdgeKey = null;   // 快照回放后原选中连线多半已不存在，避免悬空引用
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
        // #342: 防抖计时器在切换工作流/导入时不解除，回调执行时再读 workflowId/
        // version/nodes 字段会描述新工作流—— LoadWorkflowAsync 的 GET 空档里
        // workflowId 已是 B 而 nodes 还是 A 的图，回调会把 A 的图 PATCH 进 B。
        // #427 起切换在 flush 前就前移 workflowId，身份检查改以画布归属为准：
        // 调度时捕获 (画布归属, version) 身份，回调发现画布已换主（或版本已随显式
        // 保存推进、焦点已离开）即放弃——切换/导入/导航路径在换画布前都已 flush
        // 本工作流；匹配时把武装目标传进保存链，排队期间再切换由 SaveNowCoreAsync
        // 的画布归属弃权兜底。
        var armedWorkflow = canvasWorkflowId;
        var armedVersion = version;
        timer.Elapsed += (_, _) => Dispatcher.BeginInvoke(async () =>
        {
            timer.Stop();   // 回调只停自己的计时器，不碰可能已被替换的 autosave 字段
            if (armedWorkflow != canvasWorkflowId || armedWorkflow != workflowId || armedVersion != version) return;   // 陈旧回调：画布/选择已离开（或编辑已随显式保存落盘）
            await SaveNowAsync(armedWorkflow);
        });
        autosave = timer;
        timer.Start();
    }

    // 保存队列尾。所有调用点都在 UI 线程上，读写无需加锁；导航离开时也要
    // await 它，否则 Deactivate 取消令牌会腰斩在途 PATCH。
    private Task saveChain = Task.CompletedTask;

    private Task SaveNowAsync(string? targetWorkflowId = null)
    {
        var previous = saveChain;
        var run = SaveAfterAsync(previous);
        saveChain = run;
        return run;

        async Task SaveAfterAsync(Task before)
        {
            try { await before; } catch (Exception) { }   // 前一次失败不阻塞本次
            await SaveNowCoreAsync(targetWorkflowId);
        }
    }

    private async Task SaveNowCoreAsync(string? targetWorkflowId = null)
    {
        // #427: (归属 id, version, 图) 必须同一时刻快照。本核在保存队列里轮到执行
        // 时，画布可能已经换成别的工作流（或选择已先行切换）——URL 读"当前
        // workflowId"、载荷读当前画布，两者各说各话就会出现 A 的图 PATCH 进
        // workflows/B 的跨界写。PATCH 目标一律取画布归属 canvasWorkflowId；
        // 调用方点名的目标（防抖武装/切换离场 flush）已不等于画布归属时弃权：
        // 那份图的待存编辑已由更早的 flush 落盘，不会丢。
        var graphOwner = canvasWorkflowId;
        if (graphOwner.Length == 0) return;
        if (targetWorkflowId != null && targetWorkflowId != graphOwner) return;
        var graphVersion = version;   // PATCH 在途期间也可能切换：响应落地时不把旧工作流的版本号写进新工作流
        var generationAtSave = generation;
        UpdateStatus("保存中");
        try
        {
            var payload = BuildGraph();
            var saved = await Api.SendAsync($"workflows/{graphOwner}", HttpMethod.Patch,
                new { version = graphVersion, draft_graph = payload }, cancellation: lifetime.Token);
            if (lifetime.Token.IsCancellationRequested) return;
            if (graphOwner != canvasWorkflowId) return;   // PATCH 在途时画布已换主：版本号/定义归属别的工作流，回写会制造伪 409
            version = saved.Number("version");
            current = saved;
            if (generationAtSave == generation) UpdateStatus($"已保存 · 草稿 V{saved.Number("draft_version")}");
            else await SaveNowCoreAsync(targetWorkflowId);   // new edits landed while saving（同槽递归，避免自我等待）
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
            ["id"] = EdgeId(e.Source, e.SourcePort, e.Target, e.TargetPort),
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
         catch (OperationCanceledException) { }
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
         catch (OperationCanceledException) { }
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
        // #342: 导入会换 workflowId，先把当前工作流武装中的防抖编辑落盘（时序同
        // ConfirmLeaveAsync：先取 Enabled 再 Stop——Timer.Stop 会清掉 Enabled，之后
        // 再查就永远查不到武装状态），否则之前打开的工作流的待存编辑被静默丢弃。
        var pendingFlush = autosave is { Enabled: true };
        autosave?.Stop();
        if (pendingFlush) await SaveNowAsync();
        else await saveChain;
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
         catch (OperationCanceledException) { }
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
         catch (OperationCanceledException) { }
        catch (Exception error) when (error is not OperationCanceledException)
        {
            MessageBox.Show(Host, error.Message, "运行未启动", MessageBoxButton.OK, MessageBoxImage.Warning);
        }
    }

    private JsonElement latestRun;
    private bool runActive;
    // #380：仅剩 PAUSED（审批栅栏）时轮询不再停摆，但重取节流到约 10s 一次；
    // lastPausedFetchTicks 记录上一次 PAUSED 数据落地的 Environment.TickCount64
    // （任何一次取回 PAUSED 列表的请求都会重置窗口），PollTick 据此降频。
    private bool runPaused;
    private long lastPausedFetchTicks;

    // 运行数据端点与刷新时机对齐网页：GET workflows/{id}/runs（后端 created_at
    // 倒序）；启动/取消/审批后立即重取；轮询条件是列表里任一运行处于 RUNNING
    // 或 PAUSED（#380：审批栅栏不该让轮询停摆）——RUNNING 每 tick 重取（网页
    // refetchInterval 3000），仅剩 PAUSED 时降频为约 10s 一次（网页 10000）。
    private async Task LoadRunsAsync()
    {
        // 防重入：上一轮请求未返回时跳过本轮，避免晚到的旧响应覆盖新结果
        if (Interlocked.CompareExchange(ref runsLoading, 1, 0) != 0) return;
        try
        {
            var runs = await Api.SendAsync($"workflows/{workflowId}/runs", cancellation: lifetime.Token);
            if (lifetime.Token.IsCancellationRequested) return;
            runRows = runs.EnumerateArray().ToList();
            runMonitor.Children.Clear();
            approvalQueue.Children.Clear();
            runActive = runRows.Any(row => row.Text("status") == "RUNNING");
            // #380：无 RUNNING 但存在 PAUSED（审批栅栏）时也算活跃：别端的审批/
            // 恢复/取消都会推进 run，停摆会把页脚与节点徽标冻结在旧数据上。
            runPaused = !runActive && runRows.Any(row => row.Text("status") == "PAUSED");
            // 任何一次取回 PAUSED 列表的请求都重置 10s 节流窗口（含手动刷新与
            // 动作后的立即重取），PollTick 只在窗口外才降频补拉。
            if (runPaused) lastPausedFetchTicks = Environment.TickCount64;
            if (runRows.Count == 0)
            {
                foreach (var node in nodes) node.SetRunStatus(null);
                runMonitor.Children.Add(new TextBlock
                {
                    Text = "尚未运行已发布版本",
                    Foreground = new SolidColorBrush(Color.FromRgb(0xA4, 0xAD, 0xA7)), FontSize = 12,
                    VerticalAlignment = VerticalAlignment.Center,
                });
                RenderRunHistory();
                return;
            }
            latestRun = runRows[0];   // 后端倒序，首条即网页 displayedRun 的 runs.data[0] 回退
            var nodeRuns = latestRun.Array("node_runs");
            var done = nodeRuns.Count(n => n.Text("status") == "COMPLETED");
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
            // #365：审批栅栏会把 run 置为 PAUSED（workflow_engine/reconciliation 的
            // 栅栏语义），而 cancel_run 只拒绝终态（lifecycle 排除 COMPLETED/
            // CANCELLED/FAILED）——取消入口必须在 PAUSED 也可用；否则同 scope 的
            // 重复运行守卫 409（planning 把 PAUSED 算活跃）会指示一个 UI 上做不到
            // 的动作。取消仍是唯一的停止途径，不发明别的端点。
            if (latestRun.Text("status") is "RUNNING" or "PAUSED")
            {
                var cancel = Kit.Act("取消", async (_, _) =>
                {
                    try
                    {
                        await Api.SendAsync($"workflow-runs/{latestRun.Text("id")}/cancel", HttpMethod.Post, cancellation: lifetime.Token);
                        await LoadRunsAsync();
                    }
                    catch (OperationCanceledException) { }
                    catch (Exception error) { MessageBox.Show(Host, error.Message, "取消失败"); }
                }, "Compact");
                cancel.Margin = new Thickness(12, 0, 0, 0);
                runMonitor.Children.Add(cancel);
            }
            RenderApprovals(latestRun);
            RenderRunHistory();
        }
         catch (OperationCanceledException) { }
        catch (Exception error) when (error is not OperationCanceledException)
        {
            _ = error;
        }
        finally
        {
            Interlocked.Exchange(ref runsLoading, 0);
        }
    }

    // ============ 审批队列（网页 footer 的 WAITING_APPROVAL 行为基准） ============
    // 审批行来自「当前展示运行」的 node_runs（网页 displayedRun.node_runs 过滤
    // WAITING_APPROVAL）。网页对等待节点只有「确认继续」（approve 端点）；
    // 网页与后端都没有节点级「拒绝」动作——拒绝语义由运行级「取消」承担
    // （cancel_run 是唯一的停止途径），桌面保持一致，不发明 reject 端点。
    private void RenderApprovals(JsonElement run)
    {
        foreach (var nodeRun in run.Array("node_runs").Where(item => item.Text("status") == "WAITING_APPROVAL"))
        {
            var isGenerator = nodeRun.Text("node_type") == "generator.page";
            var row = new StackPanel { Orientation = Orientation.Horizontal, Margin = new Thickness(0, 7, 0, 0) };
            row.Children.Add(new TextBlock
            {
                Text = isGenerator ? "单页生成等待选择模型" : "采用候选后继续",
                FontWeight = FontWeights.Bold, FontSize = 12,
                Foreground = new SolidColorBrush(Color.FromRgb(0xE9, 0xE6, 0xDD)),
                VerticalAlignment = VerticalAlignment.Center, Margin = new Thickness(0, 0, 10, 0),
            });
            Button approve = null!;
            if (isGenerator)
            {
                // 图片模型列表 = 网页 imageModels（IMAGE+image_edit 再过 creatorVisibleModels）
                var modelBox = new ComboBox { Width = 220, MaxDropDownHeight = 320 };
                System.Windows.Automation.AutomationProperties.SetName(modelBox, "选择图片模型");
                modelBox.Items.Add(new ComboBoxItem { Tag = "", Content = "选择图片模型" });
                foreach (var model in imageModels.Where(m =>
                             (m.Flag("enabled") && m.Flag("display_enabled")) || m.Text("logical_alias") == drawModel))
                    modelBox.Items.Add(new ComboBoxItem
                    {
                        Tag = model.Text("logical_alias"),
                        Content = $"{model.Text("provider")} · {model.Text("display_name")}",
                    });
                modelBox.SelectedItem = modelBox.Items.OfType<ComboBoxItem>().FirstOrDefault(item => (string?)item.Tag == drawModel)
                    ?? modelBox.Items.OfType<ComboBoxItem>().First();
                modelBox.SelectionChanged += (_, _) =>
                {
                    if (modelBox.SelectedItem is ComboBoxItem { Tag: string value })
                    {
                        drawModel = value;
                        // 网页：generator.page 未选模型前「确认继续」禁用；#391 起与 web
                        // 谓词同构——选中别名还必须仍是 imageModels 目录成员（web 的
                        // imageModels.some 口径；桌面按全目录 Any 判定：豁免行
                        // alias==drawModel 时 Any 必真，与含豁免行的可见集等价）。
                        approve.IsEnabled = !approving && drawModel.Length > 0
                            && imageModels.Any(m => m.Text("logical_alias") == drawModel);
                    }
                };
                var resolutionBox = new ComboBox { Width = 76, Margin = new Thickness(8, 0, 0, 0) };
                System.Windows.Automation.AutomationProperties.SetName(resolutionBox, "选择图片清晰度");
                foreach (var option in new[] { "1K", "2K", "4K" }) resolutionBox.Items.Add(option);
                resolutionBox.SelectedItem = drawResolution;
                resolutionBox.SelectionChanged += (_, _) =>
                {
                    if (resolutionBox.SelectedItem is string value) drawResolution = value;
                };
                row.Children.Add(modelBox);
                row.Children.Add(resolutionBox);
            }
            else
            {
                // 网页此处是跳转单页生成页采用候选的链接
                var adopt = Kit.Act("前往采用", async (_, _) => await Context!.NavigateSection("generate", ""), "Compact");
                adopt.Margin = new Thickness(0, 0, 8, 0);
                row.Children.Add(adopt);
            }
            approve = Kit.Act("确认继续", async (_, _) => await ApproveNodeAsync(nodeRun), "CompactInk");
            approve.Margin = new Thickness(8, 0, 0, 0);
            // #391：别名失效后重渲染，下拉回退占位项的赋值发生在 SelectionChanged
            // 挂接之前，事件不触发、drawModel 保留失效别名——初始使能必须用与上面
            // 及 web 相同的目录成员资格谓词，拦下 image_model_alias 已不在目录的提交。
            approve.IsEnabled = !approving
                && (!isGenerator || (drawModel.Length > 0 && imageModels.Any(m => m.Text("logical_alias") == drawModel)));
            row.Children.Add(approve);
            approvalQueue.Children.Add(row);
        }
    }

    // 审批动作对齐网页 approveWorkflowNode：GENERATE 栅栏必须携带显式图片模型与
    // 清晰度，APPROVE 栅栏载荷为空对象；成功后重取 runs（网页 runs.refetch）。
    private async Task ApproveNodeAsync(JsonElement nodeRun)
    {
        if (approving) return;
        approving = true;
        try
        {
            var isGenerator = nodeRun.Text("node_type") == "generator.page";
            var payload = isGenerator
                ? new { image_model_alias = drawModel.Length > 0 ? drawModel : null, resolution = drawResolution }
                : (object)new { };
            await Api.SendAsync(
                $"workflow-runs/{nodeRun.Text("workflow_run_id")}/nodes/{nodeRun.Text("node_id")}/approve",
                HttpMethod.Post, payload, cancellation: lifetime.Token);
            statusLine.Text = "已确认继续";
            await LoadRunsAsync();
        }
        catch (OperationCanceledException) { }
        catch (Exception error) when (error is not OperationCanceledException)
        {
            statusLine.Text = $"审批失败：{error.Message.Split('\n')[0]}";
        }
        finally
        {
            approving = false;
        }
    }

    // ============ 运行历史（桌面补充的只读列表） ============
    // 网页只把 runs 列表用于页脚当前运行态与审批行；桌面把同一份数据补一个
    // 历史列表：状态 / 范围 / 节点进度 / 时间。记录无「触发者」字段（后端
    // WorkflowRunRead 不含操作人），以运行范围代替。
    private void RenderRunHistory()
    {
        runHistory.Children.Clear();
        if (runRows.Count == 0)
        {
            runHistory.Children.Add(new TextBlock
            {
                Text = "尚未运行已发布版本",
                Foreground = new SolidColorBrush(Color.FromRgb(0xA4, 0xAD, 0xA7)), FontSize = 12,
            });
            return;
        }
        foreach (var run in runRows.Take(8))
        {
            var nodeRuns = run.Array("node_runs");
            var done = nodeRuns.Count(item => item.Text("status") == "COMPLETED");
            var time = DateTimeOffset.TryParse(run.Text("created_at"), out var date)
                ? date.ToLocalTime().ToString("MM-dd HH:mm") : "";
            runHistory.Children.Add(new TextBlock
            {
                Text = $"{Labels.Map(Labels.WorkflowRunStatus, run.Text("status"))} · {ScopeLabel(run)} · {done}/{nodeRuns.Count} · {time}",
                Foreground = new SolidColorBrush(Color.FromRgb(0xE9, 0xE6, 0xDD)),
                FontSize = 12, Margin = new Thickness(0, 6, 0, 0), TextWrapping = TextWrapping.Wrap,
            });
        }
    }

    private string ScopeLabel(JsonElement run)
    {
        var id = run.Text("scope_id");
        return run.Text("scope_type") switch
        {
            "CHAPTER" => chapters.FirstOrDefault(c => c.Id == id) is { } chapter ? $"第 {chapter.Ordinal} 章" : "章节范围",
            "PAGE" => scopePages.FirstOrDefault(p => p.Id == id) is { } page ? $"第 {page.PageNumber} 页" : "页面范围",
            "CANDIDATE" => "候选范围",
            "PROJECT" => "项目范围",
            var type => type,
        };
    }

    private void UpdateStatus(string label)
    {
        var published = current.Text("published_version_id").Length > 0;
        statusLine.Text = $"{label} · 草稿 V{current.Number("draft_version")} · 已发布 {(published ? "版本就绪" : "尚未发布")}";
    }

    // ============ 节点检查器（配置项/出现条件/值域对齐网页 workflow-studio） ============
    // 每项的出现条件、默认值与写回键名逐项对齐网页：
    // - 建议清晰度：仅 generator.page（默认 1K，写回 resolution）；
    // - 文本模型+温度：仅 config.model_alias 为真值时（agent./quality./director.
    //   新节点默认 "auto"），模型下拉来源 = 网页 selectedTextModels 过滤逻辑；
    // - 超时/重试/提示词/备注：所有节点；condition 三件套：仅 control.condition。
    // 并发/锁定/需要审批三项网页检查器未渲染（仅存在于 config 契约），桌面按后端
    // schema 的值域补上编辑入口，见文末差异说明。
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
        var config = node.ConfigElement;

        var name = new TextBox { Text = node.Name };
        System.Windows.Automation.AutomationProperties.SetName(name, "节点名称");
        // SetName 同步画布标题；标题换行变化会推挤端口区并触发锚点重测+边重画
        name.TextChanged += (_, _) => { node.SetName(name.Text); ScheduleSave(); };

        inspector.Children.Add(DarkLabel("节点名称"));
        inspector.Children.Add(name);
        inspector.Children.Add(DarkLabel("节点类型"));
        inspector.Children.Add(new TextBlock { Text = node.Type, Foreground = new SolidColorBrush(Color.FromRgb(0xA4, 0xAD, 0xA7)), FontFamily = (FontFamily)Application.Current.FindResource("Mono"), FontSize = 12 });

        // generator.page：图片模型在审批时必须显式选择（网页检查器为只读说明 + 建议清晰度）
        if (node.Type == "generator.page")
        {
            inspector.Children.Add(DarkLabel("模型"));
            var modelNote = new TextBox { Text = "必须显式选择供应商图片模型", IsReadOnly = true };
            System.Windows.Automation.AutomationProperties.SetName(modelNote, "模型说明");
            inspector.Children.Add(modelNote);
            var resolution = new ComboBox { Width = 130 };
            System.Windows.Automation.AutomationProperties.SetName(resolution, "建议清晰度");
            foreach (var option in new[] { "1K", "2K", "4K" }) resolution.Items.Add(option);
            resolution.SelectedItem = config.Text("resolution", "1K") is "1K" or "2K" or "4K" ? config.Text("resolution", "1K") : "1K";
            resolution.SelectionChanged += (_, _) =>
            {
                if (resolution.SelectedItem is string value) { node.SetConfig("resolution", value); ScheduleSave(); }
            };
            inspector.Children.Add(DarkLabel("建议清晰度"));
            inspector.Children.Add(resolution);
        }

        // 文本模型+温度：网页在 config.model_alias 为真值时才渲染（新 agent./quality./
        // director. 节点默认 "auto"，source/generator 节点为 null → 不出现）。
        var alias = config.Text("model_alias");
        if (alias.Length > 0)
        {
            var aliasBox = new ComboBox();
            System.Windows.Automation.AutomationProperties.SetName(aliasBox, "文本模型");
            // 网页 workflow-studio 的下拉只有 selectedTextModels，"auto" 值无对应选项
            // 会渲染成空白；桌面固定提供 auto 选项（文案对齐网页设置页「自动路由」），
            // 保证默认值可见且可改回。
            aliasBox.Items.Add(new ComboBoxItem { Tag = "auto", Content = "自动路由" });
            foreach (var model in VisibleTextModels(node, alias))
                aliasBox.Items.Add(new ComboBoxItem
                {
                    Tag = model.Text("logical_alias"),
                    Content = $"{model.Text("provider")} · {model.Text("display_name")}",
                });
            aliasBox.SelectedItem = aliasBox.Items.OfType<ComboBoxItem>().FirstOrDefault(item => (string?)item.Tag == alias);
            aliasBox.SelectionChanged += (_, _) =>
            {
                if (aliasBox.SelectedItem is ComboBoxItem { Tag: string value }) { node.SetConfig("model_alias", value); ScheduleSave(); }
            };
            inspector.Children.Add(DarkLabel("文本模型"));
            inspector.Children.Add(aliasBox);

            var temperature = new TextBox
            {
                Text = config.Decimal("temperature", 0.2).ToString("0.###", CultureInfo.InvariantCulture),
                Width = 130,
            };
            System.Windows.Automation.AutomationProperties.SetName(temperature, "温度");
            AttachNumberEditor(temperature, node, "temperature", 0, 2, 0.2, round: false);
            inspector.Children.Add(DarkLabel("温度（0-2）"));
            inspector.Children.Add(temperature);
        }

        var timeout = new TextBox { Text = ((int)config.Decimal("timeout_seconds", 900)).ToString(), Width = 130 };
        System.Windows.Automation.AutomationProperties.SetName(timeout, "超时（秒）");
        AttachNumberEditor(timeout, node, "timeout_seconds", 30, 3600, 900, round: true);
        var retries = new TextBox { Text = ((int)config.Decimal("max_attempts", 3)).ToString(), Width = 130 };
        System.Windows.Automation.AutomationProperties.SetName(retries, "重试次数");
        AttachNumberEditor(retries, node, "max_attempts", 1, 10, 3, round: true);
        inspector.Children.Add(DarkLabel("超时（秒）"));
        inspector.Children.Add(timeout);
        inspector.Children.Add(DarkLabel("重试次数"));
        inspector.Children.Add(retries);

        // 桌面补充（网页检查器未渲染）：并发值域来自后端 schema（1-8，默认 1）
        var concurrency = new TextBox { Text = ((int)config.Decimal("concurrency", 1)).ToString(), Width = 130 };
        System.Windows.Automation.AutomationProperties.SetName(concurrency, "并发");
        AttachNumberEditor(concurrency, node, "concurrency", 1, 8, 1, round: true);
        inspector.Children.Add(DarkLabel("并发（1-8）"));
        inspector.Children.Add(concurrency);

        var prompt = new TextBox
        {
            AcceptsReturn = true, MinHeight = 70,
            Text = config.Text("prompt_template"),
        };
        System.Windows.Automation.AutomationProperties.SetName(prompt, "提示词");
        prompt.TextChanged += (_, _) => { node.SetConfig("prompt_template", prompt.Text.Length == 0 ? null : prompt.Text); ScheduleSave(); };

        inspector.Children.Add(DarkLabel("提示词"));
        inspector.Children.Add(prompt);

        // condition 三件套：仅 control.condition（默认 path="$"、operator="exists"）
        if (node.Type == "control.condition")
        {
            var path = new TextBox { Text = ConditionText(node, "path", "$"), Width = 180 };
            System.Windows.Automation.AutomationProperties.SetName(path, "JSON 路径");
            path.TextChanged += (_, _) => { SetConditionValue(node, "path", path.Text); ScheduleSave(); };
            var operators = new ComboBox { Width = 130 };
            System.Windows.Automation.AutomationProperties.SetName(operators, "比较符");
            foreach (var (value, label) in new[]
                     {
                         ("exists", "存在"), ("eq", "等于"), ("ne", "不等于"), ("contains", "包含"),
                         ("gt", "大于"), ("gte", "大于等于"), ("lt", "小于"), ("lte", "小于等于"),
                     })
                operators.Items.Add(new ComboBoxItem { Tag = value, Content = label });
            var currentOperator = ConditionText(node, "operator", "exists");
            operators.SelectedItem = operators.Items.OfType<ComboBoxItem>()
                .FirstOrDefault(item => (string?)item.Tag == currentOperator)
                ?? operators.Items.OfType<ComboBoxItem>().First(item => (string?)item.Tag == "exists");
            operators.SelectionChanged += (_, _) =>
            {
                if (operators.SelectedItem is ComboBoxItem { Tag: string value }) { SetConditionValue(node, "operator", value); ScheduleSave(); }
            };
            var conditionValue = new TextBox { Text = ConditionText(node, "value", ""), Width = 180 };
            System.Windows.Automation.AutomationProperties.SetName(conditionValue, "比较值");
            conditionValue.TextChanged += (_, _) => { SetConditionValue(node, "value", conditionValue.Text); ScheduleSave(); };
            inspector.Children.Add(DarkLabel("JSON 路径"));
            inspector.Children.Add(path);
            inspector.Children.Add(DarkLabel("比较符"));
            inspector.Children.Add(operators);
            inspector.Children.Add(DarkLabel("比较值"));
            inspector.Children.Add(conditionValue);
        }

        var notes = new TextBox
        {
            AcceptsReturn = true, MinHeight = 44,
            Text = config.Text("notes"),
        };
        System.Windows.Automation.AutomationProperties.SetName(notes, "备注");
        notes.TextChanged += (_, _) => { node.SetConfig("notes", notes.Text.Length == 0 ? null : notes.Text); ScheduleSave(); };
        inspector.Children.Add(DarkLabel("备注"));
        inspector.Children.Add(notes);

        // 桌面补充（网页检查器未渲染，config 契约支持）：锁定 / 需要审批
        var locked = new CheckBox
        {
            Content = "锁定", IsChecked = config.Flag("locked"), Margin = new Thickness(0, 12, 0, 0),
            Foreground = new SolidColorBrush(Color.FromRgb(0xE9, 0xE6, 0xDD)),
        };
        System.Windows.Automation.AutomationProperties.SetName(locked, "锁定");
        locked.Checked += (_, _) => { node.SetConfig("locked", true); ScheduleSave(); };
        locked.Unchecked += (_, _) => { node.SetConfig("locked", false); ScheduleSave(); };
        var requiresApproval = new CheckBox
        {
            Content = "需要审批", IsChecked = config.Flag("requires_approval"), Margin = new Thickness(0, 6, 0, 0),
            Foreground = new SolidColorBrush(Color.FromRgb(0xE9, 0xE6, 0xDD)),
        };
        System.Windows.Automation.AutomationProperties.SetName(requiresApproval, "需要审批");
        requiresApproval.Checked += (_, _) => { node.SetConfig("requires_approval", true); ScheduleSave(); };
        requiresApproval.Unchecked += (_, _) => { node.SetConfig("requires_approval", false); ScheduleSave(); };
        inspector.Children.Add(locked);
        inspector.Children.Add(requiresApproval);
    }

    // 网页数值输入框的等价行为：可解析的输入立即写回钳制值（网页 onChange 里
    // Number.isFinite 检查后 Math.min/max 钳制写回），半输入（"1e"）保持旧值。
    // 网页 input 是受控组件、写回会同步重渲显示；桌面在失焦时才把显示对齐到已
    // 写回的钳制值，避免输入过程中重置光标。
    private void AttachNumberEditor(TextBox box, WorkflowNode node, string key, double min, double max, double fallback, bool round)
    {
        box.TextChanged += (_, _) =>
        {
            var text = box.Text.Trim();
            if (text.Length == 0 || !double.TryParse(text, NumberStyles.Float, CultureInfo.InvariantCulture, out var parsed)) return;
            var value = Math.Clamp(round ? Math.Round(parsed) : parsed, min, max);
            node.SetConfig(key, round ? (int)value : value);
            ScheduleSave();
        };
        box.LostFocus += (_, _) =>
        {
            var value = node.ConfigElement.Decimal(key, fallback);
            box.Text = round
                ? ((int)value).ToString(CultureInfo.InvariantCulture)
                : value.ToString("0.###", CultureInfo.InvariantCulture);
        };
    }

    // condition 子键读取（网页 String(condition.path ?? "$") 等默认值语义）
    private static string ConditionText(WorkflowNode node, string key, string fallback)
    {
        var condition = node.ConfigElement.Element("condition");
        return condition.ValueKind == JsonValueKind.Object ? condition.Text(key, fallback) : fallback;
    }

    // condition 写回走「合并已有键再覆盖单键」（网页 { ...condition, path } 展开
    // 语义），不丢弃 path/operator/value 之外的键。
    private static void SetConditionValue(WorkflowNode node, string key, object? value)
    {
        var merged = new Dictionary<string, object?>();
        if (node.Config.TryGetValue("condition", out var raw))
        {
            if (raw is JsonElement { ValueKind: JsonValueKind.Object } element)
                foreach (var property in element.EnumerateObject()) merged[property.Name] = property.Value.Clone();
            else if (raw is Dictionary<string, object?> dictionary)
                foreach (var entry in dictionary) merged[entry.Key] = entry.Value;
        }
        merged[key] = value;
        node.SetConfig("condition", merged);
    }

    private static TextBlock DarkLabel(string text) => new()
    {
        Text = text, FontSize = 11, FontWeight = FontWeights.Bold,
        Foreground = new SolidColorBrush(Color.FromRgb(0xA4, 0xAD, 0xA7)), Margin = new Thickness(0, 12, 0, 4),
    };

    public override void PollTick()
    {
        // #380：RUNNING 每 tick 重取；仅剩 PAUSED 时节流到约 10s 一次（网页
        // refetchInterval 3000/10000 的桌面同构契约）；全部终态不轮询。
        if (runActive) _ = LoadRunsAsync();
        else if (runPaused && Environment.TickCount64 - lastPausedFetchTicks >= 10_000) _ = LoadRunsAsync();
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
        // Capture the armed state BEFORE Stop() — Timer.Stop() clears Enabled,
        // so checking it afterwards never flushed the pending 800ms debounce and
        // the last edits were silently lost on navigation.
        var pendingFlush = autosave is { Enabled: true };
        autosave?.Stop();
        if (pendingFlush) await SaveNowAsync();
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
        private TextBlock? titleBlock;
        // 端口站点表：连线拖拽的发起端与命中端都从这里取（Tag 挂在端口行 Border 上）
        public readonly List<PortSite> Ports = [];
        // P1-1: 实测锚点缓存——键 PortSite.Key(端口)，值为端口圆点圆心相对节点根
        // 元素左上角的偏移；布局完成前为 null（PortAnchor 回退固定公式）。
        public Dictionary<string, Point>? AnchorCache;
        // 实测锚点与旧值有实际变化（标题换行推挤端口区等）时由 MeasureAnchors
        // 触发；视图接线成 RedrawEdgesFor（见 AttachNodeHandlers）。
        public Action? AnchorsChanged;

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

        // 重命名同步画布标题；换行数变化会推挤端口区 → SizeChanged → 重测锚点 → 重画边
        public void SetName(string name)
        {
            Name = name;
            if (titleBlock == null || titleBlock.Text == name) return;
            titleBlock.Text = name;
        }

        // 推迟到布局稳定后测量：Loaded/SizeChanged 触发时同轮布局可能尚未完成，
        // 立即 TranslatePoint 会读到半途位置。
        private void QueueAnchorMeasure() =>
            Element.Dispatcher.BeginInvoke(DispatcherPriority.Loaded, new Action(MeasureAnchors));

        // P1-1: 实测各端口圆点圆心（相对节点根元素）。web 端 React Flow 从 Handle
        // 的 DOM 实测位置画边，这里等价：端口圆点无论被标题换行推到哪里，锚点
        // 跟着圆点走。未完成布局的端口直接放弃本次测量，等下一个信号重测。
        public void MeasureAnchors()
        {
            if (Ports.Count == 0 || !Element.IsArrangeValid) return;
            var measured = new Dictionary<string, Point>();
            foreach (var port in Ports)
            {
                if (port.Dot is not { } dot || !dot.IsArrangeValid) return;
                measured[PortSite.Key(port.Id, port.IsOutput)] = dot.TranslatePoint(new Point(dot.Width / 2, dot.Height / 2), Element);
            }
            if (AnchorCache != null && AnchorCache.Count == measured.Count)
            {
                var unchanged = true;
                foreach (var entry in measured)
                    if (!AnchorCache.TryGetValue(entry.Key, out var prior)
                        || Math.Abs(prior.X - entry.Value.X) > 0.5 || Math.Abs(prior.Y - entry.Value.Y) > 0.5)
                    { unchanged = false; break; }
                if (unchanged) return;   // 亚像素抖动不触发重绘
            }
            AnchorCache = measured;
            AnchorsChanged?.Invoke();
        }

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
            titleBlock = title;
            // P1-1: 标题换行改变端口区起点——尺寸一变就排队重测端口锚点
            title.SizeChanged += (_, _) => QueueAnchorMeasure();
            var ports = new Grid { Margin = new Thickness(0, 0, 0, 8), MinHeight = 40 };
            ports.ColumnDefinitions.Add(new ColumnDefinition());
            ports.ColumnDefinitions.Add(new ColumnDefinition());
            var inputs = new StackPanel { Margin = new Thickness(0, 0, 4, 0) };
            if (Inputs.ValueKind == JsonValueKind.Array)
                foreach (var input in Inputs.EnumerateArray())
                {
                    var port = new PortSite
                    {
                        Node = this, Id = input.Text("id"), DataType = input.Text("data_type"),
                        IsOutput = false, Index = inputs.Children.Count,
                    };
                    Ports.Add(port);
                    inputs.Children.Add(BuildPortRow(port, input.Text("label")));
                }
            var outputs = new StackPanel { Margin = new Thickness(4, 0, 0, 0) };
            if (Outputs.ValueKind == JsonValueKind.Array)
                foreach (var output in Outputs.EnumerateArray())
                {
                    var port = new PortSite
                    {
                        Node = this, Id = output.Text("id"), DataType = output.Text("data_type"),
                        IsOutput = true, Index = outputs.Children.Count,
                    };
                    Ports.Add(port);
                    outputs.Children.Add(BuildPortRow(port, output.Text("label")));
                }
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
                QueueAnchorMeasure();   // 载入（含 topBar 插入引起的重排）后重测端口锚点
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

        // 端口行 = 整行命中区（Tag 携带 PortSite 供落点解析）+ data_type 圆点 + 标签。
        // 圆点配色与 web 端 .handle.text/.json/.image/... 一致；负边距让圆点探出
        // 节点边缘，圆心正好落在连线锚点上（web 的 Handle 同款出位方式）。
        private static FrameworkElement BuildPortRow(PortSite port, string label)
        {
            var dot = new Ellipse
            {
                Width = 10, Height = 10,
                Fill = new SolidColorBrush(PortColor(port.DataType)),
                Stroke = new SolidColorBrush(Color.FromRgb(0x16, 0x19, 0x17)), StrokeThickness = 1,
                VerticalAlignment = VerticalAlignment.Center,
            };
            var text = new TextBlock
            {
                Text = $"{label}  {port.DataType}", FontSize = 10,
                Foreground = new SolidColorBrush(Color.FromRgb(0xA4, 0xAD, 0xA7)),
                VerticalAlignment = VerticalAlignment.Center,
            };
            StackPanel content;
            if (port.IsOutput)
            {
                content = new StackPanel { Orientation = Orientation.Horizontal, HorizontalAlignment = HorizontalAlignment.Right };
                text.Margin = new Thickness(0, 0, 4, 0);
                dot.Margin = new Thickness(0, 0, -9, 0);
                content.Children.Add(text);
                content.Children.Add(dot);
            }
            else
            {
                content = new StackPanel { Orientation = Orientation.Horizontal };
                dot.Margin = new Thickness(-9, 0, 4, 0);
                content.Children.Add(dot);
                content.Children.Add(text);
            }
            var row = new Border
            {
                MinHeight = PortRowStep,   // 行距即锚点间距，端口行正好落在连线锚点上
                Padding = new Thickness(port.IsOutput ? 4 : 9, 0, port.IsOutput ? 9 : 4, 0),
                Tag = port,
                Child = content,
            };
            port.Element = row;
            port.Dot = dot;   // P1-1: 锚点实测的测量对象就是端口圆点本身
            if (port.IsOutput) row.Cursor = Cursors.Cross;   // 可拖出连线的手势提示
            return row;
        }

        private static Color PortColor(string dataType) => dataType switch
        {
            "text" => Color.FromRgb(0x3C, 0x8D, 0x78),
            "json" => Color.FromRgb(0x3C, 0x76, 0x98),
            "image" => Color.FromRgb(0xB8, 0x4A, 0x38),
            "asset" => Color.FromRgb(0xA0, 0x7B, 0x39),
            "report" => Color.FromRgb(0x80, 0x67, 0xA5),
            "boolean" => Color.FromRgb(0xD0, 0xC6, 0x5E),
            _ => Color.FromRgb(0x77, 0x84, 0x7C),
        };
    }

    // 端口站点：节点内一个可命中的输入/输出端口，携带建边校验所需的全部信息；
    // Element 由 BuildPortRow 回填为端口行 Border，Dot 回填为端口圆点（锚点实测
    // 的测量对象），Key 生成锚点缓存键（输入/输出端口 id 可能重名，须带方向）。
    private sealed class PortSite
    {
        public required WorkflowNode Node { get; init; }
        public required string Id { get; init; }
        public required string DataType { get; init; }
        public required bool IsOutput { get; init; }
        public int Index { get; init; }
        public FrameworkElement Element { get; internal set; } = null!;
        public Ellipse? Dot { get; internal set; }

        internal static string Key(string portId, bool isOutput) => (isOutput ? "o:" : "i:") + portId;
    }

    private sealed record WorkflowEdge(string Source, string SourcePort, string Target, string TargetPort);
}
