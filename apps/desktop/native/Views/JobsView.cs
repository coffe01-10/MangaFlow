using System.Net.Http;
using System.Text.Json;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Controls.Primitives;
using System.Windows.Input;
using System.Windows.Media;
using MangaFlow.Native.Controls;
using MangaFlow.Native.Services;

namespace MangaFlow.Native.Views;

/// <summary>NUI-2B: jobs center — running/failed groups, history view, archive/restore/delete, costs.</summary>
public sealed class JobsView : WorkspaceView
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

    public JobsView()
    {
        var panel = new StackPanel { Margin = new Thickness(4, 0, 24, 28) };
        var header = new Border { Style = (Style)Application.Current.FindResource("CanvasHeader") };
        var headerGrid = new Grid();
        headerGrid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        headerGrid.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        var heading = new StackPanel();
        heading.Children.Add(new TextBlock { Text = "JOBS", Style = (Style)Application.Current.FindResource("SectionIndex") });
        heading.Children.Add(new TextBlock
        {
            Text = "任务中心 · 每个生成任务都能看懂、取消和重试",
            FontFamily = (FontFamily)Application.Current.FindResource("Serif"),
            FontSize = 21, FontWeight = FontWeights.Bold, Margin = new Thickness(0, 5, 0, 0),
        });
        headerGrid.Children.Add(heading);
        var count = new TextBlock { Style = (Style)Application.Current.FindResource("Caption"), VerticalAlignment = VerticalAlignment.Bottom };
        count.SetBinding(TextBlock.TextProperty, new System.Windows.Data.Binding("JobHint") { Mode = System.Windows.Data.BindingMode.OneWay });
        Grid.SetColumn(count, 1);
        headerGrid.Children.Add(count);
        header.Child = headerGrid;
        panel.Children.Add(header);

        var toolbar = new DockPanel { Margin = new Thickness(0, 0, 0, 14) };
        var tabs = new StackPanel { Orientation = Orientation.Horizontal };
        recentTab.Margin = new Thickness(0, 0, 8, 0);
        tabs.Children.Add(recentTab);
        tabs.Children.Add(historyTab);
        DockPanel.SetDock(tabs, Dock.Left);
        toolbar.Children.Add(tabs);
        archiveAll.Click += ArchiveAllCompleted;
        DockPanel.SetDock(archiveAll, Dock.Right);
        toolbar.Children.Add(archiveAll);
        panel.Children.Add(toolbar);
        notice.Margin = new Thickness(0, 0, 0, 10);
        panel.Children.Add(notice);
        panel.Children.Add(body);
        scroller.Content = panel;
        recentTab.Click += (_, _) => SwitchView(false);
        historyTab.Click += (_, _) => SwitchView(true);
        Content = scroller;
    }

    private void SwitchView(bool history)
    {
        if (archivedView == history) return;
        archivedView = history;
        recentTab.IsChecked = !history;
        historyTab.IsChecked = history;
        selected.Clear();
        notice.Text = "";
        archiveAll.Visibility = history ? Visibility.Collapsed : Visibility.Visible;
        Render();
    }

    public override async void Activate(WorkspaceContext context)
    {
        base.Activate(context);
        await LoadAsync();
    }

    private async Task LoadAsync()
    {
        try
        {
            var rows = await Api.SendAsync(
                QueryBuilder.Build($"projects/{ProjectId}/jobs", ("archived", archivedView ? "true" : "false")),
                cancellation: lifetime.Token);
            if (lifetime.Token.IsCancellationRequested) return;
            jobs = rows.EnumerateArray().Select(JobItem.From).ToList();
            Render();
        }
        catch (Exception error) when (error is not OperationCanceledException)
        {
            body.Children.Clear();
            var stack = new StackPanel();
            stack.Children.Add(new TextBlock { Text = $"任务列表读取失败：{error.Message}", TextWrapping = TextWrapping.Wrap });
            var retry = Act("重试", async (_, _) => await LoadAsync(), "Outline");
            retry.Margin = new Thickness(0, 10, 0, 0);
            retry.HorizontalAlignment = HorizontalAlignment.Left;
            stack.Children.Add(retry);
            body.Children.Add(new Border { Style = (Style)Application.Current.FindResource("Card"), Padding = new Thickness(20), Child = stack });
        }
    }

    private void Render()
    {
        body.Children.Clear();
        State.JobHint = jobs.Count == 0 ? "" : $"{jobs.Count} 个任务";
        if (jobs.Count == 0)
        {
            var stack = new StackPanel();
            stack.Children.Add(new TextBlock
            {
                Text = archivedView ? "还没有历史任务" : "当前没有任务",
                FontFamily = (FontFamily)Application.Current.FindResource("Serif"), FontSize = 18, FontWeight = FontWeights.Bold,
            });
            stack.Children.Add(Caption(archivedView
                ? "归档后的已结束任务会保留在这里，可随时恢复。"
                : "剧本解析、页面生成、检查和修复都会列在这里。"));
            body.Children.Add(new Border { Style = (Style)Application.Current.FindResource("Card"), Padding = new Thickness(26), Child = stack });
            return;
        }

        var running = jobs.Where(j => j.Active).ToList();
        var failed = jobs.Where(j => j.State == "FAILED").ToList();
        var finished = jobs.Where(j => j.Terminal && j.State != "FAILED").ToList();

        if (running.Count > 0)
        {
            body.Children.Add(SectionLabel($"正在运行 · {running.Count}"));
            foreach (var job in running) body.Children.Add(Row(job));
        }
        if (failed.Count > 0)
        {
            body.Children.Add(SectionLabel($"失败任务 · {failed.Count} 条 · 展开查看错误与重试"));
            foreach (var job in failed) body.Children.Add(Row(job));
        }
        body.Children.Add(SectionLabel($"{(archivedView ? "已归档" : "已结束")} · {finished.Count} 条"));
        foreach (var job in finished) body.Children.Add(Row(job));
        if (!archivedView && selected.Count > 0)
        {
            var bar = new StackPanel { Orientation = Orientation.Horizontal, Margin = new Thickness(0, 12, 0, 0) };
            var bulk = Act($"归档已选（{selected.Count}）", BulkArchive, "Compact");
            bar.Children.Add(bulk);
            body.Children.Add(bar);
        }
    }

    private static TextBlock SectionLabel(string text) => new()
    {
        Text = text, Style = (Style)Application.Current.FindResource("SectionIndex"),
        Margin = new Thickness(0, 16, 0, 8),
    };

    private Border Row(JobItem job)
    {
        var row = new Border
        {
            Style = (Style)Application.Current.FindResource("Card"),
            Padding = new Thickness(18, 13, 14, 13),
            Margin = new Thickness(0, 0, 0, 7),
            Cursor = job.ResultImageUrl.Length > 0 ? Cursors.Hand : null,
            Tag = job,
        };
        var grid = new Grid();
        grid.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        grid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        grid.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        if (!archivedView && job.Terminal)
        {
            var box = new CheckBox { VerticalAlignment = VerticalAlignment.Center, Margin = new Thickness(0, 0, 12, 0) };
            box.IsChecked = selected.Contains(job.Id);
            box.Click += (_, _) =>
            {
                if (box.IsChecked == true) selected.Add(job.Id);
                else selected.Remove(job.Id);
                Render();
            };
            grid.Children.Add(box);
        }
        var info = new StackPanel();
        var titleRow = new StackPanel { Orientation = Orientation.Horizontal };
        titleRow.Children.Add(new TextBlock { Text = job.Name, FontWeight = FontWeights.Bold, FontSize = 14 });
        var status = new TextBlock
        {
            Text = job.StatusLabel, FontWeight = FontWeights.Bold, FontSize = 13,
            Foreground = job.State == "FAILED" ? (Brush)Application.Current.FindResource("Danger")
                : job.Terminal ? (Brush)Application.Current.FindResource("Muted")
                : (Brush)Application.Current.FindResource("Success"),
            Margin = new Thickness(12, 0, 0, 0), VerticalAlignment = VerticalAlignment.Center,
        };
        titleRow.Children.Add(status);
        info.Children.Add(titleRow);
        if (job.Active)
        {
            var progressRow = new StackPanel { Orientation = Orientation.Horizontal, Margin = new Thickness(0, 8, 0, 0) };
            var bar = new ProgressBar { Value = job.Progress, Width = 190, VerticalAlignment = VerticalAlignment.Center };
            System.Windows.Automation.AutomationProperties.SetName(bar, $"{job.Name}进度");
            progressRow.Children.Add(bar);
            progressRow.Children.Add(new TextBlock
            {
                Text = $"{job.Progress}% · 尝试 {job.Attempt}/{job.MaxAttempts}",
                Style = (Style)Application.Current.FindResource("Micro"), Margin = new Thickness(10, 0, 0, 0),
                VerticalAlignment = VerticalAlignment.Center,
            });
            info.Children.Add(progressRow);
        }
        var detailParts = new List<string>();
        if (job.NodeName.Length > 0) detailParts.Add(job.NodeName);
        else if (job.ModelName.Length > 0) detailParts.Add(job.ModelName);
        else detailParts.Add("系统任务");
        if (job.Duration > 0) detailParts.Add($"耗时 {job.Duration:0.#} 秒");
        else if (!job.Terminal) detailParts.Add("尚未完成");
        var cost = job.CostLabel;
        if (cost.Length > 0) detailParts.Add(cost);
        var detail = Caption(string.Join(" · ", detailParts));
        detail.Margin = new Thickness(0, 7, 0, 0);
        info.Children.Add(detail);
        if (job.State == "FAILED" && job.ErrorLabel.Length > 0)
        {
            var reason = new TextBlock
            {
                Text = job.ErrorLabel, FontStyle = FontStyles.Italic, FontSize = 12,
                Foreground = (Brush)Application.Current.FindResource("Danger"),
                TextWrapping = TextWrapping.Wrap, Margin = new Thickness(0, 6, 0, 0),
            };
            info.Children.Add(reason);
        }
        grid.Children.Add(info);
        Grid.SetColumn(info, 1);

        var actions = new StackPanel { Orientation = Orientation.Horizontal, VerticalAlignment = VerticalAlignment.Center };
        if (job.ResultImageUrl.Length > 0)
            actions.Children.Add(Act("查看结果", (_, _) => OpenImage(job.ResultImageUrl, job.Name), "Compact"));
        if (job.CanCancel)
        {
            var cancel = Act("取消", async (_, _) => await JobAction(job, "cancel", "取消任务"), "Compact");
            cancel.Margin = new Thickness(6, 0, 0, 0);
            actions.Children.Add(cancel);
        }
        if (job.CanRetry)
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
        grid.Children.Add(actions);
        Grid.SetColumn(actions, 2);
        row.Child = grid;
        if (job.ResultImageUrl.Length > 0)
            row.MouseLeftButtonDown += (_, e) => { if (e.OriginalSource is Border or TextBlock) OpenImage(job.ResultImageUrl, job.Name); };
        return row;
    }

    private async Task JobAction(JobItem job, string action, string label, bool useDelete = false)
    {
        if (pending.Contains(job.Id)) return;
        pending.Add(job.Id);
        Render();
        try
        {
            if (useDelete)
                await Api.SendOptionalAsync($"jobs/{job.Id}", HttpMethod.Delete, cancellation: lifetime.Token);
            else
                await Api.SendAsync($"jobs/{job.Id}/{action}?project_id={ProjectId}", HttpMethod.Post, cancellation: lifetime.Token);
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
            Cache.Invalidate("jobs:" + ProjectId);
        }
        catch (Exception error) when (error is not OperationCanceledException)
        {
            notice.Text = $"{label}失败：{error.Message}";
        }
        finally
        {
            pending.Remove(job.Id);
        }
    }

    private async void ArchiveAllCompleted(object sender, RoutedEventArgs e)
    {
        if (MessageBox.Show(Host, "将所有已完成、失败和已取消任务移入历史记录？生成候选与溯源信息不会删除。",
            "归档全部终态", MessageBoxButton.YesNo, MessageBoxImage.Question) != MessageBoxResult.Yes) return;
        try
        {
            var result = await Api.SendAsync($"projects/{ProjectId}/jobs/archive-completed", HttpMethod.Post, cancellation: lifetime.Token);
            notice.Text = result.Number("archived_count") > 0
                ? $"已归档 {result.Number("archived_count")} 条已结束任务"
                : "没有可归档的已结束任务";
            await LoadAsync();
        }
        catch (Exception error) when (error is not OperationCanceledException)
        {
            notice.Text = $"清空失败：{error.Message}";
        }
    }

    private async void BulkArchive(object sender, RoutedEventArgs e)
    {
        try
        {
            var result = await Api.SendAsync($"projects/{ProjectId}/jobs/bulk-archive", HttpMethod.Post,
                new { job_ids = selected.ToList() }, cancellation: lifetime.Token);
            notice.Text = $"已批量归档 {result.Array("archived").Count} 条任务";
            selected.Clear();
            await LoadAsync();
        }
        catch (Exception error) when (error is not OperationCanceledException)
        {
            notice.Text = $"批量归档失败：{error.Message}";
        }
    }

    public override void PollTick()
    {
        if (jobs.Any(j => j.Active)) _ = LoadAsync();
    }

    public override Task RefreshAsync()
    {
        _ = LoadAsync();
        return Task.CompletedTask;
    }
}
