using System.Net;
using System.Net.Http;
using System.Reflection;
using System.IO;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Controls.Primitives;
using System.Windows.Media;
using System.Windows.Threading;
using MangaFlow.Native;
using MangaFlow.Native.Controls;
using MangaFlow.Native.Services;
using MangaFlow.Native.Views;

// #470 / #471-2 / #471-3 的回归检查（需 STA + Dispatcher 泵，自带启动引导）：
// - #470：导航取消创建 POST 后必须浮出「提交结果未知…请刷新确认」反馈并
//   失效 dashboard/projects 缓存——否则服务端可能已建项却无任何提示，
//   引导用户重复提交造成重复项目；
// - #471-2：创建期间点取消会先合上抽屉，之后到达的 400/409 若只写进
//   已折叠抽屉里的 drawerError，用户在任何地方都看不到——抽屉关闭时
//   失败必须改道 State.Status；
// - #471-3：F5 刷新在本地服务已停止时不得静默吞错留下陈旧指标——
//   需像 ConnectAsync 一样设置 Error/Status 给出重试提示。
//
// 【需 lead 注册】本文件按任务约束未接入运行入口：建议在
// NativeInteractionChecks.RunIsolated 的 await 链追加
// `NativeIssue470Checks.Run();`——Run 内部自带 STA 调度，也可独立调用
// （无 Application 时会自建 STA 线程 + Application/Theme）。
internal static class NativeIssue470Checks
{
    public static void Run()
    {
        var previous = (string)typeof(KeyValueStore).GetField("path", BindingFlags.Static | BindingFlags.NonPublic)!.GetValue(null)!;
        var prefs = Path.Combine(Path.GetTempPath(), "mangaflow-issue470-prefs-" + Guid.NewGuid().ToString("N") + ".json");
        File.WriteAllText(prefs, "{}");
        KeyValueStore.UseLocation(prefs);
        try
        {
            if (Application.Current is null) RunOnDedicatedStaThread();
            else RunFrame();
        }
        finally
        {
            KeyValueStore.UseLocation(previous);
            File.Delete(prefs);
            File.Delete(prefs + ".tmp");
        }
    }

    // 独立运行（进程里还没有 Application）：自建 STA 线程 + Application/Theme
    //（NativeStoryboardEditChecks 的模式）。
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
        if (failure != null) throw new Exception("Issue 470 checks failed", failure);
    }

    // STA + DispatcherFrame 泵：async-void 的按钮处理经 DispatcherSynchronizationContext
    // 回到泵上执行；UnhandledException 钩子把视图内的逃逸异常变成检查失败。
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
        timeout.Tick += (_, _) => { failure = new TimeoutException("Issue 470 checks timed out"); frame.Continue = false; };
        timeout.Start();
        SynchronizationContext.SetSynchronizationContext(new DispatcherSynchronizationContext());
        Dispatcher.CurrentDispatcher.BeginInvoke(new Action(async () =>
        {
            try
            {
                await CancelledCreateSurfacesUnknownOutcome();
                await ClosedDrawerFailureReachesStatus();
                await OpenDrawerFailureStaysInline();
                await F5SurfacesDashboardFailure();
            }
            catch (Exception error) { failure = error; }
            finally { frame.Continue = false; }
        }));
        Dispatcher.PushFrame(frame);
        timeout.Stop();
        if (failure != null) throw new Exception("Issue 470 checks failed", failure);
        Console.WriteLine("PASS: #470 cancelled create surfaces unknown-outcome guidance and invalidates caches; #471-2 closed-drawer failures reach the status line; #471-3 F5 failures surface an error and retry cue");
    }

    // #470：POST 在途时 Deactivate 取消令牌（服务端可能已建项）。
    private static async Task CancelledCreateSurfacesUnknownOutcome()
    {
        var cache = new ApiCache();
        var invalidated = new List<string>();
        cache.Invalidated += prefix => invalidated.Add(prefix);
        var state = new WorkspaceState();
        using var api = new ApiClient("http://127.0.0.1:12345", new Handler(async (_, token) =>
        {
            await Task.Delay(Timeout.Infinite, token);
            return new HttpResponseMessage(HttpStatusCode.OK);
        }));
        var view = new HomeView();
        SetContext(view, api, cache, state);
        try
        {
            Field<TextBox>(view, "nameInput").Text = "取消风暴测试";
            Field<Button>(view, "createButton").RaiseEvent(new RoutedEventArgs(ButtonBase.ClickEvent));
            view.Deactivate();
            await Until(() => state.Error.Length > 0);
            Require(state.Error.Contains("提交结果未知") && state.Error.Contains("刷新") && state.Error.Contains("避免重复提交"),
                "被取消的创建必须浮出与外壳一致的「提交结果未知」提示");
            Require(state.Status.Contains("刷新"), "被取消的创建必须在状态行留下刷新提示");
            Require(invalidated.Contains("dashboard") && invalidated.Contains("projects"),
                "被取消的创建必须失效 dashboard/projects 缓存");
        }
        finally { view.Deactivate(); }
    }

    // #471-2：创建期间点「取消」合上抽屉，随后的 409 不能只写进已折叠的抽屉。
    private static async Task ClosedDrawerFailureReachesStatus()
    {
        var state = new WorkspaceState();
        var pending = new TaskCompletionSource<HttpResponseMessage>();
        using var api = new ApiClient("http://127.0.0.1:12345", new Handler((_, _) => pending.Task));
        var view = new HomeView();
        SetContext(view, api, new ApiCache(), state);
        try
        {
            view.OpenCreationDrawer();
            Field<TextBox>(view, "nameInput").Text = "抽屉已关测试";
            Field<Button>(view, "createButton").RaiseEvent(new RoutedEventArgs(ButtonBase.ClickEvent));
            // DrawerOverlay 会把业务内容搬进私有 surface 面板（Content 只剩 chrome）：
            // surface.Child → ScrollViewer → Content 即 BuildDrawer 的元素树，直接走
            // 元素树找「取消」，不依赖模板应用或布局时序。
            var drawer = Field<DrawerOverlay>(view, "drawer");
            var surface = (Border)typeof(DrawerOverlay).GetField("surface", BindingFlags.Instance | BindingFlags.NonPublic)!.GetValue(drawer)!;
            var drawerContent = (FrameworkElement)((ScrollViewer)surface.Child).Content;
            var cancel = Descendants(drawerContent).OfType<Button>().Single(b => Equals(b.Content, "取消"));
            cancel.RaiseEvent(new RoutedEventArgs(ButtonBase.ClickEvent));
            Require(!Field<DrawerOverlay>(view, "drawer").Open, "取消没有合上抽屉");
            pending.SetResult(new HttpResponseMessage(HttpStatusCode.Conflict)
                { Content = new StringContent("{\"detail\":\"同名项目已存在\"}") });
            await Until(() => state.Status.Contains("创建项目失败"));
            Require(state.Status.Contains("创建项目失败"), "抽屉已关闭时的失败必须改道到外壳状态行");
            Require(Field<TextBlock>(view, "drawerError").Text.Length == 0,
                "抽屉已关闭时不得只把错误写进不可见的 drawerError");
        }
        finally { view.Deactivate(); }
    }

    // #471-2 对照组：抽屉仍打开时失败继续内联在 drawerError（不回归既有行为）。
    private static async Task OpenDrawerFailureStaysInline()
    {
        var state = new WorkspaceState();
        var pending = new TaskCompletionSource<HttpResponseMessage>();
        using var api = new ApiClient("http://127.0.0.1:12345", new Handler((_, _) => pending.Task));
        var view = new HomeView();
        SetContext(view, api, new ApiCache(), state);
        try
        {
            view.OpenCreationDrawer();
            Field<TextBox>(view, "nameInput").Text = "抽屉打开测试";
            Field<Button>(view, "createButton").RaiseEvent(new RoutedEventArgs(ButtonBase.ClickEvent));
            pending.SetResult(new HttpResponseMessage(HttpStatusCode.Conflict)
                { Content = new StringContent("{\"detail\":\"同名项目已存在\"}") });
            await Until(() => Field<TextBlock>(view, "drawerError").Text.Length > 0);
            Require(Field<TextBlock>(view, "drawerError").Text.Contains("同名项目已存在"),
                "抽屉打开时的失败必须继续内联展示服务端详情");
        }
        finally { view.Deactivate(); }
    }

    // #471-3：本地服务已停止时 F5 不得静默吞错（陈旧指标 + 无重试提示）。
    private static async Task F5SurfacesDashboardFailure()
    {
        var dataRoot = Path.Combine(Path.GetTempPath(), "mangaflow-issue471-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(dataRoot);
        using var api = new ApiClient("http://127.0.0.1:12345",
            new Handler((_, _) => throw new HttpRequestException("本地服务未响应")));
        var shellType = typeof(MainWindow);
        var shell = new MainWindow("", dataRoot);
        try
        {
            var state = (WorkspaceState)shell.DataContext;
            shellType.GetField("api", BindingFlags.Instance | BindingFlags.NonPublic)!.SetValue(shell, api);
            shellType.GetField("page", BindingFlags.Instance | BindingFlags.NonPublic)!.SetValue(shell, "home");
            state.Error = "";
            await Settle();  // 让构造期导航完全静止
            shellType.GetMethod("Refresh", BindingFlags.Instance | BindingFlags.NonPublic)!
                .Invoke(shell, [shell, new RoutedEventArgs()]);
            await Until(() => state.Error.Length > 0);
            Require(state.Error.Contains("无法连接本地服务"), "F5 命中已停止的后端必须浮出连接错误");
            Require(state.Status.Contains("刷新失败"), "F5 失败必须在状态行留下重试提示");
        }
        finally
        {
            ((CancellationTokenSource)shellType.GetField("lifetime", BindingFlags.Instance | BindingFlags.NonPublic)!
                .GetValue(shell)!).Cancel();
            Directory.Delete(dataRoot, true);
        }
    }

    // ── helpers（NativeStoryboardEditChecks 同款）──
    private static async Task Until(Func<bool> condition)
    {
        using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(5));
        while (!condition()) await Task.Delay(5, timeout.Token);
    }

    private static async Task Settle() => await Task.Delay(30);

    private static void Require(bool condition, string message)
    {
        if (!condition) throw new Exception(message);
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

    private static T Field<T>(object value, string name) =>
        (T)value.GetType().GetField(name, BindingFlags.Instance | BindingFlags.NonPublic)!.GetValue(value)!;

    private static void SetContext(WorkspaceView view, ApiClient api, ApiCache cache, WorkspaceState state) =>
        typeof(WorkspaceView).GetProperty("Context", BindingFlags.Instance | BindingFlags.NonPublic)!
            .SetValue(view, new WorkspaceContext
            {
                Api = api, Cache = cache, State = state, Window = null!,
                NavigateSection = (_, _) => Task.CompletedTask, OpenDashboard = () => Task.CompletedTask,
            });

    private sealed class Handler(Func<HttpRequestMessage, CancellationToken, Task<HttpResponseMessage>> respond) : HttpMessageHandler
    {
        protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken cancellationToken) =>
            respond(request, cancellationToken);
    }
}
