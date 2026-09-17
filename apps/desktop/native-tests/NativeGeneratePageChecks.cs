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
            Require(Texts(view).Contains("本页主场景将进入生成输入") && Texts(view).Any(text => text.Contains("京都老宅")),
                "scene inheritance card shows the bound page scene asset");
            Require(fixture.SceneOffsets.SequenceEqual(new[] { 0, 50 }), "scene inheritance follows pagination to the bound 51st asset");
            fixture.ArchivedScene = true;
            await Invoke(view, "LoadWorkbenchAsync"); Layout(view, 1240, 1800);
            Require(Texts(view).Any(text => text.Contains("京都老宅 已归档")), "archived bound asset is identified separately from a missing asset");
            fixture.ArchivedScene = false;
            await Invoke(view, "LoadWorkbenchAsync"); Layout(view, 1240, 1800);
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
            var pane = new DirectorPane(view);
            await Invoke(pane, "LoadHistoryAsync");
            for (var step = 0; step < 6; step++)
            {
                Layout(pane, 1000, 1800);
                var actionLabel = step % 2 == 0 ? "撤销" : "重做";
                var buttons = Desc(pane).OfType<Button>().Where(b => Equals(b.Content, "撤销") || Equals(b.Content, "重做")).ToList();
                Require(buttons.Count == 1 && Equals(buttons[0].Content, actionLabel), "history exposes only the applicable inverse-chain action");
                Click(buttons[0]);
                await Until(() => !pane.Busy);
                Require(fixture.JournalStep == step + 1, "journal posts the latest executable command ID, including repeated undo/redo");
            }
            foreach (var status in new[] { "ACCEPTED", "SUPERSEDED", "FAILED" })
            {
                var unavailable = JsonSerializer.Deserialize<JsonElement>($"[{{\"command_id\":\"blocked\",\"status\":\"{status}\"}}]").EnumerateArray().ToList();
                Require(DirectorPane.HistoryActionIds(unavailable) == (null, null), "non-executed history has no undo/redo action");
            }
            var region = JsonSerializer.Deserialize<JsonElement>("""[{"command_id":"region","status":"EXECUTED","operation":"regenerate_region"}]""").EnumerateArray().ToList();
            Require(DirectorPane.HistoryActionIds(region) == (null, null), "image regeneration is not exposed as reversible");

            // ── R2D-01（#429-1 家族漏网）：导演台有草稿时，用户触发的完成路径
            //（生成/收藏/升清的 LoadWorkbenchAsync+Render）不得整建重渲染——
            // 旧实现只守 PollTick/RefreshAsync/保真激活，会 new DirectorPane 把
            // 输入中的指令静默清空。 ──
            fixture.Ready = true; fixture.ShowCandidates = true;
            await view.RefreshAsync();   // 先用 ready=true 的工作台数据重载（此时尚无草稿，可整建渲染）
            Set(view, "director", true);
            var draftPane = new DirectorPane(view);
            Set(view, "directorPane", draftPane);
            var commandInput = Field<TextBox>(draftPane, "commandInput");
            commandInput.Text = "把格 1 的台词改为「保留我_R2D01_」";
            Require(draftPane.HasDraft, "指令文本应让导演台判定为有草稿");
            var generatesBefore = fixture.Generates;
            await Invoke(view, "GenerateAsync");
            await Until(() => fixture.Generates == generatesBefore + 1);
            await Task.Delay(60);   // 给完成路径的 LoadWorkbenchAsync/finally Render 留出执行窗口
            Require(fixture.Generates == generatesBefore + 1, "生成请求应正常入队（守卫只拦渲染，不拦动作）");
            Require(ReferenceEquals(Field<DirectorPane?>(view, "directorPane"), draftPane),
                "有草稿时生成完成路径不得重建导演台（R2D-01）");
            Require(commandInput.Text.Contains("保留我_R2D01_"),
                "生成完成路径不得清空已输入的导演指令（R2D-01）");

            // ── R2D-03：切到无章节项目必须清空上一项目的页/工作台/追踪集，
            // 否则一个轮询周期后上一项目的候选网格被整页画进新项目。
            //（先把导演草稿/导演态清掉：旧实现的 PollTick 也会被 keep-drafts
            // 守卫挡住，清掉后新旧行为才有区分度。） ──
            commandInput.Text = "";
            Set(view, "director", false);
            fixture.EmptyChapters = true;
            view.Activate(new WorkspaceContext { Api = api, State = new(), Window = null!, Project = new ProjectItem("empty-proj", "无章节项目", "", 0, 0), NavigateSection = (s, q) => { destination = s + "?" + q; return Task.CompletedTask; }, OpenDashboard = () => Task.CompletedTask });
            await Until(() => Texts(view).Contains("没有可抽卡页面。先完成动态分页。"));
            Require(Field<PageItem?>(view, "currentPage") is null && Field<List<PageItem>>(view, "pages").Count == 0,
                "无章节项目必须清空上一项目的当前页与页列表（R2D-03）");
            Require(Field<WrapPanel>(view, "pageBar").Children.Count == 0, "无章节项目必须清空页签（R2D-03）");
            var readsBefore = fixture.Reads;
            view.PollTick();
            await Task.Delay(120);
            Require(fixture.Reads == readsBefore, "无章节项目不得轮询拉取上一项目的工作台（R2D-03）");

            Console.WriteLine("PASS: generation layout, full scene pagination/archive state, inverse-chain undo/redo, payloads, dedup/error recovery and readiness navigation.");
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
    private static void Set(object o, string name, object? value) => o.GetType().GetField(name, BindingFlags.Instance | BindingFlags.NonPublic)!.SetValue(o, value);
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
        internal bool Ready = true, FailBatch, ShowCandidates, EmptyChapters;
        internal TaskCompletionSource<bool>? HoldBatch;
        internal JsonElement GenerationBody;
        internal readonly List<int> SceneOffsets = [];
        internal bool ArchivedScene;
        internal int JournalStep;
        private object[] History() => new object[] { new { command_group_id = "history", commands = Enumerable.Range(0, JournalStep + 1).Select(i => new
        {
            command_id = $"cmd-{i}", inverse_of_command_id = i == 0 ? null : $"cmd-{i - 1}",
            operation = "update_panel_shot", status = i == JournalStep ? "EXECUTED" : "SUPERSEDED",
            source = new { user_prompt = i == 0 ? "改成远景" : "" },
        }).ToArray() } };
        private object Page(int n) => new { id = $"pg-{n}", chapter_id = "ch", page_number = n, panel_count = 3, storyboard_version = 7, estimated_text_chars = 130, estimated_bubbles = 3, continuity_status = "NEEDS_REVIEW", scene_ids = new[] { "sc-1" }, source_coverage = new { ranges = new[] { new { text = Source } } } };
        private static object Batch(int n) => new { id = $"batch-{n}", ordinal = n, status = "COMPLETED" };
        private static HttpResponseMessage Json(object data) => new(HttpStatusCode.OK) { Content = new StringContent(JsonSerializer.Serialize(data)) };
        protected override async Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken token)
        {
            string path = request.RequestUri!.AbsolutePath;
            if (request.Method == HttpMethod.Get)
            {
                if (path.EndsWith("/chapters")) return Json(EmptyChapters ? Array.Empty<object>() : new object[] { new { id = "ch", title = "第一章", ordinal = 1, page_count = 11 } });
                if (path.EndsWith("/models")) return Json(new[] { new { logical_alias = "model-a", model_type = "IMAGE", enabled = true, display_enabled = true, display_name = "Codex CLI ImageGen", provider = "Codex CLI", model_id = "codex-imagegen", operations = new[] { "image_edit" } }, new { logical_alias = "model-b", model_type = "IMAGE", enabled = true, display_enabled = true, display_name = "Google: Nano Banana 2 (Gemini 3.1 Flash Image Preview)", provider = "OpenRouter", model_id = "google/gemini-3.1-flash-image-preview", operations = new[] { "image_edit" } } });
                if (path.EndsWith("/pages")) return Json(Enumerable.Range(1, 11).Select(Page).ToArray());
                if (path.EndsWith("/script")) return Json(new { scenes = new[] { new { id = "sc-1", ordinal = 1, location = "京都，爸爸的灵牌前", scene_asset_id = "asset1", scene_asset_variant_id = "rain" } } });
                if (path.EndsWith("/command-groups")) return Json(History());
                if (path.EndsWith("/scene-assets"))
                {
                    var query = System.Web.HttpUtility.ParseQueryString(request.RequestUri.Query);
                    var offset = int.TryParse(query["offset"], out var value) ? value : 0;
                    var limit = int.TryParse(query["limit"], out var count) ? count : 50;
                    SceneOffsets.Add(offset);
                    var assets = Enumerable.Range(0, 51).Select(i => new { id = i == 50 ? "asset1" : $"other-{i}", name = i == 50 ? "京都老宅" : $"场景{i}", deleted_at = i == 50 && ArchivedScene ? "2026-09-16T00:00:00Z" : null, variants = new[] { new { id = "rain", name = "小雨", deleted_at = (string?)null } } });
                    if (query["include_deleted"] != "true") assets = assets.Where(a => a.deleted_at == null);
                    return Json(assets.Skip(offset).Take(limit).ToArray());
                }
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
            if (path.Contains("/director/commands/"))
            {
                var action = JournalStep % 2 == 0 ? "undo" : "redo";
                if (!path.EndsWith($"/cmd-{JournalStep}/{action}")) return new(HttpStatusCode.Conflict) { Content = new StringContent("{\"detail\":\"wrong inverse command\"}") };
                JournalStep++;
                return Json(History()[0]);
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
