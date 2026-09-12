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

// #428 回归（P2）：重连（重新连接 → ConnectAsync → OpenProjectAsync 同项目分支）
// 绕过 ConfirmLeaveAsync，重新激活所有视图做全量重载，未保存的剧本表单/分镜草稿/
// 导演指令被静默清空（#341 只修了 RefreshAsync）。
// 四条确定性检查（TaskCompletionSource 门控/计数假 HTTP 夹具，零真实网络——除
// 明确注明的回环拒绝连接外）：
// ① 脏 ScriptView + 真实 MainWindow.OpenProjectAsync 同项目缝，拒绝弃稿——
//    离开确认缝被咨询恰好一次，编辑表单/输入原地存活，不发起破坏性重载，且
//    视图上下文已重绑重连换上的新 ApiClient（旧实例已 Dispose）；
// ② 同缝同意弃稿——走全量重载（新客户端上恰好一轮 script 读取，表单按用户
//    同意被替换）；
// ③ StoryboardView 保真激活入口：几何草稿/撤销栈存活，不重读服务端锚点；
// ④ GenerateView 保真激活入口：导演台草稿（HasDraft）存活，Pane 不重建。
//
// 【需 lead 注册】本文件按任务约束未接入运行入口：建议在
// NativeInteractionChecks.RunIsolated 的 await 链追加 `NativeIssue428Checks.Run();`
// ——Run 内部自带双模式 STA 调度（Application 已存在时复用调用方线程），
// 也可独立调用（无 Application 时自建 STA 线程 + Application/Theme）。
// 注册调用点必须在 STA/Dispatcher 线程上下文中（WPF 视觉树访问要求）。
internal static class NativeIssue428Checks
{
    private const BindingFlags All = BindingFlags.Instance | BindingFlags.NonPublic | BindingFlags.Public;

    public static void Run()
    {
        // Application 是 AppDomain 级单例：链内已有 Application 时必须复用调用方
        // 线程，只有独立运行才自建 STA 线程 + Application（NativeIssue427Checks.Run
        // 的同款双模式）。
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
        if (failure != null) throw new Exception("NativeIssue428Checks failed", failure);
    }

    // STA + DispatcherFrame 泵（NativeIssue427Checks 的模式）：async 续体经由
    // DispatcherSynchronizationContext 回到泵上执行。KeyValueStore 是进程级全局
    // 位置（MainWindow 构造与分镜/生成视图都会改写），整体隔离到一次性 scratch
    // 文件，收尾恢复原位置（NativeStoryboardEditChecks.Run 同款纪律）。
    private static void RunFrame()
    {
        var store = Path.Combine(Path.GetTempPath(), "mangaflow-issue428-store-" + Guid.NewGuid().ToString("N"));
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
        var timeout = new System.Windows.Threading.DispatcherTimer { Interval = TimeSpan.FromSeconds(60) };
        timeout.Tick += (_, _) => { failure ??= new TimeoutException("NativeIssue428Checks timed out"); frame.Continue = false; };
        timeout.Start();
        System.Threading.SynchronizationContext.SetSynchronizationContext(
            new System.Windows.Threading.DispatcherSynchronizationContext());
        System.Windows.Threading.Dispatcher.CurrentDispatcher.BeginInvoke(new Action(async () =>
        {
            try
            {
                await ReconnectDeclinedKeepsScriptForms();
                Console.WriteLine("PASS: #428 重连同项目缝先过离开确认——拒绝弃稿时表单/输入原地存活且零重载（上下文已换新 ApiClient）");
                await ReconnectAcceptedReloadsCleanly();
                Console.WriteLine("PASS: #428 同意弃稿后重连同项目走全量重载（新客户端恰好一轮 script 读取）");
                await StoryboardPreserveActivationKeepsDrafts();
                Console.WriteLine("PASS: #428 StoryboardView 保真激活——几何草稿/撤销栈存活，不重读服务端锚点");
                await GeneratePreserveActivationKeepsDirectorDraft();
                Console.WriteLine("PASS: #428 GenerateView 保真激活——导演台草稿存活，Pane 不重建");
            }
            catch (Exception error) { failure = error; }
            finally { frame.Continue = false; }
        }));
        System.Windows.Threading.Dispatcher.PushFrame(frame);
        timeout.Stop();
        if (failure != null) throw new Exception("NativeIssue428Checks failed", failure);
    }

    // ── ① 真实 MainWindow.OpenProjectAsync 同项目缝 + 拒绝弃稿 ──
    // 失败构造（原始缺陷）：同项目分支不经过 ConfirmLeaveAsync，直接
    // ActivateCurrentViewAsync → ScriptView.Activate 非 quiet LoadScriptAsync 先
    // body.Children.Clear() ——「确认缝被咨询」与「表单存活」两条断言同时失败。
    private static async Task ReconnectDeclinedKeepsScriptForms()
    {
        var scratch = NewScratchRoot();
        var oldFixture = new ScriptProjectFixture();
        var newFixture = new ScriptProjectFixture();
        using var oldApi = new ApiClient("http://127.0.0.1:12345", oldFixture);
        using var newApi = new ApiClient("http://127.0.0.1:12345", newFixture);
        var previousStore = KeyValueStorePath();
        var shellType = typeof(MainWindow);
        var shell = new MainWindow("", scratch);
        ScriptView view = new();
        try
        {
            var state = (WorkspaceState)shell.DataContext;
            view.Activate(Context(oldApi, "p428"));
            var body = Field<StackPanel>(view, "body");
            await Until(() => body.Children.OfType<SceneSection>().Any());
            var scene = body.Children.OfType<SceneSection>().Single();
            Click(Buttons(scene, "编辑场景").Single());
            var locationInput = Descendants(scene).OfType<TextBox>().First();
            locationInput.Text = "重连后仍在的地点";

            // 接上重连后的外壳状态：新 ApiClient 已换上、连接已恢复、当前项目与
            // 活动视图都是脏的 ScriptView（page 必须是项目页，同项目分支才会走
            // ActivateCurrentViewAsync）。
            shellType.GetField("api", All)!.SetValue(shell, newApi);
            state.Connected = true;
            var project = new ProjectItem("p428", "#428 项目", "", 0, 0);
            state.CurrentProject = project;
            shellType.GetField("page", All)!.SetValue(shell, "script");
            shellType.GetField("activeView", All)!.SetValue(shell, view);
            ((ContentControl)shellType.GetField("ContentHost", All)!.GetValue(shell)!).Content = view;

            var confirmations = 0;
            view.LeaveConfirmOverride = () => { confirmations++; return Task.FromResult(false); };   // 拒绝弃稿
            var readsBefore = oldFixture.ScriptReads;
            await (Task)shellType.GetMethod("OpenProjectAsync", All)!.Invoke(shell, new object?[] { project })!;

            Require(confirmations == 1, $"重连同项目必须恰好咨询一次离开确认缝（实际 {confirmations} 次）");
            var scenes = body.Children.OfType<SceneSection>().ToList();
            Require(scenes.Count == 1 && scenes[0].IsEditing, "拒绝弃稿后场景编辑表单必须原地存活（#428）");
            Require(Descendants(scenes[0]).OfType<TextBox>().Any(box => box.Text == "重连后仍在的地点"),
                "拒绝弃稿后表单输入必须保留");
            Require(oldFixture.ScriptReads == readsBefore && newFixture.ScriptReads == 0,
                $"拒绝弃稿后不得发起破坏性重载（旧客户端读取 {oldFixture.ScriptReads - readsBefore}，新客户端 {newFixture.ScriptReads}）");
            Require(ReferenceEquals(ContextOf(view), newApi), "保真激活必须重绑新 ApiClient（旧实例已随重连 Dispose）");
            Require(ReferenceEquals(shellType.GetField("activeView", All)!.GetValue(shell), view),
                "拒绝弃稿后活动视图不得被替换");
        }
        finally
        {
            view.Deactivate();
            CancelShellLifetime(shell);
            KeyValueStore.UseLocation(previousStore);
            DeleteScratch(scratch);
        }
    }

    // ② 同意弃稿 → 全量重载照常工作（守卫不得把重连永久挡死）。
    private static async Task ReconnectAcceptedReloadsCleanly()
    {
        var scratch = NewScratchRoot();
        var oldFixture = new ScriptProjectFixture();
        var newFixture = new ScriptProjectFixture();
        using var oldApi = new ApiClient("http://127.0.0.1:12345", oldFixture);
        using var newApi = new ApiClient("http://127.0.0.1:12345", newFixture);
        var previousStore = KeyValueStorePath();
        var shellType = typeof(MainWindow);
        var shell = new MainWindow("", scratch);
        ScriptView view = new();
        try
        {
            var state = (WorkspaceState)shell.DataContext;
            view.Activate(Context(oldApi, "p428"));
            var body = Field<StackPanel>(view, "body");
            await Until(() => body.Children.OfType<SceneSection>().Any());
            Click(Buttons(body.Children.OfType<SceneSection>().Single(), "编辑场景").Single());

            shellType.GetField("api", All)!.SetValue(shell, newApi);
            state.Connected = true;
            var project = new ProjectItem("p428", "#428 项目", "", 0, 0);
            state.CurrentProject = project;
            shellType.GetField("page", All)!.SetValue(shell, "script");
            shellType.GetField("activeView", All)!.SetValue(shell, view);
            ((ContentControl)shellType.GetField("ContentHost", All)!.GetValue(shell)!).Content = view;

            view.LeaveConfirmOverride = () => Task.FromResult(true);   // 同意弃稿
            await (Task)shellType.GetMethod("OpenProjectAsync", All)!.Invoke(shell, new object?[] { project })!;
            await Until(() => newFixture.ScriptReads >= 1);
            await Until(() => body.Children.OfType<SceneSection>().Any());
            await Until(() => body.Children.OfType<SceneSection>().All(s => !s.IsEditing));

            Require(newFixture.ScriptReads == 1,
                $"同意弃稿后应在重连的新客户端上恰好重载一轮（实际 {newFixture.ScriptReads} 轮）");
            Require(ReferenceEquals(ContextOf(view), newApi), "全量重载必须使用重连后的新 ApiClient");
        }
        finally
        {
            view.Deactivate();
            CancelShellLifetime(shell);
            KeyValueStore.UseLocation(previousStore);
            DeleteScratch(scratch);
        }
    }

    // ── ③ StoryboardView 保真激活入口（#428 的 Activate 入口镜像 1345b5d 的刷新测试）──
    // 失败构造（把 ActivatePreservingDrafts 退化为 Activate）：LoadPagesAsync →
    // SelectPageAsync（preserveDrafts:false）清掉撤销栈并把几何弹回服务端值。
    private static async Task StoryboardPreserveActivationKeepsDrafts()
    {
        var oldFixture = new BoardFixture();
        var newFixture = new BoardFixture();
        using var oldApi = new ApiClient("http://127.0.0.1:12345", oldFixture);
        using var newApi = new ApiClient("http://127.0.0.1:12345", newFixture);
        var view = new StoryboardView();
        view.Activate(Context(oldApi, "p428-board"));
        try
        {
            await Until(() => view.PanelCountForTest == 1);
            view.ResizeViaHandleForTest(0, "e", new Point(0.8, 0.35));   // 制造几何草稿
            var draft = view.PanelRectForTest(0);
            Require(draft.Width > 0.6, "几何草稿构造失败（宽度应偏离服务端 0.5）");
            Require(view.CanUndoForTest, "几何草稿必须点亮撤销栈");

            var readsBefore = oldFixture.StoryboardGets;
            view.ActivatePreservingDrafts(Context(newApi, "p428-board"));
            await Settle();

            Require(view.PanelRectForTest(0) == draft, "保真激活必须保留画布几何草稿（#428）");
            Require(view.CanUndoForTest, "保真激活必须保留撤销栈");
            Require(oldFixture.StoryboardGets == readsBefore && newFixture.StoryboardGets == 0,
                $"保真激活不得重读服务端锚点（旧客户端 {oldFixture.StoryboardGets - readsBefore}，新客户端 {newFixture.StoryboardGets}）");
            Require(ReferenceEquals(ContextOf(view), newApi), "保真激活必须重绑新 ApiClient");
        }
        finally { view.Deactivate(); }
    }

    // ── ④ GenerateView 保真激活入口：导演台草稿存活，Pane 不重建 ──
    // 失败构造（把 ActivatePreservingDrafts 退化为 Activate）：Activate →
    // LoadPagesAsync → … → Render() new 一个 DirectorPane，已输入指令消失。
    private static async Task GeneratePreserveActivationKeepsDirectorDraft()
    {
        var oldFixture = new GenerateDraftFixture();
        var newFixture = new GenerateDraftFixture();
        using var oldApi = new ApiClient("http://127.0.0.1:12345", oldFixture);
        using var newApi = new ApiClient("http://127.0.0.1:12345", newFixture);
        var view = new GenerateView();
        view.Activate(Context(oldApi, "p428-generate"));
        try
        {
            await Until(() => oldFixture.WorkbenchReads >= 1);
            await Settle();
            typeof(GenerateView).GetMethod("SwitchMode", All)!.Invoke(view, new object?[] { true });
            var paneBefore = Field<DirectorPane>(view, "directorPane")!;
            // 在 Pane 子树内查找输入框：UserControl 未布局前模板未应用，从视图根
            // 走 VisualTreeHelper 拿不到子树；Border/Panel 的直接子元素即时可遍历。
            var commandInput = Descendants(paneBefore).OfType<TextBox>().Single(box => GetName(box) == "导演指令");
            commandInput.Text = "给主角加一把伞";
            Require(paneBefore.HasDraft, "导演台草稿构造失败（HasDraft 应为真）");

            var readsBefore = oldFixture.WorkbenchReads;
            view.ActivatePreservingDrafts(Context(newApi, "p428-generate"));
            await Settle();

            var paneAfter = Field<DirectorPane>(view, "directorPane")!;
            Require(ReferenceEquals(paneAfter, paneBefore), "保真激活不得重建 DirectorPane（#428）");
            Require(paneAfter.HasDraft && commandInput.Text == "给主角加一把伞",
                "保真激活必须保留已输入的导演指令");
            Require(oldFixture.WorkbenchReads == readsBefore && newFixture.WorkbenchReads == 0,
                $"保真激活不得发起工作台重载（旧客户端 {oldFixture.WorkbenchReads - readsBefore}，新客户端 {newFixture.WorkbenchReads}）");
            Require(ReferenceEquals(ContextOf(view), newApi), "保真激活必须重绑新 ApiClient");
        }
        finally { view.Deactivate(); }
    }

    // ── 反射/断言辅助（NativeIssue427Checks 的模式）──

    private static T Field<T>(object target, string name) => (T)target.GetType().GetField(name, All)!.GetValue(target)!;

    private static ApiClient ContextOf(WorkspaceView view) =>
        ((WorkspaceContext)typeof(WorkspaceView).GetProperty("Context", BindingFlags.Instance | BindingFlags.NonPublic)!.GetValue(view)!).Api;

    private static void CancelShellLifetime(MainWindow shell) =>
        ((CancellationTokenSource)typeof(MainWindow).GetField("lifetime", All)!.GetValue(shell)!).Cancel();

    private static WorkspaceContext Context(ApiClient api, string projectId) => new()
    {
        Api = api, Cache = new ApiCache(), State = new WorkspaceState(), Window = null!,
        Project = new ProjectItem(projectId, "#428 检查", "", 0, 0),
        NavigateSection = (_, _) => Task.CompletedTask, OpenDashboard = () => Task.CompletedTask,
    };

    private static string KeyValueStorePath() =>
        (string)typeof(KeyValueStore).GetField("path", BindingFlags.Static | BindingFlags.NonPublic)!.GetValue(null)!;

    // MainWindow 的 dataRoot/prefs 必须每次唯一（KeyValueStore 是进程级全局位置，
    // 构造函数会改写它）——检查自管 GUID 目录并在收尾恢复全局位置。
    private static string NewScratchRoot()
    {
        var root = Path.Combine(Path.GetTempPath(), "mangaflow-issue428-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(root);
        return root;
    }

    private static void DeleteScratch(string root)
    {
        try { Directory.Delete(root, true); } catch (IOException) { } catch (UnauthorizedAccessException) { }
    }

    private static async Task Until(Func<bool> condition)
    {
        using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(5));
        while (!condition()) await Task.Delay(5, timeout.Token);
    }

    private static async Task Settle() => await Task.Delay(50);

    private static void Require(bool condition, string message)
    {
        if (!condition) throw new Exception(message);
    }

    private static IEnumerable<DependencyObject> Descendants(DependencyObject root)
    {
        var count = System.Windows.Media.VisualTreeHelper.GetChildrenCount(root);
        for (var i = 0; i < count; i++)
        {
            var child = System.Windows.Media.VisualTreeHelper.GetChild(root, i);
            yield return child;
            foreach (var nested in Descendants(child)) yield return nested;
        }
    }

    private static IEnumerable<Button> Buttons(DependencyObject root, string content) =>
        Descendants(root).OfType<Button>().Where(button => Equals(button.Content, content));

    private static string GetName(UIElement element) => System.Windows.Automation.AutomationProperties.GetName(element) ?? "";

    private static void Click(ButtonBase button) => button.RaiseEvent(new RoutedEventArgs(ButtonBase.ClickEvent));

    // #428 剧本夹具：一章一场景一情节拍；只计数 script 读取（「拒绝弃稿 → 零读取」
    // 即保真守卫的直接判据）。项目号 p428 由调用方传入 Context，端点按项目过滤。
    private sealed class ScriptProjectFixture : HttpMessageHandler
    {
        public int ScriptReads;

        protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken token)
        {
            var path = request.RequestUri!.AbsolutePath;
            if (request.Method == HttpMethod.Get)
            {
                if (path.EndsWith("/chapters/ch-1/script")) { ScriptReads++; return Task.FromResult(Json(Script)); }
                if (path.EndsWith("/projects/p428/chapters"))
                    return Task.FromResult(Json("""[{"id":"ch-1","title":"第一章","ordinal":1,"page_count":1}]"""));
                if (path.EndsWith("/projects/p428/characters")) return Task.FromResult(Json("[]"));
                if (path.EndsWith("/projects/p428/outfits")) return Task.FromResult(Json("[]"));
                if (path.EndsWith("/scene-assets")) return Task.FromResult(Json("[]"));
            }
            return Task.FromResult(Json("{}"));
        }

        private const string Script = """
            {"status":"CONFIRMED","coverage_ratio":1.0,"source_segments":[{"id":"seg-1","text":"原文片段"}],
             "scenes":[
              {"id":"sc-1","location":"旧地点","time_label":"夜","weather":"小雨","purpose":"建立场景","emotional_arc":"平静转不安",
               "version":3,"scene_asset_id":"","scene_asset_variant_id":"","outfit_assignments":{},"source_segments":[],
               "beats":[
                {"id":"bt-1","action":"旧动作","speaker_name":"樱","dialogue":"……","narration":"","subtext":"",
                 "must_visualize":true,"mergeable":false,"page_turn_hook":false,"importance":0.5,"version":2,"source_segments":[]}]}]}
            """;
    }

    // #428 分镜夹具：一页一格（0.5×0.4 @ (0.1,0.1)），无气泡；只计数整页分镜读取。
    private sealed class BoardFixture : HttpMessageHandler
    {
        public int StoryboardGets;

        protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken token)
        {
            var path = request.RequestUri!.AbsolutePath;
            if (request.Method == HttpMethod.Get)
            {
                if (path.EndsWith("/chapters/ch-1/pages"))
                    return Task.FromResult(Json("""[{"id":"pg-1","chapter_id":"ch-1","page_number":1,"panel_count":1,"storyboard_version":5,"status":"","selected_candidate_id":"","continuity_status":""}]"""));
                if (path.EndsWith("/pages/pg-1/storyboard")) { StoryboardGets++; return Task.FromResult(Json(Storyboard)); }
                if (path.EndsWith("/projects/p428-board/chapters"))
                    return Task.FromResult(Json("""[{"id":"ch-1","title":"第一章","ordinal":1,"page_count":1}]"""));
                if (path.EndsWith("/projects/p428-board/characters"))
                    return Task.FromResult(Json("""[{"id":"c1","primary_name":"樱","aliases":[],"locked_features":[],"forbidden_changes":[],"references":[]}]"""));
                if (path.EndsWith("/projects/p428-board/outfits")) return Task.FromResult(Json("[]"));
                return Task.FromResult(Json("[]"));
            }
            return Task.FromResult(Json("{}"));
        }

        private const string Storyboard = """
            {"page":{"id":"pg-1","chapter_id":"ch-1","page_number":1,"storyboard_version":5,"canvas":{"width_mm":182,"height_mm":257,"bleed_mm":3,"safe_mm":5},"status":""},
             "panels":[{"id":"panel-a","page_id":"pg-1","reading_order":1,"version":2,
               "bounds":{"x":0.1,"y":0.1,"width":0.5,"height":0.4},
               "geometry":{"type":"rect","rect":{"x":0.1,"y":0.1,"width":0.5,"height":0.4},"rotation":0,"z_order":1},
               "shot_type":"medium_close_up","camera_angle":"eye_level","bleed":false,"borderless":false,
               "actions":{"script_action":"雨夜，两人对望"},"background":"","props":[],"sound_effects":[],
               "characters":[],"character_presence":{},"expressions":{},"outfits":{},"dialogues":[]}],
             "candidate_count":0}
            """;
    }

    // #428 导演台夹具：一章一页一工作台（readiness ready、无批次无候选），
    // 计数工作台读取与导演命令历史读取。
    private sealed class GenerateDraftFixture : HttpMessageHandler
    {
        public int WorkbenchReads;

        protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken token)
        {
            var path = request.RequestUri!.AbsolutePath;
            if (request.Method == HttpMethod.Get)
            {
                if (path.EndsWith("/pages/page-1/generation-workbench")) { WorkbenchReads++; return Task.FromResult(Json(Workbench)); }
                if (path.EndsWith("/pages/page-1/batches")) return Task.FromResult(Json("[]"));
                if (path.EndsWith("/chapters/ch-1/pages"))
                    return Task.FromResult(Json("""[{"id":"page-1","chapter_id":"ch-1","page_number":1,"panel_count":1,"storyboard_version":4,"status":"","selected_candidate_id":"","continuity_status":""}]"""));
                if (path.EndsWith("/projects/p428-generate/chapters"))
                    return Task.FromResult(Json("""[{"id":"ch-1","title":"第一章","ordinal":1,"page_count":1}]"""));
                if (path.EndsWith("/projects/p428-generate/characters")) return Task.FromResult(Json("[]"));
                if (path.EndsWith("/projects/p428-generate/outfits")) return Task.FromResult(Json("[]"));
                if (path.Contains("/character-packages")) return Task.FromResult(Json("[]"));
                if (path.EndsWith("/models")) return Task.FromResult(Json("[]"));
                if (path.Contains("/director/command-groups")) return Task.FromResult(Json("[]"));
            }
            return Task.FromResult(Json("{}"));
        }

        private const string Workbench = """
            {"page":{"id":"page-1","page_number":1,"storyboard_version":4},
             "storyboard":{"panels":[{"characters":[],"outfits":{}}]},
             "readiness":{"ready":true},"current_batch":{"id":"batch"},"candidates":[]}
            """;
    }

    private static HttpResponseMessage Json(string text) => new(HttpStatusCode.OK) { Content = new StringContent(text) };
}
