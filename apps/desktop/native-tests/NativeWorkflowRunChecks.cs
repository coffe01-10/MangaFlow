using System.Diagnostics;
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
//     POST approve 的请求路径与 JSON 载荷、审批后列表重取）；
//  ③补 #391：审批选中的模型别名失效（目录剔除/清空）后，「确认继续」按
//     imageModels 全目录成员资格禁用（与 web #387-round-4 谓词同构）。
//  B02：PAGE 运行范围可选后续章节页面；迟到的第一章页面响应不得覆盖已选章节。
//  B04：FAILED 页脚重试 POST /workflow-runs/{id}/retry，在途防重，不自动触发。
//  B03：空列表创建 manga_default + chapter_export；部分失败后按名称补建。
//  B01：发布版本列表/恢复/409/迟到响应隔离。
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
        // 调用方（NativeInteractionChecks.RunIsolated 的调用列表）当前未 await 本
        // Task：故障任务会被静默吞掉，套件仍绿。这里至少把失败打印成显式 FAIL
        // 行（接线补 await 需改 NativeInteractionChecks.cs，超出本文件的改动互斥）。
        try
        {
            InspectorShapeChecks();
            ConfigWriteBackChecks();
            await RunsAndApprovalChecks();
            await StaleApprovalModelChecks();
            await DuplicateAutosaveChecks();
            await DebounceSwitchChecks();
            await PageScopeChapterChecks();
            await FailedRunRetryChecks();
            await DefaultWorkflowTemplateChecks();
            await VersionRestoreChecks();
            await VersionListFailureChecks();
            await VersionStaleResponseChecks();
            await RestoreConflictReArmChecks();
            await LoadFailureRollbackChecks();
            await ActionTargetsCanvasChecks();
        }
        catch (Exception error)
        {
            Console.WriteLine("FAIL: workflow inspector/run checks: " + error.Message);
            throw;
        }
        Console.WriteLine("PASS: workflow inspector full config surface, clamped write-back, run history and approval queue wire contract, stale approval model alias gating, PAGE scope chapter picker (B02), FAILED run retry (B04), default templates (B03), version restore (B01)");
    }

    // ── ① 检查器按节点类型渲染的配置项集合（对齐网页 workflow-studio 出现条件） ──
    private static void InspectorShapeChecks()
    {
        var view = new WorkflowView();
        try
        {
            Require(Name(Field<Canvas>(view, "minimap")) == "工作流小地图", "流程画布缺少小地图");
            Require(Descendants(view).OfType<Button>().Any(button => Equals(button.Content, "全屏")),
                "流程画布缺少全屏入口");
            Require(Descendants(view).OfType<TextBlock>().Any(text => text.Text.Contains("空格/中键 拖动")),
                "流程画布缺少空格/中键手势说明");
            typeof(WorkflowView).GetMethod("ToggleFocusMode", All)!.Invoke(view, null);
            Require(!Field<bool>(view, "libraryOpen") && !Field<bool>(view, "inspectorOpen"),
                "专注模式应收起节点库和属性面板");
            typeof(WorkflowView).GetMethod("ToggleFocusMode", All)!.Invoke(view, null);
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
                && edited.GetValueOrDefault("value") is 3,
                "condition 三件套未写回对应子键（比较值须按 JSON 字面量提交）");
            Box(view, "比较值")!.Text = "null";
            if (Config(condition).TryGetValue("condition", out raw) && raw is Dictionary<string, object?> withNull)
                edited = withNull;
            Require(edited.GetValueOrDefault("value") is null, "eq/gt 比较值 null 必须写成 JSON null 而不是字符串");
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
        var cancels = 0;
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
        // #365：命中审批栅栏的 run 语义（reconciliation 把 run 置 PAUSED，卡在
        // 栅栏的节点保持 WAITING_APPROVAL）。
        var pausedJson = """
            [{"id":"run-1","workflow_id":"wf-1","scope_type":"CHAPTER","scope_id":"ch-1","status":"PAUSED","created_at":"2026-09-08T01:02:03Z",
              "node_runs":[
                {"id":"nr-2","workflow_run_id":"run-1","node_id":"adopt-1","node_type":"control.approval","status":"WAITING_APPROVAL"}]}]
            """;
        var cancelledJson = """
            [{"id":"run-1","workflow_id":"wf-1","scope_type":"CHAPTER","scope_id":"ch-1","status":"CANCELLED","created_at":"2026-09-08T01:02:03Z",
              "node_runs":[
                {"id":"nr-2","workflow_run_id":"run-1","node_id":"adopt-1","node_type":"control.approval","status":"CANCELLED"}]}]
            """;
        using var api = new ApiClient("http://127.0.0.1:12345", new Handler(request =>
        {
            var path = request.RequestUri!.AbsolutePath;
            if (request.Method == HttpMethod.Post && path.EndsWith("/workflow-runs/run-1/cancel"))
            {
                cancels++;
                runsJson = cancelledJson;   // cancel_run 生效后下一轮 runs 反映终态
                return Task.FromResult(Response("""{"id":"run-1","status":"CANCELLED"}"""));
            }
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
            if (path.EndsWith("/versions")) return Task.FromResult(Response("[]"));
            throw new Exception("Unexpected workflow request: " + request.RequestUri);
        }));
        var view = new WorkflowView();
        try
        {
            view.Activate(new WorkspaceContext
            {
                Api = api, State = new WorkspaceState(), Window = null!,
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

            // 轮询条件（#380 新契约）：列表里仍有 RUNNING 运行 → PollTick 每 tick
            // 重取（网页 refetchInterval 3000）；仅剩 PAUSED → 约 10s 节流一次。
            var before = runsGets;
            view.PollTick();
            await Until(() => runsGets == before + 1);

            // ── #365（桌面半边）：审批栅栏 PAUSED 的运行必须能取消 ──
            // 后端把命中审批栅栏的 run 置为 PAUSED，而 cancel_run 只拒绝终态——
            // 取消入口在 PAUSED 不可用会让同 scope 的重复运行 409 指示一个 UI 上
            // 做不到的动作。回归（只在 RUNNING 渲染取消钮）在「取消入口存在」处失败。
            runsJson = pausedJson;
            await view.RefreshAsync();
            var runMonitor = Field<StackPanel>(view, "runMonitor");
            await Until(() => runMonitor.Children.OfType<Button>().Any(button => (string?)button.Content == "取消"));
            Require(runMonitor.Children.OfType<TextBlock>().Any(block => block.Text.Contains("暂停中")),
                "PAUSED 运行的页脚摘要必须显示中文状态（Labels.WorkflowRunStatus 补 PAUSED）");
            Require(runMonitor.Children.OfType<Button>().All(button => (string?)button.Content != "重试"),
                "PAUSED 不是 FAILED：不渲染重试入口");
            // #380：PAUSED 不再让轮询停摆，而是降频重取（网页 refetchInterval 10000）。
            // RefreshAsync 刚取回 PAUSED 列表 → 节流窗口已重置：窗口内 PollTick 不得重取。
            var pausedBefore = runsGets;
            view.PollTick();
            await Task.Delay(50);
            Require(runsGets == pausedBefore, "PAUSED 10s 节流窗口内 PollTick 不应重取");
            // 模拟 10s 流逝（操纵节流时间戳字段）：PollTick 必须触发一次 PAUSED 驱动重取，
            // 且重取落地后窗口再次重置（数据仍为 PAUSED，取消入口重渲染）。
            viewType.GetField("lastPausedFetchTicks", All)!.SetValue(view, Environment.TickCount64 - 10_001);
            view.PollTick();
            await Until(() => runsGets == pausedBefore + 1
                && Field<StackPanel>(view, "runMonitor").Children.OfType<Button>().Any(button => (string?)button.Content == "取消"));
            // 取消入口必须真的发出 cancel_run 请求（唯一的停止途径）并刷新列表。
            var getsBeforeCancel = runsGets;
            runMonitor.Children.OfType<Button>().Single(button => (string?)button.Content == "取消")
                .RaiseEvent(new RoutedEventArgs(ButtonBase.ClickEvent));
            await Until(() => cancels == 1 && runsGets > getsBeforeCancel);
            await Until(() => Field<StackPanel>(view, "runMonitor").Children.OfType<Button>()
                .All(button => (string?)button.Content != "取消"));
            Require(HistoryText(view).Contains("已取消"), "取消后运行历史应显示已取消");

            view.Deactivate();
        }
        finally
        {
            StopAutosave(view);
        }
    }

    // ── ③补 #391: 审批选中的模型别名失效（目录剔除/清空）后，「确认继续」必须按
    // 目录成员资格禁用（与 web #387-round-4 修复同构：选中别名仍需存在于 imageModels）。
    // 失败构造：别名失效后重渲染，下拉回退占位项的赋值发生在 SelectionChanged 挂接
    // 之前，事件不触发、drawModel 保留失效别名；初始使能若只查长度，按钮可点，POST
    // 出的 image_model_alias 会被后端 resolve_model 拒绝。目录变化经假 Handler 的
    // 可变 /models 返回 + 反射调用 LoadModelsAsync 驱动（生产的目录加载路径），
    // 重渲染经 RefreshAsync → LoadRunsAsync → RenderApprovals（生产的审批行渲染路径）。
    private static async Task StaleApprovalModelChecks()
    {
        var runsGets = 0;
        var runsJson = """
            [{"id":"run-st","workflow_id":"wf-st","scope_type":"CHAPTER","scope_id":"ch-1","status":"RUNNING","created_at":"2026-09-08T01:02:03Z",
              "node_runs":[
                {"id":"nr-st","workflow_run_id":"run-st","node_id":"gen-page-st","node_type":"generator.page","status":"WAITING_APPROVAL"}]}]
            """;
        var withSelected =
            """[{"catalog_id":"mi","logical_alias":"image.b","provider":"乙","display_name":"图片模型B","model_type":"IMAGE","operations":["image_edit"],"enabled":true,"display_enabled":true}]""";
        var withOther =
            """[{"catalog_id":"mc","logical_alias":"image.c","provider":"丙","display_name":"图片模型C","model_type":"IMAGE","operations":["image_edit"],"enabled":true,"display_enabled":true}]""";
        var modelsJson = withSelected;
        using var api = new ApiClient("http://127.0.0.1:12345", new Handler(request =>
        {
            var path = request.RequestUri!.AbsolutePath;
            if (path.EndsWith("/models")) return Task.FromResult(Response(modelsJson));
            if (request.Method == HttpMethod.Get && path.EndsWith("/workflows/wf-st/runs"))
            {
                runsGets++;
                return Task.FromResult(Response(runsJson));
            }
            if (path.EndsWith("/workflow-node-types")) return Task.FromResult(Response(
                """[{"type":"agent.parse","label":"解析","display_name":"解析","category":"AGENT","description":"","inputs":[],"outputs":[]}]"""));
            if (path.EndsWith("/projects/p1/workflows"))
                return Task.FromResult(Response("""[{"id":"wf-st","name":"流程","version":3,"draft_version":1,"draft_graph":{"nodes":[],"edges":[]}}]"""));
            if (path.EndsWith("/projects/p1/chapters"))
                return Task.FromResult(Response("""[{"id":"ch-1","title":"第一章","ordinal":1,"status":"READY"}]"""));
            if (path.EndsWith("/workflows/wf-st"))
                return Task.FromResult(Response("""{"id":"wf-st","name":"流程","version":3,"draft_version":1,"draft_graph":{"nodes":[],"edges":[]}}"""));
            if (path.EndsWith("/versions")) return Task.FromResult(Response("[]"));
            throw new Exception("Unexpected stale-alias check request: " + request.RequestUri);
        }));
        var view = new WorkflowView();
        var viewType = typeof(WorkflowView);
        try
        {
            view.Activate(new WorkspaceContext
            {
                Api = api, State = new WorkspaceState(), Window = null!,
                Project = new ProjectItem("p1", "别名失效检查", "", 0, 0),
                NavigateSection = (_, _) => Task.CompletedTask, OpenDashboard = () => Task.CompletedTask,
            });
            var workflowId = viewType.GetField("workflowId", All)!;
            await Until(() => (string?)workflowId.GetValue(view) == "wf-st" && runsGets >= 1);
            await Until(() => GeneratorApprove(view) != null);

            // 前置对照：未选模型禁用；选择目录内的 image.b 后解禁（既有行为）。
            var modelBox = GeneratorModelBox(view) ?? throw new Exception("generator 审批行缺少图片模型下拉");
            Require(!GeneratorApprove(view)!.IsEnabled, "未选图片模型前确认继续应禁用（前置对照）");
            modelBox.SelectedItem = modelBox.Items.Cast<ComboBoxItem>().Single(item => (string?)item.Tag == "image.b");
            Require(GeneratorApprove(view)!.IsEnabled, "选中目录内模型后确认继续应解禁（前置对照）");

            // 目录剔除已选别名（只剩 image.c）→ 重渲染后禁用；新下拉回退到占位项。
            var removed = await RebuiltApproveAsync(withOther);
            Require(!removed.IsEnabled, "#391：选中别名被目录剔除后重渲染，确认继续必须禁用");
            Require((string?)((ComboBoxItem?)GeneratorModelBox(view)!.SelectedItem)?.Tag == "",
                "别名失效后下拉应回退到占位项（SelectionChanged 尚未挂接，事件不触发）");

            // 回归：目录恢复含 image.b → 重渲染后解禁（防止谓词写反）。
            Require((await RebuiltApproveAsync(withSelected)).IsEnabled,
                "目录仍含选中别名时重渲染，确认继续应解禁（回归）");

            // 目录清空 → 重渲染后禁用。
            Require(!(await RebuiltApproveAsync("[]")).IsEnabled,
                "目录为空时确认继续必须禁用");

            view.Deactivate();
        }
        finally
        {
            StopAutosave(view);
        }

        // 换目录并重渲染：LoadModelsAsync 重取可变 /models，RefreshAsync 触发
        // LoadRunsAsync 重建审批行；等待新按钮实例出现（行清空到重加之间有间隙）。
        async Task<Button> RebuiltApproveAsync(string catalog)
        {
            modelsJson = catalog;
            var before = runsGets;
            var previous = GeneratorApprove(view);
            await (Task)viewType.GetMethod("LoadModelsAsync", All)!.Invoke(view, null)!;
            await view.RefreshAsync();
            await Until(() => runsGets >= before + 1
                && GeneratorApprove(view) is not null
                && !ReferenceEquals(previous, GeneratorApprove(view)));
            return GeneratorApprove(view)!;
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
            if (path.EndsWith("/versions")) return Task.FromResult(Response("[]"));
            throw new Exception("Unexpected duplicate-check request: " + request.RequestUri);
        }));
        var view = new WorkflowView();
        try
        {
            view.Activate(new WorkspaceContext
            {
                Api = api, State = new WorkspaceState(), Window = null!,
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

    // ── ⑤ #342: 防抖回调不得把 A 的图 PATCH 进 B（门控请求构造切换空档） ──
    // 失败构造（原始缺陷）：A 上武装防抖→800ms 内切到 B。选择器路径先 flush A 再
    // 换 workflowId、再 GET B；GET B 被门控挂起时 workflowId 已是 B 而 nodes 还是
    // A 的图，武装中的计时器回调此刻执行（旧代码读执行时的字段）→ PATCH B 携带
    // A 的 nodes。伴生形态：回调在切换 flush 在途时通过身份检查、排进保存链，切换
    // 完成后才执行——由 SaveNowCoreAsync 的目标身份弃权兜住（第二段验证）。
    private static async Task DebounceSwitchChecks()
    {
        var aGraph = """
            {"schema_version":2,"nodes":[{"id":"a-node","type":"agent.parse","name":"甲","position":{"x":10,"y":20},"inputs":[],"outputs":[],
             "config":{"model_alias":"auto","temperature":0.2,"notes":""}}],"edges":[]}
            """;
        var bGraph = """
            {"schema_version":2,"nodes":[{"id":"b-node","type":"agent.parse","name":"乙","position":{"x":10,"y":20},"inputs":[],"outputs":[],
             "config":{"model_alias":"auto","temperature":0.5,"notes":""}}],"edges":[]}
            """;
        var aPatches = 0;
        var bPatches = 0;
        var getA = 0;
        var getB = 0;
        var bLoadGate = new TaskCompletionSource<HttpResponseMessage>();
        var bFlushGate = new TaskCompletionSource<HttpResponseMessage>();
        using var api = new ApiClient("http://127.0.0.1:12345", new Handler(async request =>
        {
            var path = request.RequestUri!.AbsolutePath;
            if (request.Method == HttpMethod.Patch && path.EndsWith("/workflows/wf-swa"))
            {
                aPatches++;
                return Response("""{"id":"wf-swa","version":6,"draft_version":2,"draft_graph":{"nodes":[],"edges":[]}}""");
            }
            if (request.Method == HttpMethod.Patch && path.EndsWith("/workflows/wf-swb"))
            {
                bPatches++;
                if (bPatches == 1) return await bFlushGate.Task;   // 第二段：flush B 的响应挂起，制造“回调已排队、切换未完成”的窗口
                return Response("""{"id":"wf-swb","version":4,"draft_version":2,"draft_graph":{"nodes":[],"edges":[]}}""");
            }
            if (request.Method == HttpMethod.Get && path.EndsWith("/workflows/wf-swa"))
            {
                getA++;
                // #427 修复后 Activate 恰好载入一次：A 的载入共 2 次（初始 + 切回），
                // 切回时 A 已被 flush 过，服务端版本应为 7（旧校准按双载入的 3 次计）。
                return Response($"{{\"id\":\"wf-swa\",\"name\":\"A\",\"version\":{(getA >= 2 ? 7 : 5)},\"draft_version\":1,\"draft_graph\":{aGraph}}}");
            }
            if (request.Method == HttpMethod.Get && path.EndsWith("/workflows/wf-swb"))
            {
                getB++;
                if (getB == 1) return await bLoadGate.Task;   // 第一段：GET B 挂起 → workflowId=B 而 nodes 仍是 A 的图
                return Response($"{{\"id\":\"wf-swb\",\"name\":\"B\",\"version\":3,\"draft_version\":1,\"draft_graph\":{bGraph}}}");
            }
            if (path.EndsWith("/workflow-node-types")) return Response(
                """[{"type":"agent.parse","label":"解析","display_name":"解析","category":"AGENT","description":"","inputs":[],"outputs":[]}]""");
            if (path.EndsWith("/projects/p1/workflows")) return Response(
                """[{"id":"wf-swa","name":"A","version":5,"draft_version":1,"draft_graph":{"nodes":[],"edges":[]}},{"id":"wf-swb","name":"B","version":3,"draft_version":1,"draft_graph":{"nodes":[],"edges":[]}}]""");
            if (path.EndsWith("/projects/p1/chapters")) return Response("[]");
            if (path.EndsWith("/models")) return Response("[]");
            if (path.EndsWith("/runs")) return Response("[]");
            if (path.EndsWith("/versions")) return Response("[]");
            throw new Exception("Unexpected debounce-check request: " + request.RequestUri);
        }));
        var view = new WorkflowView();
        try
        {
            view.Activate(new WorkspaceContext
            {
                Api = api, State = new WorkspaceState(), Window = null!,
                Project = new ProjectItem("p1", "防抖切换检查", "", 0, 0),
                NavigateSection = (_, _) => Task.CompletedTask, OpenDashboard = () => Task.CompletedTask,
            });
            var workflowIdField = typeof(WorkflowView).GetField("workflowId", All)!;
            await Until(() => (string?)workflowIdField.GetValue(view) == "wf-swa" && NodeNames(view).Contains("甲"));
            var selector = (ComboBox)typeof(WorkflowView).GetField("workflowSelector", All)!.GetValue(view)!;
            ComboBoxItem Item(string tag) => selector.Items.Cast<ComboBoxItem>().Single(item => (string?)item.Tag == tag);

            // ── 第一段：A 上武装防抖 → GET B 空档内放走 800ms 计时器 ──
            Select(view, NodeById(view, "a-node"));
            Box(view, "温度")!.Text = "0.9";
            Require(typeof(WorkflowView).GetField("autosave", All)!.GetValue(view) as System.Timers.Timer is { Enabled: true },
                "编辑 A 后防抖计时器应已武装");
            var armed = Stopwatch.StartNew();
            selector.SelectedItem = Item("wf-swb");
            await Until(() => (string?)workflowIdField.GetValue(view) == "wf-swb" && getB == 1);
            await Until(() => armed.ElapsedMilliseconds >= 950);   // 覆盖 800ms 防抖窗口：陈旧回调必须已放弃
            Require(bPatches == 0, "防抖窗口内切换到 B 后，A 的武装计时器把 A 的图 PATCH 进了 B");
            Require(aPatches == 1, "切换路径应先 flush A（一次 wf-swa 的 PATCH）");
            bLoadGate.TrySetResult(Response($"{{\"id\":\"wf-swb\",\"name\":\"B\",\"version\":3,\"draft_version\":1,\"draft_graph\":{bGraph}}}"));
            await Until(() => NodeNames(view).Contains("乙"));
            Require(NodeNames(view).Contains("乙") && !NodeNames(view).Contains("甲"), "B 载入后应只渲染 B 的节点");

            // ── 第二段：B 上武装防抖 → 切回 A（flush B 响应挂起 → 回调排队期间完成切换） ──
            Select(view, NodeById(view, "b-node"));
            Box(view, "温度")!.Text = "1.2";
            armed.Restart();
            selector.SelectedItem = Item("wf-swa");
            await Until(() => bPatches == 1);   // flush B 已发出（响应被门控挂起）
            await Until(() => armed.ElapsedMilliseconds >= 950);   // 武装回调已执行并排进保存链
            bFlushGate.TrySetResult(Response("""{"id":"wf-swb","version":4,"draft_version":2,"draft_graph":{"nodes":[],"edges":[]}}"""));
            await Until(() => (string?)workflowIdField.GetValue(view) == "wf-swa" && NodeNames(view).Contains("甲"));
            await Until(() => armed.ElapsedMilliseconds >= 1250);   // 给排队中的陈旧保存留出执行窗口
            Require(aPatches == 1, "切回 A 后，B 的迟到防抖保存把 B 的图 PATCH 进了 A（保存链身份弃权失效）");
            Require(bPatches == 1, "B 的待存编辑应恰好被 flush 一次");
            Require((int?)typeof(WorkflowView).GetField("version", All)!.GetValue(view) == 7,
                "切换后版本号被旧工作流的迟到响应改写（应为 A 载入的 version 7）");
            view.Deactivate();
        }
        finally
        {
            bLoadGate.TrySetResult(Response("{}"));
            bFlushGate.TrySetResult(Response("{}"));
            StopAutosave(view);
        }
    }

    // B02：PAGE 运行范围必须能选后续章节的页面。此前恒取 chapters[0]，
    // 多章项目无法对第 2+ 章发起单页流程。迟到的第一章页面响应不得覆盖已选章节。
    private static async Task PageScopeChapterChecks()
    {
        var pageGets = new List<string>();
        var runBody = default(JsonElement);
        var ch1Pages = new TaskCompletionSource<HttpResponseMessage>(TaskCreationOptions.RunContinuationsAsynchronously);
        using var api = new ApiClient("http://127.0.0.1:12345", new Handler(request =>
        {
            var path = request.RequestUri!.AbsolutePath;
            if (path.EndsWith("/workflow-node-types")) return Task.FromResult(Response(
                """[{"type":"agent.parse","label":"解析","display_name":"解析","category":"AGENT","description":"","inputs":[],"outputs":[]}]"""));
            if (path.EndsWith("/projects/p1/workflows"))
                return Task.FromResult(Response("""[{"id":"wf-1","name":"流程","version":3,"draft_version":1,"draft_graph":{"nodes":[],"edges":[]}}]"""));
            if (path.EndsWith("/projects/p1/chapters"))
                return Task.FromResult(Response(
                    """[{"id":"ch-1","title":"第一章","ordinal":1,"status":"READY"},{"id":"ch-2","title":"第二章","ordinal":2,"status":"READY"}]"""));
            if (path.EndsWith("/models")) return Task.FromResult(Response("[]"));
            if (path.EndsWith("/workflows/wf-1/runs") && request.Method == HttpMethod.Get)
                return Task.FromResult(Response("[]"));
            if (path.EndsWith("/workflows/wf-1") && request.Method == HttpMethod.Get)
                return Task.FromResult(Response("""{"id":"wf-1","name":"流程","version":3,"draft_version":1,"draft_graph":{"nodes":[],"edges":[]}}"""));
            if (path.EndsWith("/chapters/ch-1/pages"))
            {
                pageGets.Add("ch-1");
                return ch1Pages.Task;
            }
            if (path.EndsWith("/chapters/ch-2/pages"))
            {
                pageGets.Add("ch-2");
                return Task.FromResult(Response(
                    """[{"id":"p-2-1","page_number":1},{"id":"p-2-2","page_number":2}]"""));
            }
            if (path.EndsWith("/workflows/wf-1/runs") && request.Method == HttpMethod.Post)
            {
                runBody = JsonDocument.Parse(request.Content!.ReadAsStringAsync().Result).RootElement.Clone();
                return Task.FromResult(Response("""{"id":"run-page","status":"QUEUED"}"""));
            }
            if (path.EndsWith("/versions")) return Task.FromResult(Response("[]"));
            throw new Exception("Unexpected workflow request: " + request.RequestUri);
        }));
        var view = new WorkflowView();
        try
        {
            view.Activate(new WorkspaceContext
            {
                Api = api, State = new WorkspaceState(), Window = null!,
                Project = new ProjectItem("p1", "范围测试", "", 0, 0),
                NavigateSection = (_, _) => Task.CompletedTask, OpenDashboard = () => Task.CompletedTask,
            });
            await Until(() => Field<string>(view, "workflowId") == "wf-1");

            var typeBox = Field<ComboBox>(view, "scopeType");
            var chapterBox = Field<ComboBox>(view, "scopeChapter");
            var targetBox = Field<ComboBox>(view, "scopeTarget");
            Require(Name(typeBox) == "运行范围类型" && Name(chapterBox) == "页面所属章节" && Name(targetBox) == "运行目标",
                "运行范围下拉缺少与网页一致的无障碍名称");
            Require(chapterBox.Visibility == Visibility.Collapsed, "CHAPTER 范围不应显示页面所属章节");
            Require(pageGets.Count == 0, "CHAPTER 范围不应预取页面列表");

            typeBox.SelectedItem = typeBox.Items.Cast<ComboBoxItem>().Single(item => (string?)item.Tag == "PAGE");
            await Until(() => chapterBox.Visibility == Visibility.Visible
                && chapterBox.Items.OfType<ComboBoxItem>().Any(item => (string?)item.Tag == "ch-2")
                && pageGets.Contains("ch-1"));
            Require((string?)((ComboBoxItem?)chapterBox.SelectedItem)?.Tag == "ch-1",
                "PAGE 范围默认应落在第一章");

            chapterBox.SelectedItem = chapterBox.Items.Cast<ComboBoxItem>().Single(item => (string?)item.Tag == "ch-2");
            await Until(() => pageGets.Contains("ch-2")
                && targetBox.Items.OfType<ComboBoxItem>().Any(item => (string?)item.Tag == "p-2-2"));
            Require((string?)((ComboBoxItem?)targetBox.SelectedItem)?.Tag == "p-2-1",
                "切到第二章后运行目标应默认第一章以外的该章首页");
            Require(targetBox.Items.OfType<ComboBoxItem>().Select(item => (string?)item.Content).Contains("第 2 页"),
                "第二章的第 2 页未进入运行目标");

            ch1Pages.TrySetResult(Response("""[{"id":"p-1-1","page_number":1}]"""));
            await Task.Delay(50);
            Require((string?)((ComboBoxItem?)targetBox.SelectedItem)?.Tag == "p-2-1"
                && !targetBox.Items.OfType<ComboBoxItem>().Any(item => (string?)item.Tag == "p-1-1"),
                "迟到的第一章页面响应覆盖了已选第二章");

            await (Task)typeof(WorkflowView).GetMethod("RunAsync", All)!.Invoke(view, [Array.Empty<string>(), Array.Empty<string>()])!;
            await Until(() => runBody.ValueKind == JsonValueKind.Object);
            Require(runBody.Text("scope_type") == "PAGE" && runBody.Text("scope_id") == "p-2-1",
                "运行载荷必须带 PAGE 与第二章页面 id，而不是第一章");

            view.Deactivate();
        }
        finally
        {
            ch1Pages.TrySetResult(Response("[]"));
            StopAutosave(view);
        }
    }

    // B04：FAILED 运行在页脚提供重试，POST /workflow-runs/{id}/retry，在途防重，
    // 成功后刷新列表。不在 Activate/轮询时自动重试，PAUSED/RUNNING 不露出该入口。
    private static async Task FailedRunRetryChecks()
    {
        var retries = 0;
        var runsGets = 0;
        var runsJson = """
            [{"id":"run-1","workflow_id":"wf-1","scope_type":"CHAPTER","scope_id":"ch-1","status":"FAILED","created_at":"2026-09-08T01:02:03Z",
              "node_runs":[{"id":"nr-1","workflow_run_id":"run-1","node_id":"gen-page-1","node_type":"generator.page","status":"FAILED"}]}]
            """;
        var retriedJson = """
            [{"id":"run-2","workflow_id":"wf-1","scope_type":"CHAPTER","scope_id":"ch-1","status":"RUNNING","created_at":"2026-09-08T01:03:03Z",
              "node_runs":[{"id":"nr-2","workflow_run_id":"run-2","node_id":"gen-page-1","node_type":"generator.page","status":"RUNNING"}]}]
            """;
        var retryGate = new TaskCompletionSource<HttpResponseMessage>(TaskCreationOptions.RunContinuationsAsynchronously);
        using var api = new ApiClient("http://127.0.0.1:12345", new Handler(request =>
        {
            var path = request.RequestUri!.AbsolutePath;
            if (path.EndsWith("/workflow-node-types")) return Task.FromResult(Response(
                """[{"type":"agent.parse","label":"解析","display_name":"解析","category":"AGENT","description":"","inputs":[],"outputs":[]}]"""));
            if (path.EndsWith("/projects/p1/workflows"))
                return Task.FromResult(Response("""[{"id":"wf-1","name":"流程","version":3,"draft_version":1,"draft_graph":{"nodes":[],"edges":[]}}]"""));
            if (path.EndsWith("/projects/p1/chapters"))
                return Task.FromResult(Response("""[{"id":"ch-1","title":"第一章","ordinal":1,"status":"READY"}]"""));
            if (path.EndsWith("/models")) return Task.FromResult(Response("[]"));
            if (path.EndsWith("/workflows/wf-1") && request.Method == HttpMethod.Get)
                return Task.FromResult(Response("""{"id":"wf-1","name":"流程","version":3,"draft_version":1,"draft_graph":{"nodes":[],"edges":[]}}"""));
            if (path.EndsWith("/workflows/wf-1/runs") && request.Method == HttpMethod.Get)
            {
                runsGets++;
                return Task.FromResult(Response(runsJson));
            }
            if (path.EndsWith("/workflow-runs/run-1/retry") && request.Method == HttpMethod.Post)
            {
                retries++;
                return retryGate.Task;
            }
            if (path.EndsWith("/versions")) return Task.FromResult(Response("[]"));
            throw new Exception("Unexpected workflow request: " + request.RequestUri);
        }));
        var view = new WorkflowView();
        try
        {
            view.Activate(new WorkspaceContext
            {
                Api = api, State = new WorkspaceState(), Window = null!,
                Project = new ProjectItem("p1", "重试测试", "", 0, 0),
                NavigateSection = (_, _) => Task.CompletedTask, OpenDashboard = () => Task.CompletedTask,
            });
            await Until(() => Field<string>(view, "workflowId") == "wf-1" && runsGets >= 1);
            Require(retries == 0, "载入 FAILED 运行不得自动触发付费重试");

            var runMonitor = Field<StackPanel>(view, "runMonitor");
            await Until(() => runMonitor.Children.OfType<Button>().Any(button => (string?)button.Content == "重试"));
            Require(runMonitor.Children.OfType<TextBlock>().Any(block => block.Text.Contains("已失败")),
                "FAILED 运行的页脚摘要必须显示中文状态");
            Require(runMonitor.Children.OfType<Button>().All(button => (string?)button.Content != "取消"),
                "FAILED 终态不渲染取消，只露出重试");

            var retry = runMonitor.Children.OfType<Button>().Single(button => (string?)button.Content == "重试");
            retry.RaiseEvent(new RoutedEventArgs(ButtonBase.ClickEvent));
            retry.RaiseEvent(new RoutedEventArgs(ButtonBase.ClickEvent));
            await Until(() => retries == 1 && !retry.IsEnabled);
            Require(retries == 1, "重试请求必须防重，双击不得发出第二次 POST");

            var getsBefore = runsGets;
            runsJson = retriedJson;
            retryGate.TrySetResult(Response("""{"id":"run-2","status":"RUNNING"}"""));
            await Until(() => runsGets > getsBefore
                && Field<StackPanel>(view, "runMonitor").Children.OfType<Button>().Any(button => (string?)button.Content == "取消"));
            Require(Field<StackPanel>(view, "runMonitor").Children.OfType<Button>().All(button => (string?)button.Content != "重试"),
                "重试成功后页脚应切到新运行，不再显示重试");
            Require(retries == 1, "刷新列表不得再次 POST retry");

            view.Deactivate();
        }
        finally
        {
            retryGate.TrySetResult(Response("{}"));
            StopAutosave(view);
        }
    }

    // B03：空列表并行创建单页生产流程 + 整章导出流程；部分失败后重试只补缺失项，双击不重复创建。
    private static async Task DefaultWorkflowTemplateChecks()
    {
        var created = new List<(string Name, string Template)>();
        var listCalls = 0;
        var listJson = "[]";
        var failExport = true;
        var listGate = new TaskCompletionSource<HttpResponseMessage>(TaskCreationOptions.RunContinuationsAsynchronously);
        using var api = new ApiClient("http://127.0.0.1:12345", new Handler(request =>
        {
            var path = request.RequestUri!.AbsolutePath;
            if (path.EndsWith("/workflow-node-types")) return Task.FromResult(Response(
                """[{"type":"agent.parse","label":"解析","display_name":"解析","category":"AGENT","description":"","inputs":[],"outputs":[]}]"""));
            if (path.EndsWith("/projects/p1/chapters") || path.EndsWith("/models") || path.EndsWith("/runs") || path.EndsWith("/versions"))
                return Task.FromResult(Response("[]"));
            if (path.EndsWith("/projects/p1/workflows") && request.Method == HttpMethod.Get)
            {
                listCalls++;
                if (listCalls > 1) return listGate.Task;
                return Task.FromResult(Response(listJson));
            }
            if (path.EndsWith("/projects/p1/workflows") && request.Method == HttpMethod.Post)
            {
                var body = JsonDocument.Parse(request.Content!.ReadAsStringAsync().Result).RootElement;
                var name = body.Text("name");
                var template = body.Text("template");
                created.Add((name, template));
                if (failExport && template == "chapter_export")
                    return Task.FromResult(Response("""{"detail":"整章创建被拒"}""", HttpStatusCode.BadGateway));
                var id = template == "chapter_export" ? "wf-export" : "wf-page";
                return Task.FromResult(Response($"{{\"id\":\"{id}\",\"name\":\"{name}\",\"template\":\"{template}\",\"version\":1,\"draft_version\":1,\"draft_graph\":{{\"schema_version\":2,\"nodes\":[],\"edges\":[]}}}}"));
            }
            if (path.EndsWith("/workflows/wf-page") || path.EndsWith("/workflows/wf-export"))
                return Task.FromResult(Response("""{"id":"wf-page","name":"单页生产流程","version":1,"draft_version":1,"draft_graph":{"schema_version":2,"nodes":[],"edges":[]}}"""));
            throw new Exception("Unexpected template-check request: " + request.RequestUri);
        }));
        var view = new WorkflowView();
        try
        {
            view.Activate(new WorkspaceContext
            {
                Api = api, State = new WorkspaceState(), Window = null!,
                Project = new ProjectItem("p1", "模板测试", "", 0, 0),
                NavigateSection = (_, _) => Task.CompletedTask, OpenDashboard = () => Task.CompletedTask,
            });
            await Until(() => created.Count == 2 && Field<string>(view, "createFailed").Length > 0);
            Require(created.Select(item => item.Template).OrderBy(item => item).SequenceEqual(["chapter_export", "manga_default"]),
                "空列表必须并行创建 manga_default 与 chapter_export");
            var selector = Field<ComboBox>(view, "workflowSelector");
            Require(selector.Items.OfType<ComboBoxItem>().Any(item => (string?)item.Content == "单页生产流程"),
                "部分失败时已建成的单页流程必须进入选择器");
            Require(!selector.Items.OfType<ComboBoxItem>().Any(item => (string?)item.Content == "整章导出流程"),
                "失败的整章导出流程不应进入选择器");
            var retry = Field<Button>(view, "retryCreateButton");
            Require(retry.Visibility == Visibility.Visible && retry.IsEnabled, "部分失败必须露出重试创建");

            failExport = false;
            listJson = """[{"id":"wf-page","name":"单页生产流程","version":1,"draft_version":1,"draft_graph":{"nodes":[],"edges":[]}}]""";
            created.Clear();
            retry.RaiseEvent(new RoutedEventArgs(ButtonBase.ClickEvent));
            retry.RaiseEvent(new RoutedEventArgs(ButtonBase.ClickEvent));
            await Until(() => listCalls >= 2 && !retry.IsEnabled);
            Require(created.Count == 0, "重试进行中不得并发补建");
            listGate.TrySetResult(Response(listJson));
            await Until(() => created.Count == 1);
            Require(created.Single() is { Name: "整章导出流程", Template: "chapter_export" },
                "重试只补建缺失的整章导出流程");
            await Until(() => selector.Items.OfType<ComboBoxItem>().Any(item => (string?)item.Content == "整章导出流程"));
            Require(Field<string>(view, "createFailed").Length == 0, "补建成功后应清除失败状态");
            view.Deactivate();
        }
        finally
        {
            listGate.TrySetResult(Response("[]"));
            StopAutosave(view);
        }
    }

    // B01：发布版本列表、覆盖确认、409 不覆盖草稿、切换后隔离迟到版本响应。
    private static async Task VersionRestoreChecks()
    {
        var restores = 0;
        var restoreBody = default(JsonElement);
        var versionsJson = """
            [{"id":"ver-3","workflow_id":"wf-1","revision":3,"published_at":"2026-08-27T12:00:00Z"},
             {"id":"ver-2","workflow_id":"wf-1","revision":2,"published_at":"2026-08-26T12:00:00Z"}]
            """;
        var workflowJson = """
            {"id":"wf-1","name":"流程","version":5,"draft_version":2,"published_version_id":"ver-3",
             "draft_graph":{"schema_version":2,"nodes":[{"id":"draft-node","type":"agent.parse","name":"草稿节点","position":{"x":10,"y":20},"inputs":[],"outputs":[],"config":{}}],"edges":[]}}
            """;
        var restoredJson = """
            {"id":"wf-1","name":"流程","version":8,"draft_version":4,"published_version_id":"ver-3",
             "draft_graph":{"schema_version":2,"nodes":[{"id":"restored-node","type":"agent.parse","name":"恢复节点","position":{"x":10,"y":20},"inputs":[],"outputs":[],"config":{}}],"edges":[]}}
            """;
        var conflict = false;
        using var api = new ApiClient("http://127.0.0.1:12345", new Handler(request =>
        {
            var path = request.RequestUri!.AbsolutePath;
            if (path.EndsWith("/workflow-node-types")) return Task.FromResult(Response(
                """[{"type":"agent.parse","label":"解析","display_name":"解析","category":"AGENT","description":"","inputs":[],"outputs":[]}]"""));
            if (request.Method == HttpMethod.Patch)
                return Task.FromResult(Response("""{"id":"wf-1","name":"流程","version":10,"draft_version":5,"draft_graph":{"nodes":[],"edges":[]}}"""));
            if (path.EndsWith("/projects/p1/workflows"))
                return Task.FromResult(Response("""[{"id":"wf-1","name":"流程"},{"id":"wf-2","name":"另一条"}]"""));
            if (path.EndsWith("/projects/p1/chapters") || path.EndsWith("/models") || path.EndsWith("/runs"))
                return Task.FromResult(Response("[]"));
            if (path.EndsWith("/workflows/wf-1/versions")) return Task.FromResult(Response(versionsJson));
            if (path.EndsWith("/workflows/wf-2/versions"))
                return Task.FromResult(Response("""[{"id":"ver-b","workflow_id":"wf-2","revision":1,"published_at":"2026-08-27T00:00:00Z"}]"""));
            if (path.EndsWith("/workflows/wf-2"))
                return Task.FromResult(Response("""{"id":"wf-2","name":"另一条","version":1,"draft_version":1,"draft_graph":{"schema_version":2,"nodes":[{"id":"b-node","type":"agent.parse","name":"乙","position":{"x":10,"y":20},"inputs":[],"outputs":[],"config":{}}],"edges":[]}}"""));
            if (path.EndsWith("/workflows/wf-1") && request.Method == HttpMethod.Get)
                return Task.FromResult(Response(workflowJson));
            if (path.EndsWith("/workflow-versions/ver-3/restore") && request.Method == HttpMethod.Post)
            {
                restores++;
                restoreBody = JsonDocument.Parse(request.Content!.ReadAsStringAsync().Result).RootElement.Clone();
                if (conflict)
                    return Task.FromResult(Response("""{"detail":"工作流已被其他页面修改，请刷新后重试"}""", HttpStatusCode.Conflict));
                return Task.FromResult(Response(restoredJson));
            }
            throw new Exception("Unexpected version-check request: " + request.RequestUri);
        }));
        var view = new WorkflowView();
        try
        {
            view.RestoreConfirmOverride = _ => Task.FromResult(true);
            view.Activate(new WorkspaceContext
            {
                Api = api, State = new WorkspaceState(), Window = null!,
                Project = new ProjectItem("p1", "版本测试", "", 0, 0),
                NavigateSection = (_, _) => Task.CompletedTask, OpenDashboard = () => Task.CompletedTask,
            });
            await Until(() => NodeNames(view).Contains("草稿节点")
                && Field<StackPanel>(view, "versionList").Children.OfType<Button>().Any(button => Name(button) == "V3"));
            Require(Field<TextBlock>(view, "publishedValue").Text == "V3", "已发布版本指标应显示最新 revision，而不是尚未发布");
            Require(Field<StackPanel>(view, "versionList").Children.OfType<Button>().Any(button => Name(button) == "V2"),
                "版本列表应展示最近发布版本");

            conflict = true;
            workflowJson = """{"id":"wf-1","name":"流程","version":9,"draft_version":2,"published_version_id":"ver-3","draft_graph":{"schema_version":2,"nodes":[{"id":"draft-node","type":"agent.parse","name":"草稿节点","position":{"x":10,"y":20},"inputs":[],"outputs":[],"config":{}}],"edges":[]}}""";
            Field<StackPanel>(view, "versionList").Children.OfType<Button>().Single(button => Name(button) == "V3")
                .RaiseEvent(new RoutedEventArgs(ButtonBase.ClickEvent));
            await Until(() => restores == 1 && Field<int>(view, "version") == 9);
            Require(NodeNames(view).Contains("草稿节点") && !NodeNames(view).Contains("恢复节点"),
                "409 恢复不得用版本图覆盖本地草稿");
            Require(restoreBody.Number("version") == 5, "恢复必须携带当前草稿 version 做 CAS");

            conflict = false;
            Field<StackPanel>(view, "versionList").Children.OfType<Button>().Single(button => Name(button) == "V3")
                .RaiseEvent(new RoutedEventArgs(ButtonBase.ClickEvent));
            await Until(() => restores == 2 && NodeNames(view).Contains("恢复节点"));
            Require(!NodeNames(view).Contains("草稿节点"), "恢复成功后画布应换成发布版本的图");
            Require(Field<int>(view, "version") == 8, "恢复成功后应采用服务端新 version");

            var selector = Field<ComboBox>(view, "workflowSelector");
            selector.SelectedItem = selector.Items.OfType<ComboBoxItem>().Single(item => (string?)item.Tag == "wf-2");
            await Until(() => Field<string>(view, "workflowId") == "wf-2" && NodeNames(view).Contains("乙"));
            Require(Field<StackPanel>(view, "versionList").Children.OfType<Button>().All(button => Name(button) != "V3"),
                "切换工作流后不得继续显示上一条的发布版本");
            Require(Field<TextBlock>(view, "publishedValue").Text == "V1", "切换后已发布版本指标应跟随新工作流");

            view.Deactivate();
        }
        finally
        {
            StopAutosave(view);
        }
    }

    private static async Task VersionListFailureChecks()
    {
        var versionCalls = 0;
        var fail = true;
        using var api = new ApiClient("http://127.0.0.1:12345", new Handler(request =>
        {
            var path = request.RequestUri!.AbsolutePath;
            if (path.EndsWith("/workflow-node-types")) return Task.FromResult(Response("[]"));
            if (path.EndsWith("/projects/p1/workflows"))
                return Task.FromResult(Response("""[{"id":"wf-1","name":"流程"}]"""));
            if (path.EndsWith("/projects/p1/chapters") || path.EndsWith("/models") || path.EndsWith("/runs"))
                return Task.FromResult(Response("[]"));
            if (path.EndsWith("/workflows/wf-1/versions"))
            {
                versionCalls++;
                if (fail) return Task.FromResult(Response("""{"detail":"版本接口 500"}""", HttpStatusCode.InternalServerError));
                return Task.FromResult(Response("""[{"id":"ver-3","workflow_id":"wf-1","revision":3,"published_at":"2026-08-27T00:00:00Z"}]"""));
            }
            if (path.EndsWith("/workflows/wf-1"))
                return Task.FromResult(Response("""{"id":"wf-1","name":"流程","version":1,"draft_version":1,"draft_graph":{"nodes":[],"edges":[]}}"""));
            throw new Exception("Unexpected version-failure request: " + request.RequestUri);
        }));
        var view = new WorkflowView();
        try
        {
            view.Activate(new WorkspaceContext
            {
                Api = api, State = new WorkspaceState(), Window = null!,
                Project = new ProjectItem("p1", "版本失败", "", 0, 0),
                NavigateSection = (_, _) => Task.CompletedTask, OpenDashboard = () => Task.CompletedTask,
            });
            await Until(() => Field<TextBlock>(view, "publishedValue").Text == "读取失败");
            Require(Field<TextBlock>(view, "publishedValue").Text != "尚未发布",
                "版本列表读取失败不得谎报尚未发布");
            var retry = Field<StackPanel>(view, "versionList").Children.OfType<Button>().Single(button => Name(button) == "重试发布版本");
            fail = false;
            retry.RaiseEvent(new RoutedEventArgs(ButtonBase.ClickEvent));
            await Until(() => Field<TextBlock>(view, "publishedValue").Text == "V3");
            Require(versionCalls >= 2, "失败后的重试必须重新请求版本列表");
            view.Deactivate();
        }
        finally
        {
            StopAutosave(view);
        }
    }

    private static async Task VersionStaleResponseChecks()
    {
        var gate = new TaskCompletionSource<HttpResponseMessage>(TaskCreationOptions.RunContinuationsAsynchronously);
        using var api = new ApiClient("http://127.0.0.1:12345", new Handler(request =>
        {
            var path = request.RequestUri!.AbsolutePath;
            if (path.EndsWith("/workflow-node-types")) return Task.FromResult(Response("[]"));
            if (request.Method == HttpMethod.Patch)
                return Task.FromResult(Response("""{"id":"wf-a","version":4,"draft_graph":{"nodes":[],"edges":[]}}"""));
            if (path.EndsWith("/projects/p1/workflows"))
                return Task.FromResult(Response("""[{"id":"wf-a","name":"甲"},{"id":"wf-b","name":"乙"}]"""));
            if (path.EndsWith("/projects/p1/chapters") || path.EndsWith("/models") || path.EndsWith("/runs"))
                return Task.FromResult(Response("[]"));
            if (path.EndsWith("/workflows/wf-a/versions")) return gate.Task;
            if (path.EndsWith("/workflows/wf-b/versions"))
                return Task.FromResult(Response("""[{"id":"ver-b","workflow_id":"wf-b","revision":1,"published_at":"2026-08-27T00:00:00Z"}]"""));
            if (path.EndsWith("/workflows/wf-a"))
                return Task.FromResult(Response("""{"id":"wf-a","name":"甲","version":2,"draft_version":1,"draft_graph":{"schema_version":2,"nodes":[{"id":"a-node","type":"agent.parse","name":"甲","position":{"x":10,"y":20},"inputs":[],"outputs":[],"config":{}}],"edges":[]}}"""));
            if (path.EndsWith("/workflows/wf-b"))
                return Task.FromResult(Response("""{"id":"wf-b","name":"乙","version":3,"draft_version":1,"draft_graph":{"schema_version":2,"nodes":[{"id":"b-node","type":"agent.parse","name":"乙","position":{"x":10,"y":20},"inputs":[],"outputs":[],"config":{}}],"edges":[]}}"""));
            throw new Exception("Unexpected stale-version request: " + request.RequestUri);
        }));
        var view = new WorkflowView();
        try
        {
            view.Activate(new WorkspaceContext
            {
                Api = api, State = new WorkspaceState(), Window = null!,
                Project = new ProjectItem("p1", "版本隔离", "", 0, 0),
                NavigateSection = (_, _) => Task.CompletedTask, OpenDashboard = () => Task.CompletedTask,
            });
            await Until(() => NodeNames(view).Contains("甲"));
            var selector = Field<ComboBox>(view, "workflowSelector");
            selector.SelectedItem = selector.Items.OfType<ComboBoxItem>().Single(item => (string?)item.Tag == "wf-b");
            await Until(() => Field<string>(view, "workflowId") == "wf-b" && NodeNames(view).Contains("乙")
                && Field<TextBlock>(view, "publishedValue").Text == "V1");
            gate.TrySetResult(Response("""[{"id":"ver-a","workflow_id":"wf-a","revision":99,"published_at":"2026-08-27T00:00:00Z"}]"""));
            await Task.Delay(80);
            Require(Field<TextBlock>(view, "publishedValue").Text == "V1",
                "迟到的上一工作流版本列表不得覆盖当前工作流");
            Require(Field<StackPanel>(view, "versionList").Children.OfType<Button>().All(button => Name(button) != "V99"),
                "迟到响应不得把旧工作流的版本画进新工作流");
            view.Deactivate();
        }
        finally
        {
            gate.TrySetResult(Response("[]"));
            StopAutosave(view);
        }
    }

    // ── R1 回归（恢复失败重臂）：恢复版本 409 后防抖必须重臂。旧实现按状态行
    // 文本嗅探"失败"——ApiClient 给 409 统一前置"数据已变化或操作条件不满足…"
    // 前缀，Split('\n')[0] 永远不含"失败"，pending 编辑从此无人落盘直到进程退出。 ──
    private static async Task RestoreConflictReArmChecks()
    {
        var workflowJson = """
            {"id":"wf-rc","name":"流程","version":5,"draft_version":2,
             "draft_graph":{"schema_version":2,"nodes":[{"id":"draft-node","type":"agent.parse","name":"草稿节点","position":{"x":10,"y":20},"inputs":[],"outputs":[],"config":{"model_alias":"auto","temperature":0.2,"notes":""}}],"edges":[]}}
            """;
        var restores = 0;
        var patches = 0;
        using var api = new ApiClient("http://127.0.0.1:12345", new Handler(request =>
        {
            var path = request.RequestUri!.AbsolutePath;
            if (request.Method == HttpMethod.Patch && path.EndsWith("/workflows/wf-rc"))
            {
                patches++;
                return Task.FromResult(Response("""{"id":"wf-rc","name":"流程","version":10,"draft_version":5,"draft_graph":{"nodes":[],"edges":[]}}"""));
            }
            if (path.EndsWith("/workflow-node-types")) return Task.FromResult(Response(
                """[{"type":"agent.parse","label":"解析","display_name":"解析","category":"AGENT","description":"","inputs":[],"outputs":[]}]"""));
            if (path.EndsWith("/projects/p1/workflows"))
                return Task.FromResult(Response("""[{"id":"wf-rc","name":"流程"}]"""));
            if (path.EndsWith("/projects/p1/chapters") || path.EndsWith("/models") || path.EndsWith("/runs"))
                return Task.FromResult(Response("[]"));
            if (path.EndsWith("/workflows/wf-rc/versions"))
                return Task.FromResult(Response("""[{"id":"ver-r1","workflow_id":"wf-rc","revision":3,"published_at":"2026-08-27T00:00:00Z"}]"""));
            if (request.Method == HttpMethod.Get && path.EndsWith("/workflows/wf-rc"))
                return Task.FromResult(Response(workflowJson));
            if (path.EndsWith("/workflow-versions/ver-r1/restore") && request.Method == HttpMethod.Post)
            {
                restores++;
                return Task.FromResult(Response(
                    """{"detail":"数据已变化或操作条件不满足。请刷新后重试。\n工作流已被其他页面修改"}""",
                    HttpStatusCode.Conflict));
            }
            throw new Exception("Unexpected rearm-check request: " + request.RequestUri);
        }));
        var view = new WorkflowView();
        try
        {
            view.RestoreConfirmOverride = _ => Task.FromResult(true);
            view.Activate(new WorkspaceContext
            {
                Api = api, State = new WorkspaceState(), Window = null!,
                Project = new ProjectItem("p1", "重臂检查", "", 0, 0),
                NavigateSection = (_, _) => Task.CompletedTask, OpenDashboard = () => Task.CompletedTask,
            });
            await Until(() => NodeNames(view).Contains("草稿节点")
                && Field<StackPanel>(view, "versionList").Children.OfType<Button>().Any(button => Name(button) == "V3"));
            // 武装防抖（等价一次编辑），确认框立即通过，恢复 409（首行是固定前缀）。
            Select(view, NodeById(view, "draft-node"));
            Box(view, "温度")!.Text = "0.9";
            Require(typeof(WorkflowView).GetField("autosave", All)!.GetValue(view) as System.Timers.Timer is { Enabled: true },
                "编辑后防抖计时器应已武装");
            workflowJson = workflowJson.Replace("\"version\":5", "\"version\":9");   // RefreshWorkflowVersionAsync 重取到的服务端新版本
            Field<StackPanel>(view, "versionList").Children.OfType<Button>().Single(button => Name(button) == "V3")
                .RaiseEvent(new RoutedEventArgs(ButtonBase.ClickEvent));
            await Until(() => restores == 1);
            // 版本同步（9）与重臂后的防抖保存（PATCH 推进到 10）存在先后竞态，只要求
            // 二者之一已生效：>= 9 足以证明 409 分支的版本刷新路径执行过。
            await Until(() => Field<int>(view, "version") >= 9);
            Require(typeof(WorkflowView).GetField("autosave", All)!.GetValue(view) as System.Timers.Timer is { Enabled: true },
                "409 恢复失败后防抖未重臂：待存编辑从此无人落盘（旧实现按状态行文本嗅探\"失败\"恒假）");
            await Until(() => patches >= 1);   // 重臂后的防抖必须真的把 wf-rc 的编辑落盘
            view.Deactivate();
        }
        finally
        {
            StopAutosave(view);
        }
    }

    // ── R1 回归（载入失败回滚 + 切换清选中）：切到乙流程但 GET 乙 500 时，必须把
    // workflowId/选择器回滚到画布归属甲——旧实现只写状态行，防抖身份检查从此恒假，
    // 画布上的编辑被静默丢弃、发布/校验/运行打到画布之外的目标。伴生：成功切换后
    // selectedNodes 必须清空（残留旧对象＝"不可见选中"的删除/复制）。 ──
    private static async Task LoadFailureRollbackChecks()
    {
        var wf1Json = """
            {"id":"wf-lf1","name":"甲流程","version":5,"draft_version":2,"draft_graph":{"schema_version":2,"nodes":[{"id":"a-node","type":"agent.parse","name":"甲","position":{"x":10,"y":20},"inputs":[],"outputs":[],"config":{"model_alias":"auto","temperature":0.2,"notes":""}}],"edges":[]}}
            """;
        var wf2Json = """
            {"id":"wf-lf2","name":"乙流程","version":3,"draft_version":1,"draft_graph":{"schema_version":2,"nodes":[{"id":"b-node","type":"agent.parse","name":"乙","position":{"x":10,"y":20},"inputs":[],"outputs":[],"config":{"model_alias":"auto","temperature":0.5,"notes":""}}],"edges":[]}}
            """;
        var failWf2Load = true;
        var wf1Patches = 0;
        using var api = new ApiClient("http://127.0.0.1:12345", new Handler(request =>
        {
            var path = request.RequestUri!.AbsolutePath;
            if (request.Method == HttpMethod.Patch && path.EndsWith("/workflows/wf-lf1"))
            {
                wf1Patches++;
                return Task.FromResult(Response("""{"id":"wf-lf1","name":"甲流程","version":6,"draft_version":3,"draft_graph":{"nodes":[],"edges":[]}}"""));
            }
            if (request.Method == HttpMethod.Patch && path.EndsWith("/workflows/wf-lf2"))
                return Task.FromResult(Response("""{"id":"wf-lf2","name":"乙流程","version":4,"draft_version":2,"draft_graph":{"nodes":[],"edges":[]}}"""));
            if (path.EndsWith("/workflow-node-types")) return Task.FromResult(Response(
                """[{"type":"agent.parse","label":"解析","display_name":"解析","category":"AGENT","description":"","inputs":[],"outputs":[]}]"""));
            if (path.EndsWith("/projects/p1/workflows"))
                return Task.FromResult(Response("""[{"id":"wf-lf1","name":"甲流程"},{"id":"wf-lf2","name":"乙流程"}]"""));
            if (path.EndsWith("/projects/p1/chapters") || path.EndsWith("/models") || path.EndsWith("/runs"))
                return Task.FromResult(Response("[]"));
            if (path.EndsWith("/workflows/wf-lf1/versions") || path.EndsWith("/workflows/wf-lf2/versions"))
                return Task.FromResult(Response("[]"));
            if (request.Method == HttpMethod.Get && path.EndsWith("/workflows/wf-lf2"))
            {
                if (failWf2Load)
                    return Task.FromResult(Response("""{"detail":"乙流程载入 500"}""", HttpStatusCode.InternalServerError));
                return Task.FromResult(Response(wf2Json));
            }
            if (request.Method == HttpMethod.Get && path.EndsWith("/workflows/wf-lf1"))
                return Task.FromResult(Response(wf1Json));
            throw new Exception("Unexpected rollback-check request: " + request.RequestUri);
        }));
        var view = new WorkflowView();
        try
        {
            view.Activate(new WorkspaceContext
            {
                Api = api, State = new WorkspaceState(), Window = null!,
                Project = new ProjectItem("p1", "载入失败回滚", "", 0, 0),
                NavigateSection = (_, _) => Task.CompletedTask, OpenDashboard = () => Task.CompletedTask,
            });
            await Until(() => Field<string>(view, "workflowId") == "wf-lf1" && NodeNames(view).Contains("甲"));
            var selector = Field<ComboBox>(view, "workflowSelector");
            // 乙流程载入 500：切换 flush 甲成功后回滚到甲。
            selector.SelectedItem = selector.Items.OfType<ComboBoxItem>().Single(item => (string?)item.Tag == "wf-lf2");
            await Until(() => Field<string>(view, "workflowId") == "wf-lf1"
                && (selector.SelectedItem as ComboBoxItem)?.Tag as string == "wf-lf1");
            Require(NodeNames(view).Contains("甲") && !NodeNames(view).Contains("乙"),
                "载入失败不得动当前画布（仍是甲的图）");
            Require(Field<TextBlock>(view, "statusLine").Text.Contains("已停留在当前画布对应的工作流"),
                "载入失败回滚后应提示已停留在画布对应的工作流");
            Require(Field<string>(view, "canvasWorkflowId") == "wf-lf1", "画布归属不应被失败载入改写");
            // 回滚后画布上的新编辑必须还能落盘（旧实现：身份恒假→静默丢弃）。
            Select(view, NodeById(view, "a-node"));
            Box(view, "温度")!.Text = "0.9";
            await Until(() => wf1Patches >= 2);   // 切换 flush 一次 + 回滚后的编辑一次
            // 成功切换必须清空 selectedNodes（DN-04：残留＝不可见选中）。
            failWf2Load = false;
            selector.SelectedItem = selector.Items.OfType<ComboBoxItem>().Single(item => (string?)item.Tag == "wf-lf2");
            await Until(() => Field<string>(view, "workflowId") == "wf-lf2" && NodeNames(view).Contains("乙"));
            Require(((System.Collections.IEnumerable)typeof(WorkflowView).GetField("selectedNodes", All)!.GetValue(view)!).Cast<object>().Count() == 0,
                "切换工作流后 selectedNodes 残留旧节点对象（不可见选中的删除/复制）");
            view.Deactivate();
        }
        finally
        {
            StopAutosave(view);
        }
    }

    // ── R1 回归（动作目标取画布归属）：切换在途窗口（workflowId 已是乙、画布仍是
    // 甲的图）内点发布，POST 必须打到甲——旧实现读 workflowId，会把乙的旧服务端
    // 草稿固化为不可变发布版本。 ──
    private static async Task ActionTargetsCanvasChecks()
    {
        var aGraph = """
            {"schema_version":2,"nodes":[{"id":"a-node","type":"agent.parse","name":"甲","position":{"x":10,"y":20},"inputs":[],"outputs":[],"config":{"model_alias":"auto","temperature":0.2,"notes":""}}],"edges":[]}
            """;
        var bGraph = """
            {"schema_version":2,"nodes":[{"id":"b-node","type":"agent.parse","name":"乙","position":{"x":10,"y":20},"inputs":[],"outputs":[],"config":{"model_alias":"auto","temperature":0.5,"notes":""}}],"edges":[]}
            """;
        var getB = 0;
        var publishPath = "";
        var bLoadGate = new TaskCompletionSource<HttpResponseMessage>();
        using var api = new ApiClient("http://127.0.0.1:12345", new Handler(async request =>
        {
            var path = request.RequestUri!.AbsolutePath;
            if (request.Method == HttpMethod.Patch && path.EndsWith("/workflows/wf-ac1"))
                return Response("""{"id":"wf-ac1","name":"甲","version":6,"draft_version":2,"draft_graph":{"nodes":[],"edges":[]}}""");
            if (request.Method == HttpMethod.Patch && path.EndsWith("/workflows/wf-ac2"))
                return Response("""{"id":"wf-ac2","name":"乙","version":4,"draft_version":2,"draft_graph":{"nodes":[],"edges":[]}}""");
            if (request.Method == HttpMethod.Post && path.EndsWith("/publish"))
            {
                publishPath = path;
                return Response("""{"id":"ver-x","revision":1}""");
            }
            if (request.Method == HttpMethod.Get && path.EndsWith("/workflows/wf-ac1"))
                return Response($"{{\"id\":\"wf-ac1\",\"name\":\"甲\",\"version\":5,\"draft_version\":1,\"draft_graph\":{aGraph}}}");
            if (request.Method == HttpMethod.Get && path.EndsWith("/workflows/wf-ac2"))
            {
                getB++;
                if (getB == 1) return await bLoadGate.Task;   // 制造"已切换、未载入"窗口
                return Response($"{{\"id\":\"wf-ac2\",\"name\":\"乙\",\"version\":3,\"draft_version\":1,\"draft_graph\":{bGraph}}}");
            }
            if (path.EndsWith("/workflow-node-types")) return Response(
                """[{"type":"agent.parse","label":"解析","display_name":"解析","category":"AGENT","description":"","inputs":[],"outputs":[]}]""");
            if (path.EndsWith("/projects/p1/workflows")) return Response(
                """[{"id":"wf-ac1","name":"甲","version":5,"draft_version":1,"draft_graph":{"nodes":[],"edges":[]}},{"id":"wf-ac2","name":"乙","version":3,"draft_version":1,"draft_graph":{"nodes":[],"edges":[]}}]""");
            if (path.EndsWith("/projects/p1/chapters")) return Response("[]");
            if (path.EndsWith("/models")) return Response("[]");
            if (path.EndsWith("/runs")) return Response("[]");
            if (path.EndsWith("/versions")) return Response("[]");
            throw new Exception("Unexpected action-target request: " + request.RequestUri);
        }));
        var view = new WorkflowView();
        try
        {
            view.Activate(new WorkspaceContext
            {
                Api = api, State = new WorkspaceState(), Window = null!,
                Project = new ProjectItem("p1", "动作目标检查", "", 0, 0),
                NavigateSection = (_, _) => Task.CompletedTask, OpenDashboard = () => Task.CompletedTask,
            });
            await Until(() => NodeNames(view).Contains("甲"));
            var selector = Field<ComboBox>(view, "workflowSelector");
            selector.SelectedItem = selector.Items.OfType<ComboBoxItem>().Single(item => (string?)item.Tag == "wf-ac2");
            await Until(() => Field<string>(view, "workflowId") == "wf-ac2"
                && Field<string>(view, "canvasWorkflowId") == "wf-ac1" && getB == 1);
            // 窗口内发布：目标必须是画布归属 wf-ac1（旧实现 POST 到 wf-ac2）。
            await (Task)typeof(WorkflowView).GetMethod("PublishAsync", All)!.Invoke(view, null)!;
            Require(publishPath.EndsWith("/workflows/wf-ac1/publish"),
                $"切换在途窗口内发布必须打到画布归属（wf-ac1），实际打到 {publishPath}");
            bLoadGate.TrySetResult(Response($"{{\"id\":\"wf-ac2\",\"name\":\"乙\",\"version\":3,\"draft_version\":1,\"draft_graph\":{bGraph}}}"));
            await Until(() => Field<string>(view, "canvasWorkflowId") == "wf-ac2" && NodeNames(view).Contains("乙"));
            view.Deactivate();
        }
        finally
        {
            bLoadGate.TrySetResult(Response("{}"));
            StopAutosave(view);
        }
    }

    private static IEnumerable<string> NodeNames(WorkflowView view) =>
        Nodes(view).Cast<object>().Select(node => (string)node.GetType().GetField("Name", All)!.GetValue(node)!);

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

    // 按数值读取整数配置：AttachNumberEditor 的三元 `round ? (int)value : value`
    // 公共类型是 double——即使走 round 分支也会被加宽后按 Double 装箱，超时/重试/
    // 并行的写回值是 Double 30 而非 Int32（网页 JS 数字本就是 double，JSON 序列化
    // 后无差别）。钳制断言必须按数值比较；按 CLR 装箱类型断言会恒假（该检查此前
    // 因调用方未 await 而从未真正执行，这次复活时修正）。
    private static int AsInt(Dictionary<string, object?> config, string key) =>
        config.TryGetValue(key, out var value) && value is JsonElement { ValueKind: JsonValueKind.Number } number
            ? number.GetInt32()
            : value is int parsed ? parsed
            : value is double integral ? (int)integral
            : int.MinValue;

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

    // 审批队列里 generator.page 行的两个控件（LoadRunsAsync 清空/重加之间会短暂取不到）。
    private static ComboBox? GeneratorModelBox(WorkflowView view) =>
        Field<StackPanel>(view, "approvalQueue").Children.OfType<StackPanel>()
            .FirstOrDefault(row => row.Children.OfType<ComboBox>().Any(combo => Name(combo) == "选择图片模型"))
            ?.Children.OfType<ComboBox>().FirstOrDefault(combo => Name(combo) == "选择图片模型");

    private static Button? GeneratorApprove(WorkflowView view) =>
        Field<StackPanel>(view, "approvalQueue").Children.OfType<StackPanel>()
            .FirstOrDefault(row => row.Children.OfType<ComboBox>().Any(combo => Name(combo) == "选择图片模型"))
            ?.Children.OfType<Button>().SingleOrDefault(button => (string?)button.Content == "确认继续");

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
