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

// #429（条目 1/3/4）+ #449（条目 3）回归（P3）：
// ① GenerateView.RefreshAsync 在数据变化时 Render() 重建 DirectorPane——已输入的
//    导演指令被 F5 丢弃（PollTick 早已 keep-drafts-alive，刷新路径复用同一判定）；
// ② GenerateView 章节选择器缺离开确认——切章静默丢指令；
// ③ StoryboardView.LoadPagesAsync 无纪元守卫（pageLoadVersion 只覆盖
//    SelectPageAsync）——A 章慢响应在切到 B 后落地，把 pages/画布翻回 A 而选择器
//    仍显示 B；
// ④ LocalEditWindow groupId==0 直接关闭——已画选区与修改指令无守卫静默丢弃；
// ⑤（#449-3）LocalEditWindow 源图读取失败把英文 HttpRequestException.Message
//    原文上屏——改为「中文 + 状态码」的本地化映射（ApiClient 契约同型）。
//
// 【需 lead 注册】本文件按任务约束未接入运行入口：建议在
// NativeInteractionChecks.RunIsolated 的 await 链追加 `NativeIssue429Checks.Run();`
// ——Run 内部自带双模式 STA 调度，也可独立调用（无 Application 时自建 STA 线程 +
// Application/Theme）。注册调用点必须在 STA/Dispatcher 线程上下文中。
internal static class NativeIssue429Checks
{
    private const BindingFlags All = BindingFlags.Instance | BindingFlags.NonPublic | BindingFlags.Public;

    public static void Run()
    {
        // Application 是 AppDomain 级单例：链内已有 Application 时复用调用方线程，
        // 只有独立运行才自建 STA 线程 + Application（NativeIssue427Checks.Run 同款）。
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
        if (failure != null) throw new Exception("NativeIssue429Checks failed", failure);
    }

    // STA + DispatcherFrame 泵；KeyValueStore 全局位置整体隔离到一次性 scratch
    // 文件（分镜/生成视图的页号记忆会写它），收尾恢复原位置。
    private static void RunFrame()
    {
        var store = Path.Combine(Path.GetTempPath(), "mangaflow-issue429-store-" + Guid.NewGuid().ToString("N"));
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
        timeout.Tick += (_, _) => { failure ??= new TimeoutException("NativeIssue429Checks timed out"); frame.Continue = false; };
        timeout.Start();
        System.Threading.SynchronizationContext.SetSynchronizationContext(
            new System.Windows.Threading.DispatcherSynchronizationContext());
        System.Windows.Threading.Dispatcher.CurrentDispatcher.BeginInvoke(new Action(async () =>
        {
            try
            {
                await GenerateRefreshKeepsDirectorDraft();
                Console.WriteLine("PASS: #429 F5 刷新不丢导演指令（工作台零重读、Pane 不重建）");
                await GenerateChapterSwitchConfirmsDirectorDraft();
                Console.WriteLine("PASS: #429 切章先过离开确认——拒绝弹回原章零读取，同意后正常切换");
                await StoryboardStalePagesDiscarded();
                Console.WriteLine("PASS: #429 迟到的旧章 pages 响应被丢弃——画布/页列表/选择器保持新章（B）");
                LocalEditCloseGuardsDrafts();
                Console.WriteLine("PASS: #429 局部修改窗口关闭先确认——拒绝保留选区与指令，同意正常关闭");
                await LocalEditImageFailureLocalized();
                Console.WriteLine("PASS: #449 源图读取失败按「中文 + 状态码」契约上屏，英文原文不上屏");
            }
            catch (Exception error) { failure = error; }
            finally { frame.Continue = false; }
        }));
        System.Windows.Threading.Dispatcher.PushFrame(frame);
        timeout.Stop();
        if (failure != null) throw new Exception("NativeIssue429Checks failed", failure);
    }

    // ── ① F5 保留导演指令 ──
    // 失败构造（原始缺陷）：夹具在刷新前置 ChangeWorkbench=true——LoadWorkbenchAsync
    // 重读后 raw text 与旧值不同 → unchanged=false → Render() new 一个 DirectorPane，
    // 已输入指令消失（无守卫的 RefreshAsync 直接发起读取，「零重读」断言先失败）。
    private static async Task GenerateRefreshKeepsDirectorDraft()
    {
        var fixture = new GenerateChapterFixture();
        using var api = new ApiClient("http://127.0.0.1:12345", fixture);
        var view = new GenerateView();
        view.Activate(Context(api));
        try
        {
            await Until(() => fixture.WorkbenchReadsPage1 >= 1);
            await Settle();
            typeof(GenerateView).GetMethod("SwitchMode", All)!.Invoke(view, new object?[] { true });
            var pane = Field<DirectorPane>(view, "directorPane")!;
            var commandInput = CommandInput(pane);
            commandInput.Text = "给主角加一把伞";
            Require(pane.HasDraft, "导演台草稿构造失败（HasDraft 应为真）");

            fixture.ChangeWorkbench = true;   // 工作台数据已变化（轮询/他端操作）
            var reads = fixture.WorkbenchReadsPage1;
            await view.RefreshAsync();
            await Settle();

            Require(fixture.WorkbenchReadsPage1 == reads,
                $"有草稿时刷新不得发起工作台重载（实际新发起 {fixture.WorkbenchReadsPage1 - reads} 次）");
            Require(ReferenceEquals(Field<DirectorPane>(view, "directorPane"), pane),
                "有草稿时刷新不得重建 DirectorPane（#429）");
            Require(CommandInput(Field<DirectorPane>(view, "directorPane")!).Text == "给主角加一把伞",
                "刷新必须保留已输入的导演指令");

            // 对照组：草稿清空后刷新恢复工作（守卫不得把刷新永久挡死）。
            CommandInput(Field<DirectorPane>(view, "directorPane")!).Text = "";
            reads = fixture.WorkbenchReadsPage1;
            await view.RefreshAsync();
            await Until(() => fixture.WorkbenchReadsPage1 > reads);
        }
        finally { view.Deactivate(); }
    }

    // ── ② 切章的离开确认 ──
    // 失败构造（原始缺陷）：章节处理器没有 ConfirmLeaveAsync——拒绝分支的
    // 「确认缝被咨询」与「选择器弹回」断言直接失败（切章照常发生并丢稿）。
    private static async Task GenerateChapterSwitchConfirmsDirectorDraft()
    {
        var fixture = new GenerateChapterFixture();
        using var api = new ApiClient("http://127.0.0.1:12345", fixture);
        var view = new GenerateView();
        view.Activate(Context(api));
        try
        {
            await Until(() => fixture.WorkbenchReadsPage1 >= 1);
            await Settle();
            typeof(GenerateView).GetMethod("SwitchMode", All)!.Invoke(view, new object?[] { true });
            CommandInput(Field<DirectorPane>(view, "directorPane")!).Text = "切章时仍要保留的指令";

            var selector = Field<ComboBox>(view, "chapterSelector");
            ComboBoxItem Item(string tag) => selector.Items.Cast<ComboBoxItem>().Single(item => (string?)item.Tag == tag);
            var confirmations = 0;
            view.LeaveConfirmOverride = () => { confirmations++; return Task.FromResult(false); };   // 拒绝弃稿

            var reads = fixture.PagesReadsCh2;
            selector.SelectedItem = Item("ch-2");
            await Settle();
            Require(confirmations == 1, $"切章必须恰好咨询一次离开确认缝（实际 {confirmations} 次，#429）");
            Require(ReferenceEquals(selector.SelectedItem, Item("ch-1")), "拒绝弃稿后章节选择器必须弹回原章节");
            Require(Field<string>(view, "chapterId") == "ch-1", "拒绝弃稿后 chapterId 不得切换");
            Require(fixture.PagesReadsCh2 == reads, "拒绝弃稿后不得发起目标章节的 pages 读取");
            Require(CommandInput(Field<DirectorPane>(view, "directorPane")!).Text == "切章时仍要保留的指令",
                "拒绝弃稿后导演指令必须保留");

            view.LeaveConfirmOverride = () => Task.FromResult(true);   // 同意弃稿
            selector.SelectedItem = Item("ch-2");
            await Until(() => fixture.PagesReadsCh2 > reads);
            Require(Field<string>(view, "chapterId") == "ch-2", "同意弃稿后应切换到目标章节");
        }
        finally { view.Deactivate(); }
    }

    // ── ③ 迟到的旧章 pages 响应被丢弃 ──
    // 失败构造（原始缺陷）：LoadPagesAsync 无纪元守卫。序列：B 切换完成 → A 的
    // pages 读取被门控挂起 → 再切回 B（新纪元）→ 放行 A 的响应。未修复代码让 A
    // 的响应落地（pages 翻回 A、对 pg-a 追加一次整页分镜读取）；修复代码整份丢弃。
    private static async Task StoryboardStalePagesDiscarded()
    {
        var fixture = new BoardChapterFixture();
        using var api = new ApiClient("http://127.0.0.1:12345", fixture);
        var view = new StoryboardView();
        view.Activate(Context(api));
        try
        {
            await Until(() => view.PanelCountForTest == 1, 15, "初始章 A 未载入（#429-3 前置）");
            Require(PageId(view) == "pg-a", "前置失败：初始章 A 未载入");
            var selector = Field<ComboBox>(view, "chapterSelector");
            ComboBoxItem Item(string tag) => selector.Items.Cast<ComboBoxItem>().Single(item => (string?)item.Tag == tag);

            selector.SelectedItem = Item("ch-2");   // A→B（干净状态，确认自动通过）
            await Until(() => PageId(view) == "pg-b", 15, "A→B 切换未完成（#429-3 前置）");
            var storyboardReadsA = fixture.StoryboardReadsPageA;

            fixture.GateChapterAPages = NewGate();
            selector.SelectedItem = Item("ch-1");   // B→A：A 的 pages 响应被门控挂起
            // 计数含初始 A 载入的一次；>=2 即「门控中的第二次 A 读取确已发出」。
            // （lead：等值判定在高负载链内曾 flaky——读取计数短暂越过目标值会让
            // 条件永假直到超时；改为 >= 并给切换类等待 15s 预算。）
            await Until(() => fixture.ChapterAPagesReads >= 2, 15, "B→A 切换的第二次 A 章 pages 读取未发出（#429-3 前置）");

            selector.SelectedItem = Item("ch-2");   // A 尚未落地又切回 B（最新请求）
            await Until(() => PageId(view) == "pg-b" && fixture.StoryboardReadsPageB >= 2, 15, "切回 B 未完成（#429-3 前置）");

            fixture.GateChapterAPages!.TrySetResult(Json(Pages("pg-a", "ch-1")));   // 放行 A 的迟到响应
            await Settle(120);   // 让被丢弃（或未丢弃）的续体提交完再断言

            Require(fixture.StoryboardReadsPageA == storyboardReadsA,
                $"迟到的 A 章 pages 响应不得触发对 A 页分镜的读取（实际多发起 {fixture.StoryboardReadsPageA - storyboardReadsA} 次，#429）");
            Require(PageId(view) == "pg-b", "迟到的 A 章 pages 响应把当前页翻回了 A（#429）");
            Require(FirstPageId(view) == "pg-b", "迟到的 A 章 pages 响应把页列表翻回了 A（#429）");
            Require(ReferenceEquals(selector.SelectedItem, Item("ch-2")), "选择器必须仍显示用户选择的 B 章");
            Require(Field<string>(view, "chapterId") == "ch-2", "chapterId 必须仍是 B");
        }
        finally
        {
            fixture.GateChapterAPages?.TrySetResult(Json(Pages("pg-a", "ch-1")));
            view.Deactivate();
        }
    }

    // ── ④ 局部修改窗口的关闭守卫 ──
    // 失败构造（原始缺陷）：groupId==0 直接 return——确认缝从未被咨询，Closed
    // 立即触发、选区与指令直接丢失。
    private static void LocalEditCloseGuardsDrafts()
    {
        using var api = new ApiClient("http://127.0.0.1:12345", new ThrowingHandler());
        var window = MakeLocalEditWindow(api);
        try
        {
            var closed = false;
            window.Closed += (_, _) => closed = true;
            var consulted = 0;
            window.CloseConfirmOverride = () => { consulted++; return false; };   // 拒绝丢弃
            window.Close();
            Require(consulted == 1, $"带草稿关闭必须恰好咨询一次确认缝（实际 {consulted} 次，#429）");
            Require(!closed, "拒绝丢弃后窗口不得关闭");
            Require(Field<List<Point[]>>(window, "regions").Count == 1, "拒绝丢弃后已画选区必须保留");
            Require(Field<TextBox>(window, "instruction").Text == "修正雨伞", "拒绝丢弃后修改指令必须保留");

            window.CloseConfirmOverride = () => true;   // 同意丢弃
            window.Close();
            Require(closed, "同意丢弃后窗口必须正常关闭（守卫不得挡死关闭）");
        }
        finally
        {
            window.CloseConfirmOverride = () => true;
            window.Close();
        }
    }

    // ── ⑤（#449-3）源图读取失败的本地化 ──
    // 失败构造（原始缺陷）：catch 原样上屏 error.Message——回环拒绝连接的英文
    // 原文（含 127.0.0.1:12345）出现在 notice 上。注意：这条检查对
    // http://127.0.0.1:12345 发起一次真实的回环连接（无监听 → 立即拒绝），
    // 与渲染链里 ImageBox 的既有行为一致。
    private static async Task LocalEditImageFailureLocalized()
    {
        using var api = new ApiClient("http://127.0.0.1:12345", new ThrowingHandler());
        var window = MakeLocalEditWindow(api);
        window.CloseConfirmOverride = () => true;
        try
        {
            await (Task)typeof(LocalEditWindow).GetMethod("LoadImage", All)!.Invoke(window, null)!;
            var notice = Field<TextBlock>(window, "notice");
            Require(notice.Text == LocalEditWindow.DescribeImageLoadFailure(null),
                $"源图连接失败必须上屏本地化文案（实际：{notice.Text}，#449）");
            Require(!notice.Text.Contains("127.0.0.1", StringComparison.Ordinal),
                "连接层失败的英文原文（含地址）不得上屏（#449）");

            // 状态码分支按「中文 + 状态码」契约映射（ApiClient 同型）。
            Require(LocalEditWindow.DescribeImageLoadFailure(404).Contains("HTTP 404")
                && LocalEditWindow.DescribeImageLoadFailure(404).Contains("源图"), "404 映射必须携带中文语义与状态码");
            Require(LocalEditWindow.DescribeImageLoadFailure(409).Contains("HTTP 409"), "409 映射必须携带状态码");
            Require(LocalEditWindow.DescribeImageLoadFailure(500).Contains("HTTP 500"), "其他状态码映射必须携带状态码");
            Require(!LocalEditWindow.DescribeImageLoadFailure(404).Contains("Exception", StringComparison.Ordinal),
                "映射文案不得泄漏英文异常原文");
        }
        finally { window.Close(); }
    }

    // ── 夹具/辅助 ──

    private static LocalEditWindow MakeLocalEditWindow(ApiClient api)
    {
        var models = JsonSerializer.Deserialize<List<JsonElement>>(
            """[{"logical_alias":"mask-model","display_name":"选区模型","model_type":"IMAGE","enabled":true,"operations":["image_edit"],"accepts_explicit_mask":true,"resolutions":["1K"]}]""")!;
        var window = new LocalEditWindow(Context(api), new PageItem("page", 1),
            new CandidateItem("source", 1, "COMPLETED") { Resolution = "1K", AssetId = "source-image" }, models);
        Set(window, "loaded", true);
        Field<TextBox>(window, "instruction").Text = "修正雨伞";
        Field<List<Point[]>>(window, "regions").Add(LocalEditRules.Rectangle(new(10, 10), new(30, 30), new(100, 100)));
        return window;
    }

    private static WorkspaceContext Context(ApiClient api) => new()
    {
        Api = api, Cache = new ApiCache(), State = new WorkspaceState(), Window = null!,
        Project = new ProjectItem("p429", "#429 检查", "", 0, 0),
        NavigateSection = (_, _) => Task.CompletedTask, OpenDashboard = () => Task.CompletedTask,
    };

    private static TextBox CommandInput(DirectorPane pane) =>
        // 在 Pane 子树内查找：UserControl 未布局前模板未应用，从视图根走
        // VisualTreeHelper 拿不到子树；Border/Panel 的直接子元素即时可遍历。
        Descendants(pane).OfType<TextBox>().Single(box => GetName(box) == "导演指令");

    private static string PageId(StoryboardView view) =>
        ((PageItem?)typeof(StoryboardView).GetField("currentPage", All)!.GetValue(view))?.Id ?? "";

    private static string FirstPageId(StoryboardView view) =>
        ((List<PageItem>)typeof(StoryboardView).GetField("pages", All)!.GetValue(view)!)[0].Id;

    private static T Field<T>(object target, string name) => (T)target.GetType().GetField(name, All)!.GetValue(target)!;

    private static void Set(object target, string name, object value) =>
        target.GetType().GetField(name, All)!.SetValue(target, value);

    private static TaskCompletionSource<HttpResponseMessage> NewGate() =>
        new(TaskCreationOptions.RunContinuationsAsynchronously);

    private static Task Until(Func<bool> condition) => Until(condition, 5, "等待条件超时");

    // lead：链内跑在数十个检查之后，调度器上残留 11+ 个宿主窗口的定时器——5s 预算
    // 在高负载下曾 flaky（TaskCanceledException 裸抛难以定位）。带预算与说明文案，
    // 超时转成可读异常；切换/重载类等待按 15s 起步。
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
        var count = System.Windows.Media.VisualTreeHelper.GetChildrenCount(root);
        for (var i = 0; i < count; i++)
        {
            var child = System.Windows.Media.VisualTreeHelper.GetChild(root, i);
            yield return child;
            foreach (var nested in Descendants(child)) yield return nested;
        }
    }

    private static string GetName(UIElement element) => System.Windows.Automation.AutomationProperties.GetName(element) ?? "";

    private static HttpResponseMessage Json(string text) => new(HttpStatusCode.OK) { Content = new StringContent(text) };

    // 两章两页：ch-1/page-1、ch-2/page-2；工作台 raw text 可翻转（ChangeWorkbench=true
    // 后 page-1 的工作台多一个 note 字段 → LoadWorkbenchAsync 的 unchanged=false）。
    private sealed class GenerateChapterFixture : HttpMessageHandler
    {
        public bool ChangeWorkbench;
        public int WorkbenchReadsPage1, PagesReadsCh2;

        protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken token)
        {
            var path = request.RequestUri!.AbsolutePath;
            if (request.Method == HttpMethod.Get)
            {
                if (path.EndsWith("/pages/page-1/generation-workbench")) { WorkbenchReadsPage1++; return Task.FromResult(Json(Workbench("page-1", ChangeWorkbench))); }
                if (path.EndsWith("/pages/page-2/generation-workbench")) return Task.FromResult(Json(Workbench("page-2", false)));
                if (path.EndsWith("/pages/page-1/batches") || path.EndsWith("/pages/page-2/batches")) return Task.FromResult(Json("[]"));
                if (path.EndsWith("/chapters/ch-1/pages")) return Task.FromResult(Json(Pages("page-1", "ch-1")));
                if (path.EndsWith("/chapters/ch-2/pages")) { PagesReadsCh2++; return Task.FromResult(Json(Pages("page-2", "ch-2"))); }
                if (path.EndsWith("/projects/p429/chapters"))
                    return Task.FromResult(Json("""[{"id":"ch-1","title":"第一章","ordinal":1,"page_count":1},{"id":"ch-2","title":"第二章","ordinal":2,"page_count":1}]"""));
                if (path.EndsWith("/projects/p429/characters")) return Task.FromResult(Json("[]"));
                if (path.EndsWith("/projects/p429/outfits")) return Task.FromResult(Json("[]"));
                if (path.Contains("/character-packages")) return Task.FromResult(Json("[]"));
                if (path.EndsWith("/models")) return Task.FromResult(Json("[]"));
                if (path.Contains("/director/command-groups")) return Task.FromResult(Json("[]"));
            }
            return Task.FromResult(Json("{}"));
        }
    }

    // 两章两页分镜：ch-1→pg-a（panel-a）、ch-2→pg-b（panel-b）；A 章的 pages 读取
    // 可被门控挂起（GateChapterAPages 非空时），构造「慢的 A 响应晚于 B 切换落地」。
    private sealed class BoardChapterFixture : HttpMessageHandler
    {
        public TaskCompletionSource<HttpResponseMessage>? GateChapterAPages;
        public int ChapterAPagesReads, StoryboardReadsPageA, StoryboardReadsPageB;

        protected override async Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken token)
        {
            var path = request.RequestUri!.AbsolutePath;
            if (request.Method == HttpMethod.Get)
            {
                if (path.EndsWith("/chapters/ch-1/pages"))
                {
                    ChapterAPagesReads++;
                    if (GateChapterAPages is { } gate) return await gate.Task;
                    return Json(Pages("pg-a", "ch-1"));
                }
                if (path.EndsWith("/chapters/ch-2/pages")) return Json(Pages("pg-b", "ch-2"));
                if (path.EndsWith("/pages/pg-a/storyboard")) { StoryboardReadsPageA++; return Json(Storyboard("pg-a", "ch-1", "panel-a")); }
                if (path.EndsWith("/pages/pg-b/storyboard")) { StoryboardReadsPageB++; return Json(Storyboard("pg-b", "ch-2", "panel-b")); }
                if (path.EndsWith("/projects/p429/chapters"))
                    return Json("""[{"id":"ch-1","title":"第一章","ordinal":1,"page_count":1},{"id":"ch-2","title":"第二章","ordinal":2,"page_count":1}]""");
                if (path.EndsWith("/projects/p429/characters"))
                    return Json("""[{"id":"c1","primary_name":"樱","aliases":[],"locked_features":[],"forbidden_changes":[],"references":[]}]""");
                if (path.EndsWith("/projects/p429/outfits")) return Json("[]");
                return Json("[]");
            }
            return Json("{}");
        }
    }

    // GenerateView 夹具的页行（占位符替换，避免插值与 JSON 花括号冲突）。
    private static string Pages(string pageId, string chapterId) =>
        """[{"id":"PAGE","chapter_id":"CHAPTER","page_number":1,"panel_count":1,"storyboard_version":4,"status":"","selected_candidate_id":"","continuity_status":""}]"""
        .Replace("PAGE", pageId).Replace("CHAPTER", chapterId);

    // GenerateView 夹具的工作台；changed=true 时多一个 note 字段构造数据变化。
    private static string Workbench(string pageId, bool changed) =>
        """{"page":{"id":"PAGE","page_number":1,"storyboard_version":4}NOTE,"storyboard":{"panels":[{"characters":[],"outfits":{}}]},"readiness":{"ready":true},"current_batch":{"id":"batch"},"candidates":[]}"""
        .Replace("PAGE", pageId).Replace("NOTE", changed ? ",\"note\":\"data-changed\"" : "");

    // StoryboardView 夹具的整页分镜。
    private static string Storyboard(string pageId, string chapterId, string panelId) =>
        """
        {"page":{"id":"PAGE","chapter_id":"CHAPTER","page_number":1,"storyboard_version":5,"canvas":{"width_mm":182,"height_mm":257,"bleed_mm":3,"safe_mm":5},"status":""},
         "panels":[{"id":"PANEL","page_id":"PAGE","reading_order":1,"version":2,
           "bounds":{"x":0.1,"y":0.1,"width":0.5,"height":0.4},
           "geometry":{"type":"rect","rect":{"x":0.1,"y":0.1,"width":0.5,"height":0.4},"rotation":0,"z_order":1},
           "shot_type":"medium_close_up","camera_angle":"eye_level","bleed":false,"borderless":false,
           "actions":{"script_action":"雨夜，两人对望"},"background":"","props":[],"sound_effects":[],
           "characters":[],"character_presence":{},"expressions":{},"outfits":{},"dialogues":[]}],
         "candidate_count":0}
        """
        .Replace("PAGE", pageId).Replace("CHAPTER", chapterId).Replace("PANEL", panelId);

    private sealed class ThrowingHandler : HttpMessageHandler
    {
        protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken token) =>
            throw new HttpRequestException("connection refused");
    }
}
