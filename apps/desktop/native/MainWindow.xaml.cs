using System.ComponentModel;
using System.Diagnostics;
using System.IO;
using System.IO.Compression;
using System.Net.Http;
using System.Text.Json;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;
using System.Windows.Threading;
using MangaFlow.Native.Services;
using Microsoft.Win32;

namespace MangaFlow.Native;

public partial class MainWindow : Window
{
    private readonly WorkspaceState state;
    private readonly NativeBackend backend;
    private readonly string dataRoot;
    private readonly Preferences preferences;
    private readonly CancellationTokenSource lifetime = new();
    private readonly DispatcherTimer poll = new() { Interval = TimeSpan.FromSeconds(5) };
    private ApiClient? api;
    private Task? connectionTask;
    private CancellationTokenSource? viewRead, chapterRead;
    private List<JobItem> jobs = [];
    private string page = "home", projectTab = "source";
    private bool closing, closed, polling, syncingProjects, settingsLoaded, settingsSaving;
    private int settingsVersion;
    private string savedConcurrency = "", savedTimeout = "";
    private int pendingReads;

    public MainWindow(string repository, string dataRoot)
    {
        InitializeComponent();
        this.dataRoot = dataRoot;
        state = new WorkspaceState { DataPath = dataRoot };
        DataContext = state;
        HomePage.CreateRequested += (_, _) => CreateProject(this, new());
        HomePage.SettingsRequested += (_, _) => ShowSettings(this, new());
        HomePage.ProjectRequested += project =>
        {
            if (Equals(ProjectList.SelectedItem, project)) _ = OpenProjectAsync(project);
            else ProjectList.SelectedItem = project;
        };
        backend = new NativeBackend(repository, dataRoot);
        preferences = Preferences.Load(dataRoot);
        var work = SystemParameters.WorkArea;
        Width = double.IsFinite(preferences.Width) ? Math.Clamp(preferences.Width, MinWidth, Math.Max(MinWidth, work.Width)) : 1320;
        Height = double.IsFinite(preferences.Height) ? Math.Clamp(preferences.Height, MinHeight, Math.Max(MinHeight, work.Height)) : 860;
        if (preferences.Maximized) WindowState = WindowState.Maximized;
        ApplySidebar();
        Navigate("home");
        poll.Tick += Poll;
    }

    private async void OnLoaded(object sender, RoutedEventArgs e)
    {
        connectionTask = ConnectAsync();
        await connectionTask;
        poll.Start();
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
            api = new ApiClient(origin);
            state.Connected = true;
            state.ConnectionLabel = "● 本地服务已连接";
            await LoadDashboardAsync(lifetime.Token);
            if (state.CurrentProject == null && preferences.RecentProject != null)
            {
                var recent = state.Projects.FirstOrDefault(p => p.Id == preferences.RecentProject);
                if (recent != null) ProjectList.SelectedItem = recent;
            }
            else await LoadCurrentAsync();
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
            // Retain identical rows and selected identity; do not rebuild the sidebar on every poll.
            Sync(state.Projects, list, p => p.Id);
            if (selectedId != null)
            {
                state.CurrentProject = state.Projects.FirstOrDefault(p => p.Id == selectedId);
                ProjectList.SelectedItem = state.CurrentProject;
                if (state.CurrentProject == null && page == "project") Navigate("home");
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

    private void CancelReads()
    {
        viewRead?.Cancel();
        chapterRead?.Cancel();
    }

    private void Navigate(string destination)
    {
        CancelReads();
        page = destination;
        ApplySidebar();
        HomePage.Visibility = destination == "home" ? Visibility.Visible : Visibility.Collapsed;
        ProjectPage.Visibility = destination == "project" ? Visibility.Visible : Visibility.Collapsed;
        SettingsPage.Visibility = destination == "settings" ? Visibility.Visible : Visibility.Collapsed;
        HelpPage.Visibility = destination == "help" ? Visibility.Visible : Visibility.Collapsed;
        state.Breadcrumb = destination switch { "project" => state.CurrentProject?.Name ?? "项目", "settings" => "系统设置", "help" => "使用帮助", _ => "漫画生产台" };
        var selected = (System.Windows.Media.Brush)FindResource("Selected");
        HomeNav.Background = destination is "home" or "project" ? selected : System.Windows.Media.Brushes.Transparent;
        SettingsNav.Background = destination == "settings" ? selected : System.Windows.Media.Brushes.Transparent;
        HelpNav.Background = destination == "help" ? selected : System.Windows.Media.Brushes.Transparent;
    }

    private async void ShowHome(object sender, RoutedEventArgs e) { Navigate("home"); await LoadCurrentAsync(); }
    private async void ShowSettings(object sender, RoutedEventArgs e) { Navigate("settings"); await LoadCurrentAsync(); }
    private void ShowHelp(object sender, RoutedEventArgs e) => Navigate("help");
    private async void SelectProject(object sender, SelectionChangedEventArgs e)
    {
        if (syncingProjects || ProjectList.SelectedItem is not ProjectItem item) return;
        await OpenProjectAsync(item);
    }
    private void OpenHomeProject(object sender, SelectionChangedEventArgs e)
    {
        if (e.AddedItems.Count > 0 && e.AddedItems[0] is ProjectItem item)
        {
            if (Equals(ProjectList.SelectedItem, item)) _ = OpenProjectAsync(item);
            else ProjectList.SelectedItem = item;
            ((ListBox)sender).SelectedItem = null;
        }
    }

    private async Task OpenProjectAsync(ProjectItem item)
    {
        state.CurrentProject = item;
        preferences.RecentProject = item.Id;
        state.Chapters.Clear();
        state.VisibleJobs.Clear();
        jobs.Clear();
        state.ReaderTitle = "选择一个章节";
        state.ReaderText = "从左侧选择章节阅读原文，或导入新的故事。";
        Navigate("project");
        await LoadCurrentAsync();
    }

    private async void ShowSource(object sender, RoutedEventArgs e)
    {
        projectTab = "source";
        await LoadCurrentAsync();
    }
    private async void ShowJobs(object sender, RoutedEventArgs e)
    {
        chapterRead?.Cancel();
        projectTab = "jobs";
        await LoadCurrentAsync();
    }

    private async Task LoadCurrentAsync(bool background = false)
    {
        if (!state.Connected || api == null || closing) return;
        viewRead?.Cancel();
        var request = CancellationTokenSource.CreateLinkedTokenSource(lifetime.Token);
        viewRead = request;
        pendingReads++;
        if (!background) state.Busy = true;
        try
        {
            var token = request.Token;
            if (page == "home") await LoadDashboardAsync(token);
            if (page == "project" && state.CurrentProject is { } project)
            {
                SourcePane.Visibility = projectTab == "source" ? Visibility.Visible : Visibility.Collapsed;
                JobsPane.Visibility = projectTab == "jobs" ? Visibility.Visible : Visibility.Collapsed;
                var selectedBrush = (System.Windows.Media.Brush)FindResource("Selected");
                SourceTab.Background = projectTab == "source" ? selectedBrush : System.Windows.Media.Brushes.Transparent;
                JobsTab.Background = projectTab == "jobs" ? selectedBrush : System.Windows.Media.Brushes.Transparent;
                var section = projectTab == "source" ? "chapters" : "jobs";
                var rows = await api.SendAsync($"projects/{project.Id}/{section}", cancellation: token);
                token.ThrowIfCancellationRequested();
                if (section == "chapters")
                {
                    Sync(state.Chapters, rows.EnumerateArray().Select(ChapterItem.From).ToList(), c => c.Id);
                    state.ChapterHint = state.Chapters.Count == 0 ? "还没有章节，导入一段故事开始。" : $"共 {state.Chapters.Count} 个章节";
                }
                else
                {
                    jobs = rows.EnumerateArray().Select(JobItem.From).ToList();
                    ApplyJobFilter();
                }
            }
            if (page == "settings" && !settingsLoaded)
            {
                var settings = await api.SendAsync("settings/runtime", cancellation: token);
                token.ThrowIfCancellationRequested();
                settingsVersion = settings.Number("version");
                savedConcurrency = ConcurrencyInput.Text = settings.Text("default_concurrency");
                savedTimeout = TimeoutInput.Text = settings.Text("job_timeout_seconds");
                SettingsMessage.Text = "";
                settingsLoaded = true;
            }
            request.Token.ThrowIfCancellationRequested();
            state.Error = "";
            state.Status = $"已更新 · {DateTime.Now:HH:mm:ss}";
        }
        catch (OperationCanceledException) when (request.IsCancellationRequested) { }
        catch (Exception error) when (!request.IsCancellationRequested) { state.Error = ErrorText(error); state.Status = "刷新失败，保留已有内容"; }
        catch (Exception) when (request.IsCancellationRequested) { }
        finally
        {
            if (viewRead == request) viewRead = null;
            request.Dispose();
            pendingReads--;
            state.Busy = pendingReads > 0;
        }
    }

    private async void ReadChapter(object sender, SelectionChangedEventArgs e)
    {
        if (api == null || e.AddedItems.Count == 0 || e.AddedItems[0] is not ChapterItem chapter) return;
        chapterRead?.Cancel();
        var request = CancellationTokenSource.CreateLinkedTokenSource(lifetime.Token);
        chapterRead = request;
        state.ReaderTitle = chapter.Title;
        state.ReaderText = "正在读取原文…";
        try
        {
            var revisions = await api.SendAsync($"chapters/{chapter.Id}/revisions", cancellation: request.Token);
            request.Token.ThrowIfCancellationRequested();
            var latest = revisions.EnumerateArray().OrderByDescending(r => r.Number("revision")).FirstOrDefault();
            state.ReaderText = latest.ValueKind == JsonValueKind.Undefined ? "这个章节尚无原文。" : latest.Text("original_text");
        }
        catch (OperationCanceledException) when (request.IsCancellationRequested) { }
        catch (Exception error) when (!request.IsCancellationRequested) { state.ReaderText = ErrorText(error); }
        catch (Exception) when (request.IsCancellationRequested) { }
        finally { if (chapterRead == request) chapterRead = null; request.Dispose(); }
    }

    private void FilterJobs(object sender, SelectionChangedEventArgs e) { if (state != null) ApplyJobFilter(); }
    private void ApplyJobFilter()
    {
        var visible = jobs.Where(j => JobFilter.SelectedIndex switch
        {
            1 => j.CanCancel, 2 => j.State is "FAILED" or "NEEDS_REVIEW", _ => true,
        }).ToList();
        Sync(state.VisibleJobs, visible, j => j.Id);
        state.JobHint = jobs.Count == 0 ? "暂无任务。" : $"最近 {jobs.Count} 项任务 · 当前显示 {visible.Count} 项 · 每 5 秒更新";
    }

    private async void Poll(object? sender, EventArgs e)
    {
        if (polling || closing || !state.Connected || WindowState == WindowState.Minimized ||
            connectionTask is { IsCompleted: false } || pendingReads > 0 || !IsActive) return;
        polling = true;
        try
        {
            if (!backend.IsRunning)
            {
                state.Connected = false;
                state.ConnectionLabel = "本地服务已断开";
                state.Error = "本地服务已退出。点击重新连接恢复工作，已有数据保留。";
                return;
            }
            if (page == "home" || (page == "project" && projectTab == "jobs")) await LoadCurrentAsync(true);
        }
        finally { polling = false; }
    }

    private async void Refresh(object sender, RoutedEventArgs e)
    {
        if (page == "settings")
        {
            if (settingsSaving) return;
            if (settingsLoaded && (ConcurrencyInput.Text != savedConcurrency || TimeoutInput.Text != savedTimeout) &&
                MessageBox.Show(this, "放弃未保存的运行偏好并重新加载？", "刷新设置", MessageBoxButton.YesNo, MessageBoxImage.Question) != MessageBoxResult.Yes) return;
            settingsLoaded = false;
        }
        await LoadCurrentAsync();
    }

    private void CreateProject(object sender, RoutedEventArgs e)
    {
        if (api == null || !state.Connected) return;
        var client = api;
        new EditorDialog(this, "新建项目", "给这个故事起个名字", false, async (name, _, _) =>
        {
            var created = await client.SendAsync("projects", HttpMethod.Post, new { name });
            await LoadDashboardAsync(lifetime.Token);
            ProjectList.SelectedItem = state.Projects.FirstOrDefault(p => p.Id == created.Text("id"));
        }).ShowDialog();
    }

    private void ImportSource(object sender, RoutedEventArgs e)
    {
        if (api == null || state.CurrentProject == null || !state.Connected) return;
        var project = state.CurrentProject;
        var client = api;
        new EditorDialog(this, "导入原作", $"导入到「{project.Name}」", true, async (title, text, type) =>
        {
            await client.SendAsync($"projects/{project.Id}/sources/import", HttpMethod.Post, new { title, text, source_type = type });
            await LoadDashboardAsync(lifetime.Token);
            await LoadCurrentAsync();
        }).ShowDialog();
    }

    private async void CancelJob(object sender, RoutedEventArgs e) => await JobActionAsync((Button)sender, "cancel");
    private async void RetryJob(object sender, RoutedEventArgs e)
    {
        if (MessageBox.Show(this, "重试可能再次调用模型并产生费用。确认重试这个任务？", "重试任务", MessageBoxButton.YesNo, MessageBoxImage.Question) == MessageBoxResult.Yes)
            await JobActionAsync((Button)sender, "retry");
    }
    private async Task JobActionAsync(Button button, string action)
    {
        if (api == null || button.Tag is not JobItem job || state.CurrentProject == null) return;
        var projectId = state.CurrentProject.Id;
        button.IsEnabled = false;
        try
        {
            await api.SendAsync($"jobs/{job.Id}/{action}?project_id={projectId}", HttpMethod.Post);
            await LoadCurrentAsync();
        }
        catch (Exception error) { MessageBox.Show(this, ErrorText(error), "操作未完成", MessageBoxButton.OK, MessageBoxImage.Warning); }
        finally { button.IsEnabled = true; }
    }

    private async void SaveSettings(object sender, RoutedEventArgs e)
    {
        if (api == null || !settingsLoaded || settingsSaving) return;
        if (!int.TryParse(ConcurrencyInput.Text, out var concurrency) || concurrency is < 1 or > 8 ||
            !int.TryParse(TimeoutInput.Text, out var timeout) || timeout is < 30 or > 3600)
        {
            SettingsMessage.Text = "请输入有效范围内的整数：并发 1–8，超时 30–3600 秒。";
            return;
        }
        settingsSaving = true;
        ((Button)sender).IsEnabled = false;
        try
        {
            var saved = await api.SendAsync("settings/runtime", HttpMethod.Patch,
                new { version = settingsVersion, default_concurrency = concurrency, job_timeout_seconds = timeout });
            settingsVersion = saved.Number("version");
            savedConcurrency = concurrency.ToString();
            savedTimeout = timeout.ToString();
            SettingsMessage.Text = "运行偏好已保存。";
        }
        catch (Exception error) { SettingsMessage.Text = ErrorText(error); }
        finally { settingsSaving = false; ((Button)sender).IsEnabled = state.Connected; }
    }

    private void OpenPalette(object sender, RoutedEventArgs e)
    {
        var palette = new ProjectPalette(this, state.Projects.ToList());
        if (palette.ShowDialog() == true && palette.SelectedProject is { } project)
        {
            if (Equals(ProjectList.SelectedItem, project)) _ = OpenProjectAsync(project);
            else ProjectList.SelectedItem = project;
        }
    }
    private void ToggleSidebar(object sender, RoutedEventArgs e)
    {
        preferences.SidebarCollapsed = !preferences.SidebarCollapsed;
        ApplySidebar();
    }
    private void ApplySidebar()
    {
        SidebarColumn.Width = new GridLength(page == "project" && !preferences.SidebarCollapsed ? 214 : 0);
        SidebarToggle.Visibility = page == "project" ? Visibility.Visible : Visibility.Collapsed;
        UpdateChromeWidth();
    }
    private void OnWindowSizeChanged(object sender, SizeChangedEventArgs e) => UpdateChromeWidth();
    private void UpdateChromeWidth()
    {
        if (RuntimeBadge == null) return;
        RuntimeBadge.Visibility = (ActualWidth > 0 ? ActualWidth : Width) - SidebarColumn.Width.Value < 1120
            ? Visibility.Collapsed : Visibility.Visible;
    }

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
                if ((File.GetAttributes(root) & FileAttributes.ReparsePoint) != 0) throw new IOException("日志目录不能是链接。");
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
        if (settingsSaving) { state.Status = "正在保存运行偏好，请稍候再关闭。"; return; }
        if (settingsLoaded && (ConcurrencyInput.Text != savedConcurrency || TimeoutInput.Text != savedTimeout) &&
            MessageBox.Show(this, "运行偏好尚未保存，仍要退出？", "退出 MangaFlow", MessageBoxButton.YesNo, MessageBoxImage.Question) != MessageBoxResult.Yes) return;
        closing = true;
        IsEnabled = false;
        poll.Stop();
        lifetime.Cancel();
        CancelReads();
        state.Status = "正在安全停止本地服务…";
        try
        {
            if (connectionTask != null) await connectionTask;
            await backend.StopAsync();
            preferences.Width = RestoreBounds.Width;
            preferences.Height = RestoreBounds.Height;
            preferences.Maximized = WindowState == WindowState.Maximized;
            preferences.Save(dataRoot);
            api?.Dispose();
            closed = true;
            Close();
        }
        catch (Exception error)
        {
            state.Error = "退出收尾未完成：" + error.Message + "。请再次关闭以重试。";
            IsEnabled = true;
            closing = false;
        }
    }
}
