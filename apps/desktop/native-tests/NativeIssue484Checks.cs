using System.IO;
using System.Net;
using System.Net.Http;
using System.Reflection;
using System.Text.Json;
using System.Threading;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Threading;
using MangaFlow.Native;
using MangaFlow.Native.Services;
using MangaFlow.Native.Views;

// #484/#440 回归：deleted_at 的线上形状是 null | ISO 时间戳字符串（网页按
// `item.deleted_at == null` 过滤，见 scene-picker.tsx），而 JsonFields.Flag 只匹配
// 布尔 true —— 旧代码里 SceneAssetItem.Deleted 在生产数据下恒为 false：已归档资产
// 仍进入 Script 绑定下拉、行状态显示原始「已上传」而非「已归档」。同因：变体下拉
// 不过滤场景详情内嵌的已归档变体（选中后保存必 422「场景变体已归档」，#440）。
//
// 【需 lead 注册】本文件按任务约束未接入运行入口：建议在
// NativeInteractionChecks.RunIsolated 的 await 链（或专用入口）追加
// `NativeIssue484Checks.Run(output);`——Run 内部自带 STA 调度，也可独立调用
// （无 Application 时会自建 STA 线程 + Application/Theme，NativeStoryboardEditChecks
// 同款）。注册调用点必须在 STA/Dispatcher 线程上下文中（WPF 视觉树访问要求）。
internal static class NativeIssue484Checks
{
    internal static void Run(string output)
    {
        var previous = (string)typeof(KeyValueStore).GetField("path", BindingFlags.Static | BindingFlags.NonPublic)!.GetValue(null)!;
        Directory.CreateDirectory(output);
        var prefs = Path.Combine(output, "issue484-test-prefs.json");
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
        if (failure != null) throw new Exception("Issue 484/440 checks failed", failure);
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
        timeout.Tick += (_, _) => { failure = new TimeoutException("Issue 484/440 checks timed out"); frame.Continue = false; };
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
        if (failure != null) throw new Exception("Issue 484/440 checks failed", failure);
    }

    private static async Task CheckAsync()
    {
        // ── 1. 模型层：deleted_at 四种形状的 Deleted 判定（#484 的直接判据）──
        using (var json = JsonDocument.Parse("""{"id":"sa-ts","name":"老宅","status":"UPLOADED","deleted_at":"2026-09-01T00:00:00Z"}"""))
        {
            var item = SceneAssetItem.From(json.RootElement);
            Require(item.Deleted, "时间戳字符串 deleted_at 必须判定为已归档（Flag 只匹配布尔 true，#484）");
            Require(item.StatusLabel == "已归档", "归档资产的状态行必须显示「已归档」而不是原始上传状态（#484）");
            Require(!json.RootElement.Flag("deleted_at"), "Flag 必须保持仅布尔语义；时间戳列一律走 FlagDate（#484）");
        }
        using (var json = JsonDocument.Parse("""{"id":"sa-null","name":"老宅","status":"UPLOADED","deleted_at":null}"""))
            Require(!SceneAssetItem.From(json.RootElement).Deleted, "deleted_at 为 null 必须判定为未归档（#484）");
        using (var json = JsonDocument.Parse("""{"id":"sa-absent","name":"老宅","status":"UPLOADED"}"""))
            Require(!SceneAssetItem.From(json.RootElement).Deleted, "缺失 deleted_at 必须判定为未归档（#484）");
        using (var json = JsonDocument.Parse("""{"id":"sa-legacy","name":"老宅","status":"UPLOADED","deleted_at":true}"""))
            Require(SceneAssetItem.From(json.RootElement).Deleted, "遗留布尔 true 的 deleted_at 仍必须判定为已归档（#484）");

        // ── 2. UI 层：绑定下拉不得提供已归档资产 / 已归档变体（#484/#440 的用户可见面）──
        var fixture = new Fixture();
        using var api = new ApiClient("http://127.0.0.1:12345", fixture);
        var view = new ScriptView();
        view.Activate(new WorkspaceContext
        {
            Api = api, State = new WorkspaceState(), Window = null!,
            Project = new ProjectItem("issue484", "归档资产测试", "", 0, 0),
            NavigateSection = (_, _) => Task.CompletedTask, OpenDashboard = () => Task.CompletedTask,
        });
        try
        {
            var body = Field<StackPanel>(view, "body");
            await Until(() => body.Children.OfType<SceneSection>().Any());
            var scene = body.Children.OfType<SceneSection>().Single();
            var combos = Descendants(scene).OfType<ComboBox>().ToList();
            var assetSelector = combos.Single(box => box.Items.OfType<ComboBoxItem>().Any(i => (string?)i.Tag == "sa-live"));
            var variantSelector = combos.Single(box => box.Items.OfType<ComboBoxItem>().Any(i => (string?)i.Tag == "v-live"));
            Require(!assetSelector.Items.OfType<ComboBoxItem>().Any(i => (string?)i.Tag == "sa-dead"),
                "已归档（时间戳 deleted_at）资产不得出现在场景资产绑定下拉（#484）");
            Require(!variantSelector.Items.OfType<ComboBoxItem>().Any(i => (string?)i.Tag == "v-gone"),
                "场景详情内嵌的已归档变体不得出现在变体下拉（#440：选中后保存必 422 场景变体已归档）");
            Require(assetSelector.SelectedItem is ComboBoxItem { Tag: "sa-live" } && variantSelector.SelectedItem is ComboBoxItem { Tag: "v-live" },
                "已保存的绑定必须仍选中活跃资产与活跃变体");
        }
        finally { view.Deactivate(); }
        Console.WriteLine("PASS: deleted_at timestamp/null/absent/legacy-boolean shapes, archived status label, and archived asset/variant exclusion from Script binding dropdowns (#484/#440)");
    }

    // ── helpers（NativeStoryboardEditChecks 同款）──
    private static async Task Until(Func<bool> condition)
    {
        using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(5));
        while (!condition()) await Task.Delay(5, timeout.Token);
    }

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

    private static T Field<T>(object value, string name) => (T)value.GetType().GetField(name, BindingFlags.Instance | BindingFlags.NonPublic)!.GetValue(value)!;

    private static HttpResponseMessage Json(string text) => new(HttpStatusCode.OK) { Content = new StringContent(text) };

    // 一章一场景（已绑定 sa-live / v-live）；资产表里混入时间戳归档资产 sa-dead，
    // 活跃资产的变体列表里混入时间戳归档变体 v-gone —— 与线上 include_deleted
    // 嵌入形状一致（scene-assets 列表接口默认不裁剪变体，见 #440 引证）。
    private sealed class Fixture : HttpMessageHandler
    {
        protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken token)
        {
            var path = request.RequestUri!.AbsolutePath;
            if (request.Method == HttpMethod.Get)
            {
                if (path.EndsWith("/chapters/ch-1/script")) return Task.FromResult(Json(Script));
                if (path.EndsWith("/projects/issue484/chapters"))
                    return Task.FromResult(Json("""[{"id":"ch-1","title":"第一章","ordinal":1,"page_count":1}]"""));
                if (path.EndsWith("/projects/issue484/characters")) return Task.FromResult(Json("[]"));
                if (path.EndsWith("/projects/issue484/outfits")) return Task.FromResult(Json("[]"));
                if (path.EndsWith("/scene-assets")) return Task.FromResult(Json(SceneAssets));
            }
            return Task.FromResult(Json("{}"));
        }

        private const string Script = """
            {"status":"CONFIRMED","coverage_ratio":1.0,"source_segments":[{"id":"seg-1","text":"原文片段"}],
             "scenes":[
              {"id":"sc-1","location":"老宅","time_label":"夜","weather":"小雨","purpose":"建立场景","emotional_arc":"平静",
               "version":2,"scene_asset_id":"sa-live","scene_asset_variant_id":"v-live","outfit_assignments":{},
               "source_segments":[],"beats":[]}]}
            """;

        private const string SceneAssets = """
            [
             {"id":"sa-live","name":"活跃老宅","status":"UPLOADED","version":2,"interior":true,"deleted_at":null,
              "variants":[
               {"id":"v-live","name":"晴日","version":1,"is_canonical":true,"deleted_at":null},
               {"id":"v-gone","name":"雨夜","version":1,"deleted_at":"2026-09-01T00:00:00Z"}]},
             {"id":"sa-dead","name":"归档老宅","status":"UPLOADED","version":2,"interior":false,
              "deleted_at":"2026-09-01T00:00:00Z","variants":[]}
            ]
            """;
    }
}
