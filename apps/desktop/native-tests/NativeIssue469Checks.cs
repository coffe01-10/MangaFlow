using System.Net;
using System.Net.Http;
using System.Reflection;
using System.Text.Json;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Controls.Primitives;
using System.Windows.Threading;
using MangaFlow.Native;
using MangaFlow.Native.Services;
using MangaFlow.Native.Views;

/// <summary>
/// #469 / #471-1 / #429-2 回归检查：项目设置页的未保存编辑守护（脏标记 +
/// ConfirmLeaveAsync + RefreshAsync 跳过）、PATCH 在途被导航取消时的「提交结果
/// 未知」反馈，以及全局设置页刷新不得清掉运行参数编辑与录入到一半的 API Key。
/// 复用 NativeInteractionChecks 的 STA + DispatcherFrame 骨架，让视图的
/// async void Activate/LoadAsync 续体得到泵送；自建 Application 以便独立注册。
/// </summary>
internal static class NativeIssue469Checks
{
    public static void Run()
    {
        Exception? failure = null;
        var thread = new Thread(() =>
        {
            try
            {
                // AppDomain 只允许一个 Application：注册进 --render 流水线时复用
                // NativeVisualChecks 已建好的实例（主题已加载）；独立运行才自建。
                var app = Application.Current ?? new Application();
                if (app.Resources.MergedDictionaries.Count == 0)
                    app.Resources.MergedDictionaries.Add(new ResourceDictionary { Source = new Uri("/MangaFlow.Native;component/Theme.xaml", UriKind.Relative) });
                RunOnDispatcher();
            }
            catch (Exception error) { failure = error; }
        });
        thread.SetApartmentState(ApartmentState.STA);
        thread.Start();
        thread.Join();
        if (failure != null) throw new Exception("Issue 469 / 471-1 / 429-2 checks failed", failure);
        Console.WriteLine("PASS: project settings guard unsaved edits on leave/refresh; cancelled saves report unknown outcome; settings refresh preserves drafts");
    }

    private static void RunOnDispatcher()
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
        timeout.Tick += (_, _) => { failure = new TimeoutException("issue-469 checks timed out"); frame.Continue = false; };
        timeout.Start();
        Dispatcher.CurrentDispatcher.BeginInvoke(new Action(async () =>
        {
            try
            {
                await ProjectSettingsLeaveAndRefreshChecks();
                await ProjectSettingsCancelledSaveChecks();
                await SettingsRefreshDraftChecks();
            }
            catch (Exception error) { failure = error; }
            finally { frame.Continue = false; }
        }));
        Dispatcher.PushFrame(frame);
        timeout.Stop();
        if (failure != null) throw failure;
    }

    // ── #469: 脏表单必须触发离开确认；干净表单必须直接放行；F5（RefreshAsync
    //    不经过 ConfirmLeaveAsync）在脏时跳过重载，弃稿后恢复重载。──
    private static async Task ProjectSettingsLeaveAndRefreshChecks()
    {
        var fixture = new ProjectFixture();
        using var api = new ApiClient("http://127.0.0.1:12345", fixture);
        var state = new WorkspaceState();
        var view = new ProjectSettingsView();
        view.Activate(Context(api, state));
        try
        {
            await UntilTree(view, () => Concurrency(view) != null);
            var concurrency = Concurrency(view)!;
            Require(concurrency.Text == "2", "fixture 并发应加载为 2（检查前置失败）");

            // 干净表单：离开确认不得触发（测试缝只在脏时被咨询）。
            var prompts = 0;
            view.LeaveConfirmOverride = () => { prompts++; return Task.FromResult(false); };
            Require(await view.ConfirmLeaveAsync() && prompts == 0, "干净的项目设置表单不得弹出离开确认");

            // 真实手势改脏（issue 复现路径：任务并发 2→8）。
            concurrency.Text = "8";
            Require(!await view.ConfirmLeaveAsync() && prompts == 1, "脏表单必须咨询离开确认并尊重拒绝");
            Require(!await view.ConfirmLeaveAsync() && prompts == 2, "被拒绝的离开必须保住草稿（仍脏）");

            // F5：MainWindow.Refresh 直接调 RefreshAsync——脏时不得重载，编辑保留。
            var reads = fixture.ProjectGets;
            await view.RefreshAsync();
            await Task.Delay(40);
            Require(fixture.ProjectGets == reads, "脏状态下的刷新不得重新拉取项目设置");
            Require(Concurrency(view)?.Text == "8", "脏状态下的刷新不得清掉已输入的并发值");

            // 同意离开即弃稿：之后刷新恢复重载语义（守卫不是一刀切 no-op）。
            view.LeaveConfirmOverride = () => Task.FromResult(true);
            Require(await view.ConfirmLeaveAsync(), "同意离开必须返回 true");
            reads = fixture.ProjectGets;
            await view.RefreshAsync();
            await Until(() => fixture.ProjectGets > reads);
            await UntilTree(view, () => Concurrency(view)?.Text == "2");
            Require(Concurrency(view)!.Text == "2", "弃稿后的刷新必须重读服务端值");
        }
        finally { view.Deactivate(); }
    }

    // ── #471-1: PATCH 在途时导航离开（Deactivate 取消 lifetime 令牌）——不得
    //    沉默：必须给出「提交结果未知，请刷新后确认」反馈。──
    private static async Task ProjectSettingsCancelledSaveChecks()
    {
        var fixture = new ProjectFixture { HoldPatch = true };
        using var api = new ApiClient("http://127.0.0.1:12345", fixture);
        var state = new WorkspaceState();
        var view = new ProjectSettingsView();
        view.Activate(Context(api, state));
        try
        {
            await UntilTree(view, () => Concurrency(view) != null);
            Concurrency(view)!.Text = "8";
            SaveButton(view).RaiseEvent(new RoutedEventArgs(ButtonBase.ClickEvent));
            await Until(() => fixture.PatchEntered > 0);
            view.Deactivate();   // 复现缺陷场景：保存请求在途时切换页面
            await Until(() => state.Status.Contains("提交结果未知"));
            var error = Field<TextBlock>(view, "saveError");
            Require(error.Visibility == Visibility.Visible && error.Text.Contains("提交结果未知"),
                "被取消的保存必须显示「提交结果未知」反馈而不是沉默");
            Require(Field<Border>(view, "saveSuccess").Visibility == Visibility.Collapsed,
                "被取消的保存不得显示成功横幅");
            Require(Field<int>(view, "version") == 3,
                "被取消的保存不得推进本地 version（这正是重存会 409 的机制）");
        }
        finally { view.Deactivate(); }
    }

    // ── #429-2: 全局设置页——连接面板录入到一半的 API Key 与运行参数编辑，
    //    刷新后必须原样保留；干净视图的刷新必须照常重载。──
    private static async Task SettingsRefreshDraftChecks()
    {
        var fixture = new SettingsFixture();
        using var api = new ApiClient("http://127.0.0.1:12345", fixture);
        var view = new SettingsView();
        view.Activate(Context(api, new WorkspaceState()));
        try
        {
            await UntilTree(view, () => Password(view) != null && RuntimeConcurrency(view) != null);

            // 对照组：干净刷新确实重载（证明守卫只作用于草稿，而非一律跳过）。
            var reads = fixture.ProvidersGets;
            await view.RefreshAsync();
            await Until(() => fixture.ProvidersGets > reads);
            await UntilTree(view, () => Password(view) != null);

            var password = Password(view)!;
            password.Password = "sk-half-typed";
            var concurrency = RuntimeConcurrency(view)!;
            concurrency.Text = "5";
            var queue = QueueMode(view)!;
            queue.SelectedIndex = queue.Items.OfType<ComboBoxItem>().ToList().FindIndex(item => (string?)item.Tag == "LOCAL");
            Require(((queue.SelectedItem as ComboBoxItem)?.Tag as string) == "LOCAL", "队列模式应已切到 LOCAL（检查前置失败）");

            reads = fixture.ProvidersGets;
            var runtimeReads = fixture.RuntimeGets;
            await view.RefreshAsync();
            await Task.Delay(60);
            Require(fixture.ProvidersGets == reads && fixture.RuntimeGets == runtimeReads,
                "有草稿时的刷新不得重新拉取供应商/运行设置");
            var current = Password(view)!;
            Require(ReferenceEquals(password, current) && current.Password == "sk-half-typed",
                "刷新不得清掉录入到一半的 API Key（面板未重建）");
            Require(RuntimeConcurrency(view)!.Text == "5", "刷新不得清掉已编辑的默认并发");
            Require(((QueueMode(view)!.SelectedItem as ComboBoxItem)?.Tag as string) == "LOCAL",
                "刷新不得清掉已切换的队列模式");
        }
        finally { view.Deactivate(); }
    }

    // ============ 夹具与工具 ============

    private static WorkspaceContext Context(ApiClient api, WorkspaceState state) => new()
    {
        Api = api, Cache = new ApiCache(), State = state, Window = null!,
        Project = new ProjectItem("p1", "雨夜来信", "", 0, 0),
        NavigateSection = (_, _) => Task.CompletedTask, OpenDashboard = () => Task.CompletedTask,
    };

    private static TextBox? Concurrency(FrameworkElement view) =>
        Descendants(view).OfType<TextBox>().FirstOrDefault(t => System.Windows.Automation.AutomationProperties.GetName(t) == "任务并发");
    private static Button SaveButton(FrameworkElement view) =>
        Descendants(view).OfType<Button>().Single(b => Equals(b.Content, "保存项目设置"));
    private static PasswordBox? Password(FrameworkElement view) =>
        Descendants(view).OfType<PasswordBox>().FirstOrDefault();
    private static TextBox? RuntimeConcurrency(FrameworkElement view) =>
        Descendants(view).OfType<TextBox>().FirstOrDefault(t => (string?)t.Tag == "default_concurrency");
    private static ComboBox? QueueMode(FrameworkElement view) =>
        Descendants(view).OfType<ComboBox>().FirstOrDefault(c => (string?)c.Tag == "queue_mode");

    private static IEnumerable<DependencyObject> Descendants(DependencyObject root) => NativeParityChecks.Descendants(root);
    private static T Field<T>(object value, string name) => (T)value.GetType().GetField(name, BindingFlags.Instance | BindingFlags.NonPublic)!.GetValue(value)!;
    private static void Require(bool value, string message) { if (!value) throw new Exception(message); }
    private static void Layout(FrameworkElement view, int width, int height) { view.Measure(new Size(width, height)); view.Arrange(new Rect(0, 0, width, height)); view.UpdateLayout(); }

    private static async Task Until(Func<bool> condition)
    {
        using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(5));
        while (!condition()) await Task.Delay(10, timeout.Token);
    }

    // 可视树要布局后才完整（ScrollViewer/UserControl 走模板展开）；每次先 Layout 再判定。
    private static async Task UntilTree(FrameworkElement view, Func<bool> condition)
    {
        var deadline = DateTime.UtcNow + TimeSpan.FromSeconds(5);
        while (true)
        {
            Layout(view, 1440, 1000);
            if (condition()) return;
            if (DateTime.UtcNow > deadline) throw new Exception("等待视图渲染超时");
            await Task.Delay(15);
        }
    }

    private static HttpResponseMessage Json(string body) => new(HttpStatusCode.OK) { Content = new StringContent(body) };

    private sealed class ProjectFixture : HttpMessageHandler
    {
        public int ProjectGets;
        public int PatchEntered;
        public bool HoldPatch;
        protected override async Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken token)
        {
            var path = request.RequestUri!.AbsolutePath;
            if (request.Method == HttpMethod.Patch && path.EndsWith("/projects/p1"))
            {
                PatchEntered++;
                // HoldPatch 时挂起到令牌取消：Deactivate 取消 lifetime 后沿真实路径
                // 抛 OperationCanceledException（ApiClient 不改写调用方取消）。
                if (HoldPatch) await Task.Delay(Timeout.Infinite, token);
                return Json("""{"name":"雨夜来信","version":4,"workflow_mode":"SEMI_AUTO","draft_resolution":"1K","default_resolution":"2K","default_concurrency":8,"consistency_check_enabled":true}""");
            }
            if (path.EndsWith("/projects/p1")) { ProjectGets++; return Json("""{"name":"雨夜来信","version":3,"workflow_mode":"SEMI_AUTO","draft_resolution":"1K","default_resolution":"2K","default_concurrency":2,"consistency_check_enabled":true}"""); }
            if (path.EndsWith("/models")) return Json("[]");
            throw new Exception("Unexpected project-settings request: " + path);
        }
    }

    private sealed class SettingsFixture : HttpMessageHandler
    {
        public int ProvidersGets, RuntimeGets;
        protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken token)
        {
            var path = request.RequestUri!.AbsolutePath;
            if (path.EndsWith("/providers")) { ProvidersGets++; return Task.FromResult(Json("""[{"id":"prov-1","name":"测试供应商","preset_key":"test","category":"CUSTOM","risk_label":"LOW","description":"","enabled":true,"connections":[{"id":"conn-1","name":"默认连接","protocol":"OPENAI","base_url":"http://127.0.0.1:9","health_state":"HEALTHY","credential_source":"API_KEY","credential_writable":true,"configured":true,"enabled":true,"key_count":0,"model_count":0,"latency_ms":0,"keys":[],"supported_model_types":["TEXT"],"supports_model_discovery":false,"supports_balance":false}]}]""")); }
            if (path.EndsWith("/models")) return Task.FromResult(Json("[]"));
            if (path.EndsWith("/settings/runtime")) { RuntimeGets++; return Task.FromResult(Json("""{"version":9,"queue_mode":"AUTO","job_timeout_seconds":900,"job_lease_seconds":300,"default_concurrency":2,"max_auto_repairs":2,"health_check_interval_seconds":300,"ui_poll_interval_seconds":3000,"database_backend":"SQLITE","storage_root":"D:\\data","upload_root":"D:\\upload"}""")); }
            if (path.EndsWith("/settings/diagnostics")) return Task.FromResult(Json("""{"queue":{"actual_executor":"LOCAL"},"checked_at":null,"checks":[]}"""));
            if (path.EndsWith("/connections/conn-1/models")) return Task.FromResult(Json("[]"));
            throw new Exception("Unexpected settings request: " + path);
        }
    }
}
