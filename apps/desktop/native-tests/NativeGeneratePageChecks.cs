using System.IO;
using System.Net;
using System.Net.Http;
using System.Reflection;
using System.Text.Json;
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

internal static class NativeGeneratePageChecks
{
    internal static void Run(string output)
    {
        Directory.CreateDirectory(output);
        var previous = (string)typeof(KeyValueStore).GetField("path", BindingFlags.Static | BindingFlags.NonPublic)!.GetValue(null)!;
        var prefs = Path.Combine(output, "test-prefs.json"); File.WriteAllText(prefs, "{}"); KeyValueStore.UseLocation(prefs);
        try
        {
            Exception? failure = null; var thread = new Thread(() =>
            {
                var app = new Application { ShutdownMode = ShutdownMode.OnExplicitShutdown };
                app.Resources.MergedDictionaries.Add(new ResourceDictionary { Source = new Uri("/MangaFlow.Native;component/Theme.xaml", UriKind.Relative) });
                SynchronizationContext.SetSynchronizationContext(new DispatcherSynchronizationContext());
                app.Dispatcher.BeginInvoke(new Action(async () => { try { await Checks(output); await NativeInspectionChecks.Run(output); } catch (Exception e) { failure = e; } finally { app.Shutdown(); } })); app.Run();
            }); thread.SetApartmentState(ApartmentState.STA); thread.Start(); thread.Join();
            if (failure != null) throw new Exception("Generate page checks failed", failure);
        }
        finally { KeyValueStore.UseLocation(previous); File.Delete(prefs); File.Delete(prefs + ".tmp"); }
    }
    private static async Task Checks(string output)
    {
        var fixture = new Fixture(); using var api = new ApiClient("http://127.0.0.1:12345", fixture);
        KeyValueStore.Set("image-model:layout", "model-a"); var view = new GenerateView(); string destination = "";
        try
        {
            view.Activate(new WorkspaceContext { Api = api, State = new(), Window = null!, Project = new ProjectItem("layout", "我最讨厌妹妹了", "", 0, 0), NavigateSection = (s, q) => { destination = s + "?" + q; return Task.CompletedTask; }, OpenDashboard = () => Task.CompletedTask });
            await Until(() => Field<JsonElement>(view, "workbench").ValueKind == JsonValueKind.Object); Layout(view, 1240, 1800);
            Require(Texts(view).Contains("第 1 页候选") && Texts(view).Contains("130 字") && Texts(view).Contains(fixture.Source), "header and source strip use current workbench data");
            Require(Field<WrapPanel>(view, "pageBar").Children.Count == 11 && Field<WrapPanel>(view, "pageBar").Children.OfType<ToggleButton>().All(b => b.MinWidth == 42), "page picker uses compact numbered squares");
            var diagnostics = Desc(view).OfType<Expander>().Single(e => Equals(e.Header, "查看原文覆盖、供应商目录与执行器诊断"));
            diagnostics.IsExpanded = true;
            var modelGrid = Desc(view).OfType<TilePanel>().First(p => p.MaximumColumns == 2);
            Click(modelGrid.Children.OfType<ToggleButton>().Last()); Layout(view, 1240, 1800);
            Require(KeyValueStore.Get("image-model:layout") == "model-b" && Desc(view).OfType<Expander>().Any(e => e.IsExpanded), "model selection persists and retains diagnostic expansion");
            Click(Desc(view).OfType<TilePanel>().First(p => p.MaximumColumns == 2).Children.OfType<ToggleButton>().First()); Layout(view, 1240, 1800);
            Desc(view).OfType<Expander>().Single(e => Equals(e.Header, "查看原文覆盖、供应商目录与执行器诊断")).IsExpanded = false;
            Click(Button(view, "检查分镜")); Require(destination == "storyboard?page=pg-1", "continuity warning opens the exact page");
            Render(view, 1240, 1800, Path.Combine(output, "native-generate-1240.png"));
            Render(view, 650, 2100, Path.Combine(output, "native-generate-650.png"));
            Require(Desc(view).OfType<TilePanel>().First(p => p.MaximumColumns == 2).ActualWidth <= 650, "model grid fits narrow viewport");
            var readCount = fixture.Reads; var selected = Field<WrapPanel>(view, "pageBar").Children.OfType<ToggleButton>().First(); Click(selected);
            Require(fixture.Reads == readCount, "active page does not reload");
            Click(Button(view, "← 上一批")); await Until(() => Field<string?>(view, "viewedBatchId") == "batch-2"); Layout(view, 1240, 2000);
            Require(!Button(view, "先切回最新批次再抽卡").IsEnabled, "history batch disables generation");
            await Invoke(view, "GenerateAsync"); Require(fixture.Generates == 0, "history guard also blocks direct invocation");
            Click(Button(view, "下一批 →")); await Until(() => Field<string?>(view, "viewedBatchId") == "batch-3"); Layout(view, 1240, 2000);
            var generate = Button(view, "生成 1 个 1K 彩色候选"); Click(generate); await Until(() => fixture.Generates == 1); await Task.Delay(20);
            Require(fixture.GenerationBody.Text("model_alias") == "model-a" && fixture.GenerationBody.Text("resolution") == "1K" && fixture.GenerationBody.Number("storyboard_version") == 7, "generation carries selected model, 1K and workbench version");
            fixture.HoldBatch = new TaskCompletionSource<bool>(); var create = Button(view, "＋ 新批次"); Click(create); Click(create);
            await Until(() => fixture.Starts == 1); Require(!Button(view, "正在创建…").IsEnabled, "new batch duplicate clicks submit once");
            fixture.HoldBatch.SetResult(true); await Until(() => !Field<HashSet<string>>(view, "pendingRows").Contains("batch")); fixture.HoldBatch = null; Layout(view, 1240, 2000);
            Require(Field<string?>(view, "viewedBatchId") == null && Texts(view).Contains("批次 4 · 最新"), "new batch returns to latest batch");
            fixture.FailBatch = true; Click(Button(view, "＋ 新批次")); await Until(() => Field<TextBlock>(view, "notice").Text.Contains("创建批次失败")); Layout(view, 1240, 2000);
            Require(Button(view, "＋ 新批次").IsEnabled && Texts(view).Contains("批次 4 · 最新"), "failed batch creation restores controls and preserves latest batch");
            fixture.FailBatch = false; fixture.HoldBatch = new TaskCompletionSource<bool>();
            Click(Button(view, "＋ 新批次")); await Until(() => fixture.Starts == 3);
            Click(Field<WrapPanel>(view, "pageBar").Children.OfType<ToggleButton>().ElementAt(1));
            await Until(() => view.CurrentPage?.PageNumber == 2 && Field<JsonElement>(view, "workbench").Element("page").Text("id") == "pg-2");
            fixture.HoldBatch.SetResult(true); await Until(() => !Field<HashSet<string>>(view, "pendingRows").Contains("batch")); fixture.HoldBatch = null;
            Require(Button(view, "＋ 新批次").IsEnabled && view.CurrentPage?.PageNumber == 2, "late batch response releases new-page controls without changing selected page");
            fixture.ShowCandidates = true; await view.RefreshAsync(); Layout(view, 1240, 2000);
            var frame = Desc(view).OfType<ArtworkFrame>().First();
            Require(Math.Abs(frame.ActualHeight / frame.ActualWidth - 4d / 3) < .01, "candidate preview keeps its artwork aspect ratio");
            Render(view, 1240, 2000, Path.Combine(output, "native-generate-candidates.png"));
            Require(VisualTreeHelper.GetClip(Field<StackPanel>(view, "body")) == null, "refreshed candidate actions are not clipped by the previous body height");
            fixture.Ready = false; await view.RefreshAsync(); Layout(view, 1240, 2000);
            Require(!Button(view, "＋ 新批次").IsEnabled && !Button(view, "先完成页面生产准备").IsEnabled, "readiness blockers disable batch creation and generation");
            Click(Button(view, "去处理")); Require(destination == "assets?view=characters&character=c1", "readiness action keeps blocker target");
            Render(view, 1240, 1800, Path.Combine(output, "native-generate-blocked.png"));
            Console.WriteLine("PASS: generation wide/narrow layout, source data, warning target, compact pages, history guard, model/version payload, new batch dedup/error recovery, readiness gate and blocker navigation.");
            Console.WriteLine("Offscreen WPF + HTTP fixtures; live backend/provider, high-DPI window and frame timing NOT RUN.");
        }
        finally { view.Deactivate(); }
    }
    private static IEnumerable<DependencyObject> Desc(DependencyObject root) => NativeParityChecks.Descendants(root);
    private static string[] Texts(DependencyObject root) => Desc(root).OfType<TextBlock>().Select(t => t.Text).ToArray();
    private static Button Button(FrameworkElement view, string label) { Layout(view, 1240, 2400); return Desc(view).OfType<Button>().Single(b => Equals(b.Content, label)); }
    private static void Click(ButtonBase b) => b.RaiseEvent(new RoutedEventArgs(ButtonBase.ClickEvent));
    private static Task Invoke(object o, string name) => (Task)o.GetType().GetMethod(name, BindingFlags.Instance | BindingFlags.NonPublic)!.Invoke(o, null)!;
    private static T Field<T>(object o, string name) => (T)o.GetType().GetField(name, BindingFlags.Instance | BindingFlags.NonPublic)!.GetValue(o)!;
    private static void Require(bool ok, string text) { if (!ok) throw new Exception(text); }
    private static async Task Until(Func<bool> predicate) { using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(8)); while (!predicate()) await Task.Delay(10, timeout.Token); }
    private static void Layout(FrameworkElement e, int w, int h)
    {
        // Detached roots have no presentation source to schedule ancestor remeasurement.
        // Invalidate the complete test tree before rendering an asynchronously replaced body.
        foreach (var element in Desc(e).OfType<UIElement>()) element.InvalidateMeasure();
        e.InvalidateMeasure();
        e.Measure(new Size(w, h)); e.Arrange(new Rect(0, 0, w, h)); e.UpdateLayout();
        e.Dispatcher.Invoke(() => { }, DispatcherPriority.Render);
        e.Measure(new Size(w, h)); e.Arrange(new Rect(0, 0, w, h)); e.UpdateLayout();
    }
    private static void Render(FrameworkElement e, int w, int h, string path) { Layout(e, w, h); var image = new RenderTargetBitmap(w, h, 96, 96, PixelFormats.Pbgra32); image.Render(e); var encoder = new PngBitmapEncoder(); encoder.Frames.Add(BitmapFrame.Create(image)); using var f = File.Create(path); encoder.Save(f); }
    private sealed class Fixture : HttpMessageHandler
    {
        internal string Source = "四月初，京都下起了小雨。空气湿润，温度适宜。我来到了爸爸的灵牌前，失声痛哭。几天前，我的爸爸去世了。";
        internal int Latest = 3, Reads, Generates, Starts;
        internal bool Ready = true, FailBatch, ShowCandidates;
        internal TaskCompletionSource<bool>? HoldBatch;
        internal JsonElement GenerationBody;
        private object Page(int n) => new { id = $"pg-{n}", chapter_id = "ch", page_number = n, panel_count = 3, storyboard_version = 7, estimated_text_chars = 130, estimated_bubbles = 3, continuity_status = "NEEDS_REVIEW", source_coverage = new { ranges = new[] { new { text = Source } } } };
        private static object Batch(int n) => new { id = $"batch-{n}", ordinal = n, status = "COMPLETED" };
        private static HttpResponseMessage Json(object data) => new(HttpStatusCode.OK) { Content = new StringContent(JsonSerializer.Serialize(data)) };
        protected override async Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken token)
        {
            string path = request.RequestUri!.AbsolutePath;
            if (request.Method == HttpMethod.Get)
            {
                if (path.EndsWith("/chapters")) return Json(new[] { new { id = "ch", title = "第一章", ordinal = 1, page_count = 11 } });
                if (path.EndsWith("/models")) return Json(new[] { new { logical_alias = "model-a", model_type = "IMAGE", enabled = true, display_enabled = true, display_name = "Codex CLI ImageGen", provider = "Codex CLI", model_id = "codex-imagegen", operations = new[] { "image_edit" } }, new { logical_alias = "model-b", model_type = "IMAGE", enabled = true, display_enabled = true, display_name = "Google: Nano Banana 2 (Gemini 3.1 Flash Image Preview)", provider = "OpenRouter", model_id = "google/gemini-3.1-flash-image-preview", operations = new[] { "image_edit" } } });
                if (path.EndsWith("/pages")) return Json(Enumerable.Range(1, 11).Select(Page).ToArray());
                if (path.EndsWith("/batches")) return Json(Enumerable.Range(1, Latest).Select(Batch).ToArray());
                if (path.EndsWith("/generation-workbench"))
                {
                    Reads++; int n = int.Parse(path.Split('/')[^2].Split('-')[1]);
                    return Json(new { page = Page(n), current_batch = Batch(Latest), storyboard = new { panels = new[] { new { characters = Array.Empty<string>(), dialogues = new[] { new { target_text = "四月初，京都下起了小雨。" } } } } },
                        readiness = new { ready = Ready, source_complete = true, script_complete = true, blockers = Ready ? Array.Empty<object>() : new object[] { new { code = "MISSING_CHARACTER_REFERENCE", message = "请先补齐人物参考图", target_id = "c1", stage = "ASSETS" } }, visible_characters = Array.Empty<object>(), style = new { name = "日式彩色漫画" }, provider = new { usable_image_model_count = 2, auto_image_model_count = 1 }, worker = new { can_execute = true, executor = "local", queue_mode = "inline" } },
                        candidates = ShowCandidates ? new object[] { new { id = "candidate-1", ordinal = 1, status = "FAILED", model_alias = "model-a", resolution = "1K", version_state = "CURRENT" }, new { id = "candidate-2", ordinal = 2, status = "QUEUED", model_alias = "model-b", resolution = "1K", version_state = "CURRENT" } } : Array.Empty<object>(), production = new { ready = false, blockers = new[] { new { message = "请先人工校对并暂选候选" } } } });
                }
                return Json(Array.Empty<object>());
            }
            if (path.EndsWith("/batches"))
            {
                Starts++; if (HoldBatch != null) await HoldBatch.Task;
                if (FailBatch) return new(HttpStatusCode.Conflict) { Content = new StringContent("{\"detail\":\"页面已更新\"}") };
                return Json(Batch(++Latest));
            }
            if (path.EndsWith("/candidates"))
            {
                Generates++; GenerationBody = JsonSerializer.Deserialize<JsonElement>(await request.Content!.ReadAsStringAsync(token)); return Json(new { id = "candidate-new", status = "QUEUED" });
            }
            throw new Exception("Unexpected mutation " + path);
        }
    }
}
