using System.Net;
using System.Net.Http;
using System.IO;
using System.Text.Json;
using System.Windows;
using MangaFlow.Native;
using MangaFlow.Native.Services;

// Client-side regression checks: API safety, models, preferences, navigation, director rules.
if (args.Contains("--sidebar"))
{
    NativeSidebarChecks.Run(args.FirstOrDefault(a => !a.StartsWith("--")) ?? Path.Combine(Path.GetTempPath(), "mangaflow-sidebar-checks"));
    return 0;
}
// NUI-8 defect #4: keep-alive / pooled-connection lifecycle. Offline runs the
// in-process uvicorn-emulating server (CI gate); live mode replays the acceptance
// load against a running sidecar and writes the full exception chain to disk.
if (args.Contains("--keepalive"))
{
    NativeKeepAliveChecks.RunOffline(args.FirstOrDefault(a => !a.StartsWith("--")) ??
        Path.Combine(Path.GetTempPath(), "mangaflow-keepalive-checks"));
    return 0;
}
if (args.Contains("--keepalive-live"))
{
    var origin = (args.FirstOrDefault(a => a.StartsWith("--origin=")) ?? "--origin=http://127.0.0.1:8000")["--origin=".Length..];
    NativeKeepAliveChecks.RunLive(origin, args.FirstOrDefault(a => !a.StartsWith("--")) ??
        Path.Combine(Path.GetTempPath(), "mangaflow-keepalive-live"));
    return 0;
}
if (args.Contains("--usage-page"))
{
    NativeUsagePageChecks.Run(args.FirstOrDefault(a => !a.StartsWith("--")) ?? Path.Combine(Path.GetTempPath(), "mangaflow-usage-page-checks"));
    return 0;
}
if (args.Contains("--system-settings-page"))
{
    NativeSystemSettingsPageChecks.Run(args.FirstOrDefault(a => !a.StartsWith("--")) ?? Path.Combine(Path.GetTempPath(), "mangaflow-system-settings-page-checks"));
    return 0;
}
if (args.Contains("--project-settings-page"))
{
    NativeProjectSettingsPageChecks.Run(args.FirstOrDefault(a => !a.StartsWith("--")) ?? Path.Combine(Path.GetTempPath(), "mangaflow-project-settings-page-checks"));
    return 0;
}
if (args.Contains("--workflow-page"))
{
    NativeWorkflowPageChecks.Run(args.FirstOrDefault(a => !a.StartsWith("--")) ?? Path.Combine(Path.GetTempPath(), "mangaflow-workflow-page-checks"));
    return 0;
}
if (args.Contains("--jobs-page"))
{
    NativeJobsPageChecks.Run(args.FirstOrDefault(a => !a.StartsWith("--")) ?? Path.Combine(Path.GetTempPath(), "mangaflow-jobs-page-checks"));
    return 0;
}
if (args.Contains("--library-page"))
{
    NativeLibraryPageChecks.Run(args.FirstOrDefault(a => !a.StartsWith("--")) ?? Path.Combine(Path.GetTempPath(), "mangaflow-library-page-checks"));
    return 0;
}
if (args.Contains("--generate-page"))
{
    NativeGeneratePageChecks.Run(args.FirstOrDefault(a => !a.StartsWith("--")) ?? Path.Combine(Path.GetTempPath(), "mangaflow-generate-page-checks"));
    return 0;
}
if (args.Contains("--storyboard-page"))
{
    NativeStoryboardPageChecks.Run(args.FirstOrDefault(a => !a.StartsWith("--")) ?? Path.Combine(Path.GetTempPath(), "mangaflow-storyboard-page-checks"));
    return 0;
}
if (args.Contains("--script"))
{
    NativeScriptPageChecks.Run(args.FirstOrDefault(a => !a.StartsWith("--")) ?? Path.Combine(Path.GetTempPath(), "mangaflow-script-checks"));
    return 0;
}
if (args.Contains("--references"))
{
    NativeReferenceChecks.Run(args.FirstOrDefault(a => !a.StartsWith("--")) ?? Path.Combine(Path.GetTempPath(), "mangaflow-reference-checks"));
    return 0;
}
if (args.Contains("--style"))
{
    NativeStyleChecks.Run(args.FirstOrDefault(a => !a.StartsWith("--")) ?? Path.Combine(Path.GetTempPath(), "mangaflow-style-checks"));
    return 0;
}
if (args.Contains("--scenes"))
{
    NativeSceneChecks.Run(args.FirstOrDefault(a => !a.StartsWith("--")) ?? Path.Combine(Path.GetTempPath(), "mangaflow-scene-checks"));
    return 0;
}
if (args.Contains("--storyedit"))
{
    NativeStoryboardEditChecks.Run(args.FirstOrDefault(a => !a.StartsWith("--")) ?? Path.Combine(Path.GetTempPath(), "mangaflow-storyedit-checks"));
    return 0;
}
if (args.Contains("--issue441"))
{
    NativeIssue441Checks.Run();
    return 0;
}
if (args.Contains("--p2cluster"))
{
    NativeIssueP2ClusterChecks.Run();
    return 0;
}
if (args.Contains("--issue426"))
{
    NativeIssue426Checks.Run(args.FirstOrDefault(a => !a.StartsWith("--")) ?? Path.Combine(Path.GetTempPath(), "mangaflow-issue426"));
    return 0;
}
if (args.Contains("--interaction"))
{
    // --render 的 parity 前奏在 master 上有一个独立的既有失败（generate/940
    // PageHeading 重叠）时，仍可单独驱动 NativeInteractionChecks 全链——该链
    // 覆盖 #428/#429/#469 等所有视图行为套件，是视图行为改动的匹配验证面。
    // 与 --render 同款专用 STA 线程 + Application 泵（MainWindow 需要 STA）。
    Exception? interactionFailure = null;
    var interactionThread = new Thread(() =>
    {
        try
        {
            var app = new Application();
            app.Resources.MergedDictionaries.Add(new ResourceDictionary { Source = new Uri("/MangaFlow.Native;component/Theme.xaml", UriKind.Relative) });
            NativeInteractionChecks.Run(args.FirstOrDefault(a => !a.StartsWith("--")) ?? Path.Combine(Path.GetTempPath(), "mangaflow-interaction-checks"));
            app.Shutdown();
        }
        catch (Exception e) { interactionFailure = e; }
    });
    interactionThread.SetApartmentState(ApartmentState.STA);
    interactionThread.Start();
    interactionThread.Join();
    if (interactionFailure != null) throw new Exception("Native interaction checks failed", interactionFailure);
    return 0;
}
var count = 0;
void Check(bool condition, string name)
{
    if (!condition) throw new Exception("FAIL: " + name);
    Console.WriteLine("PASS: " + name); count++;
}
foreach (var invalid in new[] { "https://127.0.0.1:80", "http://localhost:80", "http://example.com", "http://127.0.0.1/a", "http://u:p@127.0.0.1", "http://127.0.0.1?x=1", "http://127.0.0.1#x" })
{
    try { using var invalidClient = new ApiClient(invalid); throw new Exception("Accepted untrusted origin"); }
    catch (ArgumentException) { count++; }
}
Console.WriteLine("PASS: untrusted origins rejected");
using (var media = new ApiClient("http://127.0.0.1:12345"))
{
    Check(media.PublicUrl("/api/v1/assets/fixture/content") == "http://127.0.0.1:12345/api/v1/assets/fixture/thumbnail/640", "server media paths have exactly one API prefix");
    Check(media.PublicUrl("assets/fixture/content") == media.PublicUrl("/api/v1/assets/fixture/content"), "local and server media paths use identical thumbnails");
    Check(media.OriginUrl("/api/v1/assets/fixture/content") == "http://127.0.0.1:12345/api/v1/assets/fixture/content", "lightbox preserves original resolution");
}
var handler = new FakeHandler((request, _) =>
{
    Check(request.RequestUri!.AbsoluteUri == "http://127.0.0.1:12345/api/v1/projects", "exact versioned API path");
    return Task.FromResult(new HttpResponseMessage(HttpStatusCode.OK) { Content = new StringContent("[{\"name\":\"雨夜来信\"}]") });
});
using (var client = new ApiClient("http://127.0.0.1:12345", handler))
{
    var result = await client.SendAsync("projects");
    Check(result[0].Text("name") == "雨夜来信", "UTF-8 JSON survives response disposal");
    foreach (var invalid in new[] { "/api/v1/projects", "https://example.com", "../health" })
    {
        try { await client.SendAsync(invalid); throw new Exception("unsafe path accepted"); }
        catch (ArgumentException) { count++; }
    }
}
using (var client = new ApiClient("http://127.0.0.1:12345", new FakeHandler((_, _) =>
    Task.FromResult(new HttpResponseMessage(HttpStatusCode.Conflict) { Content = new StringContent("{\"detail\":\"版本已更新\"}") }))))
{
    try { await client.SendAsync("settings/runtime", HttpMethod.Patch, new { version = 1 }); throw new Exception("409 accepted"); }
    catch (ApiException e) { Check(e is InvalidOperationException && e.Status == 409 && e.Message.Contains("版本已更新") && e.Message.Contains("刷新"), "409 preserves status and actionable server detail (A15)"); }
}
// A15：404 与 5xx 保留可判别的状态码——调用点（场景引用检查、冲突恢复）需要
// 区分“资源不存在”与“读取失败”，不再解析 Message 文本。
using (var client = new ApiClient("http://127.0.0.1:12345", new FakeHandler((_, _) =>
    Task.FromResult(new HttpResponseMessage(HttpStatusCode.NotFound) { Content = new StringContent("{\"detail\":\"章节不存在\"}") }))))
{
    try { await client.SendAsync("chapters/gone"); throw new Exception("404 accepted"); }
    catch (ApiException e) { Check(e.Status == 404 && e.Message.Contains("章节不存在"), "404 carries its status for missing-resource branches (A15)"); }
}
using (var client = new ApiClient("http://127.0.0.1:12345", new FakeHandler((_, _) =>
    Task.FromResult(new HttpResponseMessage(HttpStatusCode.ServiceUnavailable) { Content = new StringContent("plain text") }))))
{
    try { await client.SendAsync("projects"); throw new Exception("503 accepted"); }
    catch (ApiException e) { Check(e.Status == 503 && e.Detail.ValueKind == JsonValueKind.Undefined, "non-JSON errors keep status with no structured detail (A15)"); }
}
var mutationCalls = 0;
using (var client = new ApiClient("http://127.0.0.1:12345", new FakeHandler((_, _) =>
{
    mutationCalls++; throw new HttpRequestException("connection interrupted");
})))
{
    try { await client.SendAsync("projects", HttpMethod.Post, new { name = "test" }); }
    catch (HttpRequestException) { }
    Check(mutationCalls == 1, "interrupted mutation never automatically retries");
}
using (var client = new ApiClient("http://127.0.0.1:12345", new FakeHandler(async (_, token) =>
{
    await Task.Delay(5000, token); return new HttpResponseMessage(HttpStatusCode.OK);
})))
{
    using var cancellation = new CancellationTokenSource(20);
    try { await client.SendAsync("projects", cancellation: cancellation.Token); throw new Exception("cancel ignored"); }
    catch (OperationCanceledException) { Check(true, "navigation cancellation reaches HTTP handler"); }
}
using (var json = JsonDocument.Parse("{\"id\":\"1\",\"status\":\"COMPLETED\",\"progress\":120}"))
{
    var job = JobItem.From(json.RootElement);
    Check(job.Progress == 100 && !job.CanCancel && !job.CanRetry, "terminal actions disabled and progress clamped");
}
using (var json = JsonDocument.Parse("{\"status\":\"FAILED\",\"attempt_count\":3,\"max_attempts\":3}"))
    Check(!JobItem.From(json.RootElement).CanRetry, "exhausted task cannot retry");
var temp = Path.Combine(Path.GetTempPath(), "mangaflow-native-test-" + Guid.NewGuid().ToString("N"));
Directory.CreateDirectory(temp);
try
{
    new Preferences { Width = 1111, RecentProject = "中文项目", SidebarCollapsed = true, DockHidden = true }.Save(temp);
    var loaded = Preferences.Load(temp);
    Check(loaded.Width == 1111 && loaded.RecentProject == "中文项目" && loaded.SidebarCollapsed && loaded.DockHidden, "window preferences persist");
    Check(Directory.GetFiles(temp).Length == 1, "atomic save leaves no temporary files");
    File.WriteAllText(Path.Combine(temp, "window.json"), "broken");
    Check(Preferences.Load(temp).Width == 1320, "corrupt preferences fall back safely");
}
finally { Directory.Delete(temp, true); }

var workspace = new WorkspaceState();
for (var i = 0; i < 29; i++) workspace.Projects.Add(new ProjectItem($"p{i}", $"故事{i}", "", 0, 0));
workspace.ChangeDashboardPage(0);
Check(workspace.DashboardProjects.Count == 12 && !workspace.HasPreviousPage && workspace.HasNextPage, "dashboard bounds visual count");
workspace.ChangeDashboardPage(2);
Check(workspace.DashboardProjects.Count == 5 && workspace.DashboardProjects[0].Id == "p24" && !workspace.HasNextPage, "dashboard paging preserves remaining projects");
workspace.Projects.Clear();
workspace.ChangeDashboardPage(0);
Check(workspace.DashboardProjects.Count == 0 && workspace.DashboardPageLabel == "1 / 1", "archive emptying last page returns to valid page");
Check(new ProjectItem("p", "雨🌧️夜", "", 0, 0).CoverTitle == "雨\n🌧️\n夜", "cover lettering preserves Unicode graphemes");

var navigation = new ProjectNavigation();
Check(ProjectPages.All.Select(p => p.WebSection).SequenceEqual(new[] { "source", "assets", "script", "storyboard", "generate", "library", "jobs", "workflow", "settings" }), "project navigation covers Web routes in order");
var changes = 0;
navigation.PropertyChanged += (_, _) => changes++;
Check(!navigation.Select(navigation.Current) && changes == 0, "reselecting a page does not rebuild it");
Check(navigation.Select(ProjectPages.Get(ProjectPageId.Assets)) && changes == 1, "selecting a new page notifies once");
Check(!navigation.Select(ProjectPages.Get(ProjectPageId.Source) with { WebSection = "unknown" }), "unknown page definition is rejected");

NativeNavigationChecks.RunDirectorRules();
using (var maskModel = JsonDocument.Parse("""{"model_type":"IMAGE","enabled":true,"accepts_explicit_mask":true,"operations":["image_edit"]}"""))
    Check(LocalEditRules.SupportsMask(maskModel.RootElement), "local edit accepts explicitly mask-capable image models");
using (var wholeModel = JsonDocument.Parse("""{"model_type":"IMAGE","enabled":true,"whole_image_reference_only":true,"operations":["image_edit"]}"""))
    Check(!LocalEditRules.SupportsMask(wholeModel.RootElement), "local edit never falls back to whole-image reference editing");
var region = LocalEditRules.Rectangle(new Point(-20, 100), new Point(200, 300), new Size(1024, 1024));
Check(region[0].X == 0 && LocalEditRules.Area(region) == 40000, "rectangle masks clamp to real image pixels");
var strokeRegion = LocalEditRules.Brush(Enumerable.Range(0, 200).Select(i => new Point(i + 20, 100)).ToArray(), 40, new Size(1024, 1024));
Check(strokeRegion.Length <= 64 && LocalEditRules.Area(strokeRegion) > 7000, "brush masks retain area and obey backend point limit");
using (var editPage = JsonDocument.Parse("""{"id":"page-id","version":7}"""))
{
    var envelope = JsonSerializer.SerializeToElement(LocalEditRules.Envelope("project-id", editPage.RootElement, "修正雨伞", "mask-model", "1K", [region], Guid.NewGuid().ToString(), Guid.NewGuid().ToString()));
    Check(envelope.Text("operation") == "regenerate_region" && envelope.Element("expected_version").Number("value") == 7, "local edits preserve the versioned director command contract");
    Check(envelope.Element("payload").Array("mask")[0].Array("points").Count == 4, "native mask envelope uses polygon points");
    try { LocalEditRules.Envelope("p", editPage.RootElement, "修正", "m", "1K", [], "c", "g"); throw new Exception("empty mask accepted"); }
    catch (ArgumentException) { Check(true, "empty masks cannot be proposed"); }
}
await NativeBehaviorChecks.Run(Check);
var output = args.FirstOrDefault(a => !a.StartsWith("--"))
    ?? Path.Combine(Path.GetTempPath(), "mangaflow-native-checks");
// 连接生命周期回归（NUI-8 缺陷 #4 排查产物）：默认全套即执行，无需专用 flag。
NativeKeepAliveChecks.RunOffline(output);
var uiChecked = false;
if (args.Contains("--render"))
{
    // UI checks must run on a dedicated STA thread (see NativeVisualChecks).
    NativeVisualChecks.Run(output);
    uiChecked = true;
}
Console.WriteLine($"Native client checks passed: {count}" + (uiChecked ? "; WPF navigation and visual checks passed" : ""));
return 0;

sealed class FakeHandler(Func<HttpRequestMessage, CancellationToken, Task<HttpResponseMessage>> respond) : HttpMessageHandler
{
    protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken cancellationToken) => respond(request, cancellationToken);
}
