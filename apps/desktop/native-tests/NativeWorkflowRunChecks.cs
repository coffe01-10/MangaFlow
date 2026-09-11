using System.Net;
using System.Net.Http;
using System.Reflection;
using System.Text.Json;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Controls.Primitives;
using MangaFlow.Native;
using MangaFlow.Native.Services;
using MangaFlow.Native.Views;

// F3 回归：WorkflowView 节点检查器完整配置项 + 运行历史/审批队列。此前检查器只
// 渲染 name/timeout/retries/prompt/notes，与网页 workflow-studio.tsx 的完整
// 配置面（model_alias/temperature/concurrency/resolution/locked/condition/
// requires_approval）不一致；runs 数据也只喂给页脚摘要，无历史与审批队列。
//
// 本文件覆盖：
//  ① 检查器按节点类型渲染的配置项集合（generator.page 有建议清晰度、agent 类有
//     文本模型+温度、control.condition 有三件套，出现条件对齐网页）；
//  ② 配置编辑写回 config 的键名与值域钳制（temperature 越界钳制 0-2、半输入不
//     写回；timeout 30-3600 / attempts 1-10 / concurrency 1-8；condition 合并写回）；
//  ③ 运行历史与审批队列的端点+载荷+动作后刷新（假 Handler 断言 GET runs、
//     POST approve 的请求路径与 JSON 载荷、审批后列表重取）。
//
// 注册说明（需 lead 注册）：本仓库的发现机制是 NativeInteractionChecks.RunIsolated
// 里的手动调用列表（--render STA 链，Application 已带 Theme 资源）。因文件级互斥
// 本文件未自行接线——需在该列表加入 `await NativeWorkflowRunChecks.Run();`。
// 直接 new WorkflowView() 依赖 Application.Current 的 Theme 资源（Kit.Act）与
// Mono/Serif 字体资源（WorkflowNode.BuildVisual），所以必须运行在 --render 的
// STA 线程上，而不是默认 STA 主线程。全部检查零网络：Part A/B 不 Activate，
// Part C 用假 HttpMessageHandler。
internal static class NativeWorkflowRunChecks
{
    private const BindingFlags All = BindingFlags.Static | BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic;

    public static async Task Run()
    {
        InspectorShapeChecks();
        ConfigWriteBackChecks();
        await RunsAndApprovalChecks();
        await DuplicateAutosaveChecks();
        Console.WriteLine("PASS: workflow inspector full config surface, clamped write-back, run history and approval queue wire contract");
    }

    // ── ① 检查器按节点类型渲染的配置项集合（对齐网页 workflow-studio 出现条件） ──
    private static void InspectorShapeChecks()
    {
        var view = new WorkflowView();
        try
        {
            var generator = CreateNode(view, "gen-1", "generator.page");
            var agent = CreateNode(view, "agent-1", "agent.parse");
            var condition = CreateNode(view, "cond-1", "control.condition");

            // generator.page：建议清晰度（1K/2K/4K，缺省回落 1K）+ 只读模型说明；
            // Create 默认 model_alias=null → 不渲染文本模型/温度（网页同款真值条件）。
            Select(view, generator);
            var resolution = Combo(view, "建议清晰度") ?? throw new Exception("generator.page 缺少建议清晰度下拉");
            Require(resolution.Items.OfType<string>().SequenceEqual(new[] { "1K", "2K", "4K" }),
                "resolution 选项不是网页的 1K/2K/4K");
            Require((string?)resolution.SelectedItem == "1K", "resolution 缺省应回落 1K");
            Require(Box(view, "模型说明")?.Text == "必须显式选择供应商图片模型", "generator.page 缺少只读模型说明");
            Require(Combo(view, "文本模型") == null && Box(view, "温度") == null,
                "model_alias 为空的节点不应出现文本模型/温度");

            // agent.*：Create 默认 model_alias="auto" → 文本模型（auto 选中）+温度；无清晰度。
            Select(view, agent);
            var aliasBox = Combo(view, "文本模型") ?? throw new Exception("agent 节点缺少文本模型下拉");
            Require((string?)((ComboBoxItem?)aliasBox.SelectedItem)?.Tag == "auto",
                "新 agent 节点的文本模型默认值应是 auto");
            Require(Box(view, "温度") != null, "有 model_alias 的节点缺少温度输入");
            Require(Combo(view, "建议清晰度") == null, "非 generator.page 不应出现建议清晰度");

            // control.condition：JSON 路径/比较符/比较值三件套。
            Select(view, condition);
            var operators = Combo(view, "比较符") ?? throw new Exception("control.condition 缺少比较符下拉");
            Require(operators.Items.Cast<ComboBoxItem>().Select(item => (string)item.Tag!).SequenceEqual(
                new[] { "exists", "eq", "ne", "contains", "gt", "gte", "lt", "lte" }),
                "比较符选项与网页不一致");
            Require((string?)((ComboBoxItem?)operators.SelectedItem)?.Tag == "exists", "比较符缺省应是 exists");
            Require(Box(view, "JSON 路径") != null && Box(view, "比较值") != null,
                "control.condition 缺少路径/比较值输入");

            // 所有节点共有：超时/重试/并发/提示词/备注 + 桌面补充的锁定/需要审批。
            Require(Box(view, "超时（秒）") != null && Box(view, "重试次数") != null && Box(view, "并发") != null
                && Box(view, "提示词") != null && Box(view, "备注") != null,
                "通用配置项（超时/重试/并发/提示词/备注）缺失");
            Require(Check(view, "锁定") != null && Check(view, "需要审批") != null,
                "锁定/需要审批开关缺失");
        }
        finally
        {
            StopAutosave(view);
        }
    }

    // ── ② 配置编辑写回 config 键名与值域钳制 ──
    private static void ConfigWriteBackChecks()
    {
        var view = new WorkflowView();
        try
        {
            var generator = CreateNode(view, "gen-1", "generator.page");
            var agent = CreateNode(view, "agent-1", "agent.parse");
            var condition = CreateNode(view, "cond-1", "control.condition");

            // resolution 写回键名。
            Select(view, generator);
            (Combo(view, "建议清晰度") ?? throw new Exception("缺少建议清晰度")).SelectedItem = "4K";
            Require(Config(generator)["resolution"] is "4K", "resolution 未写回 config.resolution");

            // temperature 值域钳制（0-2）与半输入语义。
            Select(view, agent);
            var temperature = Box(view, "温度") ?? throw new Exception("缺少温度输入");
            temperature.Text = "5";
            Require(AsDouble(Config(agent), "temperature") == 2, "温度上限 2 未钳制");
            temperature.Text = "-1";
            Require(AsDouble(Config(agent), "temperature") == 0, "温度下限 0 未钳制");
            temperature.Text = "1e";
            Require(AsDouble(Config(agent), "temperature") == 0, "半输入（1e）不应写回");
            temperature.Text = "0.75";
            Require(AsDouble(Config(agent), "temperature") == 0.75, "合法温度未写回");

            // 文本模型下拉来源（网页 selectedTextModels 过滤）与写回键名。
            SetTextModels(view, """
                [{"catalog_id":"m1","logical_alias":"text.a","provider":"甲","display_name":"模型A","model_type":"TEXT","operations":["structured_text"],"enabled":true,"display_enabled":true},
                 {"catalog_id":"m2","logical_alias":"text.hidden","provider":"乙","display_name":"隐藏模型","model_type":"TEXT","operations":["structured_text"],"enabled":true,"display_enabled":false}]
                """);
            Select(view, agent);
            var aliasBox = Combo(view, "文本模型") ?? throw new Exception("缺少文本模型下拉");
            var tags = aliasBox.Items.Cast<ComboBoxItem>().Select(item => (string?)item.Tag).ToList();
            Require(tags.Contains("auto") && tags.Contains("text.a") && !tags.Contains("text.hidden"),
                "文本模型下拉未按网页过滤（auto/可见模型保留、隐藏模型剔除）");
            aliasBox.SelectedItem = aliasBox.Items.Cast<ComboBoxItem>().Single(item => (string?)item.Tag == "text.a");
            Require(Config(agent)["model_alias"] is "text.a", "model_alias 未写回 config.model_alias");
            // 当前绑定的隐藏别名保持可见（creatorVisibleModels 不失效已绑定值）。
            SetConfig(agent, "model_alias", "text.hidden");
            Select(view, agent);
            tags = (Combo(view, "文本模型") ?? throw new Exception("缺少文本模型下拉")).Items.Cast<ComboBoxItem>().Select(item => (string?)item.Tag).ToList();
            Require(tags.Contains("text.hidden"), "当前绑定的隐藏模型别名应保留在下拉里");

            // 超时/重试/并发钳制（网页 timeout 30-3600、attempts 1-10；并发 1-8 来自后端 schema）。
            Select(view, agent);
            Box(view, "超时（秒）")!.Text = "10";
            Require(AsInt(Config(agent), "timeout_seconds") == 30, "超时下限 30 未钳制");
            Box(view, "超时（秒）")!.Text = "99999";
            Require(AsInt(Config(agent), "timeout_seconds") == 3600, "超时上限 3600 未钳制");
            Box(view, "重试次数")!.Text = "0";
            Require(AsInt(Config(agent), "max_attempts") == 1, "重试下限 1 未钳制");
            Box(view, "并发")!.Text = "99";
            Require(AsInt(Config(agent), "concurrency") == 8, "并发上限 8 未钳制");

            // condition 合并写回（{ ...condition, key } 语义：不丢其它键）。
            Select(view, condition);
            SetConfig(condition, "condition", new Dictionary<string, object?> { ["keep"] = "me" });
            Select(view, condition);
            Box(view, "JSON 路径")!.Text = "$.page.turn";
            (Combo(view, "比较符") ?? throw new Exception("缺少比较符")).SelectedItem =
                ((ComboBox)Combo(view, "比较符")!).Items.Cast<ComboBoxItem>().Single(item => (string?)item.Tag == "gt");
            Box(view, "比较值")!.Text = "3";
            var edited = new Dictionary<string, object?>();
            if (Config(condition).TryGetValue("condition", out var raw) && raw is Dictionary<string, object?> merged)
                edited = merged;
            Require((string?)edited.GetValueOrDefault("path") == "$.page.turn"
                && (string?)edited.GetValueOrDefault("operator") == "gt"
                && (string?)edited.GetValueOrDefault("value") == "3",
                "condition 三件套未写回对应子键");
            Require((string?)edited.GetValueOrDefault("keep") == "me", "condition 写回丢弃了已有键");

            // 锁定/需要审批写回。
            Check(view, "锁定")!.IsChecked = true;
            Check(view, "需要审批")!.IsChecked = true;
            Require(Config(condition)["locked"] is true && Config(condition)["requires_approval"] is true,
                "locked/requires_approval 未写回 config");

            // 保存链路：配置编辑走 ScheduleSave 防抖（版本化 PATCH 的既有链路）。
            Require(typeof(WorkflowView).GetField("autosave", All)!.GetValue(view) as System.Timers.Timer is { Enabled: true },
                "配置编辑未武装自动保存防抖");
            // BuildGraph 携带编辑后的 config（保存载荷包含这些键）。
            var graph = (Dictionary<string, object?>)typeof(WorkflowView).GetMethod("BuildGraph", All)!.Invoke(view, null)!;
            var generatorRow = ((List<Dictionary<string, object?>>)graph["nodes"]!).Single(row => (string)row["id"]! == "gen-1");
            var graphConfig = (Dictionary<string, object?>)generatorRow["config"]!;
            Require(graphConfig["resolution"] is "4K", "BuildGraph 未携带编辑后的 config.resolution");
        }
        finally
        {
            StopAutosave(view);
        }
    }

    // ── ③ 运行历史 + 审批队列：端点、载荷、动作后刷新（假 Handler，零网络） ──
    private static async Task RunsAndApprovalChecks()
    {
        var runsGets = 0;
        var approves = 0;
        var approveBody = default(JsonElement);
        // 初始为空列表：空历史文案断言后再切到等待审批的数据（后端 created_at
        // 倒序，首条即网页 displayedRun）。
        var runsJson = "[]";
        var waitingJson = """
            [{"id":"run-1","workflow_id":"wf-1","scope_type":"CHAPTER","scope_id":"ch-1","status":"RUNNING","created_at":"2026-09-08T01:02:03Z",
              "node_runs":[
                {"id":"nr-1","workflow_run_id":"run-1","node_id":"gen-page-1","node_type":"generator.page","status":"WAITING_APPROVAL"},
                {"id":"nr-2","workflow_run_id":"run-1","node_id":"adopt-1","node_type":"control.approval","status":"WAITING_APPROVAL"}]}]
            """;
        var progressedJson = """
            [{"id":"run-1","workflow_id":"wf-1","scope_type":"CHAPTER","scope_id":"ch-1","status":"RUNNING","created_at":"2026-09-08T01:02:03Z",
              "node_runs":[
                {"id":"nr-1","workflow_run_id":"run-1","node_id":"gen-page-1","node_type":"generator.page","status":"RUNNING"},
                {"id":"nr-2","workflow_run_id":"run-1","node_id":"adopt-1","node_type":"control.approval","status":"WAITING_APPROVAL"}]}]
            """;
        using var api = new ApiClient("http://127.0.0.1:12345", new Handler(request =>
        {
            var path = request.RequestUri!.AbsolutePath;
            if (request.Method == HttpMethod.Post && path.EndsWith("/workflow-runs/run-1/nodes/gen-page-1/approve"))
            {
                approves++;
                approveBody = JsonDocument.Parse(request.Content!.ReadAsStringAsync().Result).RootElement.Clone();
                runsJson = progressedJson;   // 审批后下一轮 runs 反映进度（网页 runs.refetch）
                return Task.FromResult(Response(runsJson));
            }
            if (path.EndsWith("/workflows/wf-1/runs") && request.Method == HttpMethod.Get)
            {
                runsGets++;
                return Task.FromResult(Response(runsJson));
            }
            if (path.EndsWith("/workflow-node-types")) return Task.FromResult(Response(
                """[{"type":"agent.parse","label":"解析","display_name":"解析","category":"AGENT","description":"","inputs":[],"outputs":[]}]"""));
            if (path.EndsWith("/projects/p1/workflows"))
                return Task.FromResult(Response("""[{"id":"wf-1","name":"流程","version":3,"draft_version":1,"draft_graph":{"nodes":[],"edges":[]}}]"""));
            if (path.EndsWith("/projects/p1/chapters"))
                return Task.FromResult(Response("""[{"id":"ch-1","title":"第一章","ordinal":1,"status":"READY"}]"""));
            if (path.EndsWith("/models")) return Task.FromResult(Response(
                """[{"catalog_id":"mi","logical_alias":"image.b","provider":"乙","display_name":"图片模型B","model_type":"IMAGE","operations":["image_edit"],"enabled":true,"display_enabled":true}]"""));
            if (path.EndsWith("/workflows/wf-1"))
                return Task.FromResult(Response("""{"id":"wf-1","name":"流程","version":3,"draft_version":1,"draft_graph":{"nodes":[],"edges":[]}}"""));
            throw new Exception("Unexpected workflow request: " + request.RequestUri);
        }));
        var view = new WorkflowView();
        try
        {
            view.Activate(new WorkspaceContext
            {
                Api = api, Cache = new ApiCache(), State = new WorkspaceState(), Window = null!,
                Project = new ProjectItem("p1", "运行测试", "", 0, 0),
                NavigateSection = (_, _) => Task.CompletedTask, OpenDashboard = () => Task.CompletedTask,
            });
            var viewType = typeof(WorkflowView);
            var workflowId = viewType.GetField("workflowId", All)!;
            await Until(() => (string?)workflowId.GetValue(view) == "wf-1" && runsGets >= 1);

            // 空历史：与网页页脚同文案。
            Require(HistoryText(view).Contains("尚未运行已发布版本"), "空运行历史缺少提示文案");

            // 首轮数据：两条审批行（generator.page 选模型；control.approval 前往采用）。
            runsJson = waitingJson;
            await view.RefreshAsync();
            await Until(() => Field<StackPanel>(view, "approvalQueue").Children.Count == 2);
            var queue = Field<StackPanel>(view, "approvalQueue");
            var generatorRow = (StackPanel)queue.Children[0];
            var adoptRow = (StackPanel)queue.Children[1];
            var modelBox = generatorRow.Children.OfType<ComboBox>().Single(b => Name(b) == "选择图片模型");
            var tags = modelBox.Items.Cast<ComboBoxItem>().Select(item => (string?)item.Tag).ToList();
            Require(tags.Contains("image.b"), "审批下拉缺少 IMAGE+image_edit 模型");
            var generatorApprove = generatorRow.Children.OfType<Button>().Single(b => (string?)b.Content == "确认继续");
            Require(!generatorApprove.IsEnabled, "未选图片模型前确认继续应禁用（网页同款）");
            Require(adoptRow.Children.OfType<Button>().Any(b => (string?)b.Content == "前往采用"),
                "非 generator 审批行缺少前往采用入口");
            Require(adoptRow.Children.OfType<Button>().Single(b => (string?)b.Content == "确认继续").IsEnabled,
                "非 generator 审批行的确认继续不应被禁用");

            // 运行历史行：状态/范围/进度来自同一 runs 列表。
            var historyText = HistoryText(view);
            Require(historyText.Contains("运行中") && historyText.Contains("第 1 章"),
                "运行历史未显示状态与章节范围");

            // 选择模型 → 按钮解禁；点击后 POST 端点+载荷正确并刷新列表。
            var getsBefore = runsGets;
            modelBox.SelectedItem = modelBox.Items.Cast<ComboBoxItem>().Single(item => (string?)item.Tag == "image.b");
            Require(generatorApprove.IsEnabled, "选择图片模型后确认继续未解禁");
            generatorApprove.RaiseEvent(new RoutedEventArgs(ButtonBase.ClickEvent));
            await Until(() => approves == 1 && runsGets == getsBefore + 1);
            Require(approveBody.Text("image_model_alias") == "image.b" && approveBody.Text("resolution") == "1K",
                "审批载荷未携带显式图片模型与清晰度（网页 approveWorkflowNode 契约）");
            // 刷新后 generator.page 审批行消失（来自轮询数据，不冻结在快照上）。
            await Until(() => Field<StackPanel>(view, "approvalQueue").Children.Count == 1);
            Require(HistoryText(view).Contains("运行中"), "审批后运行历史未随刷新更新");

            // 轮询条件：列表里仍有 RUNNING 运行 → PollTick 继续重取（网页 refetchInterval）。
            var before = runsGets;
            view.PollTick();
            await Until(() => runsGets == before + 1);

            view.Deactivate();
        }
        finally
        {
            StopAutosave(view);
        }
    }

    // ── ④ #340: 复制→编辑克隆→防抖 PATCH 载荷里原节点 config 不变（假 Handler） ──
    // 失败构造：DuplicateSelected 若共享 config 引用，克隆上的检查器编辑会写进
    // 原节点的字典，800ms 防抖后 PATCH 的 draft_graph 里两个节点携带同一份被改的
    // config（服务器侧双写）。这里在 PATCH 前后各断言一次：编辑后立即查内存里的
    // 原节点，PATCH 到达后查载荷里的原节点行。
    private static async Task DuplicateAutosaveChecks()
    {
        var patches = 0;
        var lastPatch = default(JsonElement);
        var graph = """
            {"schema_version":2,"nodes":[
              {"id":"orig-a","type":"agent.parse","name":"解析","position":{"x":10,"y":20},"inputs":[],"outputs":[],
               "config":{"model_alias":"auto","temperature":0.2,"notes":""}},
              {"id":"orig-c","type":"control.condition","name":"条件","position":{"x":330,"y":20},"inputs":[],"outputs":[],
               "config":{"condition":{"path":"$","operator":"exists","value":""}}}],
             "edges":[]}
            """;
        using var api = new ApiClient("http://127.0.0.1:12345", new Handler(request =>
        {
            var path = request.RequestUri!.AbsolutePath;
            if (request.Method == HttpMethod.Patch && path.EndsWith("/workflows/wf-dup"))
            {
                patches++;
                lastPatch = JsonDocument.Parse(request.Content!.ReadAsStringAsync().Result).RootElement.Clone();
                return Task.FromResult(Response("""{"id":"wf-dup","name":"复制检查","version":4,"draft_version":2,"draft_graph":{"nodes":[],"edges":[]}}"""));
            }
            if (path.EndsWith("/workflow-node-types")) return Task.FromResult(Response(
                """[{"type":"agent.parse","label":"解析","display_name":"解析","category":"AGENT","description":"","inputs":[],"outputs":[]},{"type":"control.condition","label":"条件","display_name":"条件","category":"CONTROL","description":"","inputs":[],"outputs":[]}]"""));
            if (path.EndsWith("/projects/p1/workflows"))
                return Task.FromResult(Response("""[{"id":"wf-dup","name":"复制检查","version":3,"draft_version":1,"draft_graph":{"nodes":[],"edges":[]}}]"""));
            if (path.EndsWith("/projects/p1/chapters")) return Task.FromResult(Response("[]"));
            if (path.EndsWith("/models")) return Task.FromResult(Response("[]"));
            if (path.EndsWith("/workflows/wf-dup/runs")) return Task.FromResult(Response("[]"));
            if (path.EndsWith("/workflows/wf-dup"))
                return Task.FromResult(Response($"{{\"id\":\"wf-dup\",\"name\":\"复制检查\",\"version\":3,\"draft_version\":1,\"draft_graph\":{graph}}}"));
            throw new Exception("Unexpected duplicate-check request: " + request.RequestUri);
        }));
        var view = new WorkflowView();
        try
        {
            view.Activate(new WorkspaceContext
            {
                Api = api, Cache = new ApiCache(), State = new WorkspaceState(), Window = null!,
                Project = new ProjectItem("p1", "复制检查", "", 0, 0),
                NavigateSection = (_, _) => Task.CompletedTask, OpenDashboard = () => Task.CompletedTask,
            });
            await Until(() => (string?)typeof(WorkflowView).GetField("workflowId", All)!.GetValue(view) == "wf-dup"
                && Nodes(view).Count == 2);

            // 复制 agent 节点 → 编辑克隆的温度（检查器写回键 temperature）。
            Select(view, NodeById(view, "orig-a"));
            Duplicate(view);
            Require(Nodes(view).Count == 3, "复制 agent 节点未生成克隆");
            Box(view, "温度")!.Text = "1.5";

            // 复制 condition 节点 → 编辑克隆的条件表达式（SetConditionValue 合并写回）。
            Select(view, NodeById(view, "orig-c"));
            Duplicate(view);
            Require(Nodes(view).Count == 4, "复制 condition 节点未生成克隆");
            Box(view, "JSON 路径")!.Text = "$.page.turn";

            // PATCH 前快照：内存里的原节点 config 必须未被克隆编辑改写。
            Require(AsDouble(Config(NodeById(view, "orig-a")), "temperature") == 0.2,
                "编辑克隆后（PATCH 前）原 agent 节点的温度被改写");
            Require(ConditionPath(NodeById(view, "orig-c")) == "$",
                "编辑克隆后（PATCH 前）原 condition 节点的路径被改写");

            // 防抖落盘（800ms 真实计时器）后：载荷里原节点行携带各自原值，克隆行携带编辑值。
            await Until(() => patches == 1);
            var rows = lastPatch.Element("draft_graph").Array("nodes");
            Require(rows.Count == 4, "防抖 PATCH 未携带全部 4 个节点");
            var originalAgent = rows.Single(row => row.Text("id") == "orig-a");
            var clonedAgent = rows.Single(row => row.Text("name") == "解析 副本");
            var originalCondition = rows.Single(row => row.Text("id") == "orig-c");
            var clonedCondition = rows.Single(row => row.Text("name") == "条件 副本");
            Require(originalAgent.Element("config").Decimal("temperature") == 0.2 && clonedAgent.Element("config").Decimal("temperature") == 1.5,
                "PATCH 载荷里原/克隆 agent 节点的温度应当分离（原 0.2 / 克隆 1.5）");
            Require(originalCondition.Element("config").Element("condition").Text("path") == "$"
                && clonedCondition.Element("config").Element("condition").Text("path") == "$.page.turn",
                "PATCH 载荷里原/克隆 condition 节点的路径应当分离");
            Require(patches == 1, "防抖应合并为一次 PATCH");

            view.Deactivate();
        }
        finally
        {
            StopAutosave(view);
        }
    }

    private static System.Collections.IList Nodes(WorkflowView view) =>
        (System.Collections.IList)typeof(WorkflowView).GetField("nodes", All)!.GetValue(view)!;

    private static object NodeById(WorkflowView view, string id) =>
        Nodes(view).Cast<object>().Single(node => (string)node.GetType().GetProperty("Id")!.GetValue(node)! == id);

    private static void Duplicate(WorkflowView view) =>
        typeof(WorkflowView).GetMethod("DuplicateSelected", All)!.Invoke(view, null);

    private static string ConditionPath(object node) =>
        ((JsonElement)node.GetType().GetProperty("ConfigElement")!.GetValue(node)!).Element("condition").Text("path");

    // ── 反射/断言辅助（沿用 NativeWorkflowConnectionChecks 的零网络模式） ──

    private static object CreateNode(WorkflowView view, string id, string type)
    {
        // JsonDocument 刻意不释放：节点持有的 JsonElement 是它的视图。
        var definition = JsonDocument.Parse("{}").RootElement;
        var create = NodeType().GetMethod("Create", All) ?? throw Missing("WorkflowNode.Create");
        var node = create.Invoke(null, [id, type, type, (10.0, 20.0), definition])!;
        var nodes = (System.Collections.IList)typeof(WorkflowView).GetField("nodes", All)!.GetValue(view)!;
        nodes.Add(node);
        return node;
    }

    private static void Select(WorkflowView view, object node) =>
        typeof(WorkflowView).GetMethod("Select", All)!.Invoke(view, [node]);

    private static Dictionary<string, object?> Config(object node) =>
        (Dictionary<string, object?>)node.GetType().GetProperty("Config")!.GetValue(node)!;

    private static void SetConfig(object node, string key, object? value) =>
        node.GetType().GetMethod("SetConfig", All)!.Invoke(node, [key, value]);

    private static void SetTextModels(WorkflowView view, string json)
    {
        var rows = JsonSerializer.Deserialize<List<JsonElement>>(json)!;
        typeof(WorkflowView).GetField("textModels", All)!.SetValue(view, rows);
    }

    private static double AsDouble(Dictionary<string, object?> config, string key) =>
        config.TryGetValue(key, out var value) && value is JsonElement { ValueKind: JsonValueKind.Number } number
            ? number.GetDouble() : value is double parsed ? parsed : double.NaN;

    private static int AsInt(Dictionary<string, object?> config, string key) =>
        config.TryGetValue(key, out var value) && value is JsonElement { ValueKind: JsonValueKind.Number } number
            ? number.GetInt32() : value is int parsed ? parsed : int.MinValue;

    private static IEnumerable<DependencyObject> Descendants(DependencyObject root)
    {
        yield return root;
        foreach (var child in LogicalTreeHelper.GetChildren(root).OfType<DependencyObject>())
            foreach (var nested in Descendants(child)) yield return nested;
    }

    private static string? Name(DependencyObject element) =>
        System.Windows.Automation.AutomationProperties.GetName(element);

    private static TextBox? Box(WorkflowView view, string name) =>
        Descendants(Field<StackPanel>(view, "inspector")).OfType<TextBox>().FirstOrDefault(box => Name(box) == name);

    private static ComboBox? Combo(WorkflowView view, string name) =>
        Descendants(Field<StackPanel>(view, "inspector")).OfType<ComboBox>().FirstOrDefault(combo => Name(combo) == name);

    private static System.Windows.Controls.CheckBox? Check(WorkflowView view, string name) =>
        Descendants(Field<StackPanel>(view, "inspector")).OfType<System.Windows.Controls.CheckBox>().FirstOrDefault(check => Name(check) == name);

    private static string HistoryText(WorkflowView view) =>
        string.Join("\n", Descendants(Field<StackPanel>(view, "runHistory")).OfType<TextBlock>().Select(block => block.Text));

    private static T Field<T>(object target, string name) => (T)target.GetType().GetField(name, All)!.GetValue(target)!;

    private static Type NodeType() => typeof(WorkflowView).GetNestedType("WorkflowNode", BindingFlags.NonPublic) ?? throw Missing("WorkflowNode");

    private static void StopAutosave(WorkflowView view) =>
        (typeof(WorkflowView).GetField("autosave", All)!.GetValue(view) as System.Timers.Timer)?.Stop();

    private static Exception Missing(string member) => new("反射入口缺失：WorkflowView." + member);

    private static void Require(bool condition, string message) { if (!condition) throw new Exception(message); }

    private static async Task Until(Func<bool> condition)
    {
        using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(3));
        while (!condition()) await Task.Delay(5, timeout.Token);
    }

    private static HttpResponseMessage Response(string json, HttpStatusCode status = HttpStatusCode.OK) =>
        new(status) { Content = new StringContent(json) };

    private sealed class Handler(Func<HttpRequestMessage, Task<HttpResponseMessage>> handler) : HttpMessageHandler
    {
        protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken token) => handler(request);
    }
}
