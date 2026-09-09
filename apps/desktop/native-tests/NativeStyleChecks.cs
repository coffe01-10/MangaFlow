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
using MangaFlow.Native.Services;
using MangaFlow.Native.Views;

internal static class NativeStyleChecks
{
    internal static void Run(string output)
    {
        Exception? failure = null;
        var thread = new Thread(() =>
        {
            var app = new Application { ShutdownMode = ShutdownMode.OnExplicitShutdown };
            app.Resources.MergedDictionaries.Add(new ResourceDictionary { Source = new Uri("/MangaFlow.Native;component/Theme.xaml", UriKind.Relative) });
            SynchronizationContext.SetSynchronizationContext(new DispatcherSynchronizationContext());
            app.Dispatcher.BeginInvoke(new Action(async () => { try { Directory.CreateDirectory(output); await Checks(output); } catch (Exception ex) { failure = ex; } finally { app.Shutdown(); } })); app.Run();
        }); thread.SetApartmentState(ApartmentState.STA); thread.Start(); thread.Join();
        if (failure != null) throw new Exception("Style workspace checks failed", failure);
    }
    private static async Task Checks(string output)
    {
        var previous = (string)typeof(KeyValueStore).GetField("path", BindingFlags.Static | BindingFlags.NonPublic)!.GetValue(null)!;
        var prefs = Path.Combine(output, "style-test-prefs.json"); KeyValueStore.UseLocation(prefs);
        var fixture = new Fixture(); using var api = new ApiClient("http://127.0.0.1:12345", fixture); var view = new AssetsView();
        try
        {
            KeyValueStore.Set("image-model:p", "model-a"); KeyValueStore.Set("style-mode:p", "color");
            view.Activate(new WorkspaceContext { Api = api, Cache = new(), State = new(), Window = null!, Project = new ProjectItem("p", "漫画风格测试", "", 0, 0), NavigateSection = (_, _) => Task.CompletedTask, OpenDashboard = () => Task.CompletedTask });
            await view.RefreshAsync(); await view.SwitchAsync(AssetsView.Style); await Task.Delay(30); Layout(view, 1100, 1500);
            var pane = NativeParityChecks.Descendants(view).OfType<StyleWorkspace>().Single(); await pane.InitialLoad;
            var cards = Field<Dictionary<string, StyleProductionCard>>(pane, "cards"); var card = cards["style1"];
            Require(Field<TextBox>(pane, "name").Text == "彩色漫画风格", "project mode is remembered");
            Require(!Field<Button>(pane, "create").IsEnabled, "creation requires references");
            Field<HashSet<string>>(pane, "selected").Add("ref1"); Field<TextBox>(pane, "name").Text = "雨夜漫画风格"; Field<TextBox>(pane, "locked").Text = "色板、肤色，光影";
            Require(Field<Button>(pane, "create").IsEnabled, "selected reference and name enable create");
            Require(Field<TextBlock>(card, "status").Text.Contains("1 张参考") && Field<TextBlock>(card, "status").Text.Contains("2 项锁定"), "profile reference ids and locked arrays are read from real schema");
            Require(card.Palette()["肤色"] == "#F0C2A0", "palette object is editable");
            Require(Field<TextBlock>(card, "badge").Text == "STYLE PROFILE", "default pointer alone does not label a draft as active");
            Render(view, 1100, 1500, Path.Combine(output, "native-style-1100.png")); Render(view, 650, 1300, Path.Combine(output, "native-style-650.png"));
            var atmosphere = Field<TextBox>(card, "atmosphere"); atmosphere.Text = "保留我的章节氛围";
            var rows = Field<List<(TextBox Name, TextBox Value)>>(card, "paletteRows"); rows[0].Value.Text = "#EAB39D";
            fixture.Version = 8; await pane.ReloadAsync();
            Require(ReferenceEquals(card, cards["style1"]) && atmosphere.Text == "保留我的章节氛围" && rows[0].Value.Text == "#EAB39D", "polling preserves card and dirty inputs");
            fixture.Conflict = true; await card.SavePaletteAsync();
            Require(fixture.Writes.Last().Body.Number("version") == 7 && Field<TextBlock>(card, "error").Text.Contains("刷新") && rows[0].Value.Text == "#EAB39D", "dirty palette retains its original version and conflict input");
            fixture.Conflict = false; fixture.Version = 7; await card.SavePaletteAsync();
            Require(fixture.PaletteConfirmed && !Field<Button>(card, "activate").Content.Equals("激活彩色风格"), "palette alone must not enable activation");
            int before = fixture.Writes.Count; Click(Field<Button>(card, "activate")); await Task.Delay(10);
            Require(!fixture.Writes.Skip(before).Any(w => w.Path.EndsWith("/activate")), "activation directs user to missing test without calling backend");
            fixture.HoldGeneration = new TaskCompletionSource<HttpResponseMessage>();
            var generation = card.GenerateAsync(); await Task.Delay(5); await card.GenerateAsync();
            Require(fixture.Writes.Count(w => w.Path.EndsWith("/asset-generation-batches")) == 1, "generation double click cannot create duplicate batches");
            fixture.HoldGeneration.SetResult(Json("{\"job_id\":\"job1\"}")); await generation;
            Require(card.NeedsPoll && !Field<Button>(card, "generate").IsEnabled, "pending candidates poll and block repeat generation");
            fixture.CandidateReady = true; await pane.ReloadAsync(); Layout(view, 1100, 1600);
            Require(Field<StackPanel>(card, "candidates").Children.Count == 1, "only STYLE_TEST candidates are shown");
            var approve = NativeParityChecks.Descendants(card).OfType<Button>().Single(b => Equals(b.Content, "人工通过")); Require(approve.IsEnabled, "ready candidate enables human approval");
            Click(approve); await Task.Delay(20);
            Require(fixture.TestApproved && Equals(Field<Button>(card, "activate").Content, "激活彩色风格"), "activation becomes available only after test approval");
            Click(Field<Button>(card, "activate")); await Task.Delay(20); Require(fixture.Activated && Equals(Field<Button>(card, "activate").Content, "已用于正式页面"), "activation reads project default pointer");
            Render(card, 1060, 900, Path.Combine(output, "native-style-pipeline.png"));
            fixture.HoldCreate = new TaskCompletionSource<HttpResponseMessage>(); var create = pane.CreateAsync(); await Task.Delay(5); await pane.CreateAsync();
            var createBody = fixture.Writes.Single(w => w.Path.EndsWith("/projects/p/styles")).Body;
            Require(createBody.Array("reference_asset_ids").Count == 1 && createBody.Array("locked_fields").Count == 3, "new style sends reference ids and locked string array");
            fixture.HoldCreate.SetResult(Json("{\"id\":\"style-new\"}")); await create;
            Require(fixture.Writes.Count(w => w.Path.EndsWith("/style-new/analyze")) == 1, "create submits analysis exactly once");
            var hold = new TaskCompletionSource<HttpResponseMessage>(); fixture.HoldRead = hold; var reload = pane.ReloadAsync(); await view.SwitchAsync(AssetsView.References); hold.SetResult(Json("[]")); await reload;
            Require(cards.Count == 1, "detached style workspace ignores late list response");
            await view.SwitchAsync(AssetsView.Style); await Task.Delay(15); Layout(view, 1100, 1300);
            var oldPane = NativeParityChecks.Descendants(view).OfType<StyleWorkspace>().Single();
            view.Activate(new WorkspaceContext { Api = api, Cache = new(), State = new(), Window = null!, Project = new ProjectItem("p2", "另一个项目", "", 0, 0), NavigateSection = (_, _) => Task.CompletedTask, OpenDashboard = () => Task.CompletedTask });
            await view.RefreshAsync(); await Task.Delay(15); Layout(view, 1100, 1300);
            var nextPane = NativeParityChecks.Descendants(view).OfType<StyleWorkspace>().Single();
            Require(!ReferenceEquals(oldPane, nextPane) && nextPane.ProjectId == "p2", "switching project must replace the old style workspace");
            using var stream = Application.GetResourceStream(new Uri("/MangaFlow.Native;component/Assets/App.ico", UriKind.Relative))!.Stream;
            var icon = new IconBitmapDecoder(stream, BitmapCreateOptions.PreservePixelFormat, BitmapCacheOption.OnLoad);
            foreach (var frame in icon.Frames)
            {
                var bitmap = new FormatConvertedBitmap(frame, PixelFormats.Bgra32, null, 0); int width = bitmap.PixelWidth, height = bitmap.PixelHeight;
                var pixels = new byte[width * height * 4]; bitmap.CopyPixels(pixels, width * 4, 0); int left = width, right = -1;
                for (int y = 0; y < height; y++) for (int x = 0; x < width; x++) if (pixels[(y * width + x) * 4 + 3] > 32) { left = Math.Min(left, x); right = Math.Max(right, x); }
                Require((right - left + 1.0) / width >= .9, "icon artwork should fill at least 90% of each viewport");
            }
            Console.WriteLine("PASS: style layout, remembered mode, profile/palette schema, draft preservation and conflict version, 4-stage activation gates, async candidates, mutation dedup, detached response, all icon sizes >=90% artwork coverage");
            Console.WriteLine("UI checks use HTTP fixtures; provider generation and real-window interaction NOT RUN.");
        }
        finally { view.Deactivate(); KeyValueStore.UseLocation(previous); File.Delete(prefs); File.Delete(prefs + ".tmp"); }
    }
    private static T Field<T>(object value, string name) => (T)value.GetType().GetField(name, BindingFlags.Instance | BindingFlags.NonPublic)!.GetValue(value)!;
    private static void Click(ButtonBase button) => button.RaiseEvent(new RoutedEventArgs(ButtonBase.ClickEvent));
    private static void Require(bool value, string message) { if (!value) throw new Exception(message); }
    private static void Layout(FrameworkElement e, int w, int h) { e.Measure(new Size(w, h)); e.Arrange(new Rect(0, 0, w, h)); e.UpdateLayout(); }
    private static void Render(FrameworkElement e, int w, int h, string path) { Layout(e, w, h); var image = new RenderTargetBitmap(w, h, 96, 96, PixelFormats.Pbgra32); image.Render(e); var encoder = new PngBitmapEncoder(); encoder.Frames.Add(BitmapFrame.Create(image)); using var f = File.Create(path); encoder.Save(f); }
    private static HttpResponseMessage Json(string value) => new(HttpStatusCode.OK) { Content = new StringContent(value) };
    private sealed class Fixture : HttpMessageHandler
    {
        internal int Version = 7;
        internal bool Conflict, PaletteConfirmed, CandidateReady, TestApproved, Activated, BatchCreated;
        internal TaskCompletionSource<HttpResponseMessage>? HoldGeneration, HoldCreate, HoldRead;
        internal readonly List<(string Path, JsonElement Body)> Writes = [];
        private string Style => JsonSerializer.Serialize(new { id = "style1", name = "京都雨夜 · 暖灰彩色", color_mode = "color", status = Activated ? "ACTIVE" : "DRAFT", version = Version, locked_fields = new[] { "肤色", "低饱和" }, profile = new { reference_asset_ids = new[] { "ref1" }, palette_draft = new Dictionary<string, string> { ["肤色"] = "#F0C2A0", ["阴影"] = "#59443F" }, palette_confirmed = PaletteConfirmed, test_image_approved = TestApproved, test_candidate_id = TestApproved ? "candidate1" : "" } });
        protected override async Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken token)
        {
            var path = request.RequestUri!.AbsolutePath;
            if (request.Method != HttpMethod.Get)
            {
                Writes.Add((path, JsonSerializer.Deserialize<JsonElement>(request.Content == null ? "{}" : await request.Content.ReadAsStringAsync(token))));
                if (path.EndsWith("/palette-approve")) { if (Conflict) return new HttpResponseMessage(HttpStatusCode.Conflict) { Content = new StringContent("{\"detail\":\"版本已变化，请刷新\"}") }; PaletteConfirmed = true; Version++; return Json(Style); }
                if (path.EndsWith("/style-test-approve")) { TestApproved = true; Version++; return Json(Style); }
                if (path.EndsWith("/activate")) { Activated = true; Version++; return Json(Style); }
                if (path.EndsWith("/asset-generation-batches")) { BatchCreated = true; return Json("{\"id\":\"batch1\"}"); }
                if (path.EndsWith("/candidates") && HoldGeneration != null) return await HoldGeneration.Task;
                if (path.EndsWith("/projects/p/styles") && HoldCreate != null) return await HoldCreate.Task;
                if (path.EndsWith("/analyze")) return Json("{\"status\":\"WAITING\"}");
                return Json(Style);
            }
            if (path.EndsWith("/projects/p/styles")) { if (HoldRead != null) { var hold = HoldRead; HoldRead = null; return await hold.Task; } return Json("[" + Style + "]"); }
            if (path.EndsWith("/projects/p")) return Json("{\"default_style_id\":\"style1\"}");
            if (path.EndsWith("/asset-generation-batches")) return Json(BatchCreated ? "[{\"id\":\"batch1\",\"generation_kind\":\"STYLE_TEST\"}]" : "[]");
            if (path.EndsWith("/candidates")) return Json("[{\"id\":\"candidate1\",\"variant\":\"STYLE_TEST\",\"ordinal\":1,\"status\":\"" + (CandidateReady ? "READY" : "PENDING") + "\",\"asset_id\":\"" + (CandidateReady ? "image1" : "") + "\"},{\"id\":\"unrelated\",\"variant\":\"OUTFIT\",\"status\":\"READY\"}]");
            if (path.EndsWith("/models")) return Json("[{\"model_type\":\"IMAGE\",\"enabled\":true,\"operations\":[\"image_edit\"],\"logical_alias\":\"model-a\",\"display_name\":\"Codex CLI ImageGen\",\"provider\":\"Codex CLI\",\"model_id\":\"codex-imagegen\"}]");
            if (path.EndsWith("/assets")) return Json("[{\"id\":\"ref1\",\"kind\":\"STYLE_REFERENCE\",\"original_name\":\"京都色彩参考.png\",\"status\":\"UPLOADED\"}]");
            return Json("[]");
        }
    }
}
