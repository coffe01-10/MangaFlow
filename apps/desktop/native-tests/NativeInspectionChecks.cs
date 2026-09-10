using System.IO;
using System.Net;
using System.Net.Http;
using System.Reflection;
using System.Text.Json;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Controls.Primitives;
using System.Windows.Media;
using MangaFlow.Native;
using MangaFlow.Native.Services;
using MangaFlow.Native.Views;

// 生成台「视觉检查结果面板与逐项修复入口」回归（网页基准 inspection-panel.tsx /
// use-generation-workspace.ts / workflow/inspection.py）：
//   ① 面板按 InspectionRead 模型渲染：维度/结论/分数/摘要/气泡差异，按当前分镜
//      版本过滤并每维度取最新一条；
//   ② 逐项修复按 web repairCandidate 契约提交 POST /candidates/{id}/repairs
//      （inspection_result_id / repair_type / target_regions / target_fields /
//      model_alias / resolution），成功后收起面板并重拉工作台；
//   ③ 检查任务（含其他客户端提交的外部任务）转终态后，结果与工作台/页面数据
//      被重新拉取；
//   ④ 空结果、读取失败、迟到响应（面板已关闭/候选已切换）都有守卫。
//
// 注册说明（需 lead 完成，因文件级互斥本文件未自行接线）：本仓库的发现机制是
// NativeInteractionChecks.RunIsolated 里的手动调用列表——需在该列表加入
// await NativeInspectionChecks.Run(output);
// 直接 new GenerateView() 依赖 Application.Current 的 Theme 资源（Kit.Act / 卡片
// 样式），必须运行在 --render 的 STA 线程上（NativeVisualChecks 建立的 STA 链），
// 不能在无 Application 资源的普通线程执行。
internal static class NativeInspectionChecks
{
    public static async Task Run(string output)
    {
        var previous = (string)typeof(KeyValueStore).GetField("path", BindingFlags.Static | BindingFlags.NonPublic)!.GetValue(null)!;
        var prefs = Path.Combine(output, "inspection-panel-test-prefs.json");
        File.WriteAllText(prefs, "{}");
        KeyValueStore.UseLocation(prefs);
        try { await RunIsolated(); }
        finally { KeyValueStore.UseLocation(previous); File.Delete(prefs); File.Delete(prefs + ".tmp"); }
    }

    private static async Task RunIsolated()
    {
        var fake = new Fixture();
        using var api = new ApiClient("http://127.0.0.1:12345", fake);
        // 抽卡模型在 Activate 时从 KeyValueStore 恢复：先落盘再激活，修复载荷才能带 model_alias。
        KeyValueStore.Set("image-model:p1", "model-a");
        var view = new GenerateView();
        view.Activate(new WorkspaceContext
        {
            Api = api, Cache = new(), State = new(), Window = null!,
            Project = new ProjectItem("p1", "检查面板测试", "", 0, 0),
            NavigateSection = (_, _) => Task.CompletedTask, OpenDashboard = () => Task.CompletedTask,
        });
        try
        {
            Layout(view, 1200, 1600);
            await Until(() => Field<JsonElement>(view, "workbench").ValueKind == JsonValueKind.Object && view.CurrentPage != null,
                "生成台初始工作台数据始终未载入");
            await RenderChecks(view);
            await WaitStateChecks(view);
            await RepairChecks(view, fake);
            await TerminalRefreshChecks(view, fake);
            await GuardChecks(view, fake);
        }
        finally { view.Deactivate(); }
        Console.WriteLine("PASS: 检查面板按 Read 模型渲染（版本过滤 + 维度去重 + 气泡差异）；逐项修复按 web 载荷提交并在成功后收起；终态后结果/工作台/页面重拉；空结果/失败/迟到响应有守卫");
    }

    // ── ① 结果面板按 API Read 模型渲染 ──
    private static async Task RenderChecks(GenerateView view)
    {
        // 纯函数契约（web display.ts 的 recommendedRepairType / inspectionSummary / inspectionBubbleDiffs）。
        Require(GenerateView.RecommendedRepairType("SPEAKER") == "BUBBLE_REGION"
            && GenerateView.RecommendedRepairType("CHARACTER") == "PANEL"
            && GenerateView.RecommendedRepairType("OUTFIT") == "PANEL"
            && GenerateView.RecommendedRepairType("PROP") == "PANEL"
            && GenerateView.RecommendedRepairType("CONTINUITY") == "PAGE"
            && GenerateView.RecommendedRepairType("TEXT") == "PAGE",
            "修复范围推荐与 web recommendedRepairType 不一致");
        Require(GenerateView.InspectionSummary(Element("""{"expected":"小满","observed":"陌生男人"}""")) == "应为：小满；实为：陌生男人"
            && GenerateView.InspectionSummary(Element("""{"expected":"小满"}""")) == "应为：小满"
            && GenerateView.InspectionSummary(Element("{}")) == "模型未补充说明"
            && GenerateView.InspectionSummary(Element("""{"备注":"一致"}""")) == "备注: 一致",
            "检查摘要与 web inspectionSummary 不一致");
        Require(GenerateView.InspectionBubbleDiffs(Element("""{"bubble_diffs":[{"balloon_index":1},"bad"]}""")).Count == 1
            && GenerateView.InspectionBubbleDiffs(Element("""{}""")).Count == 0,
            "气泡差异提取未过滤非对象项");

        view.OpenInspectionPanel("cand-1");
        await UntilTree(view, () => PanelRoot(view) != null && PanelTexts(view).Any(t => t.Contains("不匹配")), "检查面板未渲染检查结论");
        Layout(view, 1200, 1600);
        var texts = PanelTexts(view);
        // latestInspections：接口按 created_at 倒序，V2 的 CONTINUITY（列表第一条）必须被
        // 分镜版本过滤，SPEAKER 取最新一条 MISMATCH（旧 PASS 被维度去重）。
        Require(texts.Any(t => t == "说话人 · 不匹配 · 42%"), "说话人 issue 的维度/结论/分数渲染不符");
        Require(texts.Any(t => t == "角色 · 通过 · 97%"), "通过项的结论/分数渲染不符");
        Require(texts.Any(t => t == "文字 · 不匹配 · —"), "无分数的 issue 未显示占位符");
        Require(texts.Any(t => t.Contains("应为：小满；实为：陌生男人")), "expected/observed 摘要未按 web 格式呈现");
        Require(texts.Any(t => t == "气泡 02 · 目标：我们回家吧 · 识别：我门回家吧 · 50%"), "气泡差异行（编号/目标/识别/相似度）渲染不符");
        Require(texts.Any(t => t == "气泡 02 · 目标：明天见 · 识别：大天见 · —"), "缺省气泡编号与相似度未按 web 回退规则渲染");
        Require(texts.Any(t => t.Contains("备注: 角色特征一致")), "无 expected/observed 的 details 未逐键罗列");
        Require(texts.Any(t => t == "请人工校对；确认后可直接采用"), "文字问题缺少人工校对提示");
        Require(texts.Count(t => t.StartsWith("连续性 · ")) == 1, "过期分镜版本的检查结果未被过滤，或当前版本结果丢失");
        var panel = PanelRoot(view)!;
        var repairButtons = Descendants(panel).OfType<Button>()
            .Where(b => ((string?)b.Content)?.StartsWith("修复", StringComparison.Ordinal) == true).ToList();
        Require(repairButtons.Select(b => (string)b.Content!).SequenceEqual(new[] { "修复气泡区域", "修复单格", "修复整页" }),
            "修复按钮的维度→范围映射或数量与 web 不一致（文字问题不应有修复按钮）");
        Require(view.LatestInspections().Select(item => item.Text("id")).SequenceEqual(
            new[] { "ins-speaker-2", "ins-text", "ins-character", "ins-prop", "ins-continuity" }),
            "latestInspections 选取（版本过滤 + 维度取最新）与 web 不一致");
    }

    // ── 等待行：无结果时显示任务状态/读取文案 ──
    private static async Task WaitStateChecks(GenerateView view)
    {
        view.OpenInspectionPanel("cand-empty");
        await UntilTree(view, () => PanelTexts(view).Any(t => t == "正在读取检查结果"), "空结果的等待行未出现");
        // 检查任务进行中：等待行显示任务状态与进度（web jobStatusLabels + progress%）。
        Set(view, "reviewInspectJob", Element("""{"id":"job-w","job_type":"PAGE_INSPECT","target_id":"cand-empty","status":"QUEUED","progress":35}"""));
        InvokeRender(view);
        Layout(view, 1200, 1600);
        Require(PanelTexts(view).Any(t => t == "检查任务 排队中 · 35%"), "进行中的检查任务未在等待行展示状态与进度");
        Click(CloseButton(view)!);
        Require(PanelRoot(view) == null, "关闭按钮没有收起检查面板");
    }

    // ── ② 逐项修复：端点与载荷（Writes 记录断言）──
    private static async Task RepairChecks(GenerateView view, Fixture fake)
    {
        view.OpenInspectionPanel("cand-1");
        await UntilTree(view, () => RepairButton(view, "修复气泡区域") != null, "修复按钮未出现");
        var hold = fake.PendingRepair = Held();
        var workbench0 = fake.WorkbenchReads;
        var repair = RepairButton(view, "修复气泡区域")!;
        Click(repair);
        Click(repair);
        Require(fake.RepairWrites.Count == 1, "修复请求被重复提交");
        var write = fake.RepairWrites[0];
        Require(write.Method == "POST" && write.Path.EndsWith("/candidates/cand-1/repairs"), "修复未命中 web 的 POST /candidates/{id}/repairs 端点");
        var body = write.Body;
        Require(body.Text("inspection_result_id") == "ins-speaker-2" && body.Text("repair_type") == "BUBBLE_REGION"
            && body.Text("model_alias") == "model-a" && body.Text("resolution") == "2K",
            "修复载荷与 web repairCandidate 契约不符（结果 id/范围/模型/分辨率）");
        Require(body.Array("target_regions").Count == 1 && body.Array("target_regions")[0].Text("panel_id") == "panel-1",
            "修复未携带该 issue 的 target_regions（inspection.regions）");
        Require(body.Array("target_fields").Count == 0, "web 契约的 target_fields 恒为空数组");
        hold.SetResult(Json("""{"job_id":"job-r1","job_status":"QUEUED","candidate":{"id":"cand-r1","status":"QUEUED","resolution":"2K"}}"""));
        await Until(() => Field<string?>(view, "reviewCandidateId") == null, "修复成功后检查面板未收起（批次已切换）");
        Layout(view, 1200, 1600);
        Require(PanelRoot(view) == null, "修复成功后面板仍然可见");
        await Until(() => fake.WorkbenchReads > workbench0, "修复成功后未重拉生成工作台");

        // 无可用模型：不提交，面板内给出 web requireDrawModel 同款文案。
        view.OpenInspectionPanel("cand-1");
        await UntilTree(view, () => RepairButton(view, "修复气泡区域") != null, "重开面板后修复按钮未出现");
        Set(view, "selectedModel", "");
        Click(RepairButton(view, "修复气泡区域")!);
        await UntilTree(view, () => PanelTexts(view).Any(t => t == "请先选择一个支持参考图编辑的图片模型"), "缺模型时未给出 web 同款错误文案");
        Require(fake.RepairWrites.Count == 1, "缺模型时仍提交了修复请求");
        Set(view, "selectedModel", "model-a");
        InvokeRender(view);

        // 分辨率未知（候选既不是已暂选也不在当前批次）：不提交，报 web 同款错误而非回退 1K。
        Set(view, "workbench", Element(Fixture.EmptyWorkbench));
        InvokeRender(view);
        var unknownResolution = RepairButton(view, "修复单格");
        Require(unknownResolution != null, "空工作台下修复按钮丢失（结果面板应独立渲染）");
        Click(unknownResolution!);
        await UntilTree(view, () => PanelTexts(view).Any(t => t == "候选分辨率未知，请刷新后重试"), "分辨率未知时未给出 web 同款错误文案");
        Require(fake.RepairWrites.Count == 1, "分辨率未知时仍提交了修复请求");
        Set(view, "workbench", Element(Fixture.DefaultWorkbench));
        InvokeRender(view);

        // 其余维度的修复范围映射：PROP → PANEL、CONTINUITY → PAGE，regions 原样透传。
        view.OpenInspectionPanel("cand-1");
        await UntilTree(view, () => RepairButton(view, "修复单格") != null, "道具修复按钮未出现");
        Click(RepairButton(view, "修复单格")!);
        await Until(() => fake.RepairWrites.Count == 2, "道具修复请求未提交");
        view.OpenInspectionPanel("cand-1");
        await UntilTree(view, () => RepairButton(view, "修复整页") != null, "连续性修复按钮未出现");
        Click(RepairButton(view, "修复整页")!);
        await Until(() => fake.RepairWrites.Count == 3, "连续性修复请求未提交");
        var prop = fake.RepairWrites[1].Body;
        Require(prop.Text("inspection_result_id") == "ins-prop" && prop.Text("repair_type") == "PANEL"
            && prop.Array("target_regions").Count == 0, "道具修复载荷（PANEL + 空 regions）不符");
        var continuity = fake.RepairWrites[2].Body;
        Require(continuity.Text("inspection_result_id") == "ins-continuity" && continuity.Text("repair_type") == "PAGE"
            && continuity.Array("target_regions").Count == 1 && continuity.Array("target_regions")[0].Text("scene_id") == "scene-1",
            "连续性修复载荷（PAGE + regions 透传）不符");
    }

    // ── ③ 检查任务转终态：结果与工作台/页面数据被重新拉取（含外部来源）──
    private static async Task TerminalRefreshChecks(GenerateView view, Fixture fake)
    {
        fake.CandidateInspections["cand-1"] = "[]";
        fake.JobsJson = """[{"id":"job-ext","job_type":"PAGE_INSPECT","target_id":"cand-1","status":"QUEUED","progress":35}]""";
        view.OpenInspectionPanel("cand-1");
        var inspections0 = fake.InspectionsReads.TryGetValue("cand-1", out var seen) ? seen : 0;
        await CallWatch(view);
        Require(Field<JsonElement>(view, "reviewInspectJob").Text("id") == "job-ext",
            "面板未按 target_id 跟踪外部来源（其他客户端提交）的检查任务");
        Require(Field<bool>(view, "reviewChecking"), "进行中的检查任务未被标记为 checking");
        await Until(() => fake.InspectionsReads.TryGetValue("cand-1", out var now) && now > inspections0,
            "检查进行中未持续重读检查结果（web 的 2500ms 轮询语义）");

        // 外部任务转终态：结果、工作台与页面（门禁/页栏）都要补拉。
        fake.JobsJson = """[{"id":"job-ext","job_type":"PAGE_INSPECT","target_id":"cand-1","status":"COMPLETED","progress":100}]""";
        fake.CandidateInspections["cand-1"] = Fixture.RichInspections;
        var workbench0 = fake.WorkbenchReads;
        var pages0 = fake.PagesReads;
        await CallWatch(view);
        await UntilTree(view, () => PanelTexts(view).Any(t => t == "说话人 · 不匹配 · 42%"), "终态后检查结果未被重新拉取并渲染");
        await Until(() => fake.WorkbenchReads > workbench0 && fake.PagesReads > pages0,
            "终态后工作台/页面数据未被重新拉取");
        Require(!Field<bool>(view, "reviewChecking"), "终态后 checking 标志未复位");

        // 本会话路径：候选卡「视觉检查」→ 打开面板并提交检查任务。
        var inspect0 = fake.InspectPosts;
        Click(Descendants(view).OfType<Button>().Single(b => (string?)b.Content == "视觉检查"));
        await Until(() => fake.InspectPosts == inspect0 + 1, "候选卡的视觉检查按钮未提交检查任务");
        await Until(() => Field<string?>(view, "reviewCandidateId") == "cand-1", "视觉检查后检查面板未打开");
        Require(Field<HashSet<string>>(view, "trackedInspectJobs").Contains("job-sess"),
            "会话提交的检查任务未进入看护集合");
        var workbench1 = fake.WorkbenchReads;
        var pages1 = fake.PagesReads;
        var inspections1 = fake.InspectionsReads.TryGetValue("cand-1", out var read1) ? read1 : 0;
        fake.JobsJson = """[{"id":"job-sess","job_type":"PAGE_INSPECT","target_id":"cand-1","status":"COMPLETED","progress":100}]""";
        await CallWatch(view);
        await Until(() => fake.InspectionsReads.TryGetValue("cand-1", out var now) && now > inspections1,
            "会话任务终态后检查结果未被重新拉取");
        await Until(() => fake.WorkbenchReads > workbench1 && fake.PagesReads > pages1,
            "会话任务终态后工作台/页面未被刷新（G-4 集合）");
        Require(Field<string?>(view, "reviewCandidateId") == "cand-1", "终态刷新不应收起仍指向当前候选的检查面板");
    }

    // ── ④ 空结果 / 加载失败 / 迟到响应的守卫 ──
    private static async Task GuardChecks(GenerateView view, Fixture fake)
    {
        // 空结果：等待行文案，无 issue 行、无修复按钮。
        fake.CandidateInspections["cand-empty"] = "[]";
        view.OpenInspectionPanel("cand-empty");
        await UntilTree(view, () => PanelTexts(view).Any(t => t == "正在读取检查结果"), "空结果的等待行未出现");
        Require(!PanelTexts(view).Any(t => t.Contains("不匹配")), "空结果渲染出了 issue 行");
        Require(RepairButton(view, "修复气泡区域") == null, "空结果不应出现修复按钮");

        // 加载失败：面板给出失败文案，视图不崩。
        fake.FailInspections = true;
        view.OpenInspectionPanel("cand-1");
        await UntilTree(view, () => PanelTexts(view).Any(t => t.StartsWith("检查结果读取失败：", StringComparison.Ordinal)),
            "检查结果读取失败未在等待行给出可见错误");
        fake.FailInspections = false;

        // 迟到响应 A：面板已关闭 → 迟到的结果不得写入/重画。
        var hold = fake.PendingInspections = Held();
        view.OpenInspectionPanel("cand-1");      // 停在挂起响应上
        await UntilTree(view, () => PanelRoot(view) != null, "挂起响应时面板应保持等待行");
        Click(CloseButton(view)!);
        Require(PanelRoot(view) == null, "关闭后面板未收起");
        hold.SetResult(Json(Fixture.RichInspections));
        await Settle();
        await Settle();
        Layout(view, 1200, 1600);
        Require(PanelRoot(view) == null, "迟到响应把已关闭的面板画了回来");
        Require(Field<List<JsonElement>>(view, "reviewInspections").Count == 0, "迟到响应写入了已关闭面板的数据");

        // 迟到响应 B：切换候选后旧响应到达 → 不得覆盖新候选的结果。
        hold = fake.PendingInspections = Held();
        view.OpenInspectionPanel("cand-1");
        view.OpenInspectionPanel("cand-2");      // 新候选立即返回并接管面板
        await UntilTree(view, () => PanelTexts(view).Any(t => t == "连续性 · 通过 · 88%"), "新候选的检查结果未渲染");
        hold.SetResult(Json(Fixture.RichInspections));
        await Settle();
        await Settle();
        Layout(view, 1200, 1600);
        Require(!PanelTexts(view).Any(t => t == "说话人 · 不匹配 · 42%"), "迟到响应覆盖了新候选的检查结果");
        Require(Field<List<JsonElement>>(view, "reviewInspections").Count == 1, "迟到响应污染了新候选的数据源");
        Click(CloseButton(view)!);
        Require(PanelRoot(view) == null, "收尾关闭失败");
    }

    // ── 基础设施：可视树、反射与断言（对齐 NativeAssetsLoopChecks 的做法）──
    private static Task Settle() => Task.Delay(30);

    private static async Task Until(Func<bool> condition, string message)
    {
        var deadline = DateTime.UtcNow + TimeSpan.FromSeconds(4);
        while (!condition())
        {
            if (DateTime.UtcNow > deadline) throw new Exception("超时：" + message);
            await Settle();
        }
    }

    /// <summary>条件依赖可视树（按钮/文案）时每拍先 Layout，让 VisualTreeHelper 看到新渲染的面板。</summary>
    private static async Task UntilTree(GenerateView view, Func<bool> condition, string message)
    {
        var deadline = DateTime.UtcNow + TimeSpan.FromSeconds(4);
        while (true)
        {
            Layout(view, 1200, 1600);
            if (condition()) return;
            if (DateTime.UtcNow > deadline) throw new Exception("超时：" + message);
            await Settle();
        }
    }

    private static IEnumerable<DependencyObject> Descendants(DependencyObject root)
    {
        var count = VisualTreeHelper.GetChildrenCount(root);
        for (var i = 0; i < count; i++)
        {
            var child = VisualTreeHelper.GetChild(root, i);
            yield return child;
            foreach (var nested in Descendants(child)) yield return nested;
        }
    }

    private static Border? PanelRoot(GenerateView view) =>
        Descendants(view).OfType<Border>()
            .FirstOrDefault(b => System.Windows.Automation.AutomationProperties.GetName(b) == "候选视觉检查面板");

    private static List<string> PanelTexts(GenerateView view) =>
        PanelRoot(view) is { } panel
            ? Descendants(panel).OfType<TextBlock>().Select(t => t.Text).ToList()
            : [];

    private static Button? RepairButton(GenerateView view, string label) =>
        PanelRoot(view) is { } panel
            ? Descendants(panel).OfType<Button>().FirstOrDefault(b => (string?)b.Content == label)
            : null;

    private static Button? CloseButton(GenerateView view) =>
        PanelRoot(view) is { } panel
            ? Descendants(panel).OfType<Button>().FirstOrDefault(b => (string?)b.Content == "关闭")
            : null;

    private static void Click(ButtonBase button) => button.RaiseEvent(new RoutedEventArgs(ButtonBase.ClickEvent));

    private static void Layout(FrameworkElement view, int width, int height)
    {
        view.Measure(new Size(width, height));
        view.Arrange(new Rect(0, 0, width, height));
        view.UpdateLayout();
    }

    private static JsonElement Element(string text) => JsonDocument.Parse(text).RootElement.Clone();

    private static T Field<T>(object target, string name) => (T)target.GetType().GetField(name, BindingFlags.Instance | BindingFlags.NonPublic)!.GetValue(target)!;

    private static void Set(object target, string name, object value) => target.GetType().GetField(name, BindingFlags.Instance | BindingFlags.NonPublic)!.SetValue(target, value);

    // 按空参数列表显式消歧：WPF 基类层级存在同名 internal 成员，仅按名字+
    // BindingFlags 解析会抛 AmbiguousMatchException。
    private static void InvokeRender(GenerateView view) =>
        view.GetType().GetMethod("Render", BindingFlags.Instance | BindingFlags.NonPublic, binder: null, types: Type.EmptyTypes, modifiers: null)!.Invoke(view, null);

    private static async Task CallWatch(GenerateView view) =>
        await (Task)view.GetType().GetMethod("WatchInspectJobsAsync", BindingFlags.Instance | BindingFlags.NonPublic, binder: null, types: Type.EmptyTypes, modifiers: null)!.Invoke(view, null)!;

    private static void Require(bool condition, string message) { if (!condition) throw new Exception(message); }

    private static TaskCompletionSource<HttpResponseMessage> Held() =>
        new(TaskCreationOptions.RunContinuationsAsynchronously);

    private static HttpResponseMessage Json(string text) => new(HttpStatusCode.OK) { Content = new StringContent(text) };

    private sealed class Fixture : HttpMessageHandler
    {
        private const string Chapters = """[{"id":"ch1","ordinal":1,"title":"第一章"}]""";
        private const string Models = """
          [{"model_type":"IMAGE","enabled":true,"operations":["image_edit"],"logical_alias":"model-a","display_name":"测试图像模型","provider":"测试","model_id":"m-1"}]
          """;
        private const string Pages = """[{"id":"page-1","page_number":1,"storyboard_version":3,"status":"PAGES_PLANNED"}]""";
        private const string Batches = """[{"id":"batch-1","ordinal":1,"status":"OPEN"}]""";
        private const string Candidates = """
          [{"id":"cand-1","ordinal":1,"status":"READY","resolution":"2K","asset_id":"asset-1",
            "content_url":"/api/v1/assets/asset-1/content","is_favorite":false,"is_selected":false,
            "version_state":"CURRENT","model_alias":"model-a"}]
          """;
        public const string DefaultWorkbench = """
          {"page":{"id":"page-1","page_number":1,"storyboard_version":3,"status":"PAGES_PLANNED"},
           "storyboard":{"panels":[]},
           "readiness":{"ready":true,"blockers":[]},
           "production":{"ready":false,"state":"NEEDS_REPAIR","blockers":[{"code":"QUALITY_REVIEW_REQUIRED","message":"存在未通过的视觉检查"}]},
           "current_batch":{"id":"batch-1","ordinal":1,"status":"OPEN"},
           "candidates":[{"id":"cand-1","ordinal":1,"status":"READY","resolution":"2K","asset_id":"asset-1",
            "content_url":"/api/v1/assets/asset-1/content","is_favorite":false,"is_selected":false,
            "version_state":"CURRENT","model_alias":"model-a"}],
           "selected_candidate":null}
          """;
        public const string EmptyWorkbench = """
          {"page":{"id":"page-1","page_number":1,"storyboard_version":3},
           "storyboard":{"panels":[]},"readiness":{"ready":true},"production":{"ready":false},
           "current_batch":null,"candidates":[],"selected_candidate":null}
          """;
        // InspectionRead 形状（apps/api schemas.InspectionRead），created_at 倒序：
        // V2 的 CONTINUITY 是列表第一条——不经版本过滤就会在维度去重时胜出。
        public const string RichInspections = """
          [
           {"id":"ins-continuity-stale","candidate_id":"cand-1","storyboard_version":2,"category":"CONTINUITY","outcome":"MISSING","score":0.1,"details":{"expected":"旧版","observed":"旧图"},"regions":[],"severity":"HIGH","created_at":"2026-09-10T10:00:07Z"},
           {"id":"ins-speaker-2","candidate_id":"cand-1","storyboard_version":3,"category":"SPEAKER","outcome":"MISMATCH","score":0.42,"details":{"expected":"小满","observed":"陌生男人"},"regions":[{"panel_id":"panel-1","bbox":[10,20,110,220]}],"severity":"MEDIUM","created_at":"2026-09-10T10:00:06Z"},
           {"id":"ins-text","candidate_id":"cand-1","storyboard_version":3,"category":"TEXT","outcome":"MISMATCH","score":null,"details":{"expected":"我们回家吧","observed":"我门回家吧","bubble_diffs":[{"balloon_index":2,"target_text":"我们回家吧","recognized_text":"我门回家吧","similarity":0.5},{"target_text":"明天见","recognized_text":"大天见"}]},"regions":[],"severity":"HIGH","created_at":"2026-09-10T10:00:05Z"},
           {"id":"ins-character","candidate_id":"cand-1","storyboard_version":3,"category":"CHARACTER","outcome":"PASS","score":0.97,"details":{"备注":"角色特征一致"},"regions":[],"severity":"INFO","created_at":"2026-09-10T10:00:04Z"},
           {"id":"ins-prop","candidate_id":"cand-1","storyboard_version":3,"category":"PROP","outcome":"MISSING","score":0.31,"details":{"expected":"红色书包","observed":"没有书包"},"regions":[],"severity":"MEDIUM","created_at":"2026-09-10T10:00:03Z"},
           {"id":"ins-continuity","candidate_id":"cand-1","storyboard_version":3,"category":"CONTINUITY","outcome":"MISMATCH","score":0.55,"details":{"expected":"雨天","observed":"晴天"},"regions":[{"scene_id":"scene-1"}],"severity":"MEDIUM","created_at":"2026-09-10T10:00:02Z"},
           {"id":"ins-speaker-1","candidate_id":"cand-1","storyboard_version":3,"category":"SPEAKER","outcome":"PASS","score":0.99,"details":{},"regions":[],"severity":"INFO","created_at":"2026-09-10T10:00:01Z"}
          ]
          """;
        private const string SecondInspections = """
          [{"id":"ins-2","candidate_id":"cand-2","storyboard_version":3,"category":"CONTINUITY","outcome":"PASS","score":0.88,"details":{"expected":"夜晚","observed":"夜晚"},"regions":[],"severity":"INFO","created_at":"2026-09-10T11:00:00Z"}]
          """;

        public readonly Dictionary<string, string> CandidateInspections = new()
        {
            ["cand-1"] = RichInspections,
            ["cand-2"] = SecondInspections,
        };
        public readonly List<(string Method, string Path, JsonElement Body)> Writes = [];
        public readonly Dictionary<string, int> InspectionsReads = new();
        public List<(string Method, string Path, JsonElement Body)> RepairWrites =>
            Writes.Where(write => write.Path.EndsWith("/repairs", StringComparison.Ordinal)).ToList();
        public TaskCompletionSource<HttpResponseMessage>? PendingInspections, PendingRepair;
        public string JobsJson = "[]";
        public string InspectStatus = "GENERATING";
        public bool FailInspections;
        public int WorkbenchReads, PagesReads, InspectPosts;

        protected override async Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken token)
        {
            var path = request.RequestUri!.AbsolutePath;
            var query = request.RequestUri.Query;
            if (request.Method != HttpMethod.Get)
            {
                var body = request.Content == null ? "{}" : await request.Content.ReadAsStringAsync(token);
                var parsed = JsonSerializer.Deserialize<JsonElement>(body);
                Writes.Add((request.Method.Method, path, parsed));
                if (path.EndsWith("/inspect", StringComparison.Ordinal))
                {
                    InspectPosts++;
                    return Json($"{{\"id\":\"job-sess\",\"job_type\":\"PAGE_INSPECT\",\"target_id\":\"{path.Split('/')[^2]}\",\"status\":\"{InspectStatus}\",\"progress\":10}}");
                }
                if (path.EndsWith("/repairs", StringComparison.Ordinal))
                {
                    if (PendingRepair is { } repair) { PendingRepair = null; return await repair.Task; }
                    return Json("""{"job_id":"job-r","job_status":"QUEUED","candidate":{"id":"cand-r","status":"QUEUED","resolution":"2K"}}""");
                }
                return Json("{}");
            }
            if (path.EndsWith("/models")) return Json(Models);
            if (path.EndsWith("/chapters")) return Json(Chapters);
            if (path.EndsWith("/characters") || path.EndsWith("/outfits")) return Json("[]");
            if (path.EndsWith("/character-packages")) return Json("[]");
            if (path.EndsWith("/pages")) { PagesReads++; return Json(Pages); }
            if (path.EndsWith("/generation-workbench")) { WorkbenchReads++; return Json(DefaultWorkbench); }
            if (path.EndsWith("/jobs") && query.Contains("archived")) return Json(JobsJson);
            if (path.EndsWith("/batches")) return Json(Batches);
            if (path.EndsWith("/candidates")) return Json(Candidates);
            if (path.EndsWith("/inspections"))
            {
                var candidate = path.Split('/')[^2];
                InspectionsReads[candidate] = InspectionsReads.TryGetValue(candidate, out var count) ? count + 1 : 1;
                if (FailInspections)
                    return new HttpResponseMessage(HttpStatusCode.InternalServerError) { Content = new StringContent("{\"detail\":\"数据库暂时不可用\"}") };
                if (candidate == "cand-1" && PendingInspections is { } hold) { PendingInspections = null; return await hold.Task; }
                return Json(CandidateInspections.TryGetValue(candidate, out var rows) ? rows : "[]");
            }
            return Json("[]");
        }
    }
}
