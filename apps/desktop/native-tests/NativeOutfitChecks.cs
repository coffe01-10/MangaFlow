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
using MangaFlow.Native;
using MangaFlow.Native.Services;
using MangaFlow.Native.Views;

internal static class NativeOutfitChecks
{
    public static async Task Run(string output)
    {
        var previous = (string)typeof(KeyValueStore).GetField("path", BindingFlags.Static | BindingFlags.NonPublic)!.GetValue(null)!;
        var prefs = Path.Combine(output, "wardrobe-test-prefs.json");
        File.WriteAllText(prefs, "{}"); KeyValueStore.UseLocation(prefs);
        try { await RunIsolated(output, prefs); }
        finally { KeyValueStore.UseLocation(previous); File.Delete(prefs); File.Delete(prefs + ".tmp"); }
    }
    private static async Task RunIsolated(string output, string prefs)
    {
        var fake = new Fixture(); using var api = new ApiClient("http://127.0.0.1:12345", fake);
        var view = new AssetsView();
        WorkspaceContext Context(string id) => new() { Api = api, Cache = new(), State = new(), Window = null!, Project = new ProjectItem(id, "服装档案测试", "", 0, 0), NavigateSection = (_, _) => Task.CompletedTask, OpenDashboard = () => Task.CompletedTask };
        view.Activate(Context("wardrobe-fixture"));
        try
        {
            await view.RefreshAsync(); view.Switch(AssetsView.Outfits); Layout(view, 1100, 1000);
            var pane = Descendants(view).OfType<OutfitWorkspace>().Single();
            var character = Field<ComboBox>(pane, "characterSelector"); var name = Field<TextBox>(pane, "outfitName"); var locked = Field<TextBox>(pane, "lockedFields"); var save = Field<Button>(pane, "save");
            Require(!save.IsEnabled && !Field<Button>(pane, "upload").IsEnabled, "empty draft should require character, name and reference");
            Require(view.outfits[0].LockedFields == "蓝色，领结", "server locked_fields array must be readable");
            Require(Buttons(pane, "管理参考图").Count() == 2, "saved workbench must include other characters' outfits");
            Require(Buttons(pane, "生成穿着图").All(b => !b.IsEnabled), "generation requires explicit model selection");
            foreach (var width in new[] { 650, 1100 })
            {
                Render(view, width, 1000, 96, Path.Combine(output, $"native-outfits-restored-{width}.png"));
                Require(name.ActualWidth >= 230, "wardrobe inputs collapse or overflow at responsive breakpoint");
            }
            Render(view, 1100, 1000, 120, Path.Combine(output, "native-outfits-text-125pct.png"));
            var preview = new AssetsView(); preview.Activate(Context("wardrobe-preview")); await preview.RefreshAsync(); preview.Switch(AssetsView.Outfits);
            var shell = new MainWindow("", Path.Combine(output, "unused-outfit-shell")); KeyValueStore.UseLocation(prefs);
            ((WorkspaceState)shell.DataContext).CurrentProject = Context("wardrobe-fixture").Project;
            typeof(MainWindow).GetField("page", BindingFlags.Instance | BindingFlags.NonPublic)!.SetValue(shell, "assets");
            typeof(MainWindow).GetMethod("ApplySidebar", BindingFlags.Instance | BindingFlags.NonPublic)!.Invoke(shell, null);
            ((TextBlock)shell.FindName("TopTitle")).Text = "服装档案测试";
            ((ListBox)shell.FindName("ProjectSections")).SelectedItem = ProjectPages.Get(ProjectPageId.Assets);
            var host = (ContentControl)shell.FindName("ContentHost"); host.Content = preview;
            Render((FrameworkElement)shell.Content, 1320, 1000, 96, Path.Combine(output, "native-outfits-restored-shell.png")); host.Content = null; preview.Deactivate();
            Layout(view, 1100, 1000);
            foreach (var key in new[] { "Card", "CardInk" })
            {
                var card = new Border { Style = (Style)Application.Current.FindResource(key), Child = new TextBlock { Text = "服装文字清晰度" } };
                Require(card.Effect == null && card.Background is SolidColorBrush { Color.A: 255 }, "text card still has intermediate effect or transparent background");
            }
            Require(name.Background is SolidColorBrush { Color.A: 255 }, "text input must use an opaque background");
            character.SelectedIndex = 1; name.Text = "冬季便装"; locked.Text = "蓝色、短靴，围巾";
            var refs = Field<StackPanel>(pane, "references"); Click(Buttons(refs, "加入当前服装档案").First());
            ((ScrollViewer)view.Content).ScrollToEnd(); Layout(view, 1100, 1000);
            Render(view, 1100, 1000, 96, Path.Combine(output, "native-outfit-references-restored.png"));
            ((ScrollViewer)view.Content).ScrollToTop(); Layout(view, 1100, 1000);
            Require(save.IsEnabled, "draft reference selection did not enable save");
            var hold = fake.PendingSave = new TaskCompletionSource<HttpResponseMessage>();
            Click(save); Click(save);
            Require(fake.Writes.Count(w => w.Path.EndsWith("/outfits")) == 1 && !pane.IsEnabled, "double click submitted duplicate outfit");
            var create = fake.Writes.Single(w => w.Path.EndsWith("/outfits")).Body;
            Require(create.Strings("locked_fields").SequenceEqual(new[] { "蓝色", "短靴", "围巾" }) && create.Strings("reference_asset_ids").SequenceEqual(new[] { "a1" }) && create.Text("character_id") == "c1", "create wire payload lost bindings or locks");
            hold.SetResult(Json("{}")); await Settle();
            Require(name.Text == "" && !save.IsEnabled && pane.IsEnabled, "successful save did not clear only draft");
            Click(Buttons(pane, "管理参考图").First());
            Require(!character.IsEnabled && name.Text == "校服" && locked.Text == "蓝色，领结", "edit did not load versioned record or locked owner");
            Click(Buttons(refs, "已选：保存后绑定").Single()); name.Text = "更新校服";
            Require(save.IsEnabled, "editing must allow removing every reference");
            fake.FailPatch = true; Click(save); await Settle();
            Require(name.Text == "更新校服" && pane.IsEnabled, "conflict discarded edit draft");
            var patch = fake.Writes.Last().Body; Require(patch.Number("version") == 7 && patch.Strings("reference_asset_ids").Count == 0, "PATCH missing server version or explicit empty references");
            fake.FailPatch = false; Click(save); await Settle(); Require(name.Text == "", "successful edit failed to reset draft");
            // Real library shape is an object with groups, never a top-level array.
            Click(Field<Button>(pane, "import")); await Settle();
            var library = Field<StackPanel>(pane, "library"); Require(Buttons(library, "加入待绑定").Count() == 1, "library group envelope or dedup broken");
            Click(Buttons(library, "加载更多生成素材").Single()); await Settle();
            Require(fake.LibraryQueries.Last().Contains("cursor=next-page") && Buttons(library, "加入待绑定").Count() == 2, "library cursor pagination lost candidates");
            Click(Buttons(library, "加入待绑定").First()); await Settle();
            Require(fake.Writes.Any(w => w.Path.EndsWith("/assets/g1/adopt-reference")) && Buttons(library, "已加入待绑定").Any(), "adoption must use reference endpoint and update draft without saving");
            var uploadPath = Path.Combine(output, "wardrobe-upload-fixture.png");
            try
            {
                File.WriteAllBytes(uploadPath, Convert.FromBase64String("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aS9sAAAAASUVORK5CYII="));
                await pane.UploadAsync(new[] { uploadPath });
                Require(fake.UploadBody.Contains("OUTFIT_REFERENCE") && fake.UploadBody.Contains("wardrobe-fixture") && fake.UploadBody.Contains("image/png"), "upload multipart lost project, purpose or file MIME");
                Require(Field<HashSet<string>>(pane, "selected").Contains("u1"), "upload did not add draft reference");
            }
            finally { File.Delete(uploadPath); }
            var model = Descendants(pane).OfType<ToggleButton>().Single(b => Equals(b.Tag, "model-a")); Click(model);
            Require(Buttons(pane, "生成穿着图").All(b => b.IsEnabled), "visible model selector did not enable generation");
            Click(Buttons(pane, "生成穿着图").First()); await Settle();
            var generation = fake.Writes.Last(w => w.Path.EndsWith("/candidates")).Body;
            Require(generation.Text("model_alias") == "model-a" && generation.Text("resolution") == "1K" && generation.Text("variant") == "OUTFIT", "outfit generation contract mismatch");
            var promptPanel = Descendants(Field<StackPanel>(pane, "liveResults")).OfType<Expander>().Single();
            promptPanel.IsExpanded = true; Layout(view, 1100, 1000);
            Require(promptPanel.Content is TextBox { Text: "测试实际穿着提示词" }, "actual prompt_preview omitted");
            fake.CandidateStatus = "UPLOADING_REFERENCES"; pane.PollTick(); await Settle(); var uploadingReads = fake.CandidateReads;
            fake.CandidateStatus = "CONSISTENCY_CHECKING"; pane.PollTick(); await Settle(); Require(fake.CandidateReads > uploadingReads, "polling stopped during reference upload");
            fake.CandidateReady = true; pane.PollTick(); await Settle(); var reads = fake.CandidateReads; pane.PollTick(); await Settle();
            Require(fake.CandidateReads == reads, "terminal result must stop polling");
            // A detached pane must not continue the second POST after a delayed batch response.
            var batchHold = fake.PendingBatch = new TaskCompletionSource<HttpResponseMessage>();
            Click(Buttons(pane, "生成穿着图").First()); var generationCount = fake.Writes.Count(w => w.Path.EndsWith("/candidates"));
            // The uploaded draft is dirty: the tab switch goes through the unsaved-draft
            // guard; discard keeps this isolation scenario deterministic (no dialog).
            view.OutfitDraftPrompt = _ => Task.FromResult("discard");
            view.Switch(AssetsView.Characters); batchHold.SetResult(Json("{\"id\":\"late\"}")); await Settle();
            Require(fake.Writes.Count(w => w.Path.EndsWith("/candidates")) == generationCount, "detached outfit pane dispatched generation follow-up");
            view.Switch(AssetsView.Outfits); await Settle(); Layout(view, 1100, 1000); pane = Descendants(view).OfType<OutfitWorkspace>().Single();
            Require(Descendants(Field<StackPanel>(pane, "liveResults")).OfType<Expander>().Any(), "returning to wardrobe lost the latest preview");
            character = Field<ComboBox>(pane, "characterSelector"); character.SelectedIndex = 1;
            var libraryHold = fake.PendingLibrary = new TaskCompletionSource<HttpResponseMessage>(); Click(Field<Button>(pane, "import"));
            character.SelectedIndex = 2; libraryHold.SetResult(Json(Fixture.Library)); await Settle();
            Require(Field<StackPanel>(pane, "library").Visibility == Visibility.Collapsed, "old owner's library response reopened picker");
        }
        finally { view.Deactivate(); }
        Console.WriteLine("PASS: wardrobe responsive layout, opaque text surfaces, reference draft, versioned editing/conflict, save dedup, library envelope/cursor/adoption, model generation/polling and detached-pane isolation");
    }
    private static async Task Settle() => await Task.Delay(25);
    private static IEnumerable<DependencyObject> Descendants(DependencyObject root) => NativeParityChecks.Descendants(root);
    private static IEnumerable<Button> Buttons(DependencyObject root, string content) => Descendants(root).OfType<Button>().Where(b => Equals(b.Content, content));
    private static T Field<T>(object value, string name) => (T)value.GetType().GetField(name, BindingFlags.Instance | BindingFlags.NonPublic)!.GetValue(value)!;
    private static void Click(ButtonBase button) => button.RaiseEvent(new RoutedEventArgs(ButtonBase.ClickEvent));
    private static void Require(bool value, string message) { if (!value) throw new Exception(message); }
    private static void Layout(FrameworkElement element, int width, int height) { element.Measure(new Size(width, height)); element.Arrange(new Rect(0, 0, width, height)); element.UpdateLayout(); }
    private static void Render(FrameworkElement element, int width, int height, int dpi, string path)
    {
        Layout(element, width, height); var bitmap = new RenderTargetBitmap(width * dpi / 96, height * dpi / 96, dpi, dpi, PixelFormats.Pbgra32); var paper = new DrawingVisual(); using (var drawing = paper.RenderOpen()) drawing.DrawRectangle((Brush)Application.Current.FindResource("Paper"), null, new Rect(0, 0, width, height)); bitmap.Render(paper); bitmap.Render(element);
        var encoder = new PngBitmapEncoder(); encoder.Frames.Add(BitmapFrame.Create(bitmap)); using var file = File.Create(path); encoder.Save(file);
    }
    private static HttpResponseMessage Json(string text) => new(HttpStatusCode.OK) { Content = new StringContent(text) };
    private sealed class Fixture : HttpMessageHandler
    {
        public const string Library = """{"groups":[{"candidates":[{"asset_id":"g1","content_url":"/assets/g1/content","variant":"OUTFIT","resolution":"1K"},{"asset_id":"g1","content_url":"/assets/g1/content"}]}],"next_cursor":"next-page"}""";
        public readonly List<(string Path, JsonElement Body)> Writes = [];
        public readonly List<string> LibraryQueries = [];
        public string UploadBody = "";
        public TaskCompletionSource<HttpResponseMessage>? PendingSave, PendingBatch, PendingLibrary;
        public string CandidateStatus = "GENERATING";
        public bool FailPatch, Adopted, CandidateReady; public int CandidateReads;
        protected override async Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken cancellationToken)
        {
            var path = request.RequestUri!.AbsolutePath;
            if (request.Method != HttpMethod.Get)
            {
                var body = request.Content == null ? "{}" : await request.Content.ReadAsStringAsync(cancellationToken);
                if (path.EndsWith("/assets/upload")) { UploadBody = body; return Json("{\"id\":\"u1\",\"kind\":\"OUTFIT_REFERENCE\",\"original_name\":\"上传参考.png\"}"); }
                Writes.Add((path, JsonSerializer.Deserialize<JsonElement>(body)));
                if (path.EndsWith("/outfits") && PendingSave is { } save) { PendingSave = null; return await save.Task; }
                if (request.Method == HttpMethod.Patch && FailPatch) return new HttpResponseMessage(HttpStatusCode.Conflict) { Content = new StringContent("{\"detail\":\"版本已更新，请刷新后重试\"}") };
                if (path.EndsWith("/adopt-reference")) { Adopted = true; return Json("{\"id\":\"g1\",\"kind\":\"OUTFIT_REFERENCE\"}"); }
                if (path.EndsWith("/asset-generation-batches")) { if (PendingBatch is { } batch) { PendingBatch = null; return await batch.Task; } return Json("{\"id\":\"batch1\"}"); }
                return Json("{}");
            }
            if (path.EndsWith("/library")) { LibraryQueries.Add(request.RequestUri.Query); if (PendingLibrary is { } library) { PendingLibrary = null; return await library.Task; } return Json(request.RequestUri.Query.Contains("cursor=") ? """{"groups":[{"candidates":[{"asset_id":"g2","content_url":"/assets/g2/content"}]}],"next_cursor":null}""" : Library); }
            if (path.EndsWith("/candidates")) { CandidateReads++; return Json("[{\"id\":\"candidate1\",\"status\":\"" + (CandidateReady ? "READY" : CandidateStatus) + "\",\"resolution\":\"1K\",\"prompt_snapshot\":{\"prompt_preview\":\"测试实际穿着提示词\"}}]"); }
            if (path.EndsWith("/asset-generation-batches")) return Json("[{\"id\":\"batch1\"}]");
            if (path.EndsWith("/characters")) return Json("""[{"id":"c1","primary_name":"樱","aliases":["妹妹"]},{"id":"c2","primary_name":"我","aliases":[]}]""");
            if (path.EndsWith("/outfits")) return Json("""[{"id":"o1","character_id":"c1","name":"校服","version":7,"locked_fields":["蓝色","领结"],"reference_asset_ids":["a1"]},{"id":"o2","character_id":"c2","name":"冬季便装","version":2,"locked_fields":[],"reference_asset_ids":["a2"]}]""");
            if (path.EndsWith("/assets")) return Json("[{\"id\":\"a1\",\"kind\":\"OUTFIT_REFERENCE\",\"original_name\":\"校服参考.png\",\"byte_size\":1400000,\"status\":\"UPLOADED\"},{\"id\":\"a2\",\"kind\":\"OUTFIT_REFERENCE\",\"original_name\":\"冬装参考.png\",\"status\":\"GENERATED\"}" + (Adopted ? ",{\"id\":\"g1\",\"kind\":\"OUTFIT_REFERENCE\",\"original_name\":\"生成服装.png\"}" : "") + "]");
            if (path.EndsWith("/models")) return Json("""[{"model_type":"IMAGE","enabled":true,"operations":["image_edit"],"logical_alias":"model-a","display_name":"Codex CLI ImageGen","provider":"Codex CLI","model_id":"codex-imagegen"},{"model_type":"IMAGE","enabled":true,"operations":["image_edit"],"logical_alias":"model-b","display_name":"Google: Nano Banana 2","provider":"OpenRouter","model_id":"google/gemini-3.1-flash-image"}]""");
            return Json("[]");
        }
    }
}
