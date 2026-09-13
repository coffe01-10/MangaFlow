using System.Net.Http;
using System.Text.Json;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Controls.Primitives;
using MangaFlow.Native.Controls;
using MangaFlow.Native.Services;

namespace MangaFlow.Native.Views;

/// <summary>NUI-2B: jobs center — running/failed groups, history view, archive/restore/delete, costs.</summary>
public sealed partial class JobsView : WorkspaceView
{
    private readonly ScrollViewer scroller = new() { VerticalScrollBarVisibility = ScrollBarVisibility.Auto };
    private readonly StackPanel body = new();
    private readonly ToggleButton recentTab = new() { Content = "近期任务", Style = (Style)Application.Current.FindResource("Pill"), IsChecked = true };
    private readonly ToggleButton historyTab = new() { Content = "历史记录", Style = (Style)Application.Current.FindResource("Pill") };
    private readonly Button archiveAll = new() { Content = "归档全部终态", Style = (Style)Application.Current.FindResource("Compact") };
    private readonly TextBlock notice = new() { Style = (Style)Application.Current.FindResource("Caption"), TextWrapping = TextWrapping.Wrap };
    private List<JobItem> jobs = [];
    private bool archivedView;
    private readonly HashSet<string> selected = [];
    private readonly HashSet<string> pending = [];
    private CancellationTokenSource? listRequest;
    private long requestVersion;
    private long activationVersion;
    private bool loading;
    private bool bulkPending;
    private bool listFailed;
    private string lastResponse = "";
    private readonly HashSet<string> expandedDates = [];

    public JobsView()
    {
        BuildPage();
        archiveAll.Click += ArchiveAllCompleted;
        archiveSelected.Click += BulkArchive;
        recentTab.Click += (_, _) => SwitchView(false);
        historyTab.Click += (_, _) => SwitchView(true);
    }

    private void SwitchView(bool history)
    {
        recentTab.IsChecked = !history;
        historyTab.IsChecked = history;
        if (archivedView == history) return;
        archivedView = history;
        recentTab.IsChecked = !history;
        historyTab.IsChecked = history;
        selected.Clear();
        notice.Text = "";
        bulkActions.Visibility = history ? Visibility.Collapsed : Visibility.Visible;
        UpdateToolbar();
        jobs.Clear();
        lastResponse = "";
        expandedDates.Clear();
        expandedDates.Add("failed");
        _ = LoadAsync();
    }

    public override async void Activate(WorkspaceContext context)
    {
        base.Activate(context);
        activationVersion++;
        jobs.Clear();
        selected.Clear();
        pending.Clear();
        expandedDates.Clear();
        expandedDates.Add("failed");
        bulkPending = false;
        UpdateToolbar();
        notice.Text = "";
        lastResponse = "";
        await LoadAsync();
    }

    public override void Deactivate()
    {
        activationVersion++;
        requestVersion++;
        listRequest?.Cancel();
        listRequest?.Dispose();
        listRequest = null;
        base.Deactivate();
    }

    private async Task LoadAsync()
    {
        listRequest?.Cancel();
        listRequest?.Dispose();
        listRequest = CancellationTokenSource.CreateLinkedTokenSource(lifetime.Token);
        var token = listRequest.Token;
        var version = ++requestVersion;
        loading = true;
        if (lastResponse.Length == 0)
        {
            body.Children.Clear();
            body.Children.Add(Caption("正在读取任务…"));
            State.JobHint = countLabel.Text = "正在读取…";
        }
        try
        {
            var rows = await Api.SendAsync(
                QueryBuilder.Build($"projects/{ProjectId}/jobs", ("archived", archivedView ? "true" : "false")),
                cancellation: token);
            if (token.IsCancellationRequested || version != requestVersion) return;
            listFailed = false;
            if (lastResponse == rows.GetRawText()) return;
            lastResponse = rows.GetRawText();
            jobs = rows.EnumerateArray().Select(JobItem.From).ToList();
            selected.IntersectWith(jobs.Where(j => j.Terminal).Select(j => j.Id));
            Render();
        }
        catch (OperationCanceledException) when (token.IsCancellationRequested) { }
        catch (Exception error)
        {
            if (token.IsCancellationRequested || version != requestVersion) return;
            lastResponse = "";
            listFailed = true;
            State.JobHint = countLabel.Text = "读取失败";
            body.Children.Clear();
            var stack = new StackPanel();
            stack.Children.Add(new TextBlock { Text = $"任务列表读取失败：{error.Message}", TextWrapping = TextWrapping.Wrap });
            var retry = Act("重试", async (_, _) => await LoadAsync(), "Outline");
            retry.Margin = new Thickness(0, 10, 0, 0);
            retry.HorizontalAlignment = HorizontalAlignment.Left;
            stack.Children.Add(retry);
            body.Children.Add(new Border { Style = (Style)Application.Current.FindResource("Card"), Padding = new Thickness(20), Child = stack });
        }
        finally { if (version == requestVersion) loading = false; }
    }

    private void Render()
    {
        body.Children.Clear();
        UpdateToolbar();
        State.JobHint = countLabel.Text = jobs.Count == 0 ? "" : $"{jobs.Count} 个任务";
        if (jobs.Count == 0) { body.Children.Add(EmptyState()); return; }
        var running = jobs.Where(j => j.Active).ToList();
        var failed = jobs.Where(j => j.State == "FAILED").ToList();
        var finished = jobs.Where(j => j.Terminal && j.State != "FAILED").ToList();
        if (running.Count > 0)
        {
            var stack = new StackPanel();
            stack.Children.Add(GroupHeading("正在运行", $"{running.Count} 个任务"));
            foreach (var job in running) stack.Children.Add(Row(job));
            body.Children.Add(new Border { Child = stack, Margin = new Thickness(0, 0, 0, 16) });
        }
        if (failed.Count > 0)
            body.Children.Add(Group("failed", "失败任务", $"{failed.Count} 条 · 展开查看错误与重试", failed, true));
        foreach (var group in finished.GroupBy(j => DateTimeOffset.TryParse(j.CreatedAt, out var date)
            ? date.ToLocalTime().Date : DateTime.MinValue).OrderByDescending(g => g.Key))
        {
            string key = group.Key.ToString("yyyy-MM-dd");
            body.Children.Add(Group(key, group.Key == DateTime.MinValue ? "日期未知" : group.Key.ToString("yyyy/M/d"),
                $"{group.Count()} 条{(archivedView ? "已归档" : "已结束")}任务", group));
        }
    }

    private Border Row(JobItem job)
    {
        var row = new Border
        {
            BorderBrush = AssetPageUi.Brush("Line"), BorderThickness = new Thickness(1),
            Background = AssetPageUi.Brush("Surface"), Margin = new Thickness(0, 0, 0, 6), Tag = job,
        };
        var stateBrush = AssetPageUi.Brush(job.State is "FAILED" or "CANCELLED" ? "Danger" : job.Active ? "Warning" : "Success");
        var select = new Grid();
        if (!archivedView && job.Terminal)
        {
            var box = new CheckBox { VerticalAlignment = VerticalAlignment.Center, HorizontalAlignment = HorizontalAlignment.Center,
                IsChecked = selected.Contains(job.Id), IsEnabled = !bulkPending && !pending.Contains(job.Id) };
            System.Windows.Automation.AutomationProperties.SetName(box, $"选择{job.Name}");
            box.Click += (_, _) =>
            {
                if (box.IsChecked == true) selected.Add(job.Id); else selected.Remove(job.Id);
                UpdateToolbar();
            };
            select.Children.Add(box);
        }
        var type = new StackPanel { VerticalAlignment = VerticalAlignment.Center };
        type.Children.Add(new TextBlock { Text = job.Name, FontSize = 13, TextWrapping = TextWrapping.Wrap });
        type.Children.Add(new TextBlock { Text = job.StatusLabel, FontSize = 13, FontWeight = FontWeights.SemiBold,
            Foreground = stateBrush, Margin = new Thickness(0, 5, 0, 0), TextWrapping = TextWrapping.Wrap });
        var info = new StackPanel { VerticalAlignment = VerticalAlignment.Center };
        info.Children.Add(new TextBlock { Text = job.ModelName.Length > 0 ? job.ModelName : job.NodeName.Length > 0 ? job.NodeName : "系统任务",
            FontSize = 13, TextWrapping = TextWrapping.Wrap });
        if (job.Active)
        {
            var bar = new ProgressBar { Value = job.Progress, Height = 5, Margin = new Thickness(0, 10, 0, 6) };
            System.Windows.Automation.AutomationProperties.SetName(bar, $"{job.Name}进度");
            info.Children.Add(bar);
            info.Children.Add(Caption($"{job.Progress}% · 尝试 {job.Attempt}/{job.MaxAttempts}"));
        }
        var detail = Caption($"{(job.HasDuration ? $"耗时 {job.Duration:0.0} 秒" : "尚未完成")} · {job.CostLabel}");
        detail.TextWrapping = TextWrapping.Wrap; detail.Margin = new Thickness(0, 5, 0, 0); info.Children.Add(detail);
        if (job.ErrorLabel.Length > 0)
            info.Children.Add(new TextBlock { Text = job.ErrorLabel, FontSize = 12, Foreground = AssetPageUi.Brush("Danger"),
                TextWrapping = TextWrapping.Wrap, Margin = new Thickness(0, 6, 0, 0) });

        var actions = new WrapPanel { VerticalAlignment = VerticalAlignment.Center };
        var ledger = Act("调用与成本", (_, _) => new JobDetailsWindow(Host, Api, ProjectId, job).ShowDialog(), "Compact");
        ledger.HorizontalAlignment = HorizontalAlignment.Left;
        ledger.Margin = new Thickness(0, 6, 0, 0);
        ledger.IsEnabled = !bulkPending && !pending.Contains(job.Id);
        StyleAction(ledger);
        info.Children.Add(ledger);
        if (job.ResultImageUrl.Length > 0)
            actions.Children.Add(Act("查看结果", (_, _) => OpenImage(job.ResultImageUrl, job.Name), "Compact"));
        if (!archivedView && job.CanCancel)
        {
            var cancel = Act("取消", async (_, _) => await JobAction(job, "cancel", "取消任务"), "Compact");
            cancel.Margin = new Thickness(6, 0, 0, 0);
            actions.Children.Add(cancel);
        }
        if (!archivedView && job.CanRetry)
        {
            var retry = Act("重试", async (_, _) =>
            {
                if (MessageBox.Show(Host, "重试可能再次调用模型并产生费用。确认重试这个任务？", "重试任务",
                    MessageBoxButton.YesNo, MessageBoxImage.Question) == MessageBoxResult.Yes)
                    await JobAction(job, "retry", "重试任务");
            }, "Compact");
            retry.Margin = new Thickness(6, 0, 0, 0);
            actions.Children.Add(retry);
        }
        if (!archivedView && job.Terminal)
        {
            var archive = Act("归档", async (_, _) => await JobAction(job, "archive", "归档"), "Compact");
            archive.Margin = new Thickness(6, 0, 0, 0);
            actions.Children.Add(archive);
        }
        if (archivedView)
        {
            var restore = Act("恢复", async (_, _) => await JobAction(job, "restore", "恢复"), "Compact");
            restore.Margin = new Thickness(6, 0, 0, 0);
            actions.Children.Add(restore);
            if (job.State is "FAILED" or "CANCELLED")
            {
                var purge = Act("彻底删除", async (_, _) =>
                {
                    if (MessageBox.Show(Host, "仅无候选、生成记录、工作流或任务依赖的失败任务可以彻底删除。继续吗？",
                        "彻底删除", MessageBoxButton.YesNo, MessageBoxImage.Warning) == MessageBoxResult.Yes)
                        await JobAction(job, "", "删除", useDelete: true);
                }, "CompactDanger");
                purge.Margin = new Thickness(6, 0, 0, 0);
                actions.Children.Add(purge);
            }
        }
        foreach (var button in actions.Children.OfType<Button>())
        {
            button.IsEnabled = !bulkPending && !pending.Contains(job.Id);
            button.Margin = new Thickness(0, 3, 6, 3); button.MinHeight = 38;
            StyleAction(button);
        }
        // A dedicated result action avoids opening an image when another control is clicked.
        row.Child = new Border { BorderBrush = stateBrush, BorderThickness = new Thickness(3, 0, 0, 0),
            Padding = new Thickness(8, 14, 8, 14), Child = new JobRowPanel(select, type, info, actions) };
        return row;
    }

    private async Task JobAction(JobItem job, string action, string label, bool useDelete = false)
    {
        if (bulkPending || pending.Contains(job.Id)) return;
        var version = activationVersion;
        var token = lifetime.Token;
        var projectId = ProjectId;
        pending.Add(job.Id);
        Render();
        try
        {
            if (useDelete)
                await Api.SendOptionalAsync($"jobs/{job.Id}?project_id={projectId}", HttpMethod.Delete, cancellation: token);
            else
                await Api.SendAsync($"jobs/{job.Id}/{action}?project_id={projectId}", HttpMethod.Post, cancellation: token);
            if (token.IsCancellationRequested || version != activationVersion) return;
            notice.Text = label switch
            {
                "取消任务" => "任务已请求取消",
                "重试任务" => "任务已重新排队",
                "归档" => "任务已移入历史记录",
                "恢复" => "任务已恢复到近期记录",
                "删除" => "无引用任务已彻底删除",
                _ => "操作已完成",
            };
            await LoadAsync();
        }
        catch (OperationCanceledException) when (token.IsCancellationRequested) { }
        catch (Exception error)
        {
            if (!token.IsCancellationRequested && version == activationVersion)
                notice.Text = $"{label}失败：{error.Message}";
        }
        finally
        {
            if (version == activationVersion)
            {
                pending.Remove(job.Id);
                // Re-enable existing buttons without replacing an error/retry panel.
                UpdateActionAvailability();
            }
        }
    }

    private async void ArchiveAllCompleted(object sender, RoutedEventArgs e)
    {
        if (bulkPending || pending.Count > 0) return;
        if (MessageBox.Show(Host, "将所有已完成、失败和已取消任务移入历史记录？生成候选与溯源信息不会删除。",
            "归档全部终态", MessageBoxButton.YesNo, MessageBoxImage.Question) != MessageBoxResult.Yes) return;
        await BulkAction("archive-completed", null, result => result.Number("archived_count") > 0
                ? $"已归档 {result.Number("archived_count")} 条已结束任务"
                : "没有可归档的已结束任务");
    }

    private async void BulkArchive(object sender, RoutedEventArgs e)
    {
        if (selected.Count == 0) return;
        await BulkAction("bulk-archive", new { job_ids = selected.ToList() },
            result => $"已批量归档 {result.Array("archived").Count} 条任务");
    }

    private async Task BulkAction(string action, object? payload, Func<JsonElement, string> message)
    {
        if (bulkPending || pending.Count > 0) return;
        var version = activationVersion;
        var token = lifetime.Token;
        var projectId = ProjectId;
        bulkPending = true;
        Render();
        try
        {
            var result = await Api.SendAsync($"projects/{projectId}/jobs/{action}", HttpMethod.Post,
                payload, cancellation: token);
            if (token.IsCancellationRequested || version != activationVersion) return;
            notice.Text = message(result);
            selected.Clear();
            await LoadAsync();
        }
        catch (OperationCanceledException) when (token.IsCancellationRequested) { }
        catch (Exception error)
        {
            if (!token.IsCancellationRequested && version == activationVersion)
                notice.Text = $"归档失败：{error.Message}";
        }
        finally
        {
            if (version == activationVersion)
            {
                bulkPending = false;
                UpdateActionAvailability();
            }
        }
    }

    private void UpdateActionAvailability()
    {
        UpdateToolbar();
        if (!listFailed) Render();
    }

    public override void PollTick()
    {
        if (!lifetime.IsCancellationRequested && !loading && !bulkPending && pending.Count == 0) _ = LoadAsync();
    }

    public override Task RefreshAsync() => LoadAsync();
}
