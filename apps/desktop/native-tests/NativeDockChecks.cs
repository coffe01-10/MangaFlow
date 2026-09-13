using System.Net;
using System.Net.Http;
using System.Reflection;
using System.Runtime.CompilerServices;
using System.Threading;
using System.IO;
using System.Text.Json;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Controls.Primitives;
using System.Windows.Data;
using System.Windows.Input;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using System.Windows.Threading;
using MangaFlow.Native;
using MangaFlow.Native.Controls;
using MangaFlow.Native.Services;
using MangaFlow.Native.Views;

// NUI-6B regressions: queue dock counts/actions/isolation, collapse state,
// keyboard parity (Enter/Esc/focus restore), help anchors and narrow layout.
internal static class NativeDockChecks
{
    private const string RunningJob = """
        {"id":"dock-running","job_type":"GENERATE_PAGE","status":"GENERATING","progress":55,"attempt_count":1,"max_attempts":3}
        """;
    private const string WaitingJob = """
        {"id":"dock-waiting","job_type":"PARSE_SOURCE","status":"WAITING","progress":0,"attempt_count":0,"max_attempts":3}
        """;
    private const string FailedJob = """
        {"id":"dock-failed","job_type":"GENERATE_PAGE","status":"FAILED","progress":40,"attempt_count":1,"max_attempts":3}
        """;
    private const string CompletedJob = """
        {"id":"dock-done","job_type":"GENERATE_PAGE","status":"COMPLETED","progress":100,"attempt_count":1,"max_attempts":3}
        """;

    public static async Task Run(string output)
    {
        await CountsAndHeadline();
        await LateResponseIsolation();
        await CancelRetryDedup();
        await ShellDockChrome(output);
        await KeyboardAndFocus();
        await HelpPage(output);
        Console.WriteLine("PASS: dock counts/headline, cross-project isolation, cancel/retry dedup, shell collapse/restore, Enter/Esc/focus parity, help anchors and narrow layout");
    }

    private static async Task CountsAndHeadline()
    {
        var state = new WorkspaceState();
        using var api = new ApiClient("http://127.0.0.1:12345", new Handler(_ =>
            Task.FromResult(Response($"[{RunningJob},{WaitingJob},{FailedJob},{CompletedJob}]"))));
        var dock = new DockQueue(api, state);
        state.CurrentProject = new ProjectItem("dock-project", "底栏项目", "", 0, 0);
        await dock.RefreshAsync("dock-project", CancellationToken.None);
        Require(state.DockTotal == 4 && state.RunningJobs == 1 && state.WaitingJobs == 1
            && state.FailedJobs == 1 && state.CompletedJobs == 1, "dock counters diverged from the active-jobs list");
        Require(state.DockJob?.Id == "dock-running" && state.DockStatusText == "生成漫画页面 · 生成中",
            "dock headline lost the latest job");
        Require(state.DockSummary.Contains("共 4 项") && state.DockSummary.Contains("1 运行") && state.DockSummary.Contains("1 完成"),
            "dock summary lost totals or terminal states");
        Require(state.DockCancelShown && !state.DockRetryShown, "dock actions do not follow the latest job state");
        Require(state.DockWaiting, "waiting jobs must light the dock indicator");
        // Empty list: headline falls back to the section-aware idle copy (never both).
        using var idle = new ApiClient("http://127.0.0.1:12345", new Handler(_ => Task.FromResult(Response("[]"))));
        var idleDock = new DockQueue(idle, state);
        state.CurrentSection = "generate";
        await idleDock.RefreshAsync("dock-project", CancellationToken.None);
        Require(state.DockJob == null && state.DockStatusText == "当前没有任务" && !state.DockCountShown,
            "empty dock keeps a stale headline or counters");
        state.CurrentSection = "source";
        Require(state.DockStatusText == "查看生成、解析与检查进度", "idle hint does not follow the section");
    }

    private static async Task LateResponseIsolation()
    {
        var state = new WorkspaceState();
        var requests = new List<(HttpRequestMessage Request, TaskCompletionSource<HttpResponseMessage> Reply)>();
        using var api = new ApiClient("http://127.0.0.1:12345", new Handler(request =>
        {
            var reply = new TaskCompletionSource<HttpResponseMessage>(TaskCreationOptions.RunContinuationsAsynchronously);
            requests.Add((request, reply));
            return reply.Task;
        }));
        var dock = new DockQueue(api, state);
        state.CurrentProject = new ProjectItem("project-a", "A", "", 0, 0);
        dock.Reset();
        var refresh = dock.RefreshAsync("project-a", CancellationToken.None);
        // The user switches projects while project A's list is still in flight.
        state.CurrentProject = new ProjectItem("project-b", "B", "", 0, 0);
        dock.Reset();
        requests.Single(r => r.Request.RequestUri!.AbsolutePath.EndsWith("/jobs")).Reply
            .SetResult(Response($"[{RunningJob}]"));
        await refresh;
        Require(state.DockJob == null && state.DockTotal == 0 && state.RunningJobs == 0,
            "late response from the previous project repainted the dock");
        // A refresh for the current project still lands normally afterwards.
        using var fresh = new ApiClient("http://127.0.0.1:12345", new Handler(_ => Task.FromResult(Response($"[{FailedJob}]"))));
        var next = new DockQueue(fresh, state);
        await next.RefreshAsync("project-b", CancellationToken.None);
        Require(state.DockJob?.Id == "dock-failed" && state.DockRetryShown, "post-switch refresh cannot land");
        // A mutation for the switched-away project must not paint a notice on the new dock.
        var postCount = 0;
        var dispatchA = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
        var replyA = new TaskCompletionSource<HttpResponseMessage>(TaskCreationOptions.RunContinuationsAsynchronously);
        var dispatchB = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
        var replyB = new TaskCompletionSource<HttpResponseMessage>(TaskCreationOptions.RunContinuationsAsynchronously);
        using var actionApi = new ApiClient("http://127.0.0.1:12345", new Handler(request =>
        {
            if (request.Method != HttpMethod.Post) return Task.FromResult(Response($"[{FailedJob}]"));
            if (Interlocked.Increment(ref postCount) == 1)
            {
                Require(request.RequestUri!.AbsolutePath.EndsWith("/jobs/dock-failed/cancel"), "dock cancel hit an unexpected endpoint");
                dispatchA.SetResult();
                return replyA.Task;
            }
            dispatchB.SetResult();
            return replyB.Task;
        }));
        var actionDock = new DockQueue(actionApi, state);
        state.CurrentProject = new ProjectItem("project-a", "A", "", 0, 0);
        await actionDock.RefreshAsync("project-a", CancellationToken.None);
        var lateAction = actionDock.ActAsync(state.DockJob!, "cancel", "project-a", CancellationToken.None);
        await dispatchA.Task.WaitAsync(TimeSpan.FromSeconds(3));
        state.CurrentProject = new ProjectItem("project-b", "B", "", 0, 0);
        actionDock.Reset();
        replyA.SetResult(Response("{}"));
        await lateAction;
        Require(state.DockNotice.Length == 0, "late mutation notice painted the new project's dock");
        // Abandoned instances stop touching shared state: an in-flight action's cleanup
        // must not clear the pending flag the replacement dock now owns.
        var stale = JobItem.From(JsonSerializer.Deserialize<JsonElement>(FailedJob));
        state.DockActionPending = false;
        var inflight = actionDock.ActAsync(stale, "cancel", "project-b", CancellationToken.None);
        await dispatchB.Task.WaitAsync(TimeSpan.FromSeconds(3));
        actionDock.Abandon();
        replyB.SetResult(Response("{}"));
        await inflight;
        Require(state.DockActionPending, "abandoned dock queue cleared the shared pending flag");
        state.DockActionPending = false;
        var dispatched = postCount;
        await actionDock.ActAsync(stale, "retry", "project-b", CancellationToken.None);
        Require(postCount == dispatched, "abandoned dock queue accepted a new mutation");
    }

    private static async Task CancelRetryDedup()
    {
        var posts = 0;
        var release = new TaskCompletionSource<HttpResponseMessage>(TaskCreationOptions.RunContinuationsAsynchronously);
        using var api = new ApiClient("http://127.0.0.1:12345", new Handler(request =>
        {
            if (request.Method == HttpMethod.Post) { posts++; return release.Task; }
            return Task.FromResult(Response($"[{RunningJob}]"));
        }));
        var state = new WorkspaceState { CurrentProject = new ProjectItem("dock-project", "底栏项目", "", 0, 0) };
        var dock = new DockQueue(api, state);
        await dock.RefreshAsync("dock-project", CancellationToken.None);
        Require(state.DockCancelShown, "cancelable latest job did not expose the dock cancel action");
        var job = JobItem.From(JsonSerializer.Deserialize<JsonElement>(RunningJob));
        var cancel = dock.ActAsync(job, "cancel", "dock-project", CancellationToken.None);
        await dock.ActAsync(job, "cancel", "dock-project", CancellationToken.None);
        Require(posts == 1 && state.DockActionPending && !state.DockCancelShown && !state.DockRetryShown,
            "dock mutation not deduplicated or not disabled while pending");
        release.SetResult(Response("{\"detail\":\"任务状态已变化，请刷新\"}", HttpStatusCode.Conflict));
        await cancel;
        Require(posts == 1, "failed dock mutation auto-retried");
        Require(state.DockNotice.Contains("取消任务失败") && state.DockNotice.Contains("任务状态已变化"),
            "dock failure notice lost the server detail");
        Require(state.DockCancelShown, "failed dock mutation never re-enabled the action");
        // Success path: notice flips, the dock refreshes from the follow-up list read.
        release = new TaskCompletionSource<HttpResponseMessage>(TaskCreationOptions.RunContinuationsAsynchronously);
        var retried = 0;
        using var retryApi = new ApiClient("http://127.0.0.1:12345", new Handler(request =>
        {
            if (request.Method == HttpMethod.Post)
            {
                retried++;
                return Task.FromResult(Response("{}"));
            }
            return Task.FromResult(Response($"[{WaitingJob}]"));
        }));
        var retryDock = new DockQueue(retryApi, state);
        var failed = JobItem.From(JsonSerializer.Deserialize<JsonElement>(FailedJob));
        await retryDock.ActAsync(failed, "retry", "dock-project", CancellationToken.None);
        Require(retried == 1 && state.DockJob?.Id == "dock-waiting",
            "successful dock retry did not refresh the dock");
        Require(state.DockNotice.Length == 0, "stale success notice survived the post-action refresh");
        // Failure notices stay visible until the user's next action, even across polls.
        state.DockNotice = "取消任务失败：任务状态已变化，请刷新";
        await retryDock.RefreshAsync("dock-project", CancellationToken.None);
        Require(state.DockNotice.Contains("任务状态已变化"), "failure notice was cleared by a data refresh");
    }

    private static async Task ShellDockChrome(string output)
    {
        var root = Path.Combine(Path.GetTempPath(), "mangaflow-dock-shell-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(root);
        try
        {
            var shell = new MainWindow("", root);
            var state = (WorkspaceState)shell.DataContext;
            var host = (FrameworkElement)shell.Content;
            Require(state.IsWorkspace == false && !state.DockShown && !state.DockRestoreShown,
                "dock must stay hidden without a project context");
            state.IsWorkspace = true;
            Require(state.DockShown && !state.DockRestoreShown, "expanded dock not shown in workspace pages");
            state.DockHidden = true;
            Require(!state.DockShown && state.DockRestoreShown, "hidden dock keeps the bar or loses the restore chip");
            state.DockHidden = false;
            state.DockJob = JobItem.From(JsonSerializer.Deserialize<JsonElement>(FailedJob));
            state.DockNotice = "重试任务失败：任务状态已变化，请刷新";
            Layout(host, 1320, 900);
            var bar = shell.FindName("QueueDock") as FrameworkElement;
            // DockRestore is the button inside the bound restore Border (its parent panel's parent).
            var restore = (FrameworkElement)((FrameworkElement)((FrameworkElement)shell.FindName("DockRestore")).Parent).Parent;
            var cancel = (FrameworkElement)shell.FindName("DockCancel")!;
            var retry = (FrameworkElement)shell.FindName("DockRetry")!;
            // Offscreen trees evaluate bindings lazily; pull each target synchronously.
            foreach (var dock in new[] { bar!, restore!, cancel, retry })
                BindingOperations.GetBindingExpression(dock, UIElement.VisibilityProperty)?.UpdateTarget();
            Require(bar?.Visibility == Visibility.Visible && restore?.Visibility == Visibility.Collapsed,
                $"dock bar visibility diverged from the state (bar={bar?.Visibility}, restore={restore?.Visibility}, shown={state.DockShown}, restoreShown={state.DockRestoreShown})");
            Require(cancel.Visibility == Visibility.Collapsed && retry.Visibility == Visibility.Visible,
                "dock action buttons do not follow CanCancel/CanRetry");
            state.DockActionPending = true;
            Drain();
            Require(retry.Visibility == Visibility.Collapsed, "pending dock action stays clickable");
            state.DockActionPending = false;
            state.DockHidden = true;
            Layout(host, 1320, 900);
            Render(host, Path.Combine(output, "native-dock-restore.png"), 1320, 900);
            // No project selected → the poll tick must not issue any request.
            var shellType = typeof(MainWindow);
            var hideDock = shellType.GetMethod("HideDock", BindingFlags.Instance | BindingFlags.NonPublic);
            Require(hideDock != null, "反射入口缺失：MainWindow.HideDock");
            hideDock!.Invoke(shell, new object?[] { null!, new RoutedEventArgs(System.Windows.Controls.Primitives.ButtonBase.ClickEvent) });
            Require(Preferences.Load(root).DockHidden, "dock hide must persist immediately like localStorage");
            var apiField = shellType.GetField("api", BindingFlags.Instance | BindingFlags.NonPublic);
            var dockField = shellType.GetField("dock", BindingFlags.Instance | BindingFlags.NonPublic);
            var backendField = shellType.GetField("backend", BindingFlags.Instance | BindingFlags.NonPublic);
            var processField = backendField?.FieldType.GetField("process", BindingFlags.Instance | BindingFlags.NonPublic);
            Require(apiField != null && dockField != null && backendField != null && processField != null,
                "反射入口缺失：MainWindow api/dock/backend.process");
            var requests = 0;
            using var api = new ApiClient("http://127.0.0.1:12345", new Handler(_ =>
            {
                requests++;
                return Task.FromResult(Response($"[{CompletedJob}]"));
            }));
            state.Connected = true;
            state.CurrentProject = null;
            apiField!.SetValue(shell, api);
            dockField!.SetValue(shell, new DockQueue(api, state));
            shellType.GetMethod("PollDock", BindingFlags.Instance | BindingFlags.NonPublic)!.Invoke(shell, new object?[] { null, EventArgs.Empty });
            Require(requests == 0, "dock polled without a project (background pages must stay silent)");
            state.CurrentProject = new ProjectItem("shell-project", "S", "", 0, 0);
            // With the backend process down the poll must stay silent too (degraded path).
            shellType.GetMethod("PollDock", BindingFlags.Instance | BindingFlags.NonPublic)!.Invoke(shell, new object?[] { null, EventArgs.Empty });
            Require(requests == 0, "dock polled while the backend process was down");
            // PollDock gates on a live backend process; stand one in without spawning a child.
            var backend = backendField!.GetValue(shell)!;
            processField!.SetValue(backend, System.Diagnostics.Process.GetCurrentProcess());
            shellType.GetMethod("PollDock", BindingFlags.Instance | BindingFlags.NonPublic)!.Invoke(shell, new object?[] { null, EventArgs.Empty });
            Require(requests == 1, "dock poll did not fire with a project");
            await Until(() => state.DockTotal == 1 && state.CompletedJobs == 1);
        }
        finally { Directory.Delete(root, true); }
    }

    private static async Task KeyboardAndFocus()
    {
        // The Enter class handler lives on App's static constructor; force it for this harness.
        RuntimeHelpers.RunClassConstructor(typeof(App).TypeHandle);
        var owner = OffscreenWindow();
        var trigger = new Button { Content = "触发弹窗" };
        var pill = new ToggleButton { Content = "筛选", Style = (Style)Application.Current.FindResource("Pill") };
        var panel = new StackPanel();
        panel.Children.Add(trigger);
        panel.Children.Add(pill);
        owner.Content = panel;
        owner.Show();
        trigger.Focus();
        Require(Keyboard.FocusedElement == trigger, "test window never received focus");

        // Enter activates a focused button exactly like the web (Space is the WPF default).
        var enterClicks = 0;
        trigger.Click += (_, _) => enterClicks++;
        Press(trigger, Key.Enter);
        Require(enterClicks == 1, "Enter did not activate the focused button");
        pill.Focus();
        Press(pill, Key.Enter);
        Require(pill.IsChecked == true, "Enter did not toggle a filter control");
        pill.Focus();
        Press(pill, Key.Enter);
        Require(pill.IsChecked == false, "Enter cannot toggle a filter control off");

        // Held-down Enter (key repeat) must not machine-gun clicks on any control.
        if (RepeatStateAvailable())
        {
            var held = new Button { Content = "长按" };
            panel.Children.Add(held);
            var heldClicks = 0;
            held.Click += (_, _) => heldClicks++;
            held.Focus();
            Press(held, Key.Enter, isRepeat: true);
            Press(held, Key.Enter, isRepeat: true);
            Require(heldClicks == 0, "key-repeat Enter still fires clicks");
        }
        else Console.WriteLine("SKIP: KeyEventArgs repeat state not settable; Enter repeat guard not exercised");

        // InputDialog: Enter on its focused confirm button confirms exactly once (the
        // window-level handler owns the key; the class handler must not double-fire).
        var input = new InputDialog(owner, "重命名项目", "项目名称", preset: "原名");
        input.Loaded += (_, _) => Dispatcher.CurrentDispatcher.BeginInvoke(() =>
        {
            var confirm = Descendants(input).OfType<Button>().Single(b => b.Content?.ToString() == "确定");
            confirm.Focus();
            Press(confirm, Key.Enter);
        }, DispatcherPriority.Input);
        var inputOutcome = await ShowDialogAsync(input);
        Require(inputOutcome == true && input.Value == "原名", "Enter in the input dialog must confirm exactly once");

        // Modal dialog: Esc cancels and focus returns to the trigger button.
        trigger.Focus();
        var dialog = new ConfirmDialog(owner, "重试任务", "重试可能再次调用模型并产生费用。", "重试");
        dialog.Loaded += (_, _) => Dispatcher.CurrentDispatcher.BeginInvoke(
            () => Press(dialog, Key.Escape), DispatcherPriority.Input);
        var outcome = await ShowDialogAsync(dialog);
        Require(outcome == false, "Esc did not cancel the modal dialog");
        await Until(() => Keyboard.FocusedElement == trigger);
        Require(Keyboard.FocusedElement == trigger, "dialog close did not restore focus to the trigger button");

        // Drawer: Esc closes it, focus returns to the element that opened it, and a closed
        // drawer can no longer take focus (hidden surfaces must not answer the keyboard).
        var opener = new Button { Content = "打开抽屉" };
        var field = new TextBox { Text = "抽屉内容" };
        var drawer = new DrawerOverlay { Content = field };
        var grid = new Grid();
        grid.Children.Add(opener);
        grid.Children.Add(drawer);
        owner.Content = grid;
        // Reparenting clears visibility until layout runs; pump once so the opener can take focus.
        Drain();
        opener.Focus();
        drawer.Open = true;
        await Until(() => Keyboard.FocusedElement == field);
        Require(Keyboard.FocusedElement == field, "drawer opening did not move focus into the surface");
        var surface = (FrameworkElement)typeof(DrawerOverlay).GetField("surface", BindingFlags.Instance | BindingFlags.NonPublic)!.GetValue(drawer)!;
        Require(KeyboardNavigation.GetTabNavigation(surface) == KeyboardNavigationMode.Cycle,
            "drawer does not cycle Tab inside its surface");
        var closedByEsc = false;
        drawer.ClosedByUser += (_, _) => closedByEsc = true;
        Press(drawer, Key.Escape);
        await Until(() => !drawer.Open);
        Require(closedByEsc, "Esc did not close the drawer");
        try { await Until(() => Keyboard.FocusedElement == opener); }
        catch (Exception)
        {
            throw new Exception($"drawer close did not return focus to its opener (now={Keyboard.FocusedElement?.GetType().Name ?? "null"}, returnFocus={(typeof(DrawerOverlay).GetField("returnFocus", BindingFlags.Instance | BindingFlags.NonPublic)!.GetValue(drawer) as FrameworkElement)?.Name ?? "null"})");
        }
        var reclaimed = field.Focus();
        Require(!reclaimed && Keyboard.FocusedElement == opener, "hidden drawer surface still accepts focus");
        owner.Close();
    }

    private static async Task HelpPage(string output)
    {
        var openedDashboard = false;
        var view = new HelpView();
        typeof(WorkspaceView).GetProperty("Context", BindingFlags.Instance | BindingFlags.NonPublic)!.SetValue(view, new WorkspaceContext
        {
            Api = null!, State = new WorkspaceState(), Window = null!,
            Project = null, NavigateSection = (_, _) => Task.CompletedTask,
            OpenDashboard = () => { openedDashboard = true; return Task.CompletedTask; },
        });
        // The visual tree only generates after layout; measure once before reading text.
        Layout(view, 1320, 900);
        var text = Texts(view);
        Require(text.Contains("项目侧栏就是完整生产路径。每个入口都拥有独立网址，可以刷新、收藏和通过浏览器前进后退。"),
            "help hero copy diverged from the web page");
        Require(text.Contains("供应商连接显示断开时怎么办？") && text.Contains("导出 PNG、PDF 或 JSON"),
            "help lost the troubleshooting or export copy");
        foreach (var width in new[] { 1320, 900, 760, 560 })
        {
            Layout(view, width, 900);
            var stages = Descendants(view).OfType<UniformGrid>().Single(x => x.Name == "HelpStages");
            var expected = width <= 760 ? 1 : width <= 900 ? 2 : 4;
            Require(stages.Columns == expected, $"{width}: stage grid columns diverged from the web breakpoint");
            var heroIcon = Descendants(view).OfType<Viewbox>().Single(x => Math.Abs(x.Width - 110) < 1);
            var staticIcon = Descendants(view).OfType<Viewbox>().Single(x => Math.Abs(x.Width - 42) < 1);
            if (width <= 760)
            {
                Require(heroIcon.Visibility == Visibility.Collapsed && staticIcon.Visibility == Visibility.Visible,
                    $"{width}: hero icon must become the static web glyph");
                Require(stages.Children.Cast<FrameworkElement>().All(c => c.MinHeight == 165),
                    $"{width}: narrow stage cards kept the desktop height");
            }
            else Require(heroIcon.Visibility == Visibility.Visible && staticIcon.Visibility == Visibility.Collapsed,
                $"{width}: desktop hero icon misplaced");
            var troubleshooting = Descendants(view).OfType<Grid>().Single(x => x.Name == "HelpTroubleshooting");
            var stacked = width <= 900;
            Require(troubleshooting.Children.Cast<FrameworkElement>().All(c => Grid.GetColumn(c) == (stacked ? 0 : troubleshooting.Children.IndexOf(c))),
                $"{width}: troubleshooting columns diverged from the web breakpoint");
        }
        // Anchor navigation scrolls to the target section and the back entry leaves the page.
        Layout(view, 1320, 900);
        var scroller = Descendants(view).OfType<ScrollViewer>().First();
        Require(Math.Abs(scroller.VerticalOffset) < 1, "help starts scrolled");
        Descendants(view).OfType<Button>().Single(b => b.Content?.ToString() == "故障排查")
            .RaiseEvent(new RoutedEventArgs(ButtonBase.ClickEvent));
        Drain();
        // The page can be shorter than one viewport plus the section; the real contract is
        // that the anchor target's top edge lands inside the visible viewport.
        var troubleshootTarget = (FrameworkElement)Descendants(view).OfType<Grid>().Single(x => x.Name == "HelpTroubleshooting").Parent;
        var troubleshootTop = troubleshootTarget.TranslatePoint(new Point(), scroller).Y;
        Require(scroller.VerticalOffset > 0 && troubleshootTop >= 0 && troubleshootTop < scroller.ViewportHeight - 40,
            $"anchor jump did not bring troubleshooting into view (offset={scroller.VerticalOffset}, top={troubleshootTop})");
        // Jumping from a NON-zero offset must not double-count the current scroll
        // position. At a 400px viewport the page is tall enough that a correct jump
        // scrolls back UP to the first card; the double-counting formula pinned to bottom.
        Layout(view, 1320, 400);
        scroller = Descendants(view).OfType<ScrollViewer>().First();
        Descendants(view).OfType<Button>().Single(b => b.Content?.ToString() == "故障排查")
            .RaiseEvent(new RoutedEventArgs(ButtonBase.ClickEvent));
        Drain();
        var deepOffset = scroller.VerticalOffset;
        Require(deepOffset > 400, "troubleshooting jump did not scroll deep at the small viewport");
        Descendants(view).OfType<Button>().Single(b => b.Content?.ToString() == "01 导入原作")
            .RaiseEvent(new RoutedEventArgs(ButtonBase.ClickEvent));
        Drain();
        Require(scroller.VerticalOffset < deepOffset - 100,
            $"anchor jump from a scrolled position double-counted the offset (was={deepOffset}, now={scroller.VerticalOffset})");
        Descendants(view).OfType<Button>().Single(b => b.Content?.ToString() == "← 返回项目")
            .RaiseEvent(new RoutedEventArgs(ButtonBase.ClickEvent));
        Drain();
        Require(openedDashboard && view.NavigatedAway, "help back entry did not navigate to the project list");
        Layout(view, 560, 900);
        Render(view, Path.Combine(output, "native-help-560.png"), 560, 900);
    }

    private static Window OffscreenWindow() => new()
    {
        Width = 420, Height = 180, ShowInTaskbar = false, ShowActivated = false,
        WindowStartupLocation = WindowStartupLocation.Manual, Left = -2400, Top = 80,
    };

    private static bool RepeatStateAvailable() =>
        typeof(KeyEventArgs).GetMethod("SetRepeat", BindingFlags.Instance | BindingFlags.NonPublic) != null
        || typeof(KeyEventArgs).GetField("_isRepeat", BindingFlags.Instance | BindingFlags.NonPublic) != null;

    private static void Press(UIElement target, Key key, bool isRepeat = false)
    {
        var source = PresentationSource.FromVisual(target as Visual ?? throw new Exception("key target has no visual"));
        var args = new KeyEventArgs(Keyboard.PrimaryDevice, source, 0, key) { RoutedEvent = UIElement.PreviewKeyDownEvent };
        if (isRepeat)
        {
            typeof(KeyEventArgs).GetMethod("SetRepeat", BindingFlags.Instance | BindingFlags.NonPublic)
                ?.Invoke(args, new object[] { true });
            typeof(KeyEventArgs).GetField("_isRepeat", BindingFlags.Instance | BindingFlags.NonPublic)
                ?.SetValue(args, true);
        }
        target.RaiseEvent(args);
    }

    private static Task<bool?> ShowDialogAsync(Window dialog)
    {
        var completion = new TaskCompletionSource<bool?>(TaskCreationOptions.RunContinuationsAsynchronously);
        Dispatcher.CurrentDispatcher.BeginInvoke(new Action(() => completion.SetResult(dialog.ShowDialog())));
        return completion.Task;
    }

    private static IEnumerable<DependencyObject> Descendants(DependencyObject root)
    {
        yield return root;
        for (var i = 0; i < VisualTreeHelper.GetChildrenCount(root); i++)
            foreach (var child in Descendants(VisualTreeHelper.GetChild(root, i))) yield return child;
    }

    private static string Texts(DependencyObject root) =>
        string.Join("\n", Descendants(root).OfType<TextBlock>().Select(t => t.Text));

    private static void Layout(FrameworkElement view, int width, int height)
    {
        view.Measure(new Size(width, height));
        view.Arrange(new Rect(0, 0, width, height));
        view.UpdateLayout();
        Drain();
        view.UpdateLayout();
    }

    private static void Drain() => Dispatcher.CurrentDispatcher.Invoke(() => { }, DispatcherPriority.ApplicationIdle);

    private static void Render(FrameworkElement element, string path, int width, int height)
    {
        var background = new DrawingVisual();
        using (var drawing = background.RenderOpen())
            drawing.DrawRectangle((Brush)Application.Current.FindResource("Paper"), null, new Rect(0, 0, width, height));
        var bitmap = new RenderTargetBitmap(width, height, 96, 96, PixelFormats.Pbgra32);
        bitmap.Render(background);
        bitmap.Render(element);
        var encoder = new PngBitmapEncoder();
        encoder.Frames.Add(BitmapFrame.Create(bitmap));
        using var file = File.Create(path);
        encoder.Save(file);
    }

    private static async Task Until(Func<bool> condition)
    {
        using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(3));
        while (!condition()) await Task.Delay(5, timeout.Token);
    }

    private static void Require(bool condition, string message)
    {
        if (!condition) throw new Exception(message);
    }

    private static HttpResponseMessage Response(string json, HttpStatusCode status = HttpStatusCode.OK) =>
        new(status) { Content = new StringContent(json) };

    private sealed class Handler(Func<HttpRequestMessage, Task<HttpResponseMessage>> handler) : HttpMessageHandler
    {
        protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken token) => handler(request);
    }
}
