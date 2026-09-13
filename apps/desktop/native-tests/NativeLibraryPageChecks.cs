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

internal static class NativeLibraryPageChecks
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
                app.Dispatcher.BeginInvoke(new Action(async () => { try { await Checks(output); await NativeLibraryChecks.Run(output); } catch (Exception e) { failure = e; } finally { app.Shutdown(); } })); app.Run();
            }); thread.SetApartmentState(ApartmentState.STA); thread.Start(); thread.Join();
            if (failure != null) throw new Exception("Library page checks failed", failure);
        }
        finally { KeyValueStore.UseLocation(previous); File.Delete(prefs); File.Delete(prefs + ".tmp"); }
    }
    private static async Task Checks(string output)
    {
        var fixture = new Fixture(); using var api = new ApiClient("http://127.0.0.1:12345", fixture); var view = new LibraryView();
        try
        {
            view.Activate(new WorkspaceContext { Api = api, State = new(), Window = null!, Project = new("layout", "我最讨厌妹妹了", "", 0, 0), NavigateSection = (_, _) => Task.CompletedTask, OpenDashboard = () => Task.CompletedTask });
            await Until(() => Field<LibraryFeed>(view, "feed").Data.ValueKind == JsonValueKind.Object);
            foreach (int width in new[] { 1240, 940, 650, 360 })
            {
                Layout(view, width, 1600);
                var filters = Desc(view).OfType<LibraryFilterPanel>().Single(); var groups = Desc(view).OfType<LibraryBatchPanel>().Single();
                Fits(filters); Fits(groups);
                foreach (var tiles in Desc(groups).OfType<TilePanel>()) Fits(tiles);
                if (width == 1240)
                {
                    Require(groups.Children.Cast<FrameworkElement>().Select(c => c.TransformToAncestor(groups).Transform(new Point()).Y).Distinct().Count() == 1, "four single-candidate batches share the wide row");
                    Require(Field<ComboBox>(view, "modelSelector").ActualWidth > Field<ComboBox>(view, "chapterSelector").ActualWidth * 2, "wide filter grid gives model names extra space");
                }
                if (width == 650) Require(groups.Children.Cast<FrameworkElement>().All(c => c.ActualWidth > 600), "compact windows stack complete batches");
                Render(view, width, width < 680 ? 1500 : 1000, Path.Combine(output, $"native-library-{width}.png"));
            }
            var chapter = Field<ComboBox>(view, "chapterSelector"); var character = Field<ComboBox>(view, "characterSelector");
            chapter.SelectedIndex = 1; character.SelectedIndex = 1; Field<ComboBox>(view, "kindSelector").SelectedIndex = 1; Field<ComboBox>(view, "modelSelector").SelectedIndex = 1; Field<ComboBox>(view, "resolutionSelector").SelectedIndex = 1;
            var favorite = Field<ToggleButton>(view, "favoriteOnly"); favorite.IsChecked = true; Click(favorite);
            Field<DatePicker>(view, "dateFrom").SelectedDate = new DateTime(2026, 9, 1); Field<DatePicker>(view, "dateTo").SelectedDate = new DateTime(2026, 9, 13);
            await Until(() => fixture.Queries.Last()["date_to"] != null); Layout(view, 1240, 1000);
            var query = fixture.Queries.Last();
            Require(query["chapter_id"] == "ch" && query["character_id"] == "c1" && query["model_alias"] == "model-a" && query["resolution"] == "1K" && query["favorite"] == "true" && query["generation_kind"] != null && query["date_from"]!.StartsWith("2026-09-01"), "all filter controls reach the API query");
            Require(Texts(view).Contains("没有符合筛选条件的素材"), "filtered empty state describes no matches");
            Render(view, 1240, 950, Path.Combine(output, "native-library-filtered.png"));
            var count = fixture.Queries.Count; Click(Desc(view).OfType<Button>().Single(b => Equals(b.Content, "重置"))); await Until(() => fixture.Queries.Count > count);
            Require(fixture.Queries.Count == count + 1 && fixture.Queries.Last()["character_id"] == null && Field<DatePicker>(view, "dateFrom").SelectedDate == null, "reset issues one library read and clears every filter");
            fixture.Mixed = true; await view.RefreshAsync(); Layout(view, 1240, 1500);
            Fits(Desc(view).OfType<LibraryBatchPanel>().Single());
            Render(view, 1240, 1500, Path.Combine(output, "native-library-mixed.png"));
            fixture.Empty = true; await view.RefreshAsync(); Layout(view, 1240, 950);
            Require(Texts(view).Contains("素材库还是空的"), "unfiltered empty library keeps the onboarding state");
            character.SelectedIndex = 1; await Task.Delay(20); Layout(view, 1240, 950);
            Require(Texts(view).Contains("没有符合筛选条件的素材"), "identical empty API data still updates after filter change");
            Console.WriteLine("PASS: library 1240/940/650/360 layouts, four-column and mixed batch groups, filter sizing/query parameters, reset dedup and filter-scoped empty states.");
            Console.WriteLine("Offscreen WPF with HTTP fixtures; live backend/provider, image network loading, high-DPI window and frame timing NOT RUN.");
        }
        finally { view.Deactivate(); }
    }
    private static void Fits(Panel panel)
    {
        var boxes = panel.Children.Cast<FrameworkElement>().Select(c => c.TransformToAncestor(panel).TransformBounds(new Rect(c.RenderSize))).ToArray();
        Require(boxes.All(b => b.Left >= -1 && b.Right <= panel.ActualWidth + 1), "child escaped its column");
        for (int i = 0; i < boxes.Length; i++) for (int j = i + 1; j < boxes.Length; j++) Require(!boxes[i].IntersectsWith(boxes[j]), "layout children overlap");
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
        internal List<System.Collections.Specialized.NameValueCollection> Queries = [];
        internal bool Mixed, Empty;
        private object Group(int n, int count) => new
        {
            batch = new { id = $"batch-{n}", ordinal = 25 - n, generation_kind = n == 1 ? "PAGE" : n == 2 ? "OUTFIT" : "CHARACTER", created_at = "2026-09-13T05:45:23Z" },
            candidates = Enumerable.Range(1, count).Select(i => new { id = $"candidate-{n}-{i}", ordinal = i, status = n == 1 ? "FAILED" : "QUEUED", is_selected = n == 3 && i == 1, is_favorite = n == 2, page_id = "pg", model_alias = "model-a", resolution = "1K", version_state = "CURRENT" }).ToArray()
        };
        protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken token)
        {
            if (request.Method != HttpMethod.Get) throw new Exception("Layout fixture must not mutate");
            string path = request.RequestUri!.AbsolutePath; object data = Array.Empty<object>();
            if (path.EndsWith("/chapters")) data = new[] { new { id = "ch", ordinal = 1, title = "第一章", page_count = 11 } };
            else if (path.EndsWith("/characters")) data = new[] { new { id = "c1", primary_name = "我" } };
            else if (path.EndsWith("/models")) data = new[] { new { logical_alias = "model-a", model_type = "IMAGE", display_name = "Google: Nano Banana 2 (Gemini 3.1 Flash Image)" } };
            else if (path.EndsWith("/library"))
            {
                var query = HttpUtility.ParseQueryString(request.RequestUri.Query); Queries.Add(query);
                bool filtered = new[] { "chapter_id", "character_id", "generation_kind", "model_alias", "resolution", "favorite", "date_from", "date_to" }.Any(k => query[k] != null);
                int[] counts = Mixed ? [2, 1, 3, 1] : [1, 1, 1, 1];
                data = new { groups = Empty || filtered ? Array.Empty<object>() : counts.Select((c, i) => Group(i + 1, c)).ToArray(), total_candidates = Empty || filtered ? 0 : counts.Sum(), favorite_count = 1, limit = 30, next_cursor = (string?)null };
            }
            else if (path.EndsWith("/production-readiness")) data = new { ready = false, ready_pages = 0, total_pages = 11, pages = new[] { new { page_id = "pg", page_number = 1, ready = false, blockers = new[] { new { message = "本页还没有通过视觉检查的候选" } } } } };
            return Task.FromResult(new HttpResponseMessage(HttpStatusCode.OK) { Content = new StringContent(JsonSerializer.Serialize(data)) });
        }
    }
}
