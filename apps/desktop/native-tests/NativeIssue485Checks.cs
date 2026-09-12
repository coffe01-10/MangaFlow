using System.Net;
using System.Net.Http;
using System.Reflection;
using System.Text.Json;
using System.Threading;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Controls.Primitives;
using System.Windows.Threading;
using MangaFlow.Native;
using MangaFlow.Native.Services;
using MangaFlow.Native.Views;

// #485 回归（P3）+#486 条目4（P4）：CharacterPackagePane 的四个变更处理器
// （创建角色模型包 / 派生新版本 / 发布当前版本 / 设为发布版本）此前没有
// SaveSpec/Change 已有的 busy 守卫——双击发出重复 POST，第二个（典型 409）在
// 重绘之后落地，成功操作后反而弹「创建失败 / 派生失败 / expected_published_
// version_id」虚假失败提示；发布与「保存草稿规格」并发时，保存可以在发布快照
// 冻结之后才落地，已发布版本丢失刚保存的编辑。另：ShowHistory 的差异取数是
// 异步的且模态对话框在取数之后才弹出，取数期间「对比历史」仍可点击，第二次
// 点击会在第一个对话框关闭后再排一个模态。
//
// 五条确定性检查（TaskCompletionSource 门控假 HTTP 响应构造竞态，零真实网络，
// 合成 Click 双击：第二次点击确定落在第一个 POST 挂起期间）：
// ① 创建双击 → 恰好一次 POST，成功创建后无虚假「创建失败」；
// ② 派生双击 → 恰好一次 POST，成功派生后无虚假「派生失败」；
// ③ 保存草稿规格挂起期间发布 → 被拒绝（不发 publish POST，机制＝拒绝而非排队；
//    面板此时已随 SaveSpec 整体禁用），保存完成后重新点击才发布且严格排在保存
//    PATCH 之后；
// ④ 设为发布版本双击 → 恰好一次 POST，切换成功后无虚假错误提示；
// ⑤ 对比历史取数挂起期间 → 按钮禁用，第二次调用（合成点击绕过 IsEnabled）被
//    拒绝：只发一次 diff 请求、只弹一个对话框。
//
// 【需 lead 注册】本文件按任务约束未接入运行入口：建议在
// NativeInteractionChecks.RunIsolated 的 await 链追加 `NativeIssue485Checks.Run();`
// （NativeIssue484Checks 同款；Run 内部自带 STA 调度，也可独立调用，无
// Application 时自建 STA 线程 + Application/Theme）。注册调用点必须在
// STA/Dispatcher 线程上下文中（WPF 视觉树访问要求）。
internal static class NativeIssue485Checks
{
    private const BindingFlags All = BindingFlags.Instance | BindingFlags.NonPublic | BindingFlags.Public;

    public static void Run()
    {
        // Application 是 AppDomain 级单例：链内（RunIsolated）已有 Application 时
        // 必须复用调用方线程，只有独立运行才自建 STA 线程 + Application
        // （NativeIssue427Checks.Run 的同款双模式）。
        if (Application.Current is null) RunOnDedicatedStaThread();
        else RunFrame();
    }

    // 独立运行（进程里还没有 Application）：自建 STA 线程 + Application/Theme。
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
        if (failure != null) throw new Exception("NativeIssue485Checks failed", failure);
    }

    // STA + DispatcherFrame 泵（NativeInteractionChecks.RunIsolated 的模式）：
    // async-void 的按钮处理经由 DispatcherSynchronizationContext 回到泵上执行。
    private static void RunFrame()
    {
        Exception? failure = null;
        var frame = new DispatcherFrame();
        Dispatcher.CurrentDispatcher.UnhandledException += (_, e) =>
        {
            e.Handled = true;
            failure ??= new Exception("Dispatcher unhandled: " + e.Exception.Message, e.Exception);
            frame.Continue = false;
        };
        var timeout = new DispatcherTimer { Interval = TimeSpan.FromSeconds(60) };
        timeout.Tick += (_, _) => { failure = new TimeoutException("NativeIssue485Checks timed out"); frame.Continue = false; };
        timeout.Start();
        SynchronizationContext.SetSynchronizationContext(new DispatcherSynchronizationContext());
        Dispatcher.CurrentDispatcher.BeginInvoke(new Action(async () =>
        {
            try { await CheckAsync(); }
            catch (Exception error) { failure = error; }
            finally { frame.Continue = false; }
        }));
        Dispatcher.PushFrame(frame);
        timeout.Stop();
        if (failure != null) throw new Exception("NativeIssue485Checks failed", failure);
    }

    private static async Task CheckAsync()
    {
        await CreateDoubleClickSendsSinglePost();
        Console.WriteLine("PASS: #485 创建角色模型包双击只发一次 POST，成功创建后无虚假「创建失败」提示");
        await DeriveDoubleClickSendsSinglePost();
        Console.WriteLine("PASS: #485 派生新版本双击只发一次 POST，成功派生后无虚假「派生失败」提示");
        await PublishRefusedWhileSaveInFlightThenRunsAfter();
        Console.WriteLine("PASS: #485 保存草稿规格进行中发布被拒绝（不发 publish POST，面板已随保存禁用），保存完成后重试发布且严格排在保存之后");
        await ActivateDoubleClickSendsSinglePost();
        Console.WriteLine("PASS: #485 设为发布版本双击只发一次 POST，切换成功后无虚假 expected_published_version_id 错误");
        await CompareDoubleClickRejectedWhilePending();
        Console.WriteLine("PASS: #486-4 对比历史取数挂起期间按钮禁用，第二次调用被拒绝（只发一次 diff 请求、只弹一个对话框）");
    }

    // ── ① 创建角色模型包：双击（第二次点击落在第一个 POST 挂起期间）──
    // 失败构造（原始缺陷）：无 busy 守卫 → 两个 POST；第二个 409「包已存在」在
    // LoadAsync 重绘之后落地 → 成功创建后反而弹「创建失败：…」。
    private static async Task CreateDoubleClickSendsSinglePost()
    {
        var http = new Fixture();
        http.Package = () => http.CreatePosts >= 1 ? Fixture.PackagePublished : "null";
        var gate = NewGate();
        http.GateFirstCreatePost = gate;
        using var api = new ApiClient("http://127.0.0.1:12345", http);
        var (view, pane) = await Attach(api);
        try
        {
            await Until(() => Buttons(pane, "创建角色模型包").Count() == 1);
            var create = Buttons(pane, "创建角色模型包").Single();
            Click(create); Click(create);   // 双击：第二次点击在第一个 POST 挂起时到达
            await Until(() => http.CreatePosts >= 1);
            await Settle();
            Require(http.CreatePosts == 1, $"创建角色模型包双击必须只发出一次 POST（实际 {http.CreatePosts} 次，#485）");
            gate.TrySetResult(Json("{}"));
            await Until(() => Buttons(pane, "创建角色模型包").Count() == 0 && Buttons(pane, "派生新版本").Count() == 1);
            await Settle();
            var notice = Notice(view);
            Require(!notice.Text.Contains("创建失败"), $"成功创建角色模型包后不得出现虚假「创建失败」提示（实际：{notice.Text}，#485）");
        }
        finally { gate.TrySetResult(Json("{}")); view.Deactivate(); }
    }

    // ── ② 派生新版本：双击 → 重复 POST /versions（重复草稿，或 409 后的虚假「派生失败」）──
    private static async Task DeriveDoubleClickSendsSinglePost()
    {
        var http = new Fixture();
        http.Package = () => http.DerivePosts >= 1 ? Fixture.PackageWithDraft : Fixture.PackagePublished;
        var gate = NewGate();
        http.GateFirstDerivePost = gate;
        using var api = new ApiClient("http://127.0.0.1:12345", http);
        var (view, pane) = await Attach(api);
        try
        {
            await Until(() => Buttons(pane, "派生新版本").Count() == 1 && Buttons(pane, "派生新版本").Single().IsEnabled);
            var derive = Buttons(pane, "派生新版本").Single();
            Click(derive); Click(derive);
            await Until(() => http.DerivePosts >= 1);
            await Settle();
            Require(http.DerivePosts == 1, $"派生新版本双击必须只发出一次 POST（实际 {http.DerivePosts} 次，#485）");
            gate.TrySetResult(Json("{}"));
            await Until(() => Buttons(pane, "保存草稿规格").Count() == 1);   // 重绘为草稿编辑器
            await Settle();
            var notice = Notice(view);
            Require(!notice.Text.Contains("派生失败"), $"成功派生后不得出现虚假「派生失败」提示（实际：{notice.Text}，#485）");
        }
        finally { gate.TrySetResult(Json("{}")); view.Deactivate(); }
    }

    // ── ③ 发布 vs 保存草稿规格（#485 的快照竞态）──
    // 失败构造（原始缺陷）：发布没有 busy 守卫，保存 PATCH 挂起期间点击发布 →
    // publish POST 与保存并发，保存可在发布快照冻结之后落地（已发布版本丢失
    // 刚保存的编辑，两条成功提示同时弹出）。修复机制＝拒绝（不是排队等待）：
    // 保存进行中时整个面板已随 SaveSpec 禁用，busy 守卫再挡住合成点击；保存完
    // 成后重新点击即可发布。
    private static async Task PublishRefusedWhileSaveInFlightThenRunsAfter()
    {
        var http = new Fixture();
        http.Package = () => http.PublishPosts >= 1 ? Fixture.PackagePublishedDraft2 : Fixture.PackageWithDraft;
        var saveGate = NewGate();
        http.GateSavePatch = saveGate;
        using var api = new ApiClient("http://127.0.0.1:12345", http);
        var (view, pane) = await Attach(api);
        try
        {
            await Until(() => Buttons(pane, "保存草稿规格").Count() == 1);
            // 发布确认的无模态测试缝（StoryboardView.DeleteConfirmOverride 同款）；
            // 生产路径为 null 时 Publish 走 MessageBox，headless 检查会卡死在模态。
            var seam = typeof(CharacterPackagePane).GetField("PublishConfirmOverride", All);
            Require(seam != null, "发布确认测试缝缺失：CharacterPackagePane.PublishConfirmOverride");
            seam!.SetValue(pane, new Func<Task<bool>>(() => Task.FromResult(true)));
            var save = Buttons(pane, "保存草稿规格").Single();
            var publish = Buttons(pane, "发布当前版本 V2").Single();
            Click(save);
            await Until(() => http.SavePatches >= 1 && !pane.IsEnabled);   // 保存挂起：面板整体禁用
            Click(publish);   // 保存进行中的发布点击（真实 UI 下面板已禁用；合成点击必须被守卫拒绝）
            await Settle();
            Require(http.PublishPosts == 0,
                $"保存草稿规格进行中发布必须被拒绝，不得发出 publish POST（实际 {http.PublishPosts} 次，#485：快照冻结后才落地的保存会让已发布版本丢失刚保存的编辑）");
            saveGate.TrySetResult(Json("{}"));
            await Until(() => Notice(view).Text.Contains("草稿规格已保存") && !Busy(pane));
            // 保存完成后重新点击发布：这次必须发出发布请求，且严格排在保存 PATCH 之后。
            var publishAfter = Buttons(pane, "发布当前版本 V2").Single();
            Click(publishAfter);
            await Until(() => http.PublishPosts == 1);
            var requests = http.Snapshot();
            var saveAt = requests.FindIndex(row => row.StartsWith("PATCH ", StringComparison.Ordinal) && row.EndsWith("/package", StringComparison.Ordinal));
            var publishAt = requests.FindIndex(row => row.StartsWith("POST ", StringComparison.Ordinal) && row.EndsWith("/publish", StringComparison.Ordinal));
            Require(saveAt >= 0 && publishAt >= 0 && publishAt > saveAt,
                $"发布 POST 必须排在保存 PATCH 之后（save#{saveAt} publish#{publishAt}，#485 序列化断言）");
        }
        finally { saveGate.TrySetResult(Json("{}")); view.Deactivate(); }
    }

    // ── ④ 设为发布版本：双击 → 第二个 POST activate 撞 expected_published_version_id ──
    private static async Task ActivateDoubleClickSendsSinglePost()
    {
        var http = new Fixture();
        http.Package = () => http.ActivatePosts >= 1 ? Fixture.PackageActivated : Fixture.PackageTwoLocked;
        var gate = NewGate();
        http.GateFirstActivatePost = gate;
        using var api = new ApiClient("http://127.0.0.1:12345", http);
        var (view, pane) = await Attach(api);
        try
        {
            await Until(() => Buttons(pane, "设为发布版本").Count() == 1);
            var activate = Buttons(pane, "设为发布版本").Single();
            Click(activate); Click(activate);
            await Until(() => http.ActivatePosts >= 1);
            await Settle();
            Require(http.ActivatePosts == 1, $"设为发布版本双击必须只发出一次 POST（实际 {http.ActivatePosts} 次，#485）");
            gate.TrySetResult(Json("{}"));
            await Until(() => http.PackageGets >= 2);   // 切换成功后的重绘读取
            await Settle();
            var notice = Notice(view);
            Require(!notice.Text.Contains("expected_published_version_id"),
                $"切换发布版本成功后不得出现虚假错误提示（实际：{notice.Text}，#485）");
        }
        finally { gate.TrySetResult(Json("{}")); view.Deactivate(); }
    }

    // ── ⑤ 对比历史取数挂起期间的第二次点击（#486 条目4）──
    // 失败构造（原始缺陷）：ShowHistory 先 await Compare() 再 ShowDialog()，取数的
    // 多秒窗口里按钮不禁用 → 第二次点击排入第二个模态，第一个关闭后立刻弹出。
    private static async Task CompareDoubleClickRejectedWhilePending()
    {
        var http = new Fixture { Package = () => Fixture.PackageTwoLocked };
        var diffGate = NewGate();
        http.GateDiff = diffGate;
        using var api = new ApiClient("http://127.0.0.1:12345", http);
        var (view, pane) = await Attach(api);
        // 模态对话框自动关闭器：ShowDialog 弹出后 40ms 内关闭（未修复代码可能排两个
        // 模态；关闭器必须在放行 diff 门之前就位并保持运转，否则检查卡死在模态泵）。
        var closer = new DispatcherTimer(DispatcherPriority.Background) { Interval = TimeSpan.FromMilliseconds(40) };
        var closedWindows = new HashSet<Window>();
        closer.Tick += (_, _) =>
        {
            // IsLoaded 过滤：Window 在构造时就进入 Application.Windows，但只有
            // ShowDialog 之后才可 Close（提前关闭会让 ShowDialog 抛「窗口已关闭」）。
            foreach (var window in Application.Current.Windows.Cast<Window>().ToList())
                if (window.Title == "角色模型包 · 对比历史" && window.IsLoaded && closedWindows.Add(window)) window.Close();
        };
        closer.Start();
        try
        {
            await Until(() => Buttons(pane, "对比历史").Count() == 1 && Buttons(pane, "对比历史").Single().IsEnabled);
            var compare = Buttons(pane, "对比历史").Single();
            Click(compare);
            await Until(() => http.DiffGets >= 1);
            Require(!compare.IsEnabled, "差异取数挂起期间「对比历史」按钮必须禁用（#486-4）");
            Click(compare);   // 取数期间的第二次点击：合成点击绕过 IsEnabled，必须被调用层拒绝
            await Settle();
            Require(http.DiffGets == 1, $"取数挂起期间的第二次「对比历史」必须被拒绝（diff 请求实际 {http.DiffGets} 次，#486-4）");
            diffGate.TrySetResult(Json("{}"));
            await Until(() => closedWindows.Count >= 1 && compare.IsEnabled);
            Require(closedWindows.Count == 1, $"只允许弹出一个对比历史对话框（实际 {closedWindows.Count} 个，#486-4）");
        }
        finally
        {
            // 先放行再等待一拍：让未修复代码里排队的模态也被关闭器收掉，最后才停表。
            diffGate.TrySetResult(Json("{}"));
            await Settle(300);
            closer.Stop();
            view.Deactivate();
        }
    }

    // ── 反射/断言辅助（NativeIssue484Checks / NativeStoryboardEditChecks 同款）──

    // 激活 AssetsView（默认 Characters 页）并直接构造挂载好的 CharacterPackagePane：
    // pane 必须有 Parent（Showing 判据），否则所有守卫会因 !Showing 直接拒绝。
    private static async Task<(AssetsView View, CharacterPackagePane Pane)> Attach(ApiClient api)
    {
        var view = new AssetsView();
        view.Activate(new WorkspaceContext
        {
            Api = api, Cache = new ApiCache(), State = new WorkspaceState(), Window = null!,
            Project = new ProjectItem("issue485", "角色包守卫检查", "", 0, 0),
            NavigateSection = (_, _) => Task.CompletedTask, OpenDashboard = () => Task.CompletedTask,
        });
        await Until(() => view.characters.Count == 1);
        var pane = new CharacterPackagePane(view, view.characters[0]);
        var host = new StackPanel();
        host.Children.Add(pane);
        return (view, pane);
    }

    private static async Task Until(Func<bool> condition)
    {
        using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(5));
        while (!condition()) await Task.Delay(5, timeout.Token);
    }

    private static async Task Settle(int milliseconds = 50) => await Task.Delay(milliseconds);

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

    private static void Click(ButtonBase button) => button.RaiseEvent(new RoutedEventArgs(ButtonBase.ClickEvent));

    private static TextBlock Notice(AssetsView view) =>
        (TextBlock)typeof(AssetsView).GetField("notice", All)!.GetValue(view)!;

    private static bool Busy(CharacterPackagePane pane) =>
        (bool)typeof(CharacterPackagePane).GetField("busy", All)!.GetValue(pane)!;

    private static TaskCompletionSource<HttpResponseMessage> NewGate() =>
        new(TaskCreationOptions.RunContinuationsAsynchronously);

    private static HttpResponseMessage Json(string body) =>
        new(HttpStatusCode.OK) { Content = new StringContent(body) };

    private static HttpResponseMessage Conflict(string detail) =>
        new(HttpStatusCode.Conflict) { Content = new StringContent($"{{\"detail\":\"{detail}\"}}") };

    // 假 API：门控（TaskCompletionSource）挂起指定请求构造竞态；重复的变更 POST
    // 返回 409——与线上行为一致（角色包/草稿唯一性、expected_published_version_id
    // 乐观并发校验），这是缺陷里「成功操作后弹虚假失败提示」的来源。
    private sealed class Fixture : HttpMessageHandler
    {
        public const string ProjectId = "issue485";
        private const string PackagePath = "/api/v1/projects/" + ProjectId + "/characters/ch-1/package";

        public const string PackagePublished =
            """
            {"status":"ACTIVE","version":4,"published_version_id":"v-1","identity_spec":{},"visual_spec":{},"negative_constraints":[],
             "completeness":{"score":45,"missing":[]},
             "versions":[{"id":"v-1","status":"LOCKED","version_number":1,"references":[],"outfits":[],
               "spec_snapshot":{"identity_spec":{},"visual_spec":{},"negative_constraints":[]}}]}
            """;

        public const string PackageWithDraft =
            """
            {"status":"ACTIVE","version":4,"published_version_id":"v-1","identity_spec":{"hair":"黑色碎短发"},"visual_spec":{},"negative_constraints":[],
             "completeness":{"score":45,"missing":[]},
             "versions":[{"id":"v-1","status":"LOCKED","version_number":1,"references":[],"outfits":[],
               "spec_snapshot":{"identity_spec":{},"visual_spec":{},"negative_constraints":[]}},
              {"id":"v-2","status":"DRAFT","version_number":2,"references":[],"outfits":[],
               "spec_snapshot":{"identity_spec":{},"visual_spec":{},"negative_constraints":[]}}]}
            """;

        public const string PackageTwoLocked =
            """
            {"status":"ACTIVE","version":4,"published_version_id":"v-1","identity_spec":{},"visual_spec":{},"negative_constraints":[],
             "completeness":{"score":45,"missing":[]},
             "versions":[{"id":"v-1","status":"LOCKED","version_number":1,"references":[],"outfits":[],
               "spec_snapshot":{"identity_spec":{},"visual_spec":{},"negative_constraints":[]}},
              {"id":"v-2","status":"LOCKED","version_number":2,"references":[],"outfits":[],
               "spec_snapshot":{"identity_spec":{},"visual_spec":{},"negative_constraints":[]}}]}
            """;

        public const string PackageActivated =
            """
            {"status":"ACTIVE","version":4,"published_version_id":"v-2","identity_spec":{},"visual_spec":{},"negative_constraints":[],
             "completeness":{"score":45,"missing":[]},
             "versions":[{"id":"v-1","status":"LOCKED","version_number":1,"references":[],"outfits":[],
               "spec_snapshot":{"identity_spec":{},"visual_spec":{},"negative_constraints":[]}},
              {"id":"v-2","status":"LOCKED","version_number":2,"references":[],"outfits":[],
               "spec_snapshot":{"identity_spec":{},"visual_spec":{},"negative_constraints":[]}}]}
            """;

        public const string PackagePublishedDraft2 =
            """
            {"status":"ACTIVE","version":4,"published_version_id":"v-2","identity_spec":{},"visual_spec":{},"negative_constraints":[],
             "completeness":{"score":45,"missing":[]},
             "versions":[{"id":"v-1","status":"LOCKED","version_number":1,"references":[],"outfits":[],
               "spec_snapshot":{"identity_spec":{},"visual_spec":{},"negative_constraints":[]}},
              {"id":"v-2","status":"LOCKED","version_number":2,"references":[],"outfits":[],
               "spec_snapshot":{"identity_spec":{},"visual_spec":{},"negative_constraints":[]}}]}
            """;

        private const string Characters =
            """[{"id":"ch-1","primary_name":"林小满","aliases":[],"locked_features":[],"forbidden_changes":[],"version":1,"references":[]}]""";

        public Func<string> Package = () => "null";
        public TaskCompletionSource<HttpResponseMessage>? GateFirstCreatePost, GateFirstDerivePost, GateFirstActivatePost, GateSavePatch, GateDiff;
        public int CreatePosts, DerivePosts, PublishPosts, ActivatePosts, SavePatches, DiffGets, PackageGets;
        private readonly object gate = new();
        private readonly List<string> requests = [];

        public List<string> Snapshot() { lock (gate) return [.. requests]; }

        protected override async Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken token)
        {
            var path = request.RequestUri!.AbsolutePath;
            var method = request.Method.Method.ToUpperInvariant();
            lock (gate) requests.Add($"{method} {path}");
            if (path == PackagePath)
            {
                if (method == "GET")
                {
                    Interlocked.Increment(ref PackageGets);
                    return Json(Package());
                }
                if (method == "POST")
                {
                    var count = Interlocked.Increment(ref CreatePosts);
                    if (count == 1 && GateFirstCreatePost is { } createGate) return await createGate.Task;
                    return count > 1 ? Conflict("角色模型包已存在") : Json("{}");
                }
                if (method == "PATCH")
                {
                    Interlocked.Increment(ref SavePatches);
                    if (GateSavePatch is { } saveGate && !saveGate.Task.IsCompleted) return await saveGate.Task;
                    return Json("{}");
                }
            }
            if (path == PackagePath + "/versions" && method == "POST")
            {
                var count = Interlocked.Increment(ref DerivePosts);
                if (count == 1 && GateFirstDerivePost is { } deriveGate) return await deriveGate.Task;
                return count > 1 ? Conflict("已存在进行中的草稿版本") : Json("{}");
            }
            if (path == PackagePath + "/activate" && method == "POST")
            {
                var count = Interlocked.Increment(ref ActivatePosts);
                if (count == 1 && GateFirstActivatePost is { } activateGate) return await activateGate.Task;
                return count > 1 ? Conflict("expected_published_version_id 与当前发布版本不一致，请刷新后重试") : Json("{}");
            }
            if (path.EndsWith("/publish", StringComparison.Ordinal) && method == "POST")
            {
                Interlocked.Increment(ref PublishPosts);
                return Json("{}");
            }
            if (path == PackagePath + "/diff" && method == "GET")
            {
                Interlocked.Increment(ref DiffGets);
                if (GateDiff is { } diffGate && !diffGate.Task.IsCompleted) return await diffGate.Task;
                return Json("{}");
            }
            // AssetsView 激活载入（models/assets/characters/outfits）与角色包列表。
            if (path == "/api/v1/models") return Json("[]");
            if (path == "/api/v1/assets") return Json("[]");
            if (path == "/api/v1/projects/" + ProjectId + "/characters" && method == "GET") return Json(Characters);
            if (path == "/api/v1/projects/" + ProjectId + "/outfits") return Json("[]");
            if (path == "/api/v1/projects/" + ProjectId + "/character-packages") return Json("[]");
            return Json("{}");
        }
    }
}
