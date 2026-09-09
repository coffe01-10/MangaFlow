using System.ComponentModel;
using System.Diagnostics;
using System.IO;
using System.IO.Compression;
using System.Net.Http;
using System.Windows.Media;
using System.Text.Json;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;
using System.Windows.Threading;
using MangaFlow.Native.Controls;
using MangaFlow.Native.Services;
using MangaFlow.Native.Views;
using Microsoft.Win32;

namespace MangaFlow.Native;

public partial class MainWindow : Window
{
    private readonly WorkspaceState state;
    private readonly NativeBackend backend;
    private readonly string dataRoot;
    private readonly Preferences preferences;
    private readonly CancellationTokenSource lifetime = new();
    private readonly DispatcherTimer poll = new() { Interval = TimeSpan.FromSeconds(3) };
    private readonly DispatcherTimer dockPoll = new() { Interval = TimeSpan.FromSeconds(3) };
    private readonly ApiCache cache = new();
    private readonly Dictionary<string, IWorkspaceView> viewCache = new();
    private ApiClient? api;
    private DockQueue? dock;
    private Task? connectionTask;
    private CancellationTokenSource? viewRead;
    private IWorkspaceView? activeView;
    private string page = "home";
    private bool closing, closed, polling, syncingProjects;
    private int pendingReads;

    public MainWindow(string repository, string dataRoot)
    {
        InitializeComponent();
        this.dataRoot = dataRoot;
        state = new WorkspaceState { DataPath = dataRoot };
        DataContext = state;
        ProjectSections.SelectedItem = state.Navigation.Current;
        backend = new NativeBackend(repository, dataRoot);
        preferences = Preferences.Load(dataRoot);
        KeyValueStore.UseLocation(Path.Combine(dataRoot, "prefs.json"));
        var work = SystemParameters.WorkArea;
        Width = double.IsFinite(preferences.Width) ? Math.Clamp(preferences.Width, MinWidth, Math.Max(MinWidth, work.Width)) : 1320;
        Height = double.IsFinite(preferences.Height) ? Math.Clamp(preferences.Height, MinHeight, Math.Max(MinHeight, work.Height)) : 860;
        if (preferences.Maximized) WindowState = WindowState.Maximized;
        state.DockHidden = preferences.DockHidden;
        ApplySidebar();
        Navigate("home");
        poll.Tick += Poll;
        dockPoll.Tick += PollDock;
    }

    private async void OnLoaded(object sender, RoutedEventArgs e)
    {
        connectionTask = ConnectAsync();
        await connectionTask;
        poll.Start();
        dockPoll.Start();
    }

    private async Task ConnectAsync()
    {
        state.Connected = false;
        state.Busy = true;
        state.Error = "";
        state.ConnectionLabel = "正在连接本地服务…";
        try
        {
            await backend.StopAsync();
            api?.Dispose();
            api = null;
            var origin = await backend.StartAsync(lifetime.Token);
            lifetime.Token.ThrowIfCancellationRequested();
            // Detach the previous queue first: its in-flight writes must not touch the
            // shared dock state the replacement is about to own (reconnect path).
            dock?.Abandon();
            dock?.Reset();
            api = new ApiClient(origin);
            dock = new DockQueue(api, state);
            state.Connected = true;
            state.ConnectionLabel = "● 本地服务已连接";
            await LoadDashboardAsync(lifetime.Token);
            if (state.CurrentProject == null && preferences.RecentProject != null)
            {
                var recent = state.Projects.FirstOrDefault(p => p.Id == preferences.RecentProject);
                if (recent != null) ProjectList.SelectedItem = recent;
                else await OpenProjectAsync(state.Projects.FirstOrDefault());
            }
            else await OpenProjectAsync(state.CurrentProject ?? state.Projects.FirstOrDefault());
            // OpenProjectAsync already activated the project view; only activate when
            // no project opened (home stays on screen).
            if (state.CurrentProject == null) await ActivateCurrentViewAsync();
            state.Status = "已就绪 · Ctrl+K 快速切换项目";
        }
        catch (OperationCanceledException) when (closing) { }
        catch (Exception error)
        {
            state.Connected = false;
            state.ConnectionLabel = "本地服务未连接";
            state.Error = ErrorText(error);
            state.Status = "连接失败，可重新连接；项目数据已保留";
        }
        finally { state.Busy = pendingReads > 0; }
    }

    private async void Reconnect(object sender, RoutedEventArgs e)
    {
        if (closing || connectionTask is { IsCompleted: false }) return;
        CancelReads();
        connectionTask = ConnectAsync();
        await connectionTask;
    }

    private static string ErrorText(Exception error) => error switch
    {
        OperationCanceledException or TimeoutException => "请求超时，请检查本地服务后重试。提交操作可能已被服务接收，请先刷新确认，避免重复提交。",
        HttpRequestException => "无法连接本地服务，请重新连接。",
        _ => error.Message,
    };

    private async Task LoadDashboardAsync(CancellationToken cancellation)
    {
        if (api == null) return;
        var result = await api.SendAsync("projects/dashboard", cancellation: cancellation);
        cancellation.ThrowIfCancellationRequested();
        var list = result.GetProperty("projects").EnumerateArray().Select(ProjectItem.From).ToList();
        syncingProjects = true;
        try
        {
            var selectedId = state.CurrentProject?.Id;
            Sync(state.Projects, list, p => p.Id);
            if (selectedId != null)
            {
                state.CurrentProject = state.Projects.FirstOrDefault(p => p.Id == selectedId);
                ProjectList.SelectedItem = state.CurrentProject;
                if (state.CurrentProject == null && page != "home") Navigate("home");
            }
        }
        finally { syncingProjects = false; }
        state.CountsChanged();
        state.UpdateDashboard(result);
        var totals = result.GetProperty("totals");
        state.Overview = $"{totals.Number("project_count")} 个故事 · {totals.Number("pending_job_count")} 项后台任务";
        state.EmptyMessage = list.Count == 0 ? "还没有项目。新建一个项目，开始你的第一部漫画。" : "";
    }

    private static void Sync<T>(System.Collections.ObjectModel.ObservableCollection<T> target,
        IReadOnlyList<T> source, Func<T, string> key)
    {
        for (var i = 0; i < source.Count; i++)
        {
            if (i < target.Count && key(target[i]) == key(source[i]))
            {
                if (!EqualityComparer<T>.Default.Equals(target[i], source[i])) target[i] = source[i];
                continue;
            }
            var old = target.Select((value, index) => (value, index)).FirstOrDefault(x => key(x.value) == key(source[i]), (default!, -1)).index;
            if (old >= 0) target.Move(old, i); else target.Insert(i, source[i]);
            if (!EqualityComparer<T>.Default.Equals(target[i], source[i])) target[i] = source[i];
        }
        while (target.Count > source.Count) target.RemoveAt(target.Count - 1);
    }

    private void CancelReads() => viewRead?.Cancel();

    // ============ Navigation ============
    private static IWorkspaceView CreateView(string id) => id switch
    {
        "home" => new HomeView(),
        "settings-global" => new SettingsView(),
        "usage" => new UsageView(),
        "help" => new HelpView(),
        "source" => new SourceView(),
        "assets" => new AssetsView(),
        "script" => new ScriptView(),
        "storyboard" => new StoryboardView(),
        "generate" => new GenerateView(),
        "library" => new LibraryView(),
        "jobs" => new JobsView(),
        "workflow" => new WorkflowView(),
        "project-settings" => new ProjectSettingsView(),
        _ => throw new ArgumentException($"未知页面：{id}"),
    };

    private async Task NavigateAsync(string destination, bool confirmLeave = true)
    {
        destination = destination switch
        {
            // Project settings lives at WebSection "settings"; keep the internal id distinct
            // so the global settings page and the project settings page stay separate views.
            "settings" => "project-settings",
            _ => destination,
        };
        if (page == destination && ContentHost.Content != null) return;
        if (confirmLeave && activeView != null && !await activeView.ConfirmLeaveAsync()) return;
        activeView?.Deactivate();
        CancelReads();
        page = destination;
        state.CurrentSection = destination;
        ApplySidebar();
        UpdateChrome();
        var view = viewCache.TryGetValue(destination, out var cached)
            ? cached
            : viewCache[destination] = CreateView(destination);
        if (view is HomeView home)
        {
            home.CreateRequested -= OnProjectCreated;
            home.CreateRequested += OnProjectCreated;
        }
        ContentHost.Content = view as UIElement ?? throw new InvalidOperationException("视图不是 UI 元素");
        await ActivateCurrentViewAsync();
    }

    private async void OnProjectCreated()
    {
        if (api == null || closing) return;
        try
        {
            await LoadDashboardAsync(lifetime.Token);
        }
         catch (OperationCanceledException) { }
        catch (Exception error) when (error is not OperationCanceledException)
        {
            state.Error = ErrorText(error);
        }
    }

    private void Navigate(string destination) => _ = NavigateAsync(destination);

    private async Task ActivateCurrentViewAsync()
    {
        if (api == null || !state.Connected) return;
        var context = new WorkspaceContext
        {
            Api = api,
            Cache = cache,
            State = state,
            Window = this,
            Project = state.CurrentProject,
            NavigateSection = async (section, query) =>
            {
                if (section == "settings-global") await NavigateAsync("settings-global");
                else if (ProjectPages.FindBySection(section) is { } definition)
                {
                    // Home cards pass "project:{id}" to switch identity before opening the section.
                    if (query.StartsWith("project:", StringComparison.Ordinal))
                    {
                        var id = query["project:".Length..];
                        var target = state.Projects.FirstOrDefault(p => p.Id == id);
                        if (target != null && state.CurrentProject?.Id != id)
                        {
                            // Record the destination section first: OpenProjectAsync
                            // navigates to Navigation.Current, so without this the
                            // requested section would be dropped after the switch.
                            state.Navigation.Select(definition);
                            ProjectList.SelectedItem = target;   // triggers OpenProjectAsync
                            return;
                        }
                    }
                    await EnsureProjectAsync();
                    await NavigateAsync(section);
                    if (page != (section == "settings" ? "project-settings" : section)) return;
                    state.Navigation.Select(definition);
                    ProjectSections.SelectedItem = definition;
                    if (section == "assets" && ContentHost.Content is AssetsView assets)
                    {
                        var parameters = System.Web.HttpUtility.ParseQueryString(query.TrimStart('?'));
                        if (parameters["view"] is { } assetView) assets.Switch(assetView);
                    }
                }
            },
            OpenDashboard = async () =>
            {
                state.CurrentProject = null;
                preferences.RecentProject = null;
                ProjectList.SelectedItem = null;
                await NavigateAsync("home");
                if (api != null) await LoadDashboardAsync(lifetime.Token);
            },
        };
        if (ContentHost.Content is IWorkspaceView view)
        {
            activeView = view;
            var request = CancellationTokenSource.CreateLinkedTokenSource(lifetime.Token);
            viewRead = request;
            pendingReads++;
            state.Busy = true;
            try
            {
                view.Activate(context);
                request.Token.ThrowIfCancellationRequested();
                state.Error = "";
            }
            catch (Exception error) when (!request.IsCancellationRequested)
            {
                state.Error = ErrorText(error);
            }
            finally
            {
                if (viewRead == request) viewRead = null;
                request.Dispose();
                pendingReads--;
                state.Busy = pendingReads > 0;
            }
        }
        UpdateChrome();
    }

    private async Task EnsureProjectAsync()
    {
        if (state.CurrentProject != null || state.Projects.Count == 0) return;
        if (page != "home") await NavigateAsync("home");
        await OpenProjectAsync(state.Projects[0]);
    }

    private void UpdateChrome()
    {
        var project = state.CurrentProject;
        state.Breadcrumb = page switch
        {
            "home" => "漫画生产台",
            "settings-global" => "系统设置",
            "usage" => "用量与成本看板",
            "help" => "使用帮助",
            _ => $"{project?.Name ?? "项目"} / {state.Navigation.Current.Title}",
        };
        TopTitle.Text = page switch
        {
            "home" => "漫画生产台",
            "settings-global" => "系统设置",
            "usage" => "用量看板",
            "help" => "使用帮助",
            _ => project?.Name ?? "项目工作区",
        };
        BrandKicker.Text = page switch
        {
            "settings-global" => "SYSTEM / CONTROL ROOM",
            "usage" => "SYSTEM / USAGE & COST",
            "help" => "MANGAFLOW / FIELD GUIDE",
            _ => "MANGAFLOW / PRODUCTION DESK",
        };
        var selected = (Brush)FindResource("Selected");
        HomeNav.Background = page == "home" ? selected : Brushes.Transparent;
        UsageNav.Background = page == "usage" ? selected : Brushes.Transparent;
        HelpNav.Background = page == "help" ? selected : Brushes.Transparent;
        SettingsNav.Background = page == "settings-global" ? selected : Brushes.Transparent;
    }

    private async void ShowHome(object sender, RoutedEventArgs e) => await NavigateAsync("home");
    private async void ShowSettings(object sender, RoutedEventArgs e) => await NavigateAsync("settings-global");
    private async void ShowUsage(object sender, RoutedEventArgs e) => await NavigateAsync("usage");
    private void ShowWorkflow(object sender, RoutedEventArgs e) => ProjectSections.SelectedItem = ProjectPages.Get(ProjectPageId.Workflow);
    private void ShowProjectSettings(object sender, RoutedEventArgs e) => ProjectSections.SelectedItem = ProjectPages.Get(ProjectPageId.Settings);
    private void SaveRuntimeSettings(object sender, RoutedEventArgs e)
    {
        if (ContentHost.Content is SettingsView settings) settings.SaveRuntimeSettings();
    }
    private void ShowHelp(object sender, RoutedEventArgs e) => Navigate("help");

    private async void SelectProject(object sender, SelectionChangedEventArgs e)
    {
        if (syncingProjects) return;
        if (ProjectList.SelectedItem is not ProjectItem item) return;
        var previous = state.CurrentProject;
        if (previous?.Id == item.Id) return;
        // Same contract as SelectProjectSection: confirm unsaved work before
        // switching identity; a refusal rolls the selection back.
        if (activeView != null && !await activeView.ConfirmLeaveAsync())
        {
            if (ReferenceEquals(ProjectList.SelectedItem, item)) ProjectList.SelectedItem = previous;
            return;
        }
        // The await can span a real save; if the user re-targeted meanwhile, the
        // newer selection's own invocation owns the switch.
        if (!ReferenceEquals(ProjectList.SelectedItem, item)) return;
        await OpenProjectAsync(item);
    }

    private async Task OpenProjectAsync(ProjectItem? item)
    {
        if (item == null) return;
        if (state.CurrentProject?.Id != item.Id)
        {
            // Staying on the same page re-activates the same view instance; cancel the
            // old reads first so late responses cannot paint the new project.
            activeView?.Deactivate();
            CancelReads();
            // Same contract for the dock: drop the previous project's counters so a
            // slow reply from it can never re-paint after the identity switch.
            dock?.Reset();
        }
        state.CurrentProject = item;
        preferences.RecentProject = item.Id;
        if (page == "home" || page is "settings-global" or "usage" or "help")
        {
            ProjectSections.SelectedItem = state.Navigation.Current;
            await NavigateAsync(state.Navigation.Current.WebSection);
        }
        else await ActivateCurrentViewAsync();
        if (dock != null && state.Connected) _ = dock.RefreshAsync(item.Id, lifetime.Token);
    }

    private async void SelectProjectSection(object sender, SelectionChangedEventArgs e)
    {
        if (state == null || ProjectSections.SelectedItem is not ProjectPageDefinition selected) return;
        var previous = state.Navigation.Current;
        if (selected == previous) return;
        // Confirm unsaved work BEFORE mutating navigation or the sidebar highlight;
        // a refusal rolls the selection back so the page can still be entered later.
        if (activeView != null && !await activeView.ConfirmLeaveAsync())
        {
            ProjectSections.SelectedItem = previous;
            return;
        }
        if (!state.Navigation.Select(selected)) return;
        if (state.CurrentProject == null)
        {
            if (state.Projects.Count > 0) { ProjectList.SelectedItem = state.Projects[0]; return; }
            await NavigateAsync("home", confirmLeave: false);
            return;
        }
        await NavigateAsync(selected.WebSection, confirmLeave: false);
    }

    // ============ Polling ============
    private async void Poll(object? sender, EventArgs e)
    {
        if (polling || closing || !state.Connected || WindowState == WindowState.Minimized ||
            connectionTask is { IsCompleted: false }) return;
        polling = true;
        try
        {
            if (backend.IsRunning) activeView?.PollTick();
            else
            {
                state.Connected = false;
                state.ConnectionLabel = "本地服务已断开";
                state.Error = "本地服务已退出。点击重新连接恢复工作，已有数据保留。";
            }
        }
        finally { polling = false; }
    }

    private void PollDock(object? sender, EventArgs e)
    {
        if (closing || !state.Connected || !backend.IsRunning || dock == null ||
            WindowState == WindowState.Minimized || state.CurrentProject is not { } project) return;
        _ = dock.RefreshAsync(project.Id, lifetime.Token);
    }

    private void OpenJobs(object sender, MouseButtonEventArgs e)
    {
        if (state.CurrentProject == null) return;
        ProjectSections.SelectedItem = ProjectPages.Get(ProjectPageId.Jobs);
    }

    // Dock cancel/retry operate on the latest job; writes are deduplicated in DockQueue
    // and never retried automatically — a failed action only surfaces its notice.
    private async void DockCancelJob(object sender, RoutedEventArgs e)
    {
        e.Handled = true;
        if (closing || dock == null || state.CurrentProject is not { } project || state.DockJob is not { CanCancel: true } job) return;
        await dock.ActAsync(job, "cancel", project.Id, lifetime.Token);
    }

    private async void DockRetryJob(object sender, RoutedEventArgs e)
    {
        e.Handled = true;
        if (closing || dock == null || state.CurrentProject is not { } project || state.DockJob is not { CanRetry: true } job) return;
        if (new ConfirmDialog(this, "重试任务", "重试可能再次调用模型并产生费用。确认重试这个任务？", "重试") .ShowDialog() != true) return;
        // The modal keeps polling; revalidate identity AND retryability before paying again.
        if (state.CurrentProject?.Id != project.Id || state.DockJob?.Id != job.Id || !state.DockJob.CanRetry) return;
        await dock.ActAsync(job, "retry", project.Id, lifetime.Token);
    }

    private void HideDock(object sender, RoutedEventArgs e)
    {
        state.DockHidden = true;
        preferences.DockHidden = true;
        preferences.Save(dataRoot);
        e.Handled = true;
    }

    private void ShowDock(object sender, RoutedEventArgs e)
    {
        state.DockHidden = false;
        preferences.DockHidden = false;
        preferences.Save(dataRoot);
    }

    // ============ Global actions ============
    private async void Refresh(object sender, RoutedEventArgs e)
    {
        if (activeView != null) await activeView.RefreshAsync();
        if (page == "home" && api != null)
        {
            try { await LoadDashboardAsync(lifetime.Token); } catch (Exception) { }
        }
    }

    private void CreateProject(object sender, RoutedEventArgs e)
    {
        Navigate("home");
        if (viewCache.TryGetValue("home", out var view) && view is HomeView home) home.OpenCreationDrawer();
    }

    private void OpenPalette(object sender, RoutedEventArgs e)
    {
        var palette = new ProjectPalette(this, state.Projects.ToList());
        if (palette.ShowDialog() == true && palette.SelectedProject is { } project)
            ProjectList.SelectedItem = project;
    }

    private void ToggleSidebar(object sender, RoutedEventArgs e)
    {
        preferences.SidebarCollapsed = !preferences.SidebarCollapsed;
        ApplySidebar();
    }

    private void ApplySidebar()
    {
        if (preferences == null) return;
        var workspace = page is not ("home" or "settings-global" or "usage" or "help");
        SidebarColumn.Width = new GridLength(workspace ? (preferences.SidebarCollapsed ? 52 : 214) : 0);
        SidebarPanel.Visibility = workspace ? Visibility.Visible : Visibility.Collapsed;
        SidebarToggle.Visibility = workspace ? Visibility.Visible : Visibility.Collapsed;
        GlobalActions.Visibility = workspace || page == "settings-global" ? Visibility.Collapsed : Visibility.Visible;
        SettingsActions.Visibility = page == "settings-global" ? Visibility.Visible : Visibility.Collapsed;
        WorkspaceActions.Visibility = workspace ? Visibility.Visible : Visibility.Collapsed;
        BackToProjects.Visibility = workspace ? Visibility.Visible : Visibility.Collapsed;
        BrandKicker.Visibility = workspace ? Visibility.Collapsed : Visibility.Visible;
        TopbarRow.Height = new GridLength(workspace ? 58 : 74);
        Breadcrumb.Visibility = Visibility.Collapsed;
        ProjectSummary.Visibility = preferences.SidebarCollapsed ? Visibility.Collapsed : Visibility.Visible;
        ProjectList.Visibility = Visibility.Collapsed;
        SidebarFooter.Visibility = preferences.SidebarCollapsed ? Visibility.Collapsed : Visibility.Visible;
        state.IsWorkspace = workspace;
        state.SidebarCollapsed = preferences.SidebarCollapsed;
        ContentHost.Margin = page is "source" or "assets" ? new Thickness(30, 28, 30, 0) : workspace ? new Thickness(24, 24, 0, 0) : new Thickness(0);
    }

    private void OnWindowSizeChanged(object sender, SizeChangedEventArgs e) { }

    private void OnKeyDown(object sender, KeyEventArgs e)
    {
        if (e.IsRepeat || closing) return;
        if (Keyboard.Modifiers == ModifierKeys.Control)
        {
            switch (e.Key)
            {
                case Key.K: OpenPalette(this, new()); break;
                case Key.N: CreateProject(this, new()); break;
                case Key.B: ToggleSidebar(this, new()); break;
                default: return;
            }
            e.Handled = true;
        }
        else if (e.Key == Key.F5) { e.Handled = true; Refresh(this, new()); }
    }

    private void OpenDataFolder(object sender, RoutedEventArgs e)
    {
        try { Directory.CreateDirectory(dataRoot); Process.Start(new ProcessStartInfo(dataRoot) { UseShellExecute = true }); }
        catch (Exception error) { MessageBox.Show(this, error.Message, "无法打开文件夹"); }
    }

    private async void ExportLogs(object sender, RoutedEventArgs e)
    {
        var dialog = new SaveFileDialog { Title = "导出诊断日志", Filter = "ZIP 归档|*.zip", FileName = $"MangaFlow-logs-{DateTime.Now:yyyyMMdd-HHmm}.zip" };
        if (dialog.ShowDialog(this) != true) return;
        ((Button)sender).IsEnabled = false;
        try
        {
            await Task.Run(() =>
            {
                var root = Path.Combine(dataRoot, "logs");
                if (!Directory.Exists(root)) throw new IOException("暂无可导出的日志。");
                using var buffer = new MemoryStream();
                using (var zip = new ZipArchive(buffer, ZipArchiveMode.Create, true))
                {
                    long total = 0;
                    foreach (var file in Directory.EnumerateFiles(root).Order())
                    {
                        var info = new FileInfo(file);
                        if ((info.Attributes & FileAttributes.ReparsePoint) != 0) continue;
                        if (!info.Name.Contains(".log", StringComparison.OrdinalIgnoreCase)) continue;
                        total += info.Length;
                        if (total > 100 * 1024 * 1024) throw new IOException("日志超过 100 MB，请从数据目录按需提取。");
                        using var input = new FileStream(file, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete);
                        using var output = zip.CreateEntry(info.Name).Open();
                        input.CopyTo(output);
                    }
                }
                File.WriteAllBytes(dialog.FileName, buffer.ToArray());
            });
            state.Status = "诊断日志已导出";
        }
        catch (Exception error) { MessageBox.Show(this, error.Message, "导出未完成"); }
        finally { ((Button)sender).IsEnabled = true; }
    }

    private async void OnClosing(object? sender, CancelEventArgs e)
    {
        if (closed) return;
        e.Cancel = true;
        if (closing) return;
        if (activeView != null && !await activeView.ConfirmLeaveAsync()) return;
        closing = true;
        IsEnabled = false;
        poll.Stop();
        dockPoll.Stop();
        CancelReads();
        // Persist the session shape first: if backend shutdown then fails, the retry
        // path below still force-closes (the Job Object kills the child tree anyway),
        // so preferences must not depend on a clean stop.
        preferences.Width = RestoreBounds.Width;
        preferences.Height = RestoreBounds.Height;
        preferences.Maximized = WindowState == WindowState.Maximized;
        preferences.DockHidden = state.DockHidden;
        preferences.Save(dataRoot);
        lifetime.Cancel();
        state.Status = "正在安全停止本地服务…";
        try
        {
            if (connectionTask != null) await connectionTask;
            await backend.StopAsync();
        }
        catch (Exception error)
        {
            state.Error = "退出收尾未完全成功：" + error.Message + "。窗口将关闭；本地服务由系统作业对象一并回收。";
        }
        finally
        {
            api?.Dispose();
            closed = true;
            Close();
        }
    }
}
