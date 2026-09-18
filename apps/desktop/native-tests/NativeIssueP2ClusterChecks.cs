using System.Net;
using System.Net.Http;
using System.IO;
using System.Reflection;
using System.Text.Json;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Controls.Primitives;
using MangaFlow.Native;
using MangaFlow.Native.Services;
using MangaFlow.Native.Views;

// #847 / #810 / #808（P2 集群）回归：
// ① #847-1 项目切换清理导演台草稿状态：视图在缓存里跨项目复用，DirectorDraftActive
//    以 directorPane 的草稿为准——上一项目残留的草稿把渲染门/刷新/轮询/离开确认
//    全部挡死（工作台永久陈旧 + 幽灵离开提示 + accept 后冻结）。
// ② #847-2 accept/reject 清空预览草稿：旧实现把应答组（同为 JSON 对象）赋回
//    previewGroup，HasDraft 永真，ReloadWorkbench 撞渲染门被拦——工作台冻结。
// ③ #810-2 恢复版本前先排干保存链：排队中的恢复前 PATCH 若在恢复 POST 的
//    await 窗口里执行，会用恢复前的画布快照覆盖刚恢复的草稿。
// ④ #808-3 保真激活的防抖冲刷不得弹确认框：外层确认刚跑过、留守决定已做，
//    再弹「保存失败/放弃」既重复、答案还被丢弃（弃稿后画布留旧草稿且防抖未武装）。
// ⑤ #808-1 设置页保真激活不得整页重载：拒绝弃稿后 LoadAllAsync/LoadAsync 会把
//    表单静默清空（ProjectSettingsView.LoadAsync 首行 body.Children.Clear()）。
//
// 【注册】NativeInteractionChecks.RunIsolated 的 await 链已追加
// `NativeIssueP2ClusterChecks.Run();`——Run 内部自带双模式 STA 调度。
internal static class NativeIssueP2ClusterChecks
{
    private const BindingFlags All = BindingFlags.Instance | BindingFlags.NonPublic | BindingFlags.Public;

    public static void Run()
    {
        if (Application.Current is null) RunOnDedicatedStaThread();
        else RunFrame();
    }

    private static void RunOnDedicatedStaThread()
    {
        Exception? failure = null;
        var thread = new Thread(() =>
        {
            try
            {
                var app = new Application { ShutdownMode = ShutdownMode.OnExplicitShutdown };
                app.Resources.MergedDictionaries.Add(new ResourceDictionary { Source = new Uri("/MangaFlow.Native;component/Theme.xaml", UriKind.Relative) });
                RunFrame();
            }
            catch (Exception error) { failure = error; }
        });
        thread.SetApartmentState(ApartmentState.STA);
        thread.Start();
        thread.Join();
        if (failure != null) throw new Exception("NativeIssueP2ClusterChecks failed", failure);
    }

    private static void RunFrame()
    {
        var store = Path.Combine(Path.GetTempPath(), "mangaflow-p2cluster-store-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(store);
        var prefs = Path.Combine(store, "prefs.json");
        File.WriteAllText(prefs, "{}");
        var previousStore = (string)typeof(KeyValueStore).GetField("path", BindingFlags.Static | BindingFlags.NonPublic)!.GetValue(null)!;
        KeyValueStore.UseLocation(prefs);
        try { RunIsolatedFrame(); }
        finally
        {
            KeyValueStore.UseLocation(previousStore);
            try { Directory.Delete(store, true); } catch (IOException) { } catch (UnauthorizedAccessException) { }
        }
    }

    private static void RunIsolatedFrame()
    {
        Exception? failure = null;
        var frame = new System.Windows.Threading.DispatcherFrame();
        System.Windows.Threading.Dispatcher.CurrentDispatcher.UnhandledException += (_, e) =>
        {
            e.Handled = true;
            failure ??= new Exception("Dispatcher unhandled: " + e.Exception.Message, e.Exception);
            frame.Continue = false;
        };
        var timeout = new System.Windows.Threading.DispatcherTimer { Interval = TimeSpan.FromSeconds(90) };
        timeout.Tick += (_, _) => { failure ??= new TimeoutException("NativeIssueP2ClusterChecks timed out"); frame.Continue = false; };
        timeout.Start();
        System.Threading.SynchronizationContext.SetSynchronizationContext(
            new System.Windows.Threading.DispatcherSynchronizationContext());
        System.Windows.Threading.Dispatcher.CurrentDispatcher.BeginInvoke(new Action(async () =>
        {
            try
            {
                await ProjectSwitchClearsDirectorDraft();
                Console.WriteLine("PASS: #847 项目切换清理导演台草稿——渲染门不再挡新项目、无幽灵离开提示");
                await DirectorAcceptClearsDraft();
                Console.WriteLine("PASS: #847 accept 后预览草稿清空——重载不再被 HasDraft 冻结");
                await RestoreDrainsSaveChain();
                Console.WriteLine("PASS: #810 恢复版本前排干保存链——恢复 POST 晚于挂起 PATCH 完成");
                await PreserveActivationFlushesSilently();
                Console.WriteLine("PASS: #808 保真激活的防抖冲刷无提示——失败不弹框、防抖重臂");
                await SettingsPreserveSkipsReload();
                Console.WriteLine("PASS: #808 设置页保真激活不整页重载——表单草稿保留、零读取");
            }
            catch (Exception error) { failure = error; }
            finally { frame.Continue = false; }
        }));
        System.Windows.Threading.Dispatcher.PushFrame(frame);
        timeout.Stop();
        if (failure != null) throw new Exception("NativeIssueP2ClusterChecks failed", failure);
    }

    // ── ① #847-1：项目切换清理导演台草稿 ──
    // 失败构造（原始缺陷）：视图缓存跨项目复用而 Activate 从不清理 directorPane——
    // 「DirectorDraftActive 为假」与「离开确认未被咨询」两条断言直接失败。
    private static async Task ProjectSwitchClearsDirectorDraft()
    {
        var fixture = new ProjectSwitchFixture();
        using var api = new ApiClient("http://127.0.0.1:12345", fixture);
        var view = new GenerateView();
        view.Activate(Context(api, "p847a"));
        try
        {
            await Until(() => fixture.WorkbenchReads("p847a") >= 1);
            await Settle();
            typeof(GenerateView).GetMethod("SwitchMode", All)!.Invoke(view, new object?[] { true });
            CommandInput(Field<DirectorPane>(view, "directorPane")!).Text = "项目 A 的未提交导演指令";
            Require(Field<DirectorPane>(view, "directorPane")!.HasDraft, "前置失败：导演台草稿构造未成立");

            // 同一视图实例切到项目 B。
            view.Activate(Context(api, "p847b"));
            await Until(() => fixture.WorkbenchReads("p847b") >= 1);
            await Settle();

            // DirectorDraftActive 是私有问题判定：直接反射断言（提示缝设计上
            // 无条件优先于草稿判定，不能用缝计数代替状态判定）。
            var draftActive = (bool)typeof(GenerateView).GetProperty("DirectorDraftActive", All)!.GetValue(view)!;
            Require(!draftActive,
                "项目切换后 DirectorDraftActive 必须为假（渲染门/离开确认不再拿上一项目的草稿守新项目，#847）");
            var reads = fixture.WorkbenchReads("p847b");
            await view.RefreshAsync();
            await Until(() => fixture.WorkbenchReads("p847b") > reads);
        }
        finally { view.Deactivate(); }
    }

    // ── ② #847-2：accept 清空预览草稿 ──
    // 失败构造（原始缺陷）：JournalAsync 把应答组赋回 previewGroup（对象 → HasDraft
    // 永真）——「HasDraft 为假」与「重载确实发生」断言失败，工作台冻结。
    private static async Task DirectorAcceptClearsDraft()
    {
        var fixture = new DirectorAcceptFixture();
        using var api = new ApiClient("http://127.0.0.1:12345", fixture);
        var view = new GenerateView();
        view.Activate(Context(api, "p847a"));
        try
        {
            await Until(() => fixture.WorkbenchReads >= 1);
            await Settle();
            typeof(GenerateView).GetMethod("SwitchMode", All)!.Invoke(view, new object?[] { true });
            var pane = Field<DirectorPane>(view, "directorPane")!;
            CommandInput(pane).Text = "待执行的导演指令";
            // 预览已生成（previewGroup 是对象）——accept 的前置状态。
            Set(pane, "previewGroup", JsonDocument.Parse(
                """{"command_group_id":"grp-1","commands":[{"command_id":"cmd-1"}]}""").RootElement.Clone());
            Require(pane.HasDraft, "前置失败：预览草稿构造未成立");

            var reads = fixture.WorkbenchReads;
            await (Task)typeof(DirectorPane).GetMethod("JournalAsync", All)!.Invoke(pane, new object?[] { "accept" })!;
            await Until(() => fixture.Accepts >= 1);
            await Until(() => fixture.WorkbenchReads > reads);

            Require(!pane.HasDraft, "accept 后预览草稿必须清空（HasDraft 为假，#847）");
            Require(CommandInput(pane).Text.Length == 0, "accept 后指令输入必须清空（#847）");
        }
        finally { view.Deactivate(); }
    }

    // ── ③ #810-2：恢复前排干保存链 ──
    // 失败构造（原始缺陷）：RestoreVersionAsync 不等 saveChain 就发恢复 POST——门控
    // 中的 PATCH 未完成时「恢复 POST 已发出」为真，排序断言失败（迟到 PATCH 覆盖
    // 恢复后草稿的窗口敞开）。
    private static async Task RestoreDrainsSaveChain()
    {
        var workflowJson =
            """{"id":"wf-p2","name":"流程","version":5,"draft_version":2,"draft_graph":{"schema_version":2,"nodes":[{"id":"draft-node","type":"agent.parse","name":"草稿节点","position":{"x":10,"y":20},"inputs":[],"outputs":[],"config":{"model_alias":"auto","temperature":0.2,"notes":""}}],"edges":[]}}""";
        var order = new List<string>();
        var patchGate = new TaskCompletionSource<HttpResponseMessage>(TaskCreationOptions.RunContinuationsAsynchronously);
        using var api = new ApiClient("http://127.0.0.1:12345", new SequenceHandler(request =>
        {
            var path = request.RequestUri!.AbsolutePath;
            if (request.Method == HttpMethod.Patch && path.EndsWith("/workflows/wf-p2"))
                return GatedAsync("patch");
            if (request.Method == HttpMethod.Post && path.EndsWith("/workflow-versions/ver-p2/restore"))
            {
                lock (order) order.Add("restore");
                return Task.FromResult(Response("""{"id":"wf-p2","version":6,"draft_version":3,"draft_graph":{"schema_version":2,"nodes":[],"edges":[]}}"""));
            }
            if (path.EndsWith("/workflow-node-types")) return Task.FromResult(Response(
                """[{"type":"agent.parse","label":"解析","display_name":"解析","category":"AGENT","description":"","inputs":[],"outputs":[]}]"""));
            if (path.EndsWith("/projects/p1/workflows"))
                return Task.FromResult(Response("""[{"id":"wf-p2","name":"流程"}]"""));
            if (path.EndsWith("/projects/p1/chapters") || path.EndsWith("/models") || path.EndsWith("/runs"))
                return Task.FromResult(Response("[]"));
            if (path.EndsWith("/workflows/wf-p2/versions"))
                return Task.FromResult(Response("""[{"id":"ver-p2","workflow_id":"wf-p2","revision":3,"published_at":"2026-08-27T00:00:00Z"}]"""));
            if (request.Method == HttpMethod.Get && path.EndsWith("/workflows/wf-p2"))
                return Task.FromResult(Response(workflowJson));
            throw new Exception("Unexpected drain-check request: " + request.RequestUri);

            async Task<HttpResponseMessage> GatedAsync(string mark)
            {
                lock (order) order.Add(mark + ":start");
                var response = await patchGate.Task;
                lock (order) order.Add(mark + ":done");
                return new HttpResponseMessage(HttpStatusCode.OK)
                { Content = new StringContent("""{"id":"wf-p2","version":6,"draft_version":3,"draft_graph":{"nodes":[],"edges":[]}}""") };
            }
        }));
        var view = new WorkflowView();
        try
        {
            view.RestoreConfirmOverride = _ => Task.FromResult(true);
            view.Activate(new WorkspaceContext
            {
                Api = api, State = new WorkspaceState(), Window = null!,
                Project = new ProjectItem("p1", "排干检查", "", 0, 0),
                NavigateSection = (_, _) => Task.CompletedTask, OpenDashboard = () => Task.CompletedTask,
            });
            await Until(() => NodeNames(view).Contains("草稿节点")
                && Field<StackPanel>(view, "versionList").Children.OfType<Button>().Any(button => Name(button) == "V3"));

            // 构造挂起的保存：直接入队一次 SaveNow（走 saveChain），PATCH 被门控挂住。
            var saveTask = (Task<bool>)typeof(WorkflowView).GetMethod("SaveNowAsync", All)!.Invoke(view, new object?[] { null })!;
            await Until(() => { lock (order) return order.Contains("patch:start"); });

            Field<StackPanel>(view, "versionList").Children.OfType<Button>().Single(button => Name(button) == "V3")
                .RaiseEvent(new RoutedEventArgs(ButtonBase.ClickEvent));
            await Settle(300);
            lock (order) Require(!order.Contains("restore"),
                "挂起的 PATCH 未完成前恢复 POST 不得发出（#810：迟到 PATCH 会覆盖恢复后的草稿）");

            patchGate.TrySetResult(null!);
            await saveTask;
            await Until(() => { lock (order) return order.Contains("restore"); });
            lock (order)
            {
                var patchDone = order.IndexOf("patch:done");
                var restoreAt = order.IndexOf("restore");
                Require(patchDone >= 0 && restoreAt > patchDone,
                    $"恢复 POST 必须晚于挂起 PATCH 完成（实际顺序：{string.Join(" → ", order)}）");
            }
            view.Deactivate();
        }
        finally
        {
            patchGate.TrySetResult(new HttpResponseMessage(HttpStatusCode.OK) { Content = new StringContent("{}") });
            StopAutosave(view);
        }
    }

    // ── ④ #808-3：保真激活的冲刷无提示 ──
    // 失败构造（原始缺陷）：保真入口 await ConfirmLeaveAsync() 且丢弃结果——保存
    // 失败时 SaveFailLeaveOverride 被咨询（重复提示），弃稿答案被忽略。
    private static async Task PreserveActivationFlushesSilently()
    {
        var workflowJson =
            """{"id":"wf-pv","name":"流程","version":2,"draft_version":1,"draft_graph":{"schema_version":2,"nodes":[{"id":"draft-node","type":"agent.parse","name":"草稿节点","position":{"x":10,"y":20},"inputs":[],"outputs":[],"config":{"model_alias":"auto","temperature":0.2,"notes":""}}],"edges":[]}}""";
        var patches = 0;
        using var api = new ApiClient("http://127.0.0.1:12345", new SequenceHandler(request =>
        {
            var path = request.RequestUri!.AbsolutePath;
            if (request.Method == HttpMethod.Patch && path.EndsWith("/workflows/wf-pv"))
            {
                patches++;
                return Task.FromResult(Response("""{"detail":"保存失败"}""", HttpStatusCode.Conflict));
            }
            if (path.EndsWith("/workflow-node-types")) return Task.FromResult(Response(
                """[{"type":"agent.parse","label":"解析","display_name":"解析","category":"AGENT","description":"","inputs":[],"outputs":[]}]"""));
            if (path.EndsWith("/projects/p1/workflows"))
                return Task.FromResult(Response("""[{"id":"wf-pv","name":"流程"}]"""));
            if (path.EndsWith("/projects/p1/chapters") || path.EndsWith("/models") || path.EndsWith("/runs"))
                return Task.FromResult(Response("[]"));
            if (path.EndsWith("/workflows/wf-pv/versions")) return Task.FromResult(Response("[]"));
            if (request.Method == HttpMethod.Get && path.EndsWith("/workflows/wf-pv"))
                return Task.FromResult(Response(workflowJson));
            throw new Exception("Unexpected preserve-check request: " + request.RequestUri);
        }));
        var view = new WorkflowView();
        try
        {
            view.Activate(new WorkspaceContext
            {
                Api = api, State = new WorkspaceState(), Window = null!,
                Project = new ProjectItem("p1", "保真检查", "", 0, 0),
                NavigateSection = (_, _) => Task.CompletedTask, OpenDashboard = () => Task.CompletedTask,
            });
            await Until(() => NodeNames(view).Contains("草稿节点"));

            Select(view, NodeById(view, "draft-node"));
            Box(view, "温度")!.Text = "0.9";
            Require(AutosaveArmed(view), "前置失败：编辑后防抖未武装");
            var prompts = 0;
            view.SaveFailLeaveOverride = _ => { prompts++; return Task.FromResult(false); };   // 留守

            typeof(WorkflowView).GetMethod("ActivatePreservingDrafts", All)!.Invoke(view, new object?[] { Context(api, "p1") });
            await Until(() => patches >= 1);           // 冲刷确实尝试过（PATCH 已发出并 409）
            await Settle(300);                         // 让「是否弹框」与重臂都收敛完
            Require(prompts == 0,
                $"保真激活的冲刷失败不得弹「保存失败」确认（外层确认刚被拒绝，实际弹 {prompts} 次，#808）");
            Require(AutosaveArmed(view), "冲刷失败后防抖必须重臂等待重试（#808）");
            view.Deactivate();
        }
        finally { StopAutosave(view); }
    }

    // ── ⑤ #808-1：设置页保真激活不整页重载 ──
    // 失败构造（原始缺陷）：保真分派落到 default → 全量 Activate → LoadAllAsync
    // 重读providers/runtime/diagnostics 并重建表单——「零读取」与「草稿保留」断言失败。
    private static async Task SettingsPreserveSkipsReload()
    {
        var fixture = new SettingsFixture();
        using var api = new ApiClient("http://127.0.0.1:12345", fixture);
        var view = new SettingsView();
        view.Activate(Context(api, "p1"));
        try
        {
            await Until(() => fixture.RuntimeReads >= 1 && fixture.ProviderReads >= 1);
            await Settle();
            var input = Descendants(view).OfType<TextBox>().FirstOrDefault();
            Require(input != null, "前置失败：运行时表单未渲染出输入框");
            input!.Text = "用户改到一半的值";

            typeof(SettingsView).GetMethod("ActivatePreservingDrafts", All)!.Invoke(view, new object?[] { Context(api, "p1") });
            await Settle(200);
            Require(fixture.RuntimeReads == 1 && fixture.ProviderReads == 1 && fixture.DiagnosticsReads <= 1,
                $"保真激活不得整页重载（实际 runtime 读取 {fixture.RuntimeReads} 次、providers {fixture.ProviderReads} 次，#808）");
            Require(Descendants(view).OfType<TextBox>().Any(box => box.Text == "用户改到一半的值"),
                "保真激活必须保留表单草稿（#808）");
        }
        finally { view.Deactivate(); }
    }

    // ── 辅助 ──
    private static WorkspaceContext Context(ApiClient api, string projectId) => new()
    {
        Api = api, State = new WorkspaceState(), Window = null!,
        Project = new ProjectItem(projectId, "P2 集群检查", "", 0, 0),
        NavigateSection = (_, _) => Task.CompletedTask, OpenDashboard = () => Task.CompletedTask,
    };

    private static T Field<T>(object target, string name) => (T)target.GetType().GetField(name, All)!.GetValue(target)!;

    private static void Set(object target, string name, object value) =>
        target.GetType().GetField(name, All)!.SetValue(target, value);

    private static TextBox CommandInput(DirectorPane pane) =>
        Descendants(pane).OfType<TextBox>().Single(box =>
            (System.Windows.Automation.AutomationProperties.GetName(box) ?? "") == "导演指令");

    private static async Task Until(Func<bool> condition) => await Until(condition, 15, "等待条件超时");

    private static async Task Until(Func<bool> condition, int seconds, string message)
    {
        using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(seconds));
        try { while (!condition()) await Task.Delay(5, timeout.Token); }
        catch (OperationCanceledException) { throw new Exception($"等待超时（{seconds}s）：{message}"); }
    }

    private static async Task Settle(int milliseconds = 50) => await Task.Delay(milliseconds);

    private static void Require(bool condition, string message)
    {
        if (!condition) throw new Exception(message);
    }

    private static IEnumerable<DependencyObject> Descendants(DependencyObject root)
    {
        yield return root;
        foreach (var child in LogicalTreeHelper.GetChildren(root).OfType<DependencyObject>())
            foreach (var nested in Descendants(child)) yield return nested;
    }

    private static string Name(DependencyObject element) =>
        System.Windows.Automation.AutomationProperties.GetName(element) ?? "";

    private static bool AutosaveArmed(WorkflowView view) =>
        typeof(WorkflowView).GetField("autosave", All)!.GetValue(view) as System.Timers.Timer is { Enabled: true };

    private static void StopAutosave(WorkflowView view)
    {
        (typeof(WorkflowView).GetField("autosave", All)!.GetValue(view) as System.Timers.Timer)?.Stop();
    }

    private static System.Collections.IList Nodes(WorkflowView view) =>
        (System.Collections.IList)typeof(WorkflowView).GetField("nodes", All)!.GetValue(view)!;

    private static IEnumerable<string> NodeNames(WorkflowView view) =>
        Nodes(view).Cast<object>().Select(node => (string)node.GetType().GetField("Name")!.GetValue(node)!);

    private static object NodeById(WorkflowView view, string id) =>
        Nodes(view).Cast<object>().Single(node => (string)node.GetType().GetProperty("Id")!.GetValue(node)! == id);

    private static void Select(WorkflowView view, object node) =>
        typeof(WorkflowView).GetMethod("Select", All)!.Invoke(view, [node]);

    private static TextBox? Box(WorkflowView view, string name) =>
        Descendants(Field<StackPanel>(view, "inspector")).OfType<TextBox>().FirstOrDefault(box => Name(box) == name);

    private static HttpResponseMessage Response(string text, HttpStatusCode status = HttpStatusCode.OK) =>
        new(status) { Content = new StringContent(text) };

    private sealed class SequenceHandler(Func<HttpRequestMessage, Task<HttpResponseMessage>> respond) : HttpMessageHandler
    {
        protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken token) => respond(request);
    }

    // 两项目（p847a/p847b）各一章一页：工作台/批次读取的 URL 不含项目段，
    // 以最近一次 chapters 读取的项目为当前项目计数。
    private sealed class ProjectSwitchFixture : HttpMessageHandler
    {
        private readonly Dictionary<string, int> workbenchReads = new();
        private string currentProject = "";

        public int WorkbenchReads(string project) => workbenchReads.TryGetValue(project, out var count) ? count : 0;

        protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken token)
        {
            var path = request.RequestUri!.AbsolutePath;
            if (request.Method == HttpMethod.Get)
            {
                foreach (var project in new[] { "p847a", "p847b" })
                {
                    if (path.EndsWith($"/projects/{project}/chapters"))
                    {
                        currentProject = project;
                        return Task.FromResult(Response("""[{"id":"ch-1","title":"第一章","ordinal":1,"page_count":1}]"""));
                    }
                    if (path.EndsWith($"/projects/{project}/characters") || path.EndsWith($"/projects/{project}/outfits"))
                        return Task.FromResult(Response("[]"));
                }
                if (path.Contains("/character-packages")) return Task.FromResult(Response("[]"));
                if (path.EndsWith("/models")) return Task.FromResult(Response("[]"));
                if (path.EndsWith("/pages/page-1/generation-workbench"))
                {
                    workbenchReads[currentProject] = workbenchReads.TryGetValue(currentProject, out var count) ? count + 1 : 1;
                    return Task.FromResult(Response(
                        """{"page":{"id":"page-1","page_number":1,"storyboard_version":4},"storyboard":{"panels":[{"characters":[],"outfits":{}}]},"readiness":{"ready":true},"current_batch":{"id":"batch"},"candidates":[]}"""));
                }
                if (path.EndsWith("/pages/page-1/batches")) return Task.FromResult(Response("[]"));
                if (path.EndsWith("/chapters/ch-1/pages"))
                    return Task.FromResult(Response(
                        """[{"id":"page-1","chapter_id":"ch-1","page_number":1,"panel_count":1,"storyboard_version":4,"status":"","selected_candidate_id":"","continuity_status":""}]"""));
                if (path.Contains("/director/command-groups")) return Task.FromResult(Response("[]"));
            }
            return Task.FromResult(Response("{}"));
        }
    }

    // 导演台 accept 流：POST director/commands/{id}/accept 计数；工作台读取计数。
    private sealed class DirectorAcceptFixture : HttpMessageHandler
    {
        public int WorkbenchReads, Accepts;

        protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken token)
        {
            var path = request.RequestUri!.AbsolutePath;
            if (request.Method == HttpMethod.Post && path.Contains("/director/commands/") && path.EndsWith("/accept"))
            {
                Accepts++;
                return Task.FromResult(Response("""{"command_group_id":"grp-1","commands":[]}"""));
            }
            if (request.Method == HttpMethod.Get)
            {
                if (path.EndsWith("/pages/page-1/generation-workbench"))
                {
                    WorkbenchReads++;
                    return Task.FromResult(Response(
                        """{"page":{"id":"page-1","page_number":1,"storyboard_version":4},"storyboard":{"panels":[{"characters":[],"outfits":{}}]},"readiness":{"ready":true},"current_batch":{"id":"batch"},"candidates":[]}"""));
                }
                if (path.EndsWith("/pages/page-1/batches")) return Task.FromResult(Response("[]"));
                if (path.EndsWith("/chapters/ch-1/pages"))
                    return Task.FromResult(Response(
                        """[{"id":"page-1","chapter_id":"ch-1","page_number":1,"panel_count":1,"storyboard_version":4,"status":"","selected_candidate_id":"","continuity_status":""}]"""));
                if (path.EndsWith("/projects/p847a/chapters"))
                    return Task.FromResult(Response("""[{"id":"ch-1","title":"第一章","ordinal":1,"page_count":1}]"""));
                if (path.EndsWith("/projects/p847a/characters") || path.EndsWith("/projects/p847a/outfits"))
                    return Task.FromResult(Response("[]"));
                if (path.Contains("/character-packages")) return Task.FromResult(Response("[]"));
                if (path.EndsWith("/models")) return Task.FromResult(Response("[]"));
                if (path.Contains("/director/command-groups")) return Task.FromResult(Response("[]"));
            }
            return Task.FromResult(Response("{}"));
        }
    }

    // 设置页三路读取（providers/runtime/diagnostics）按路计数。
    private sealed class SettingsFixture : HttpMessageHandler
    {
        public int ProviderReads, RuntimeReads, DiagnosticsReads;

        protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken token)
        {
            var path = request.RequestUri!.AbsolutePath;
            if (request.Method == HttpMethod.Get)
            {
                if (path.EndsWith("/providers")) { ProviderReads++; return Task.FromResult(Response("[]")); }
                if (path.EndsWith("/settings/runtime")) { RuntimeReads++; return Task.FromResult(Response(
                    """{"database_backend":"sqlite","queue_mode":"AUTO","job_timeout_seconds":900,"job_lease_seconds":300,"default_concurrency":2,"max_auto_repairs":2,"health_check_interval_seconds":300,"ui_poll_interval_seconds":3000,"storage_root":"D:/mangaflow/storage","upload_root":"D:/mangaflow/uploads"}""")); }
                if (path.EndsWith("/settings/diagnostics")) { DiagnosticsReads++; return Task.FromResult(Response("{}")); }
                if (path.EndsWith("/models")) return Task.FromResult(Response("[]"));
            }
            return Task.FromResult(Response("{}"));
        }
    }
}
