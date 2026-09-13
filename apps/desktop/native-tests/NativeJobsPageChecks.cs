using System.IO;
using System.Net;
using System.Net.Http;
using System.Reflection;
using System.Text.Json;
using System.Web;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Controls.Primitives;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using System.Windows.Threading;
using MangaFlow.Native;
using MangaFlow.Native.Controls;
using MangaFlow.Native.Services;
using MangaFlow.Native.Views;

internal static class NativeJobsPageChecks
{
    internal static void Run(string output)
    {
        Directory.CreateDirectory(output);
        var previous = (string)typeof(KeyValueStore).GetField("path", BindingFlags.Static | BindingFlags.NonPublic)!.GetValue(null)!;
        var prefs = Path.Combine(output, "test-prefs.json"); File.WriteAllText(prefs, "{}"); KeyValueStore.UseLocation(prefs);
        try
        {
            Exception? failure = null;
            var thread = new Thread(() =>
            {
                var app = new Application { ShutdownMode = ShutdownMode.OnExplicitShutdown };
                app.Resources.MergedDictionaries.Add(new ResourceDictionary { Source = new Uri("/MangaFlow.Native;component/Theme.xaml", UriKind.Relative) });
                SynchronizationContext.SetSynchronizationContext(new DispatcherSynchronizationContext());
                app.Dispatcher.BeginInvoke(new Action(async () => { try { await Checks(output); await NativeJobsChecks.Run(output); } catch (Exception e) { failure = e; } finally { app.Shutdown(); } })); app.Run();
            }); thread.SetApartmentState(ApartmentState.STA); thread.Start(); thread.Join();
            if (failure != null) throw new Exception("Jobs page checks failed", failure);
        }
        finally { KeyValueStore.UseLocation(previous); File.Delete(prefs); File.Delete(prefs + ".tmp"); }
    }
    private static async Task Checks(string output)
    {
        var fixture = new Fixture();
        using var api = new ApiClient("http://127.0.0.1:12345", fixture);
        var view = new JobsView();
        try
        {
            view.Activate(new WorkspaceContext { Api = api, State = new(), Window = null!,
                Project = new("layout", "我最讨厌妹妹了", "", 0, 0),
                NavigateSection = (_, _) => Task.CompletedTask, OpenDashboard = () => Task.CompletedTask });
            await Until(() => !Field<bool>(view, "loading"));
            foreach (int width in new[] { 1240, 940, 650, 360 })
            {
                Layout(view, width, 1600);
                var rows = Desc(view).OfType<JobRowPanel>().Where(r => r.ActualHeight > 0).ToArray();
                Require(rows.Length >= 2, "running and failed task rows visible");
                foreach (var row in rows) Fits(row);
                var dates = Desc(view).OfType<Expander>().Where(e => !Equals(e.Tag, "failed")).ToArray();
                Require(dates.All(e => !e.IsExpanded), "completed dates start collapsed");
                Require(Desc(view).OfType<Expander>().Single(e => Equals(e.Tag, "failed")).IsExpanded, "failed group starts open");
                Render(view, width, width < 680 ? 1500 : 1000, Path.Combine(output, $"native-jobs-page-{width}.png"));
            }
            Layout(view, 1240, 1000);
            var failed = Desc(view).OfType<Expander>().Single(e => Equals(e.Tag, "failed"));
            var box = Desc(failed).OfType<CheckBox>().Single();
            box.IsChecked = true; Click(box);
            var selected = Field<Button>(view, "archiveSelected");
            Require(selected.IsEnabled && Equals(selected.Content, "归档已选（1）"), "selection enables toolbar archive and count");
            Require(ReferenceEquals(box, Desc(failed).OfType<CheckBox>().Single()), "selection must not replace the focused row");
            Click(selected); await Until(() => !Field<bool>(view, "bulkPending"));
            Require(fixture.BulkPayload.Contains("job-failed") && fixture.Posts == 1, "bulk archive sends selected job ids exactly once");
            Require(!selected.IsEnabled, "bulk archive clears selection");
            failed = Desc(view).OfType<Expander>().Single(e => Equals(e.Tag, "failed"));
            failed.IsExpanded = false; fixture.Progress = 68; await view.RefreshAsync(); Layout(view, 1240, 1000);
            Require(!Desc(view).OfType<Expander>().Single(e => Equals(e.Tag, "failed")).IsExpanded, "changed poll preserves collapsed failed group");
            var date = Desc(view).OfType<Expander>().First(e => !Equals(e.Tag, "failed"));
            date.IsExpanded = true; fixture.Progress = 72; await view.RefreshAsync(); Layout(view, 1240, 1000);
            Require(Desc(view).OfType<Expander>().First(e => !Equals(e.Tag, "failed")).IsExpanded, "changed poll preserves open date");
            Render(view, 1240, 1000, Path.Combine(output, "native-jobs-page-expanded.png"));
            Click(Field<ToggleButton>(view, "historyTab")); await Until(() => !Field<bool>(view, "loading")); Layout(view, 1240, 1000);
            Require(fixture.Archived && Field<WrapPanel>(view, "bulkActions").Visibility == Visibility.Collapsed, "history queries archived jobs and hides bulk actions");
            var buttons = Desc(view).OfType<Button>().ToArray();
            Require(buttons.Any(b => Equals(b.Content, "恢复")) && !buttons.Any(b => Equals(b.Content, "重试")), "history offers restore and no paid retry");
            Render(view, 1240, 1000, Path.Combine(output, "native-jobs-page-history.png"));
            fixture.Empty = true; await view.RefreshAsync(); Layout(view, 1240, 1000);
            Require(Texts(view).Contains("还没有历史任务"), "history empty state");
            Render(view, 1240, 1000, Path.Combine(output, "native-jobs-page-empty.png"));
            fixture.Fail = true; await view.RefreshAsync(); Layout(view, 1240, 1000);
            Require(Desc(view).OfType<Button>().Any(b => Equals(b.Content, "重试")), "failed read offers retry");
            Render(view, 1240, 1000, Path.Combine(output, "native-jobs-page-error.png"));
            Console.WriteLine("PASS: jobs 1240/940/650/360 layouts, selected archive payload/focus, changed-poll expansion, history action boundaries and empty/error states.");
            Console.WriteLine("Offscreen WPF and HTTP fixtures only; live backend, provider calls, high-DPI windows and frame timing NOT RUN.");
        }
        finally { view.Deactivate(); }
    }
    private static void Fits(Panel panel)
    {
        var boxes = panel.Children.Cast<FrameworkElement>().Select(c => c.TransformToAncestor(panel).TransformBounds(new Rect(c.RenderSize))).ToArray();
        Require(boxes.All(b => b.Left >= -1 && b.Right <= panel.ActualWidth + 1), "child escaped its column");
        for (int i = 0; i < boxes.Length; i++) for (int j = i + 1; j < boxes.Length; j++)
        {
            var overlap = Rect.Intersect(boxes[i], boxes[j]);
            Require(overlap.IsEmpty || overlap.Width <= 1 || overlap.Height <= 1, "layout children overlap");
        }
    }
    private static IEnumerable<DependencyObject> Desc(DependencyObject e) => NativeParityChecks.Descendants(e);
    private static string[] Texts(DependencyObject e) => Desc(e).OfType<TextBlock>().Select(t => t.Text).ToArray();
    private static void Click(ButtonBase b) => b.RaiseEvent(new RoutedEventArgs(ButtonBase.ClickEvent));
    private static T Field<T>(object e, string name) => (T)e.GetType().GetField(name, BindingFlags.Instance | BindingFlags.NonPublic)!.GetValue(e)!;
    private static void Require(bool ok, string message) { if (!ok) throw new Exception(message); }
    private static async Task Until(Func<bool> predicate) { using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(8)); while (!predicate()) await Task.Delay(10, timeout.Token); }
    private static void Layout(FrameworkElement e, int w, int h) { foreach (var child in Desc(e).OfType<UIElement>()) child.InvalidateMeasure(); e.InvalidateMeasure(); e.Measure(new Size(w, h)); e.Arrange(new Rect(0, 0, w, h)); e.UpdateLayout(); }
    private static void Render(FrameworkElement e, int w, int h, string path) { Layout(e, w, h); var bitmap = new RenderTargetBitmap(w, h, 96, 96, PixelFormats.Pbgra32); bitmap.Render(e); var encoder = new PngBitmapEncoder(); encoder.Frames.Add(BitmapFrame.Create(bitmap)); using var f = File.Create(path); encoder.Save(f); }
    private sealed class Fixture : HttpMessageHandler
    {
        internal bool Archived, Empty, Fail;
        internal int Progress = 42, Posts;
        internal string BulkPayload = "";
        protected override async Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken token)
        {
            if (request.Method == HttpMethod.Post)
            {
                Require(request.RequestUri!.AbsolutePath.EndsWith("/jobs/bulk-archive"), "unexpected mutation");
                Posts++; BulkPayload = await request.Content!.ReadAsStringAsync(token);
                return Response("{\"archived\":[\"job-failed\"]}");
            }
            Archived = request.RequestUri!.Query.Contains("archived=true");
            if (Fail) return new HttpResponseMessage(HttpStatusCode.ServiceUnavailable) { Content = new StringContent("{\"detail\":\"服务暂时不可用\"}") };
            if (Empty) return Response("[]");
            object Job(string id, string state, string day) => new
            {
                id, job_type = "GENERATE_PAGE", status = state, progress = Progress,
                attempt_count = 1, max_attempts = 3, model_alias = "Nano Banana 2",
                created_at = $"2026-09-{day}T05:45:23Z",
                duration_ms = state == "GENERATING" ? (int?)null : 4500,
                estimated_cost = (decimal?)null, estimated_cost_status = "UNKNOWN",
                estimated_cost_note = "尚无模型调用记录，无法估算；估算值不等于供应商账单",
                error_code = state == "FAILED" ? "PERMISSION" : "",
                error_message = state == "FAILED" ? "服务账号没有调用该 Vertex 模型的权限" : ""
            };
            var jobs = new List<object> { Job("job-failed", "FAILED", "13"),
                Job("job-complete", "COMPLETED", "12"), Job("job-cancelled", "CANCELLED", "11") };
            if (!Archived) jobs.Insert(0, Job("job-running", "GENERATING", "13"));
            return Response(JsonSerializer.Serialize(jobs));
        }
        private static HttpResponseMessage Response(string json) => new(HttpStatusCode.OK) { Content = new StringContent(json) };
    }
}
