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

internal static class NativeScriptPageChecks
{
    internal static void Run(string output)
    {
        Exception? failure = null;
        var thread = new Thread(() =>
        {
            var app = new Application { ShutdownMode = ShutdownMode.OnExplicitShutdown };
            app.Resources.MergedDictionaries.Add(new ResourceDictionary { Source = new Uri("/MangaFlow.Native;component/Theme.xaml", UriKind.Relative) });
            SynchronizationContext.SetSynchronizationContext(new DispatcherSynchronizationContext());
            app.Dispatcher.BeginInvoke(new Action(async () => { try { Directory.CreateDirectory(output); await Checks(output); } catch (Exception e) { failure = e; } finally { app.Shutdown(); } })); app.Run();
        }); thread.SetApartmentState(ApartmentState.STA); thread.Start(); thread.Join();
        if (failure != null) throw new Exception("Script page checks failed", failure);
    }
    private static async Task Checks(string output)
    {
        var fixture = new Fixture(); using var api = new ApiClient("http://127.0.0.1:12345", fixture); var view = new ScriptView();
        try
        {
            view.Activate(new WorkspaceContext { Api = api, State = new(), Window = null!, Project = new ProjectItem("p", "我最讨厌妹妹了", "", 0, 0), NavigateSection = (_, _) => Task.CompletedTask, OpenDashboard = () => Task.CompletedTask });
            SceneSection Scene() => Field<StackPanel>(view, "body").Children.OfType<SceneSection>().Single();
            await Until(() => Field<StackPanel>(view, "body").Children.OfType<SceneSection>().Any());
            Render(view, 1100, 1400, Path.Combine(output, "native-script-1100.png"));
            Require(Texts(view).Contains("原文覆盖 100%") && Texts(view).Any(t => t.Contains("9 / 9 个原文片段")), "coverage uses real nested API schema");
            Require(Texts(view).Any(t => t.Contains("来源 3 段")), "beat source range uses nested segment ids");
            var action = Desc(Scene()).OfType<TextBlock>().Single(t => t.Text == fixture.Action);
            var number = Desc(Scene().BeatRows.Single()).OfType<TextBlock>().Single(t => t.Text == "01");
            Require(action.TranslatePoint(new Point(), Scene()).X > number.TranslatePoint(new Point(), Scene()).X + 30, "beat action does not overlap its ordinal");
            var combos = Desc(Scene()).OfType<ComboBox>().ToList();
            var assets = combos.Single(c => c.Items.OfType<ComboBoxItem>().Any(i => Equals(i.Tag, "asset1")));
            var variants = combos.Single(c => c != assets && c.Items.OfType<ComboBoxItem>().Any(i => Equals(i.Content, "使用资产默认变体")));
            assets.SelectedItem = assets.Items.OfType<ComboBoxItem>().Single(i => Equals(i.Tag, "asset1"));
            Require(variants.Items.OfType<ComboBoxItem>().Any(i => Equals(i.Tag, "rain")), "asset selection refreshes its variants");
            Require(!variants.Items.OfType<ComboBoxItem>().Any(i => Equals(i.Tag, "archived")), "archived variants remain unavailable");
            variants.SelectedItem = variants.Items.OfType<ComboBoxItem>().Single(i => Equals(i.Tag, "rain"));
            fixture.HoldBind = new TaskCompletionSource<bool>(); var bind = Button(Scene(), "保存绑定"); Click(bind); Click(bind);
            await Until(() => fixture.Binds == 1); Require(!bind.IsEnabled, "binding double click is guarded");
            fixture.HoldBind.SetResult(true); await Until(() => bind.IsEnabled); fixture.HoldBind = null;
            Require(fixture.LastBind.Text("scene_asset_variant_id") == "rain", "selected variant id reaches binding API");
            Render(view, 650, 1450, Path.Combine(output, "native-script-650.png"));
            Require(Desc(Scene()).OfType<ComboBox>().All(c => c.ActualWidth <= 630), "binding selectors fit narrow window");
            var scene = Scene(); Click(Button(scene, "编辑场景")); Layout(view, 1100, 1600);
            var location = Desc(scene).OfType<TextBox>().First(); location.Text = "京都，爸爸的灵牌前 · 修订";
            await view.RefreshAsync(); Require(ReferenceEquals(scene, Scene()) && location.Text.EndsWith("修订"), "refresh keeps scene draft");
            Render(view, 1100, 1550, Path.Combine(output, "native-script-scene-edit.png"));
            fixture.Reject = true; var save = Button(scene, "保存场景"); Click(save); await Until(() => fixture.SceneWrites == 1 && save.IsEnabled);
            Require(scene.IsEditing && location.Text.EndsWith("修订") && Field<TextBlock>(view, "notice").Text.Contains("保留输入"), "conflict preserves scene draft and displays inline error");
            fixture.Reject = false; Click(save); await Until(() => fixture.SceneWrites == 2 && !ReferenceEquals(scene, Scene()));
            Require(fixture.LastScene.Number("version") == 7 && fixture.LastScene.Text("location").EndsWith("修订"), "scene save carries captured version and input");
            var beat = Scene().BeatRows.Single(); Click(Button(beat, "编辑")); Layout(view, 1100, 1500);
            var actionInput = Desc(beat).OfType<TextBox>().First(); actionInput.Text = "我抬起头，雨声从半开的窗外传来。";
            Render(view, 1100, 1550, Path.Combine(output, "native-script-beat-edit.png"));
            var beatSave = Button(beat, "保存情节拍"); Click(beatSave); await Until(() => fixture.BeatWrites == 1);
            Require(fixture.LastBeat.Number("version") == 3 && !fixture.LastBeat.TryGetProperty("source_range", out _), "beat save is versioned and never modifies source range");
            await Task.Delay(30); fixture.BoundAsset = null; await view.RefreshAsync(); await Task.Delay(30);
            var create = Button(Scene(), "从地点创建场景资产"); Click(create); Click(create); await Until(() => fixture.Creates == 1 && fixture.BoundAsset == "new-asset"); await Task.Delay(30);
            Require(fixture.LastCreate.Text("description").Contains("京都") && fixture.LastBind.Text("scene_asset_id") == "new-asset", "create-from-location creates then binds once");
            Console.WriteLine("PASS: screenplay wide/narrow layout, coverage/source schemas, beat column placement, variant refresh/archive filtering, binding dedup, versioned scene/beat saves, conflict draft preservation, create-from-location.");
            Console.WriteLine("Offscreen WPF + HTTP fixtures; real-window/DPI/provider acceptance NOT RUN.");
        }
        finally { view.Deactivate(); }
    }
    private static IEnumerable<DependencyObject> Desc(DependencyObject root) => NativeParityChecks.Descendants(root);
    private static string[] Texts(DependencyObject root) => Desc(root).OfType<TextBlock>().Select(t => t.Text).ToArray();
    private static Button Button(DependencyObject root, string text) { if (root is FrameworkElement element) Layout(element, 1060, 2000); return Desc(root).OfType<Button>().Single(b => Equals(b.Content, text)); }
    private static void Click(ButtonBase b) => b.RaiseEvent(new RoutedEventArgs(ButtonBase.ClickEvent));
    private static T Field<T>(object o, string name) => (T)o.GetType().GetField(name, BindingFlags.Instance | BindingFlags.NonPublic)!.GetValue(o)!;
    private static void Require(bool ok, string message) { if (!ok) throw new Exception(message); }
    private static async Task Until(Func<bool> predicate) { using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(8)); while (!predicate()) await Task.Delay(10, timeout.Token); }
    private static void Layout(FrameworkElement e, int w, int h) { e.Measure(new Size(w, h)); e.Arrange(new Rect(0, 0, w, h)); e.UpdateLayout(); }
    private static void Render(FrameworkElement e, int w, int h, string path) { Layout(e, w, h); var bitmap = new RenderTargetBitmap(w, h, 96, 96, PixelFormats.Pbgra32); bitmap.Render(e); var encoder = new PngBitmapEncoder(); encoder.Frames.Add(BitmapFrame.Create(bitmap)); using var f = File.Create(path); encoder.Save(f); }
    private sealed class Fixture : HttpMessageHandler
    {
        internal string Action = "“我”跪在爸爸的灵牌前，肩膀颤抖，失声痛哭。窗外下着淅淅沥沥的小雨。";
        internal bool Reject;
        internal int Binds, Creates, SceneWrites, BeatWrites;
        internal string? BoundAsset;
        internal TaskCompletionSource<bool>? HoldBind;
        internal JsonElement LastBind, LastCreate, LastScene, LastBeat;
        private object Scene => new { id = "s1", ordinal = 1, version = 7, location = "京都，爸爸的灵牌前", time_label = "四月初", purpose = "展现“我”因父亲去世而产生的悲痛与失落，以及母亲对“我”的安慰。", emotional_arc = "极度悲痛 → 迷茫失落", weather = "小雨", scene_asset_id = BoundAsset, scene_asset_variant_id = BoundAsset == null ? null : "rain", outfit_assignments = new Dictionary<string, string>(), beats = new[] { new { id = "b1", ordinal = 1, version = 3, action = Action, speaker_name = "妈妈", dialogue = "已经够了，爸爸会担心你的。", narration = "四月初，京都下起了小雨。", emotion = "悲痛", subtext = "克制的安慰", importance = .85, must_visualize = true, mergeable = false, page_turn_hook = true, source_range = new { segment_ids = new[] { "seg1", "seg2", "seg3" } } } } };
        private static HttpResponseMessage Json(object data) => new(HttpStatusCode.OK) { Content = new StringContent(JsonSerializer.Serialize(data)) };
        protected override async Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken token)
        {
            var path = request.RequestUri!.AbsolutePath;
            if (request.Method == HttpMethod.Get)
            {
                if (path.EndsWith("/chapters")) return Json(new[] { new { id = "ch", title = "第一章", ordinal = 1, page_count = 11 } });
                if (path.EndsWith("/script")) return Json(new { status = "READY", coverage = new { ratio = 1.0, expected = 9, covered = 9 }, scenes = new[] { Scene } });
                if (path.EndsWith("/characters")) return Json(new[] { new { id = "c1", primary_name = "我" }, new { id = "c2", primary_name = "妈妈" } });
                if (path.EndsWith("/outfits")) return Json(new[] { new { id = "o1", name = "雨夜外套", character_id = "c1" }, new { id = "o2", name = "素色便服", character_id = "c2" } });
                if (path.EndsWith("/scene-assets")) return Json(new[] { new { id = "asset1", name = "京都老宅", variants = new[] { new { id = "rain", name = "小雨", deleted_at = (string?)null }, new { id = "archived", name = "旧版本", deleted_at = "2026-09-01" } } } });
                return Json(Array.Empty<object>());
            }
            var body = JsonSerializer.Deserialize<JsonElement>(request.Content == null ? "{}" : await request.Content.ReadAsStringAsync(token));
            if (path.EndsWith("/scene-assets")) { Creates++; LastCreate = body; return Json(new { id = "new-asset" }); }
            if (path.EndsWith("/bind-asset")) { Binds++; LastBind = body; if (HoldBind != null) await HoldBind.Task; BoundAsset = body.Text("scene_asset_id"); return Json(Scene); }
            if (path.EndsWith("/scenes/s1")) { SceneWrites++; LastScene = body; }
            if (path.EndsWith("/beats/b1")) { BeatWrites++; LastBeat = body; }
            return Reject ? new(HttpStatusCode.Conflict) { Content = new StringContent("{\"detail\":\"版本已更新，请刷新\"}") } : Json(Scene);
        }
    }
}
