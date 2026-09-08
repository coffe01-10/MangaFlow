using System.IO;
using System.Net;
using System.Net.Http;
using System.Reflection;
using System.Text;
using System.Text.Json;
using System.Web;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Controls.Primitives;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using MangaFlow.Native;
using MangaFlow.Native.Controls;
using MangaFlow.Native.Services;
using MangaFlow.Native.Views;

internal static class NativeLibraryChecks
{
    private const string Library = """
        {"groups":[{"batch":{"id":"batch","ordinal":3,"generation_kind":"PAGE","created_at":"2026-09-08T01:02:03Z"},"candidates":[
        {"id":"selected","ordinal":1,"status":"READY","is_selected":true,"is_favorite":false,"page_id":"page","resolution":"1K","model_alias":"image"},
        {"id":"unselected","ordinal":2,"status":"READY","is_selected":false,"is_favorite":true,"page_id":"page","resolution":"2K","model_alias":"image"},
        {"id":"waiting","ordinal":3,"status":"QUEUED","is_selected":false,"is_favorite":false,"page_id":"page","resolution":"1K","model_alias":"image"}]}],"total_candidates":3,"favorite_count":1,"next_cursor":null,"limit":30}
        """;

    public static async Task Run(string output)
    {
        Dates(); BatchLayout(); await Paging(); await View(output); await ChapterNavigation(); await Downloads();
        Console.WriteLine("PASS: library cursor/filter isolation, selection actions, export readiness/deduplication, real file downloads and interrupted-download cleanup");
    }

    private static void BatchLayout()
    {
        var panel = new BatchPanel();
        foreach (var span in new[] { 2, 1, 3, 1, 1 })
        {
            var group = new Border { Height = 100 + span * 10 };
            Grid.SetColumnSpan(group, span); panel.Children.Add(group);
        }
        foreach (var width in new[] { 360, 650, 1000 })
        {
            panel.Measure(new Size(width, double.PositiveInfinity)); panel.Arrange(new Rect(new Point(), panel.DesiredSize));
            var boxes = panel.Children.Cast<FrameworkElement>().Select(c => c.TransformToAncestor(panel).TransformBounds(new Rect(c.RenderSize))).ToList();
            Require(boxes.All(b => b.Left >= 0 && b.Right <= width + 1), "batch span escaped the available columns");
            for (var i = 0; i < boxes.Count; i++) for (var j = i + 1; j < boxes.Count; j++) Require(!boxes[i].IntersectsWith(boxes[j]), "mixed batch groups overlap");
            if (width == 1000) Require(boxes[0].Top == boxes[1].Top && boxes[2].Top >= boxes[0].Bottom, "two- and one-candidate batches do not share a row");
        }
    }

    private static void Dates()
    {
        var picker = new DatePicker { Width = 140, Style = (Style)Application.Current.FindResource(typeof(DatePicker)) };
        picker.Measure(new Size(140, 40)); picker.Arrange(new Rect(0, 0, 140, 40)); picker.UpdateLayout();
        Require(picker.Template.FindName("PART_Button", picker) is Button && picker.Template.FindName("PART_TextBox", picker) is DatePickerTextBox,
            "styled date field lost native input or calendar button");
        var popup = (Popup)picker.Template.FindName("PART_Popup", picker);
        Require(popup.Child is Calendar, "styled date field lost the native calendar");
        ((Calendar)popup.Child).SelectedDate = new DateTime(2026, 9, 8);
        Require(picker.SelectedDate == new DateTime(2026, 9, 8) && picker.Text.Contains("2026"), "calendar selection no longer updates input");
        picker.Text = "2026/9/1";
        Require(picker.SelectedDate == new DateTime(2026, 9, 1), "typed date did not commit");
        picker.Text = "";
        Require(picker.SelectedDate == null, "clearing date did not reset filter");
    }

    private static async Task ChapterNavigation()
    {
        var paths = new List<string>();
        using var api = new ApiClient("http://127.0.0.1:12345", new Handler(request =>
        {
            var path = request.RequestUri!.AbsolutePath; paths.Add(path);
            if (path.EndsWith("/chapters")) return Task.FromResult(Response("""[{"id":"first","ordinal":1},{"id":"target","ordinal":2}]"""));
            if (path.EndsWith("/pages")) return Task.FromResult(Response("""[{"id":"target-page","page_number":2}]"""));
            if (path.EndsWith("/generation-workbench")) return Task.FromResult(Response("""{"page":{"id":"target-page","page_number":2},"storyboard":{"panels":[]},"readiness":{"ready":false},"candidates":[]}"""));
            return Task.FromResult(Response("[]"));
        }));
        KeyValueStore.Set("generate:chapter:navigation", "target");
        KeyValueStore.Set("generate:page:navigation", "target-page");
        var view = new GenerateView();
        try
        {
            view.Activate(Context(api, "navigation"));
            await Until(() => Field<PageItem?>(view, "currentPage")?.Id == "target-page");
            Require(paths.Contains("/api/v1/chapters/target/pages") && !paths.Contains("/api/v1/chapters/first/pages"), "export blocker navigation opened the wrong chapter");
        }
        finally { view.Deactivate(); KeyValueStore.Remove("generate:chapter:navigation"); KeyValueStore.Remove("generate:page:navigation"); }
    }

    private static async Task Paging()
    {
        var requests = new List<(Uri Uri, TaskCompletionSource<HttpResponseMessage> Reply)>();
        using var api = new ApiClient("http://127.0.0.1:12345", new Handler(request =>
        {
            var reply = new TaskCompletionSource<HttpResponseMessage>(TaskCreationOptions.RunContinuationsAsynchronously);
            requests.Add((request.RequestUri!, reply)); return reply.Task;
        }));
        var filter = new LibraryFilter("project", "chapter", "character", "PAGE", "model+name", "2K", true, new(2026, 9, 1), new(2026, 9, 8));
        var feed = new LibraryFeed();
        var first = feed.LoadAsync(api, filter, CancellationToken.None);
        requests[0].Reply.SetResult(Response("""{"groups":[],"next_cursor":"page-two"}""")); await first;
        var refresh = feed.LoadAsync(api, filter, CancellationToken.None);
        Require(HttpUtility.ParseQueryString(requests[1].Uri.Query)["cursor"] == null, "refresh jumped to the next page");
        requests[1].Reply.SetResult(Response("""{"groups":[],"next_cursor":"page-two"}""")); await refresh;
        var second = feed.NextAsync(api, CancellationToken.None);
        Require(!await feed.NextAsync(api, CancellationToken.None) && requests.Count == 3, "repeated next click dispatched twice");
        var firstQuery = HttpUtility.ParseQueryString(requests[0].Uri.Query);
        var secondQuery = HttpUtility.ParseQueryString(requests[2].Uri.Query);
        Require(new[] { "chapter_id", "character_id", "generation_kind", "model_alias", "resolution", "favorite", "date_from", "date_to" }.All(k => firstQuery[k] == secondQuery[k])
            && secondQuery["cursor"] == "page-two" && secondQuery["date_to"] == "2026-09-08T23:59:59Z", "paging lost filter values or web date bounds");
        requests[2].Reply.SetResult(Response("""{"groups":[],"next_cursor":"page-three"}""")); await second;
        var third = feed.NextAsync(api, CancellationToken.None);
        requests[3].Reply.SetResult(Response("""{"detail":"failed page"}""", HttpStatusCode.InternalServerError));
        try { await third; throw new Exception("page failure ignored"); } catch (InvalidOperationException) { }
        Require(feed.PageNumber == 2 && feed.Cursor == "page-two" && feed.CanNext, "failed next page changed cursor history");
        var previous = feed.PreviousAsync(api, CancellationToken.None);
        Require(HttpUtility.ParseQueryString(requests[4].Uri.Query)["cursor"] == null, "previous did not restore first page cursor");
        requests[4].Reply.SetResult(Response("""{"groups":[],"next_cursor":"page-two"}""")); await previous;
        var stale = feed.NextAsync(api, CancellationToken.None);
        var newer = feed.LoadAsync(api, filter with { ProjectId = "other" }, CancellationToken.None);
        requests[6].Reply.SetResult(Response("""{"groups":[],"next_cursor":"other-next"}""")); await newer;
        requests[5].Reply.SetResult(Response("""{"detail":"stale failure"}""", HttpStatusCode.InternalServerError));
        Require(!await stale && feed.PageNumber == 1 && feed.NextCursor == "other-next", "old page failure contaminated another project");
        try { (filter with { From = new(2026, 9, 9) }).Path(null); throw new Exception("invalid range accepted"); }
        catch (ArgumentException) { }
    }

    private static async Task View(string output)
    {
        var requests = new List<string>();
        TaskCompletionSource<HttpResponseMessage>? pendingMutation = null;
        TaskCompletionSource<HttpResponseMessage>? delayedReadiness = null;
        var readinessFails = false;
        var holdReadiness = false;
        var currentLibrary = Library;
        using var api = new ApiClient("http://127.0.0.1:12345", new Handler(request =>
        {
            var path = request.RequestUri!.AbsolutePath;
            requests.Add(request.Method + " " + request.RequestUri.PathAndQuery);
            if (request.Method != HttpMethod.Get)
            {
                pendingMutation = new(TaskCreationOptions.RunContinuationsAsynchronously); return pendingMutation.Task;
            }
            if (path.EndsWith("/chapters")) return Task.FromResult(Response("""[{"id":"chapter","ordinal":2,"title":"雨夜来信"},{"id":"blocked-chapter","ordinal":3,"title":"未完成章节"}]"""));
            if (path.EndsWith("/characters")) return Task.FromResult(Response("""[{"id":"character","primary_name":"小满"}]"""));
            if (path.EndsWith("/models")) return Task.FromResult(Response("""[{"model_type":"IMAGE","logical_alias":"image","display_name":"图像模型"}]"""));
            if (path.EndsWith("/production-readiness"))
            {
                if (holdReadiness) { holdReadiness = false; delayedReadiness = new(TaskCreationOptions.RunContinuationsAsynchronously); return delayedReadiness.Task; }
                if (readinessFails) return Task.FromResult(Response("""{"detail":"readiness unavailable"}""", HttpStatusCode.ServiceUnavailable));
                return Task.FromResult(Response(path.Contains("blocked-chapter")
                    ? """{"ready":false,"ready_pages":0,"total_pages":2,"pages":[]}"""
                    : """{"ready":true,"ready_pages":2,"total_pages":2,"pages":[]}"""));
            }
            if (path.EndsWith("/exports")) return Task.FromResult(Response("""[{"id":"export","export_type":"PNG","byte_size":2048,"page_count":2,"download_url":"/api/v1/exports/export/download"}]"""));
            if (path.Contains("/other/")) return Task.FromResult(Response("""{"groups":[],"total_candidates":0,"favorite_count":0,"next_cursor":null}"""));
            if (path.EndsWith("/library")) return Task.FromResult(Response(currentLibrary));
            throw new Exception("unexpected path " + path);
        }));
        var view = new LibraryView(); view.Activate(Context(api, "project"));
        await Until(() => Field<LibraryFeed>(view, "feed").Data.ValueKind == JsonValueKind.Object && Field<string?>(view, "readyChapter") != null);
        var cards = Descendants(view).OfType<Border>().Where(b => b.Tag is string).ToList();
        var selected = cards.Single(b => (string)b.Tag == "selected");
        var unselected = cards.Single(b => (string)b.Tag == "unselected");
        Require(Buttons(selected).Any(b => b.Content?.ToString() == "撤回") && !Buttons(selected).Any(b => b.Content?.ToString() == "删除"), "selected candidate action set wrong");
        Require(!Buttons(unselected).Any(b => b.Content?.ToString() == "撤回") && Buttons(unselected).Any(b => b.Content?.ToString() == "删除"), "ready unselected candidate was treated as selected");
        Require(Buttons(view).Any(b => b.Content?.ToString()?.Contains("2 KB") == true) && !Field<Button>(view, "next").IsEnabled, "export byte size or terminal pager incorrect");
        var row = CandidateItem.From(JsonSerializer.Deserialize<JsonElement>("""{"id":"selected","is_selected":true,"page_id":"page"}"""));
        var count = requests.Count;
        await Call(view, "CandidateAction", row, "delete");
        Require(requests.Count == count, "selected candidate allowed deletion");
        var action = Call(view, "CandidateAction", row, "favorite");
        await Call(view, "CandidateAction", row, "favorite");
        Require(requests.Count == count + 1, "favorite mutation dispatched twice");
        pendingMutation!.SetResult(Response("""{"detail":"favorite failed"}""", HttpStatusCode.Conflict)); await action;
        Require(Text(view).Contains("favorite failed") && Field<HashSet<string>>(view, "pending").Count == 0, "favorite failure did not recover controls");
        Require(Descendants(view).OfType<Border>().Any(b => ReferenceEquals(b, selected)), "failed favorite recreated candidate artwork and lost focus");
        await view.RefreshAsync();
        Require(Descendants(view).OfType<Border>().Any(b => ReferenceEquals(b, selected)), "unchanged refresh rebuilt the image grid");
        var retract = Call(view, "CandidateAction", row, "retract");
        Require(requests.Last() == "DELETE /api/v1/pages/page/selected-candidate?candidate_id=selected", "retract omitted the confirmed candidate identity");
        currentLibrary = Library.Replace("\"is_selected\":true", "\"is_selected\":false");
        pendingMutation!.SetResult(Response("", HttpStatusCode.NoContent)); await retract;
        var retractedCard = Descendants(view).OfType<Border>().Single(b => b.Tag?.ToString() == "selected");
        Require(!Buttons(retractedCard).Any(b => b.Content?.ToString() == "撤回") && Buttons(retractedCard).Any(b => b.Content?.ToString() == "删除"), "retracted candidate retained selected actions");
        var hidden = Call(view, "CandidateAction", row with { IsSelected = false }, "delete");
        Require(requests.Last() == "DELETE /api/v1/candidates/selected", "soft deletion targeted the wrong endpoint");
        currentLibrary = """{"groups":[],"total_candidates":0,"favorite_count":0,"next_cursor":null}""";
        pendingMutation!.SetResult(Response("", HttpStatusCode.NoContent)); await hidden;
        Require(!Descendants(view).OfType<Border>().Any(b => b.Tag?.ToString() == "selected") && Text(view).Contains("素材库还是空的"), "deleted candidate was not refreshed out of the grid");
        currentLibrary = Library; await view.RefreshAsync();
        var export = Call(view, "ExportChapter", "chapter", "PNG");
        var exportReply = pendingMutation;
        count = requests.Count;
        await Call(view, "ExportChapter", "chapter", "PDF");
        Require(requests.Count == count && !Field<StackPanel>(view, "exportDesk").IsEnabled, "export mutations overlapped");
        exportReply!.SetResult(Response("{}")); await export;
        Require(Field<StackPanel>(view, "exportDesk").IsEnabled && Text(view).Contains("已生成"), "export remained locked after success");
        Render(view, output);
        readinessFails = true;
        await Call(view, "LoadExportsAsync");
        count = requests.Count;
        await Call(view, "ExportChapter", "chapter", "PNG");
        Require(requests.Count == count && Buttons(view).Any(b => b.Content?.ToString() == "重试读取"), "failed readiness retained an enabled export gate");
        readinessFails = false;
        holdReadiness = true;
        var oldReadiness = Call(view, "LoadExportsAsync");
        var selector = Field<ComboBox>(view, "chapterSelector");
        selector.SelectedItem = selector.Items.OfType<ComboBoxItem>().Single(i => (string?)i.Tag == "blocked-chapter");
        await Until(() => Text(view).Contains("未完成章节"));
        delayedReadiness!.SetResult(Response("""{"ready":true,"ready_pages":2,"total_pages":2,"pages":[]}"""));
        await oldReadiness;
        Require(Field<string?>(view, "readyChapter") == null && !Text(view).Contains("2/2 页生产通过"), "late readiness opened another chapter's export gate");
        var late = Call(view, "CandidateAction", row, "favorite"); var lateReply = pendingMutation;
        view.Deactivate(); view.Activate(Context(api, "other"));
        await Until(() => Field<LibraryFeed>(view, "feed").Data.ValueKind == JsonValueKind.Object);
        lateReply!.SetResult(Response("""{"detail":"obsolete favorite error"}""", HttpStatusCode.InternalServerError)); await late;
        Require(!Text(view).Contains("obsolete") && Field<LibraryFeed>(view, "feed").Filter?.ProjectId == "other", "late mutation polluted new project");
        view.Deactivate();
    }

    private static async Task Downloads()
    {
        var root = Path.Combine(Path.GetTempPath(), "mangaflow-download-check-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(root);
        var destination = Path.Combine(root, "export.pdf");
        try
        {
            await File.WriteAllTextAsync(destination, "existing");
            using (var api = new ApiClient("http://127.0.0.1:12345", new Handler(request =>
            {
                Require(request.RequestUri!.AbsolutePath == "/api/v1/exports/export/download", "export download duplicated API prefix");
                return Task.FromResult(Response("%PDF-fixture"));
            }))) await api.SaveDownloadAsync("/api/v1/exports/export/download", destination);
            Require(await File.ReadAllTextAsync(destination) == "%PDF-fixture" && Directory.GetFiles(root).Length == 1, "download not saved atomically");
            using (var api = new ApiClient("http://127.0.0.1:12345", new Handler(_ => Task.FromResult(new HttpResponseMessage(HttpStatusCode.OK) { Content = new StreamContent(new InterruptedStream()) }))))
            {
                try { await api.SaveDownloadAsync("exports/export/download", destination); throw new Exception("interrupted file accepted"); }
                catch (IOException) { }
            }
            Require(await File.ReadAllTextAsync(destination) == "%PDF-fixture" && Directory.GetFiles(root).Length == 1, "failed download damaged existing file or leaked temporary file");
            using var cancel = new CancellationTokenSource();
            var stream = new InterruptedStream { WaitForCancellation = true };
            using (var api = new ApiClient("http://127.0.0.1:12345", new Handler(_ => Task.FromResult(new HttpResponseMessage(HttpStatusCode.OK) { Content = new StreamContent(stream) }))))
            {
                var download = api.SaveDownloadAsync("exports/export/download", destination, cancel.Token);
                await stream.Started.Task; cancel.Cancel();
                try { await download; throw new Exception("download ignored cancellation"); } catch (OperationCanceledException) { }
            }
            Require(Directory.GetFiles(root).Length == 1 && await File.ReadAllTextAsync(destination) == "%PDF-fixture", "cancelled download left partial output");
        }
        finally { foreach (var file in Directory.GetFiles(root)) File.Delete(file); Directory.Delete(root); }
    }

    private sealed class InterruptedStream : Stream
    {
        public bool WaitForCancellation { get; init; }
        public TaskCompletionSource Started { get; } = new(TaskCreationOptions.RunContinuationsAsynchronously);
        private bool read;
        public override async ValueTask<int> ReadAsync(Memory<byte> buffer, CancellationToken cancellationToken = default)
        {
            Started.TrySetResult();
            if (WaitForCancellation) await Task.Delay(Timeout.Infinite, cancellationToken);
            if (read) throw new IOException("network interrupted after partial bytes");
            read = true; Encoding.UTF8.GetBytes("partial").CopyTo(buffer); return 7;
        }
        public override bool CanRead => true; public override bool CanSeek => false; public override bool CanWrite => false;
        public override long Length => throw new NotSupportedException();
        public override long Position { get => throw new NotSupportedException(); set => throw new NotSupportedException(); }
        public override int Read(byte[] buffer, int offset, int count) => throw new NotSupportedException();
        public override void Flush() => throw new NotSupportedException();
        public override long Seek(long offset, SeekOrigin origin) => throw new NotSupportedException();
        public override void SetLength(long value) => throw new NotSupportedException();
        public override void Write(byte[] buffer, int offset, int count) => throw new NotSupportedException();
    }
    private static void Render(LibraryView view, string output)
    {
        foreach (var width in new[] { 700, 1060 })
        {
            view.Measure(new Size(width, 1000)); view.Arrange(new Rect(0, 0, width, 1000)); view.UpdateLayout();
            var tiles = NativeParityChecks.Descendants(view).OfType<TilePanel>().Single();
            foreach (var artwork in NativeParityChecks.Descendants(view).OfType<ArtworkFrame>())
            {
                Require(Math.Abs(artwork.ActualHeight - artwork.ActualWidth * 4 / 3) < 1, "library artwork lost its web aspect ratio");
                var surface = (Border)((Button)artwork.Child).Content;
                Require(surface.ActualHeight >= artwork.ActualHeight - 1 && surface.ActualWidth >= artwork.ActualWidth - 1,
                    "preview button template compressed the image inside its aspect frame");
            }
            foreach (FrameworkElement child in tiles.Children)
                Require(child.TransformToAncestor(tiles).TransformBounds(new Rect(child.RenderSize)).Right <= tiles.ActualWidth + 1, "library card overflows canvas");
            var image = new RenderTargetBitmap(width, 1000, 96, 96, PixelFormats.Pbgra32); image.Render(view);
            var encoder = new PngBitmapEncoder(); encoder.Frames.Add(BitmapFrame.Create(image));
            using var file = File.Create(Path.Combine(output, $"native-library-populated-{width}.png")); encoder.Save(file);
        }
    }
    private static WorkspaceContext Context(ApiClient api, string project) => new() { Api = api, Cache = new(), State = new(), Window = null!, Project = new(project, project, "", 0, 0), NavigateSection = (_, _) => Task.CompletedTask, OpenDashboard = () => Task.CompletedTask };
    private static IEnumerable<DependencyObject> Descendants(DependencyObject root) { yield return root; foreach (var child in LogicalTreeHelper.GetChildren(root).OfType<DependencyObject>()) foreach (var item in Descendants(child)) yield return item; }
    private static IEnumerable<Button> Buttons(DependencyObject root) => Descendants(root).OfType<Button>();
    private static string Text(DependencyObject root) => string.Join("\n", Descendants(root).OfType<TextBlock>().Select(t => t.Text));
    private static T Field<T>(object target, string name) => (T)target.GetType().GetField(name, BindingFlags.Instance | BindingFlags.NonPublic)!.GetValue(target)!;
    private static Task Call(object target, string name, params object[] args) => (Task)target.GetType().GetMethod(name, BindingFlags.Instance | BindingFlags.NonPublic | BindingFlags.DeclaredOnly)!.Invoke(target, args)!;
    private static void Require(bool condition, string message) { if (!condition) throw new Exception(message); }
    private static async Task Until(Func<bool> condition) { using var timeout = new CancellationTokenSource(3000); while (!condition()) await Task.Delay(5, timeout.Token); }
    private static HttpResponseMessage Response(string body, HttpStatusCode status = HttpStatusCode.OK) => new(status) { Content = new StringContent(body) };
    private sealed class Handler(Func<HttpRequestMessage, Task<HttpResponseMessage>> handler) : HttpMessageHandler { protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken token) => handler(request); }
}
