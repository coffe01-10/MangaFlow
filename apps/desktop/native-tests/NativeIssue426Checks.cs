using System.IO;
using System.Net;
using System.Net.Http;
using System.Reflection;
using System.Text.Json;
using System.Threading;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Controls.Primitives;
using System.Windows.Threading;
using MangaFlow.Native;
using MangaFlow.Native.Services;
using MangaFlow.Native.Views;

// #426/#448 回归（ScriptView 服装指定与编辑表单保存）：
//  1. #426 —— 两处脏改（一处删除 + 一处换装）必须合并成终版全量映射、只发一个
//     携带场景 version 的 PATCH。旧实现按角色循环、每次调用都从同一份渲染期快照
//     重建全量映射：PATCH #1 刚删除的指定会被 PATCH #2 用快照原样复活。
//  2. #448 —— 场景/情节拍保存是普通 Click 处理器，无双击在途守卫：第二次点击带
//     同一份过期 version 再发一个 PATCH，必然 409 并弹出假的「保存未完成」冲突框。
//
// 【需 lead 注册】本文件按任务约束未接入运行入口：建议在
// NativeInteractionChecks.RunIsolated 的 await 链（或专用入口）追加
// `NativeIssue426Checks.Run(output);`——Run 内部自带 STA 调度，也可独立调用
// （无 Application 时会自建 STA 线程 + Application/Theme，NativeStoryboardEditChecks
// 同款）。注册调用点必须在 STA/Dispatcher 线程上下文中（WPF 视觉树访问要求）。
internal static class NativeIssue426Checks
{
    internal static void Run(string output)
    {
        var previous = (string)typeof(KeyValueStore).GetField("path", BindingFlags.Static | BindingFlags.NonPublic)!.GetValue(null)!;
        Directory.CreateDirectory(output);
        var prefs = Path.Combine(output, "issue426-test-prefs.json");
        File.WriteAllText(prefs, "{}");
        KeyValueStore.UseLocation(prefs);
        try
        {
            if (Application.Current is null) RunOnDedicatedStaThread(output);
            else RunFrame(output);
        }
        finally
        {
            KeyValueStore.UseLocation(previous);
            File.Delete(prefs);
            File.Delete(prefs + ".tmp");
        }
    }

    // 独立运行（进程里还没有 Application）：自建 STA 线程 + Application/Theme
    //（NativeSceneChecks 的模式）。Application 是 AppDomain 级单例，本方法每
    // 进程至多走一次；已有 Application 时走 RunFrame 的调用方线程。
    private static void RunOnDedicatedStaThread(string output)
    {
        Exception? failure = null;
        var thread = new Thread(() =>
        {
            try
            {
                var app = new Application { ShutdownMode = ShutdownMode.OnExplicitShutdown };
                app.Resources.MergedDictionaries.Add(new ResourceDictionary { Source = new Uri("/MangaFlow.Native;component/Theme.xaml", UriKind.Relative) });
                RunFrame(output);
            }
            catch (Exception error) { failure = error; }
        });
        thread.SetApartmentState(ApartmentState.STA);
        thread.Start();
        thread.Join();
        if (failure != null) throw new Exception("Issue 426/448 checks failed", failure);
    }

    // STA + DispatcherFrame 泵（NativeInteractionChecks.RunIsolated 的模式）：
    // async-void 的按钮处理经由 DispatcherSynchronizationContext 回到泵上执行。
    private static void RunFrame(string output)
    {
        Exception? failure = null;
        var frame = new DispatcherFrame();
        Dispatcher.CurrentDispatcher.UnhandledException += (_, e) =>
        {
            e.Handled = true;
            failure ??= new Exception("Dispatcher unhandled: " + e.Exception.Message, e.Exception);
            frame.Continue = false;
        };
        var timeout = new DispatcherTimer { Interval = TimeSpan.FromSeconds(60) };
        timeout.Tick += (_, _) => { failure = new TimeoutException("Issue 426/448 checks timed out"); frame.Continue = false; };
        timeout.Start();
        SynchronizationContext.SetSynchronizationContext(new DispatcherSynchronizationContext());
        Dispatcher.CurrentDispatcher.BeginInvoke(new Action(async () =>
        {
            try { await CheckAsync(); }
            catch (Exception error) { failure = error; }
            finally { frame.Continue = false; }
        }));
        Dispatcher.PushFrame(frame);
        timeout.Stop();
        if (failure != null) throw new Exception("Issue 426/448 checks failed", failure);
    }

    private static async Task CheckAsync()
    {
        var fixture = new Fixture();
        using var api = new ApiClient("http://127.0.0.1:12345", fixture);
        var view = new ScriptView();
        view.Activate(new WorkspaceContext
        {
            Api = api, State = new WorkspaceState(), Window = null!,
            Project = new ProjectItem("issue426", "服装保存测试", "", 0, 0),
            NavigateSection = (_, _) => Task.CompletedTask, OpenDashboard = () => Task.CompletedTask,
        });
        try
        {
            var body = Field<StackPanel>(view, "body");
            await Until(() => body.Children.OfType<SceneSection>().Any());
            SceneSection Scene() => body.Children.OfType<SceneSection>().Single();

            // ── 1. #426：删除一个角色指定 + 换另一个角色的装 → 恰好一个全量 PATCH ──
            var scene = Scene();
            var combos = Descendants(scene).OfType<ComboBox>().ToList();
            // 含 o-r1 的是「删除位」角色（小满）的选择器；含 o-k2 的是「换装位」角色（阿澈）的。
            var removal = combos.Single(box => box.Items.OfType<ComboBoxItem>().Any(i => (string?)i.Tag == "o-r1"));
            var change = combos.Single(box => box.Items.OfType<ComboBoxItem>().Any(i => (string?)i.Tag == "o-k2"));
            removal.SelectedIndex = 0;   // 删除：选「未指定」
            SelectCombo(change, "o-k2"); // 换装：o-k1 → o-k2

            var outfitPending = new TaskCompletionSource<HttpResponseMessage>();
            fixture.PendingOutfitPatch = outfitPending;
            var reads = fixture.ScriptReads;
            var outfitSave = Buttons(Scene(), "保存服装指定").Single();
            Require(outfitSave.IsEnabled, "进入在途前保存按钮应可用");
            Click(outfitSave);
            Require(!outfitSave.IsEnabled, "首次点击进入在途后保存按钮必须禁用（视觉反馈，#448 同款守卫）");
            Click(outfitSave);
            await Until(() => fixture.OutfitPatches >= 1);

            var payload = fixture.OutfitBody;
            Require(payload.TryGetProperty("version", out var version) && version.ValueKind == JsonValueKind.Number && version.GetInt32() == 3,
                "服装指定 PATCH 必须携带观察到的场景 version（并发修改应 409 而不是末写覆盖，#426）");
            var assignments = payload.Element("assignments");
            Require(assignments.ValueKind == JsonValueKind.Object, "PATCH 载荷必须携带全量 assignments 字典（#426）");
            Require(!assignments.TryGetProperty("ch-remove", out _),
                "被删除的角色不得出现在终版 assignments（旧实现按角色循环、用渲染期快照复活删除，#426）");
            Require(assignments.TryGetProperty("ch-keep", out var kept) && kept.GetString() == "o-k2",
                "换装角色的终版指定必须是保存瞬间选择器的当前值（#426）");
            Require(assignments.EnumerateObject().Count() == 1, "终版 assignments 应只含换装后的角色（#426）");
            Require(fixture.OutfitPaths.All(path => path.EndsWith("/scenes/sc-1/outfits")), "服装指定 PATCH 必须指向 scenes/{id}/outfits");

            outfitPending.SetResult(Json("""{"scene_id":"sc-1","assignments":{"ch-keep":"o-k2"}}"""));
            await Until(() => fixture.ScriptReads > reads);
            await Until(() => body.Children.OfType<SceneSection>().Any());
            Require(fixture.OutfitPatches == 1,
                $"两处脏改必须恰好一个 PATCH（实际 {fixture.OutfitPatches} 个——第二个 PATCH 会复活已删除的指定，#426）");

            // ── 2. #448：场景保存双击守卫 ──
            scene = Scene();
            Click(Buttons(scene, "编辑场景").Single());
            var scenePending = new TaskCompletionSource<HttpResponseMessage>();
            fixture.PendingScenePatch = scenePending;
            reads = fixture.ScriptReads;
            var sceneSave = Buttons(Scene(), "保存场景").Single();
            Require(sceneSave.IsEnabled, "进入在途前保存按钮应可用");
            Click(sceneSave);
            Require(!sceneSave.IsEnabled, "首次点击进入在途后保存按钮必须禁用（视觉反馈，#448）");
            Click(sceneSave);
            await Until(() => fixture.ScenePatches >= 1);
            Require(fixture.ScenePatches == 1,
                $"双击场景保存只能发出一个 PATCH（实际 {fixture.ScenePatches} 个——第二个带同一份过期 version 必然 409 假冲突，#448）");
            scenePending.SetResult(Json("{}"));
            await Until(() => fixture.ScriptReads > reads);
            await Until(() => body.Children.OfType<SceneSection>().Any());

            // ── 3. #448：情节拍保存双击守卫 ──
            scene = Scene();
            var beat = scene.BeatRows.Single();
            Click(Buttons(beat, "编辑").Single());
            var beatPending = new TaskCompletionSource<HttpResponseMessage>();
            fixture.PendingBeatPatch = beatPending;
            reads = fixture.ScriptReads;
            var beatSave = Buttons(beat, "保存情节拍").Single();
            Require(beatSave.IsEnabled, "进入在途前保存按钮应可用");
            Click(beatSave);
            Require(!beatSave.IsEnabled, "首次点击进入在途后保存按钮必须禁用（视觉反馈，#448）");
            Click(beatSave);
            await Until(() => fixture.BeatPatches >= 1);
            Require(fixture.BeatPatches == 1,
                $"双击情节拍保存只能发出一个 PATCH（实际 {fixture.BeatPatches} 个——第二个带同一份过期 version 必然 409 假冲突，#448）");
            beatPending.SetResult(Json("{}"));
            await Until(() => fixture.ScriptReads > reads);
            await Settle();
        }
        finally { view.Deactivate(); }
        Console.WriteLine("PASS: wardrobe save merges live selector state once into a single version-carrying full-map PATCH; scene/beat saves reject double-click re-entry (#426/#448)");
        Console.WriteLine("NOT RUN: real WPF mouse double-click timing; a synthetic double RaiseEvent drives the same re-entry path");
    }

    // ── helpers（NativeStoryboardEditChecks 同款）──
    private static async Task Until(Func<bool> condition)
    {
        using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(5));
        while (!condition()) await Task.Delay(5, timeout.Token);
    }

    private static async Task Settle() => await Task.Delay(30);

    private static void Require(bool condition, string message)
    {
        if (!condition) throw new Exception(message);
    }

    private static IEnumerable<DependencyObject> Descendants(DependencyObject root)
    {
        var count = System.Windows.Media.VisualTreeHelper.GetChildrenCount(root);
        for (var i = 0; i < count; i++)
        {
            var child = System.Windows.Media.VisualTreeHelper.GetChild(root, i);
            yield return child;
            foreach (var nested in Descendants(child)) yield return nested;
        }
    }

    private static IEnumerable<Button> Buttons(DependencyObject root, string content) =>
        Descendants(root).OfType<Button>().Where(button => Equals(button.Content, content));

    private static void SelectCombo(ComboBox box, string tag)
    {
        foreach (var item in box.Items.OfType<ComboBoxItem>())
            if ((string?)item.Tag == tag) { box.SelectedItem = item; return; }
    }

    private static void Click(ButtonBase button) => button.RaiseEvent(new RoutedEventArgs(ButtonBase.ClickEvent));

    private static T Field<T>(object value, string name) => (T)value.GetType().GetField(name, BindingFlags.Instance | BindingFlags.NonPublic)!.GetValue(value)!;

    private static HttpResponseMessage Json(string text) => new(HttpStatusCode.OK) { Content = new StringContent(text) };

    // 一章一场景：两个有服装档案的角色（小满=删除位，阿澈=换装位），场景已带
    // outfit_assignments {ch-remove:o-r1, ch-keep:o-k1}；保存成功后夹具按服务端
    // 全量替换语义更新 script 响应。PATCH 可挂起（TaskCompletionSource）以便在
    // 在途期间断言「双击只发一个请求」。
    private sealed class Fixture : HttpMessageHandler
    {
        public int ScriptReads, OutfitPatches, ScenePatches, BeatPatches;
        public JsonElement OutfitBody;
        public readonly List<string> OutfitPaths = [];
        public TaskCompletionSource<HttpResponseMessage>? PendingOutfitPatch, PendingScenePatch, PendingBeatPatch;
        private bool outfitSaved;

        protected override async Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken token)
        {
            var path = request.RequestUri!.AbsolutePath;
            if (request.Method == HttpMethod.Get)
            {
                if (path.EndsWith("/chapters/ch-1/script")) { ScriptReads++; return Json(Script); }
                if (path.EndsWith("/projects/issue426/chapters"))
                    return Json("""[{"id":"ch-1","title":"第一章","ordinal":1,"page_count":1}]""");
                if (path.EndsWith("/projects/issue426/characters"))
                    return Json("""[{"id":"ch-remove","primary_name":"小满"},{"id":"ch-keep","primary_name":"阿澈"}]""");
                if (path.EndsWith("/projects/issue426/outfits"))
                    return Json("""[{"id":"o-r1","character_id":"ch-remove","name":"雨衣"},{"id":"o-k1","character_id":"ch-keep","name":"校服A"},{"id":"o-k2","character_id":"ch-keep","name":"校服B"}]""");
                if (path.EndsWith("/scene-assets")) return Json("[]");
                return Json("[]");
            }
            var body = JsonSerializer.Deserialize<JsonElement>(await request.Content!.ReadAsStringAsync(token));
            if (path.EndsWith("/scenes/sc-1/outfits"))
            {
                OutfitPatches++; OutfitPaths.Add(path); OutfitBody = body;
                if (PendingOutfitPatch is { } pending) { PendingOutfitPatch = null; return await pending.Task; }
                outfitSaved = true;
                return Json("""{"scene_id":"sc-1","assignments":{"ch-keep":"o-k2"}}""");
            }
            if (path.EndsWith("/scenes/sc-1"))
            {
                ScenePatches++;
                if (PendingScenePatch is { } pending) { PendingScenePatch = null; return await pending.Task; }
                return Json("{}");
            }
            if (path.EndsWith("/beats/bt-1"))
            {
                BeatPatches++;
                if (PendingBeatPatch is { } pending) { PendingBeatPatch = null; return await pending.Task; }
                return Json("{}");
            }
            throw new Exception("Unexpected issue-426 request: " + path);
        }

        private string Script => outfitSaved ? SavedScript : InitialScript;

        private const string InitialScript = """
            {"status":"CONFIRMED","coverage_ratio":1.0,"source_segments":[{"id":"seg-1","text":"原文片段"}],
             "scenes":[
              {"id":"sc-1","location":"老宅","time_label":"夜","weather":"小雨","purpose":"建立场景","emotional_arc":"平静",
               "version":3,"scene_asset_id":"","scene_asset_variant_id":"",
               "outfit_assignments":{"ch-remove":"o-r1","ch-keep":"o-k1"},
               "source_segments":[],
               "beats":[
                {"id":"bt-1","action":"雨夜奔跑","speaker_name":"小满","dialogue":"……","narration":"","subtext":"",
                 "must_visualize":true,"mergeable":false,"page_turn_hook":false,"importance":0.5,"version":2,"source_segments":[]}]}]}
            """;

        private const string SavedScript = """
            {"status":"CONFIRMED","coverage_ratio":1.0,"source_segments":[{"id":"seg-1","text":"原文片段"}],
             "scenes":[
              {"id":"sc-1","location":"老宅","time_label":"夜","weather":"小雨","purpose":"建立场景","emotional_arc":"平静",
               "version":4,"scene_asset_id":"","scene_asset_variant_id":"",
               "outfit_assignments":{"ch-keep":"o-k2"},
               "source_segments":[],
               "beats":[
                {"id":"bt-1","action":"雨夜奔跑","speaker_name":"小满","dialogue":"……","narration":"","subtext":"",
                 "must_visualize":true,"mergeable":false,"page_turn_hook":false,"importance":0.5,"version":2,"source_segments":[]}]}]}
            """;
    }
}
