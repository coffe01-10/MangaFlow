using System.Text.Json;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;
using System.Windows.Media;
using MangaFlow.Native.Services;

namespace MangaFlow.Native.Views;

/// <summary>Read-only job ledger, loaded on demand with a bounded cursor page.</summary>
public sealed class JobDetailsWindow : Window
{
    private readonly ApiClient api;
    private readonly string projectId;
    private readonly JobItem job;
    private readonly CancellationTokenSource lifetime = new();
    private readonly StackPanel attempts = new();
    private readonly TextBlock notice = new() { TextWrapping = TextWrapping.Wrap };
    private readonly Button more;
    private bool loading;
    private string? cursor;
    private bool loaded;
    private readonly HashSet<string> seen = [];

    public JobDetailsWindow(Window owner, ApiClient api, string projectId, JobItem job)
    {
        Owner = owner;
        this.api = api;
        this.projectId = projectId;
        this.job = job;
        Title = "调用与成本 · " + job.Name;
        Width = 660; Height = 720; MinWidth = 480; MinHeight = 360;
        WindowStartupLocation = WindowStartupLocation.CenterOwner;
        ShowInTaskbar = false;
        Background = (Brush)Application.Current.FindResource("Paper");
        var root = new DockPanel { Margin = new Thickness(24) };
        var footer = new DockPanel { Margin = new Thickness(0, 16, 0, 0) };
        var close = Kit.Act("关闭", (_, _) => Close(), "Outline");
        close.IsCancel = true;
        close.HorizontalAlignment = HorizontalAlignment.Right;
        DockPanel.SetDock(close, Dock.Right);
        footer.Children.Add(close);
        var hint = Kit.Caption("未知不等于 0；CLI 费用未知不等于免费。");
        hint.TextWrapping = TextWrapping.Wrap;
        footer.Children.Add(hint);
        DockPanel.SetDock(footer, Dock.Bottom);
        root.Children.Add(footer);
        var body = new StackPanel();
        body.Children.Add(new TextBlock { Text = "调用与成本", FontSize = 23, FontWeight = FontWeights.Bold,
            FontFamily = (FontFamily)Application.Current.FindResource("Serif") });
        body.Children.Add(Kit.Caption($"{job.Name} · {job.StatusLabel}"));
        var summary = new StackPanel();
        Field(summary, "费用估算", job.CostLabel);
        var costStatus = job.Cost.Status switch { "AVAILABLE" => "完整估算", "PARTIAL" => "部分估算", _ => "暂不可估算" };
        Field(summary, "估算币种 / 完整度", $"{(job.Cost.Currency.Length > 0 ? job.Cost.Currency : "未知")} · {costStatus}");
        Field(summary, "价格版本", job.PricingVersions.Count > 0 ? string.Join("\n", job.PricingVersions) : "无可用价格版本");
        Field(summary, "耗时 / 调度尝试", $"{(job.HasDuration ? $"{job.Duration:0.0} 秒" : "尚未完成")} · {job.Attempt}/{job.MaxAttempts}");
        if (job.ErrorLabel.Length > 0) Field(summary, "任务错误", job.ErrorLabel);
        body.Children.Add(new Border { Style = (Style)Application.Current.FindResource("Card"),
            Padding = new Thickness(16), Margin = new Thickness(0, 16, 0, 16), Child = summary });
        body.Children.Add(Kit.FieldLabel("模型调用明细"));
        body.Children.Add(Kit.Caption("每次实际派发分别记录，包括重试与路由切换。"));
        body.Children.Add(attempts);
        notice.Margin = new Thickness(0, 12, 0, 8);
        body.Children.Add(notice);
        more = Kit.Act("读取调用记录", async (_, _) => await LoadAsync(), "Outline");
        more.HorizontalAlignment = HorizontalAlignment.Left;
        body.Children.Add(more);
        root.Children.Add(new ScrollViewer { Content = body, VerticalScrollBarVisibility = ScrollBarVisibility.Auto });
        Content = root;
        Loaded += async (_, _) => { close.Focus(); await LoadAsync(); };
        Closed += (_, _) => { lifetime.Cancel(); lifetime.Dispose(); };
        PreviewKeyDown += (_, e) => { if (e.Key == Key.Escape) { e.Handled = true; Close(); } };
    }

    private async Task LoadAsync()
    {
        if (loading || (loaded && cursor == null) || lifetime.IsCancellationRequested) return;
        var token = lifetime.Token;
        loading = true;
        more.IsEnabled = false;
        notice.Text = "正在读取调用记录…";
        try
        {
            var result = await api.SendAsync(QueryBuilder.Build("usage/attempts",
                ("project_id", projectId), ("job_id", job.Id), ("limit", 50), ("cursor", cursor)), cancellation: token);
            if (token.IsCancellationRequested) return;
            foreach (var attempt in result.Array("items"))
                if (seen.Add(attempt.Text("id"))) attempts.Children.Add(AttemptCard(attempt));
            cursor = result.TextOrNull("next_cursor");
            loaded = true;
            notice.Text = seen.Count == 0 ? "暂无模型调用记录；系统任务可能不调用模型。" : $"已显示 {seen.Count} 次实际派发";
            more.Content = "加载更多";
            more.Visibility = cursor == null ? Visibility.Collapsed : Visibility.Visible;
        }
        catch (OperationCanceledException) when (token.IsCancellationRequested) { }
        catch (Exception error)
        {
            if (token.IsCancellationRequested) return;
            notice.Text = "调用记录读取失败：" + error.Message;
            more.Content = "重试读取";
        }
        finally { loading = false; if (!token.IsCancellationRequested) more.IsEnabled = true; }
    }

    private static Expander AttemptCard(JsonElement attempt)
    {
        var details = new StackPanel { Margin = new Thickness(12, 4, 12, 12) };
        Field(details, "供应商 / 模型 / 通道", $"{attempt.Text("provider")} · {attempt.Text("model_id")} · {attempt.Text("channel")}");
        Field(details, "派发", $"调度尝试 {attempt.Number("job_attempt")} · 第 {attempt.Number("dispatch_no")} 次派发" + (attempt.Flag("route_switched") ? " · 已切换路由" : ""));
        Field(details, "开始时间", DateTimeOffset.TryParse(attempt.Text("started_at"), out var time)
            ? time.ToLocalTime().ToString("yyyy-MM-dd HH:mm:ss zzz") : "未知");
        Field(details, "输入 / 输出 / 缓存 Token", $"{Value(attempt, "input_tokens")} / {Value(attempt, "output_tokens")} / {Value(attempt, "cached_input_tokens")}");
        Field(details, "输出图片 / 耗时（ms）", $"{Value(attempt, "output_images")} / {Value(attempt, "duration_ms")}");
        Field(details, "计量状态 / 来源", $"{Labels.Map(Labels.UsageStatus, attempt.Text("usage_status", "UNKNOWN"))} · {attempt.Text("usage_source", "未知")}");
        Field(details, "上游 Request ID", attempt.Text("request_id", "未返回"));
        if (attempt.Text("error_code").Length > 0 || attempt.Text("error_message").Length > 0)
            Field(details, "调用错误", $"{attempt.Text("error_code")} · {attempt.Text("error_message")}");
        return new Expander
        {
            Header = new TextBlock { Text = $"{attempt.Text("provider")} · {attempt.Text("model_id")} · {Labels.Map(Labels.AttemptOutcome, attempt.Text("outcome", "PENDING"))}", TextWrapping = TextWrapping.Wrap },
            Content = details, Margin = new Thickness(0, 10, 0, 0),
        };
    }

    private static string Value(JsonElement item, string field) =>
        item.Element(field).ValueKind == JsonValueKind.Number ? item.Text(field) : "未知";

    private static void Field(Panel parent, string label, string value)
    {
        var title = Kit.FieldLabel(label);
        title.Margin = new Thickness(0, 8, 0, 3);
        parent.Children.Add(title);
        parent.Children.Add(new TextBlock { Text = value, TextWrapping = TextWrapping.Wrap, FontSize = 13 });
    }
}
