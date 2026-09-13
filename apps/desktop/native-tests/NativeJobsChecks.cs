using System.Net;
using System.Net.Http;
using System.Reflection;
using System.Text.Json;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Controls.Primitives;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using MangaFlow.Native;
using MangaFlow.Native.Services;
using MangaFlow.Native.Views;

internal static class NativeJobsChecks
{
    // Match the real JobRead wire shape (scalar cost, computed milliseconds and nested result).
    private const string Job = """
        {"id":"job-1","job_type":"GENERATE_PAGE","status":"FAILED","progress":40,
         "attempt_count":1,"max_attempts":3,"model_alias":"image-model","workflow_node_id":"node-1",
         "duration_ms":1234,"created_at":"2026-09-08T01:02:03Z","estimated_cost":0.125,
         "estimated_cost_currency":"USD","estimated_cost_status":"PARTIAL",
         "estimated_cost_note":"估算值不等于供应商账单","estimated_cost_pricing_versions":["price-v1"],
         "result":null,"error_code":"PROVIDER_TIMEOUT","error_message":"上游等待超时，请稍后重试"}
        """;

    public static async Task Run(string output)
    {
        var item = JobItem.From(JsonSerializer.Deserialize<JsonElement>(Job));
        Require(item.Cost.Amount == "0.125" && item.Cost.Currency == "USD" && item.Cost.Status == "PARTIAL"
            && item.PricingVersions.SequenceEqual(new[] { "price-v1" }) && item.Duration == 1.234 && item.HasDuration
            && item.NodeName == "节点 node-1" && item.ModelName == "image-model" && item.ErrorLabel.Contains("上游等待超时"), "JobRead fields diverged");
        var unknown = JobItem.From(JsonSerializer.Deserialize<JsonElement>("""{"estimated_cost":null,"duration_ms":null,"result":{"content_url":"/api/v1/assets/a/content"}}"""));
        Require(unknown.CostLabel.Contains("暂不可估算") && !unknown.HasDuration && unknown.ResultImageUrl == "/api/v1/assets/a/content", "unknown cost/duration or result lost");
        var zero = JobItem.From(JsonSerializer.Deserialize<JsonElement>("""{"estimated_cost":0,"estimated_cost_currency":"CNY","duration_ms":0}"""));
        Require(zero.Cost.Amount == "0" && zero.HasDuration && zero.Duration == 0, "zero incorrectly treated as unknown");

        var requests = new List<(HttpRequestMessage Request, TaskCompletionSource<HttpResponseMessage> Reply)>();
        using var api = new ApiClient("http://127.0.0.1:12345", new Handler(request =>
        {
            var reply = new TaskCompletionSource<HttpResponseMessage>(TaskCreationOptions.RunContinuationsAsynchronously);
            requests.Add((request, reply));
            return reply.Task; // Deliberately ignores cancellation to exercise late failures.
        }));
        var view = new JobsView();
        view.Activate(Context(api, "project-a"));
        Require(requests.Count == 1, "activation did not load");
        Click(view, "historyTab");
        Require(requests.Count == 2 && requests[1].Request.RequestUri!.Query.Contains("archived=true"), "history only repainted recent rows");
        requests[1].Reply.SetResult(Response("[" + Job + "]"));
        await Until(() => !Field<bool>(view, "loading"));
        requests[0].Reply.SetResult(Response("{\"detail\":\"obsolete read failure\"}", HttpStatusCode.InternalServerError));
        await Task.Delay(30);
        Require(Field<List<JobItem>>(view, "jobs").Count == 1 && !Text(view).Contains("obsolete"), "late failure replaced history");
        Require(!Buttons(view).Any(b => b.Content?.ToString() == "重试"), "archived job can retry paid work");

        Click(view, "recentTab");
        requests[2].Reply.SetResult(Response("[" + Job + "]"));
        await Until(() => !Field<bool>(view, "loading"));
        var mutation = Call(view, "JobAction", item, "archive", "归档", false);
        await Call(view, "JobAction", item, "archive", "归档", false);
        Require(requests.Count == 4 && !Buttons(view).Single(b => b.Content?.ToString() == "归档").IsEnabled, "mutation not deduplicated/disabled");
        requests[3].Reply.SetResult(Response("{\"detail\":\"归档条件已变化\"}", HttpStatusCode.Conflict));
        await mutation;
        Require(Buttons(view).Single(b => b.Content?.ToString() == "归档").IsEnabled && Text(view).Contains("归档条件已变化"), "failed action cannot recover");
        var oldMutation = Call(view, "JobAction", item, "archive", "归档", false);
        view.Deactivate();
        view.Activate(Context(api, "project-b"));
        Require(!Text(view).Contains("上游等待超时"), "project switch retained old rows");
        requests[5].Reply.SetResult(Response("[]"));
        await Until(() => !Field<bool>(view, "loading"));
        requests[4].Reply.SetResult(Response("{\"detail\":\"obsolete mutation\"}", HttpStatusCode.InternalServerError));
        await oldMutation;
        Require(!Text(view).Contains("obsolete") && Field<List<JobItem>>(view, "jobs").Count == 0, "old mutation polluted new project");

        // Empty recent lists still discover tasks started elsewhere; unchanged polls retain controls/focus.
        view.PollTick(); view.PollTick();
        Require(requests.Count == 7, "empty list polling missing or overlapping");
        requests[6].Reply.SetResult(Response("[" + Job + "]"));
        await Until(() => !Field<bool>(view, "loading"));
        var rowButton = Buttons(view).First();
        var refresh = view.RefreshAsync();
        Require(!refresh.IsCompleted, "refresh returned before reading");
        requests[7].Reply.SetResult(Response("[" + Job + "]"));
        await refresh;
        Require(ReferenceEquals(rowButton, Buttons(view).First()), "unchanged poll recreated focused controls");
        Render(view, output);
        var failedRefresh = view.RefreshAsync();
        requests[8].Reply.SetResult(Response("{\"detail\":\"read failed\"}", HttpStatusCode.InternalServerError));
        await failedRefresh;
        Require(Text(view).Contains("read failed") && Buttons(view).Any(b => b.Content?.ToString() == "重试"), "load failure has no retry UI");
        var recovery = view.RefreshAsync();
        requests[9].Reply.SetResult(Response("[" + Job + "]"));
        await recovery;
        Require(!Text(view).Contains("read failed"), "same data cannot recover error panel");
        view.Deactivate();
        await Details(api, requests, item, output);
        await BulkAndEmptyPolling();
        Console.WriteLine("PASS: jobs real wire contract, history loading, late read/mutation isolation, deduplication, error recovery, stable polling, ledger paging and unknown values");
    }

    private static async Task BulkAndEmptyPolling()
    {
        var posts = 0;
        var release = new TaskCompletionSource<HttpResponseMessage>();
        using var api = new ApiClient("http://127.0.0.1:12345", new Handler(request =>
        {
            if (request.Method == HttpMethod.Post) { posts++; return release.Task; }
            return Task.FromResult(Response("[]"));
        }));
        var view = new JobsView();
        view.Activate(Context(api, "bulk-project"));
        await Until(() => !Field<bool>(view, "loading"));
        await view.RefreshAsync();
        Require(Text(view).Contains("当前没有任务") && !Text(view).Contains("正在读取"), "unchanged empty poll leaves perpetual loading");
        Func<JsonElement, string> message = result => $"归档 {result.Number("archived_count")} 条";
        var first = Call(view, "BulkAction", "archive-completed", null!, message);
        await Call(view, "BulkAction", "archive-completed", null!, message);
        Require(posts == 1 && !Field<Button>(view, "archiveAll").IsEnabled, "bulk mutation not deduplicated/disabled");
        release.SetResult(Response("{\"detail\":\"batch failure\"}", HttpStatusCode.Conflict));
        await first;
        Require(Text(view).Contains("batch failure") && Field<Button>(view, "archiveAll").IsEnabled, "bulk failure cannot recover");
        release = new TaskCompletionSource<HttpResponseMessage>();
        var retry = Call(view, "BulkAction", "archive-completed", null!, message);
        release.SetResult(Response("{\"archived_count\":2}"));
        await retry;
        Require(posts == 2 && Text(view).Contains("归档 2 条") && Field<Button>(view, "archiveAll").IsEnabled, "bulk retry did not refresh and settle");
        view.Deactivate();
    }

    private static async Task Details(ApiClient api, List<(HttpRequestMessage Request, TaskCompletionSource<HttpResponseMessage> Reply)> requests, JobItem item, string output)
    {
        var start = requests.Count;
        var window = new JobDetailsWindow(null!, api, "project-b", item);
        var first = Call(window, "LoadAsync");
        await Call(window, "LoadAsync");
        Require(requests.Count == start + 1 && requests[start].Request.RequestUri!.Query.Contains("project_id=project-b")
            && requests[start].Request.RequestUri!.Query.Contains("job_id=job-1"), "ledger lost scope or deduplication");
        const string attempt = """{"id":"a1","provider":"示例供应商","model_id":"image-model","channel":"CLI","outcome":"SUCCEEDED","job_attempt":1,"dispatch_no":2,"route_switched":true,"duration_ms":null,"input_tokens":null,"output_tokens":0,"usage_status":"UNKNOWN"}""";
        requests[start].Reply.SetResult(Response("{\"items\":[" + attempt + "],\"next_cursor\":\"cursor+1\"}"));
        await first;
        Require(Text(window).Contains("未知 / 0 / 未知") && Text(window).Contains("已切换路由") && Text(window).Contains("price-v1"), "ledger lost unknowns, routes or pricing versions");
        Field<StackPanel>(window, "attempts").Children.OfType<Expander>().Single().IsExpanded = true;
        var content = (FrameworkElement)window.Content;
        content.Measure(new Size(640, 900)); content.Arrange(new Rect(0, 0, 640, 900)); content.UpdateLayout();
        var close = Buttons(content).Single(b => b.Content?.ToString() == "关闭");
        Require(close.ActualHeight > 0 && close.TranslatePoint(new Point(0, close.ActualHeight), content).Y <= content.ActualHeight,
            "ledger close action falls outside the viewport");
        SavePreview(content, 640, 900, System.IO.Path.Combine(output, "native-job-details.png"));
        var next = Call(window, "LoadAsync");
        Require(requests[start + 1].Request.RequestUri!.Query.Contains("cursor=cursor%2b1", StringComparison.OrdinalIgnoreCase), "ledger cursor not encoded");
        requests[start + 1].Reply.SetResult(Response("{\"detail\":\"暂时离线\"}", HttpStatusCode.ServiceUnavailable));
        await next;
        Require(Text(window).Contains("暂时离线") && Field<Button>(window, "more").IsEnabled, "ledger error cannot retry");
        var retry = Call(window, "LoadAsync");
        requests[start + 2].Reply.SetResult(Response("{\"items\":[" + attempt + "],\"next_cursor\":null}"));
        await retry;
        Require(Field<StackPanel>(window, "attempts").Children.Count == 1 && Field<Button>(window, "more").Visibility == Visibility.Collapsed, "ledger duplicated cursor rows or ignored end");
        window.Close();
        var closing = new JobDetailsWindow(null!, api, "project-b", item);
        var late = Call(closing, "LoadAsync");
        closing.Close();
        requests[start + 3].Reply.SetResult(Response("{\"items\":[" + attempt + "],\"next_cursor\":null}"));
        await late;
        Require(Field<StackPanel>(closing, "attempts").Children.Count == 0, "closed ledger accepted a late response");
    }

    private static void Render(JobsView view, string output)
    {
        var rows = Field<List<JobItem>>(view, "jobs");
        rows.Add(rows[0] with { Id = "job-running", State = "GENERATING", StatusLabel = "生成中", Detail = "", ErrorCode = "", CanCancel = true, CanRetry = false });
        rows.Add(rows[0] with { Id = "job-finished", State = "COMPLETED", StatusLabel = "已完成", Detail = "", ErrorCode = "", CanCancel = false, CanRetry = false,
            Cost = new CostEstimate("0.125", "USD", "AVAILABLE", "估算值不等于供应商账单") });
        typeof(JobsView).GetMethod("Render", BindingFlags.Instance | BindingFlags.NonPublic | BindingFlags.DeclaredOnly)!.Invoke(view, null);
        var group = Descendants(view).OfType<Expander>().Single();
        group.IsExpanded = true;
        typeof(JobsView).GetMethod("Render", BindingFlags.Instance | BindingFlags.NonPublic | BindingFlags.DeclaredOnly)!.Invoke(view, null);
        Require(Descendants(view).OfType<Expander>().Single().IsExpanded, "poll lost expanded date group");
        foreach (var width in new[] { 940, 1320 })
        {
            view.Width = width; view.Height = 760;
            view.Measure(new Size(width, 760)); view.Arrange(new Rect(0, 0, width, 760)); view.UpdateLayout();
            SavePreview(view, width, 760, System.IO.Path.Combine(output, $"native-jobs-{width}.png"));
        }
    }

    private static void SavePreview(FrameworkElement view, int width, int height, string path)
    {
        var bitmap = new RenderTargetBitmap(width, height, 96, 96, PixelFormats.Pbgra32);
        var background = new DrawingVisual();
        using (var drawing = background.RenderOpen()) drawing.DrawRectangle((Brush)Application.Current.FindResource("Paper"), null, new Rect(0, 0, width, height));
        bitmap.Render(background); bitmap.Render(view);
        var encoder = new PngBitmapEncoder(); encoder.Frames.Add(BitmapFrame.Create(bitmap));
        using var file = System.IO.File.Create(path);
        encoder.Save(file);
    }

    private static WorkspaceContext Context(ApiClient api, string project) => new()
    {
        Api = api, State = new WorkspaceState(), Window = null!,
        Project = new ProjectItem(project, project, "", 0, 0),
        NavigateSection = (_, _) => Task.CompletedTask, OpenDashboard = () => Task.CompletedTask,
    };
    private static IEnumerable<DependencyObject> Descendants(DependencyObject root)
    {
        yield return root;
        foreach (var child in LogicalTreeHelper.GetChildren(root).OfType<DependencyObject>())
            foreach (var nested in Descendants(child)) yield return nested;
    }
    private static IEnumerable<Button> Buttons(DependencyObject root) => Descendants(root).OfType<Button>();
    private static string Text(DependencyObject root) => string.Join("\n", Descendants(root).OfType<TextBlock>().Select(t => t.Text));
    private static T Field<T>(object target, string name) => (T)target.GetType().GetField(name, BindingFlags.Instance | BindingFlags.NonPublic)!.GetValue(target)!;
    private static Task Call(object target, string name, params object[] args) => (Task)target.GetType().GetMethod(name, BindingFlags.Instance | BindingFlags.NonPublic)!.Invoke(target, args)!;
    private static void Click(JobsView view, string field) => Field<ToggleButton>(view, field).RaiseEvent(new RoutedEventArgs(ButtonBase.ClickEvent));
    private static void Require(bool value, string message) { if (!value) throw new Exception(message); }
    private static async Task Until(Func<bool> condition)
    {
        using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(3));
        while (!condition()) await Task.Delay(5, timeout.Token);
    }
    private static HttpResponseMessage Response(string json, HttpStatusCode status = HttpStatusCode.OK) => new(status) { Content = new StringContent(json) };
    private sealed class Handler(Func<HttpRequestMessage, Task<HttpResponseMessage>> handler) : HttpMessageHandler
    {
        protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken token) => handler(request);
    }
}
