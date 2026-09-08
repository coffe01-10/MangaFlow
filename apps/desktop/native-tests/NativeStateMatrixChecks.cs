using System.Net;
using System.Net.Http;
using System.IO;
using System.Reflection;
using System.Text.Json;
using System.Text.RegularExpressions;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using System.Windows.Threading;
using MangaFlow.Native;
using MangaFlow.Native.Services;
using MangaFlow.Native.Views;

// NUI-6C: per-page state acceptance matrix. Drives every registered page (and the five
// asset subpages) through empty / loading / error / retry-recovery / project-switch
// states against a controlled HTTP fake, and renders the empty state as evidence.
// Cells that need a live machine (real data, real dialogs over real windows) stay
// NOT RUN in docs; this harness registers everything the offline contract can prove.
internal static class NativeStateMatrixChecks
{
    private const string ErrorDetail = "状态矩阵读取失败";

    public static async Task Run(string output)
    {
        var factory = typeof(MainWindow).GetMethod("CreateView", BindingFlags.NonPublic | BindingFlags.Static)
            ?? throw new Exception("反射入口缺失：MainWindow.CreateView");
        // Web's 17 business pages map onto 13 native view ids + 5 asset subviews below.
        var globalPages = new[] { "home", "help", "settings-global", "usage" };
        var projectPages = new[] { "source", "assets", "script", "storyboard", "generate", "library", "jobs", "workflow", "project-settings" };

        foreach (var id in globalPages.Concat(projectPages))
        {
            try { await EmptyState(id, factory, output); }
            catch (Exception error) { throw new Exception($"{id}/empty: {error.Message}", error); }
            try { await ErrorState(id, factory); }
            catch (Exception error) { throw new Exception($"{id}/error: {error.Message}", error); }
        }
        await AssetSubpages(factory);

        foreach (var id in projectPages)
        {
            try { await ProjectSwitch(id, factory); }
            catch (Exception error) { throw new Exception($"{id}/switch: {error.Message}", error); }
        }
        Console.WriteLine("PASS: state matrix — empty/loading/error/recovery/switch for 17 pages and asset subviews");
    }

    private static async Task EmptyState(string id, MethodInfo factory, string output)
    {
        var fake = new Fake(FakeMode.Empty);
        using var api = new ApiClient("http://127.0.0.1:12345", new Handler(fake));
        var element = (FrameworkElement)factory.Invoke(null, [id])!;
        var view = (IWorkspaceView)element;
        view.Activate(Context(api, "matrix-a"));
        await Settle(fake);
        Layout(element);
        var text = Text(element);
        Require(text.Length > 0, $"{id}: empty state rendered nothing at all");
        Require(!text.Contains("正在读取"), $"{id}: empty state left a perpetual loading label");
        // Back-navigation contract: a clean page must let the user leave without a dialog.
        Require(await view.ConfirmLeaveAsync(), $"{id}: clean page blocks back navigation");
        view.Deactivate();
        Render(element, Path.Combine(output, $"native-matrix-{id}-empty.png"));
    }

    private static async Task AssetSubpages(MethodInfo factory)
    {
        var fake = new Fake(FakeMode.Empty);
        using var api = new ApiClient("http://127.0.0.1:12345", new Handler(fake));
        var view = (AssetsView)factory.Invoke(null, ["assets"])!;
        view.Activate(Context(api, "matrix-a"));
        await Settle(fake);
        foreach (var sub in new[] { "characters", "outfits", "scenes", "style", "references" })
        {
            view.Switch(sub);
            await Settle(fake);
            Layout(view);
            var text = Text(view);
            Require(text.Length > 0, $"assets/{sub}: empty state rendered nothing");
            Require(!text.Contains("正在读取"), $"assets/{sub}: empty state left a perpetual loading label");
        }
        view.Deactivate();
    }

    private static async Task ErrorState(string id, MethodInfo factory)
    {
        var fake = new Fake(FakeMode.Error);
        using var api = new ApiClient("http://127.0.0.1:12345", new Handler(fake));
        var element = (FrameworkElement)factory.Invoke(null, [id])!;
        var view = (IWorkspaceView)element;
        view.Activate(Context(api, "matrix-a"));
        await Settle(fake);
        Layout(element);
        if (fake.Dispatched == 0)
        {
            view.Deactivate();
            return; // Static pages (help) never read; the error cell is N/A offline.
        }
        var text = Text(element);
        Require(text.Contains(ErrorDetail), $"{id}: server error detail is not visible in the failure state");
        // Recovery: retry every error panel's button, then a full refresh (some views
        // keep several independent error panels; refresh reloads them all).
        fake.Mode = FakeMode.Empty;
        for (var round = 0; round < 4; round++)
        {
            var retries = Buttons(element).Where(b => b.Content?.ToString() == "重试").ToList();
            if (retries.Count == 0) break;
            foreach (var retry in retries)
                retry.RaiseEvent(new RoutedEventArgs(System.Windows.Controls.Primitives.ButtonBase.ClickEvent));
            await Settle(fake);
            Layout(element);
        }
        await view.RefreshAsync();
        await Settle(fake);
        // Strongest in-app recovery: leave the page and come back (navigation re-queries).
        view.Deactivate();
        view.Activate(Context(api, "matrix-a"));
        await Settle(fake);
        Layout(element);
        Require(!Text(element).Contains(ErrorDetail), $"{id}: error state did not recover after retry");
        view.Deactivate();
    }

    private static async Task ProjectSwitch(string id, MethodInfo factory)
    {
        // The source page also exercises REAL cancellation: its parked replies are
        // cancelled by the deactivation token, so the views' cancellation swallows are
        // actually executed (not just the late-500 path of the other pages).
        var realCancellation = id == "source";
        var fake = new Fake(FakeMode.Parked) { CancelParkedOnToken = realCancellation };
        using var api = new ApiClient("http://127.0.0.1:12345", new Handler(fake));
        var element = (FrameworkElement)factory.Invoke(null, [id])!;
        var view = (IWorkspaceView)element;
        view.Activate(Context(api, "matrix-a"));
        // Let project A's reads fly, then switch identities before any reply arrives.
        await Until(() => fake.ParkedCount > 0);
        view.Deactivate();
        fake.FailParkedWith("旧项目错误-" + id);
        fake.Mode = FakeMode.Empty;
        view.Activate(Context(api, "matrix-b"));
        await Settle(fake);
        Layout(element);
        var text = Text(element);
        Require(!text.Contains("旧项目错误"), $"{id}: late failure from the previous project polluted the new page");
        Require(!text.Contains("甲项目"), $"{id}: previous project identity leaked into the new page");
        view.Deactivate();
    }

    // ============ controlled API ============

    private enum FakeMode { Empty, Error, Parked }

    private sealed class Fake
    {
        public FakeMode Mode;
        public bool CancelParkedOnToken;
        public int Dispatched;
        private int unresolved;
        private readonly List<TaskCompletionSource<HttpResponseMessage>> parked = [];
        private readonly object gate = new();

        public Fake(FakeMode mode) => Mode = mode;

        public int Unresolved => Volatile.Read(ref unresolved);
        public int ParkedCount { get { lock (gate) return parked.Count; } }

        public async Task<HttpResponseMessage> Respond(HttpRequestMessage request, CancellationToken token)
        {
            Interlocked.Increment(ref Dispatched);
            Interlocked.Increment(ref unresolved);
            try
            {
                if (Mode == FakeMode.Parked)
                {
                    TaskCompletionSource<HttpResponseMessage> reply;
                    lock (gate)
                    {
                        reply = new(TaskCreationOptions.RunContinuationsAsynchronously);
                        parked.Add(reply);
                    }
                    if (CancelParkedOnToken)
                        token.Register(() => reply.TrySetCanceled(token));
                    return await reply.Task;
                }
                return Mode == FakeMode.Error
                    ? Response($"{{\"detail\":\"{ErrorDetail}\"}}", HttpStatusCode.InternalServerError)
                    : Response(Shape(request));
            }
            finally { Interlocked.Decrement(ref unresolved); }
        }

        public void FailParkedWith(string detail)
        {
            List<TaskCompletionSource<HttpResponseMessage>> held;
            lock (gate) { held = [.. parked]; parked.Clear(); }
            foreach (var reply in held) reply.TrySetResult(Response($"{{\"detail\":\"{detail}\"}}", HttpStatusCode.InternalServerError));
        }
    }

    private static string Shape(HttpRequestMessage request)
    {
        var path = request.RequestUri!.AbsolutePath.Split('?')[0];
        if (request.Method == HttpMethod.Post && path.EndsWith("/workflows"))
            return """{"id":"wf-matrix","name":"单页生产流程"}""";
        return path switch
        {
            var p when p.EndsWith("/dashboard") => """
                {"projects":[],"totals":{"project_count":0,"page_count":0,"selected_page_count":0,"review_page_count":0,"pending_job_count":0},"ai_overview":{"enabled_model_count":0,"configured_connection_count":0,"healthy_connection_count":0}}
                """,
            // Project detail follows the requested id so the evidence PNG matches its context.
            var p when Regex.IsMatch(p, "/projects/([^/]+)$") =>
                "{\"id\":\"" + Regex.Match(p, "/projects/([^/]+)$").Groups[1].Value
                    + "\",\"name\":\"详情项目\",\"workflow_mode\":\"DIRECTOR\",\"default_resolution\":\"2K\",\"default_concurrency\":4}",
            var p when p.TrimEnd('/').Equals("/projects") => """[{"id":"matrix-b","name":"乙项目","summary":"","pending_jobs":0,"failed_jobs":0}]""",
            // Object-shaped endpoints: the library feed, the generation workbench and a
            // chapter's script are JSON objects; an array reply reads as "still loading".
            var p when p.EndsWith("/library") || p.EndsWith("/generation-workbench") || p.EndsWith("/script") => "{}",
            _ => "[]",
        };
    }

    // ============ helpers ============

    private static WorkspaceContext Context(ApiClient api, string project) => new()
    {
        Api = api, Cache = new ApiCache(), State = new WorkspaceState { Connected = true }, Window = null!,
        Project = new ProjectItem(project, project == "matrix-a" ? "甲项目" : "乙项目", "", 0, 0),
        NavigateSection = (_, _) => Task.CompletedTask, OpenDashboard = () => Task.CompletedTask,
    };

    private static async Task Settle(Fake fake)
    {
        using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(5));
        while (true)
        {
            await Until(() => fake.Unresolved == 0, timeout.Token);
            Drain();
            await Task.Delay(40, timeout.Token);
            if (fake.Unresolved == 0) return;
        }
    }

    private static async Task Until(Func<bool> condition, CancellationToken? cancellation = null)
    {
        using var timeout = cancellation == null ? new CancellationTokenSource(TimeSpan.FromSeconds(3)) : null;
        var token = cancellation ?? timeout!.Token;
        while (!condition()) await Task.Delay(5, token);
    }

    private static void Drain() => Dispatcher.CurrentDispatcher.Invoke(() => { }, DispatcherPriority.ApplicationIdle);

    private static IEnumerable<DependencyObject> Descendants(DependencyObject root)
    {
        yield return root;
        for (var i = 0; i < VisualTreeHelper.GetChildrenCount(root); i++)
            foreach (var child in Descendants(VisualTreeHelper.GetChild(root, i))) yield return child;
    }

    private static IEnumerable<Button> Buttons(DependencyObject root) => Descendants(root).OfType<Button>();
    private static string Text(DependencyObject root) =>
        string.Join("\n", Descendants(root).OfType<TextBlock>().Select(t => t.Text));

    private static void Layout(FrameworkElement view, int width = 1320, int height = 900)
    {
        view.Measure(new Size(width, height));
        view.Arrange(new Rect(0, 0, width, height));
        view.UpdateLayout();
        Drain();
    }

    private static void Render(FrameworkElement view, string path)
    {
        const int width = 1320, height = 900;
        Layout(view, width, height);
        var background = new DrawingVisual();
        using (var drawing = background.RenderOpen())
            drawing.DrawRectangle((Brush)Application.Current.FindResource("Paper"), null, new Rect(0, 0, width, height));
        var bitmap = new RenderTargetBitmap(width, height, 96, 96, PixelFormats.Pbgra32);
        bitmap.Render(background);
        bitmap.Render(view);
        var encoder = new PngBitmapEncoder();
        encoder.Frames.Add(BitmapFrame.Create(bitmap));
        using var file = File.Create(path);
        encoder.Save(file);
    }

    private static void Require(bool condition, string message)
    {
        if (!condition) throw new Exception(message);
    }

    private static HttpResponseMessage Response(string json, HttpStatusCode status = HttpStatusCode.OK) =>
        new(status) { Content = new StringContent(json) };

    private sealed class Handler(Fake fake) : HttpMessageHandler
    {
        protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken token)
        {
            if (token.IsCancellationRequested && !fake.CancelParkedOnToken)
                return Task.FromCanceled<HttpResponseMessage>(token);
            return fake.Respond(request, token);
        }
    }
}
