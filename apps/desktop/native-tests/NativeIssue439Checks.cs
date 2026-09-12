using System.IO;
using System.Net;
using System.Net.Http;
using System.Reflection;
using System.Text.Json;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Threading;
using MangaFlow.Native;
using MangaFlow.Native.Services;
using MangaFlow.Native.Views;

// 注册说明（需 lead 注册）：本仓库的发现机制是 NativeInteractionChecks.RunIsolated 里的
// 手动调用列表——需在该列表加入 NativeIssue439Checks.Run(output)。Run 内部自带 STA 调度
//（无 Application 时自建 STA 线程 + Application/Theme，参照 NativeStoryboardEditChecks），
// 也可从 Program.cs 独立调用。
// #439: UploadAsync 缺少 SendOptionalAsync 的 TaskCanceledException→TimeoutException 翻译，
// 30 秒 HTTP 超时被 SourceView 的 catch (OperationCanceledException) {} 静默吞掉（导入文件
// “消失”），OutfitWorkspace 则把英文 “A task was canceled.” 原样弹给用户。
// #471-4: 设为规范参考先 DELETE 旧绑定再 POST 新绑定，POST 失败时场景丢失该参考且恢复需
// 重新上传文件；修复后失败时会尽力把原参考按原样（非规范）回绑，消息如实说明最终状态。
internal static class NativeIssue439Checks
{
    public static void Run(string output)
    {
        Directory.CreateDirectory(output);
        if (Application.Current is null) RunOnDedicatedStaThread(output);
        else RunFrame(output);
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
        if (failure != null) throw new Exception("Issue 439/471 checks failed", failure);
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
        timeout.Tick += (_, _) => { failure = new TimeoutException("Issue 439/471 checks timed out"); frame.Continue = false; };
        timeout.Start();
        SynchronizationContext.SetSynchronizationContext(new DispatcherSynchronizationContext());
        Dispatcher.CurrentDispatcher.BeginInvoke(new Action(async () =>
        {
            try { await CheckAsync(output); }
            catch (Exception error) { failure = error; }
            finally { frame.Continue = false; }
        }));
        Dispatcher.PushFrame(frame);
        timeout.Stop();
        if (failure != null) throw new Exception("Issue 439/471 checks failed", failure);
    }

    private static async Task CheckAsync(string output)
    {
        await UploadTimeoutTranslatesToTimeoutException();
        await SourceImportTimeoutSurfacesNotice(output);
        await OutfitUploadTimeoutUsesChineseNotice(output);
        await FailedCanonicalSwapRestoresReference(output);
    }

    // HttpClient 的 30 秒超时表现为「调用方 token 未取消的 TaskCanceledException」；
    // 直接复现该异常形状，而不是真的等 30 秒。
    private static async Task UploadTimeoutTranslatesToTimeoutException()
    {
        using var client = new ApiClient("http://127.0.0.1:12345", new TimeoutHandler());
        try
        {
            await client.UploadAsync("projects/p1/sources/upload", new Dictionary<string, string> { ["title"] = "第一章" },
                ("file", "第一章.txt", "text/plain", "完整章节内容"u8.ToArray()), CancellationToken.None);
            throw new Exception("upload timeout was swallowed");
        }
        catch (TimeoutException) { }
        // 调用方主动取消（页面切换）必须仍然是 OperationCanceledException，不能被翻译成超时。
        using var canceled = new CancellationTokenSource(); canceled.Cancel();
        try
        {
            await client.UploadAsync("projects/p1/sources/upload", new Dictionary<string, string> { ["title"] = "第一章" },
                ("file", "第一章.txt", "text/plain", "完整章节内容"u8.ToArray()), canceled.Token);
            throw new Exception("genuine cancellation accepted");
        }
        catch (OperationCanceledException) { }
        Console.WriteLine("PASS: upload timeouts surface as TimeoutException while genuine cancellations stay cancellations");
    }

    private static async Task SourceImportTimeoutSurfacesNotice(string output)
    {
        using var api = new ApiClient("http://127.0.0.1:12345", new SourceFixture());
        var view = new SourceView();
        view.Activate(Context(api, "source-439"));
        try
        {
            var file = Path.Combine(output, "issue439-import-timeout.md");
            await File.WriteAllTextAsync(file, "一份相当长的完整章节原文");
            try { await (Task)typeof(SourceView).GetMethod("ImportFileAsync", BindingFlags.Instance | BindingFlags.NonPublic)!.Invoke(view, [file])!; }
            finally { File.Delete(file); }
            var error = Field<TextBlock>(view, "error");
            var notice = Field<TextBlock>(view, "notice");
            Require(error.Text.Contains("请求超时") && error.Text.Contains("请先刷新确认"), "import timeout must surface a visible notice instead of silently dropping the file");
            Require(!notice.Text.Contains("已导入"), "timed-out import must not claim success");
            Require(!BoolField(view, "importing") && Field<Button>(view, "fileButton").IsEnabled, "timed-out import must reset the busy state");
        }
        finally { view.Deactivate(); }
        Console.WriteLine("PASS: source file import timeout shows the localized notice and re-enables the form");
    }

    private static async Task OutfitUploadTimeoutUsesChineseNotice(string output)
    {
        var fixture = new OutfitFixture(); using var api = new ApiClient("http://127.0.0.1:12345", fixture);
        var view = new AssetsView();
        view.Activate(Context(api, "outfit-439"));
        try
        {
            await view.RefreshAsync(); await view.SwitchAsync(AssetsView.Outfits);
            Layout(view, 1100, 1000);
            var pane = Descendants(view).OfType<OutfitWorkspace>().Single();
            Field<ComboBox>(pane, "characterSelector").SelectedIndex = 1;
            var image = Path.Combine(output, "issue439-upload.png");
            try
            {
                File.WriteAllBytes(image, Convert.FromBase64String("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aS9sAAAAASUVORK5CYII="));
                await pane.UploadAsync(new[] { image });
                var banner = Field<TextBlock>(view, "notice");
                Require(banner.Text.Contains("请求超时") && banner.Text.Contains("请先刷新确认"), "outfit upload timeout must show the localized Chinese notice, not the raw English cancellation text");
            }
            finally { File.Delete(image); }
        }
        finally { view.Deactivate(); }
        Console.WriteLine("PASS: outfit reference upload timeout reports the localized Chinese notice");
    }

    private static async Task FailedCanonicalSwapRestoresReference(string output)
    {
        var fixture = new SceneFixture(); using var api = new ApiClient("http://127.0.0.1:12345", fixture);
        var view = new AssetsView();
        view.Activate(Context(api, "scene-471"));
        try
        {
            await view.RefreshAsync(); await view.SwitchAsync(AssetsView.Scenes); await Task.Delay(25);
            Layout(view, 1100, 1000);
            var pane = Descendants(view).OfType<SceneWorkspace>().Single();
            await pane.InitialLoad;
            var scene = JsonSerializer.Deserialize<JsonElement>(SceneFixture.Row);
            var alternative = scene.Array("references").First(r => !r.Flag("is_canonical"));
            await (Task)typeof(SceneWorkspace).GetMethod("CanonicalAsync", BindingFlags.Instance | BindingFlags.NonPublic)!.Invoke(pane, [scene, alternative])!;
            Require(fixture.DeletedReferences.Count == 1 && fixture.DeletedReferences[0] == "a1", "canonical swap must delete exactly the promoted reference");
            Require(fixture.PromotePosts == 1, "canonical swap must attempt the canonical POST once");
            Require(fixture.RestorePosts == 1, "failed canonical swap must best-effort rebind the unbound reference instead of leaving recovery to a re-upload");
            var feedback = Field<StackPanel>(pane, "feedback");
            var message = string.Join("\n", Descendants(feedback).OfType<TextBlock>().Select(t => t.Text));
            Require(message.Contains("设为规范参考未完成") && message.Contains("已恢复"), "failure message must state that the swap failed and the original binding was restored");
        }
        finally { view.Deactivate(); }
        Console.WriteLine("PASS: failed canonical-reference swap restores the unbound reference and reports the real state");
    }

    private static WorkspaceContext Context(ApiClient api, string projectId) => new()
    {
        Api = api, Cache = new ApiCache(), State = new WorkspaceState(), Window = null!,
        Project = new ProjectItem(projectId, "超时与规范参考测试", "", 0, 0),
        NavigateSection = (_, _) => Task.CompletedTask, OpenDashboard = () => Task.CompletedTask,
    };

    private static IEnumerable<DependencyObject> Descendants(DependencyObject root) => NativeParityChecks.Descendants(root);
    private static T Field<T>(object value, string name) => (T)value.GetType().GetField(name, BindingFlags.Instance | BindingFlags.NonPublic)!.GetValue(value)!;
    private static bool BoolField(object value, string name) => (bool)value.GetType().GetField(name, BindingFlags.Instance | BindingFlags.NonPublic)!.GetValue(value)!;
    private static void Require(bool value, string message) { if (!value) throw new Exception(message); }
    private static void Layout(FrameworkElement element, int width, int height) { element.Measure(new Size(width, height)); element.Arrange(new Rect(0, 0, width, height)); element.UpdateLayout(); }

    // #468 说明：同根因（Validate 拒绝含 `..` 的查询值）的另一个受害路径是
    // ProjectSettingsView 的删除项目；它的回归在 NativeIssue442Checks 里。
    private sealed class TimeoutHandler : HttpMessageHandler
    {
        protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken cancellationToken) =>
            throw new TaskCanceledException("A task was canceled.");
    }

    private sealed class SourceFixture : HttpMessageHandler
    {
        protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken cancellationToken)
        {
            var path = request.RequestUri!.AbsolutePath;
            if (path.EndsWith("/sources/upload")) throw new TaskCanceledException("A task was canceled.");
            return Task.FromResult(Json(path.EndsWith("/chapters") ? "[]" : "{}"));
        }
        private static HttpResponseMessage Json(string text) => new(HttpStatusCode.OK) { Content = new StringContent(text) };
    }

    private sealed class OutfitFixture : HttpMessageHandler
    {
        protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken cancellationToken)
        {
            var path = request.RequestUri!.AbsolutePath;
            if (path.EndsWith("/assets/upload")) throw new TaskCanceledException("A task was canceled.");
            if (path.EndsWith("/models")) return Task.FromResult(Json("""[{"model_type":"IMAGE","enabled":true,"operations":["image_edit"],"logical_alias":"model-a","display_name":"Codex CLI ImageGen","provider":"Codex CLI","model_id":"codex-imagegen"}]"""));
            if (path.EndsWith("/characters")) return Task.FromResult(Json("""[{"id":"c1","primary_name":"樱","aliases":[]},{"id":"c2","primary_name":"我","aliases":[]}]"""));
            return Task.FromResult(Json("[]"));
        }
        private static HttpResponseMessage Json(string text) => new(HttpStatusCode.OK) { Content = new StringContent(text) };
    }

    private sealed class SceneFixture : HttpMessageHandler
    {
        internal const string Row = """{"id":"s1","name":"京都老宅佛堂","description":"老木结构，佛龛位于房间尽头","location_hint":"京都，爸爸的灵牌前","status":"NEEDS_CONFIRMATION","version":7,"structured":{"place":"京都老宅","interior":true,"fixed_props":["佛龛","木柱"]},"references":[{"asset_id":"a0","role":"main","is_canonical":true},{"asset_id":"a1","role":"alt","is_canonical":false}],"variants":[]}""";
        internal readonly List<string> DeletedReferences = [];
        internal int PromotePosts, RestorePosts;
        protected override async Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken cancellationToken)
        {
            var path = request.RequestUri!.AbsolutePath;
            if (path.Contains("/references") && request.Method == HttpMethod.Post)
            {
                var body = JsonSerializer.Deserialize<JsonElement>(await request.Content!.ReadAsStringAsync(cancellationToken));
                if (body.Flag("is_canonical")) { PromotePosts++; return new HttpResponseMessage(HttpStatusCode.InternalServerError) { Content = new StringContent("{\"detail\":\"服务暂不可用\"}") }; }
                RestorePosts++;
                return Json(Row);
            }
            if (path.Contains("/references") && request.Method == HttpMethod.Delete)
            {
                DeletedReferences.Add(path.Split('/')[^1]);
                return Json("{}");
            }
            if (path.EndsWith("/scene-assets")) return Json("[" + Row + "]");
            if (path.EndsWith("/chapters")) return Json("[]");
            return Json("[]");
        }
        private static HttpResponseMessage Json(string text) => new(HttpStatusCode.OK) { Content = new StringContent(text) };
    }
}
