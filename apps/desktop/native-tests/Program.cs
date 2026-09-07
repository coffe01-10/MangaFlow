using System.Net;
using System.Net.Http;
using System.IO;
using System.Text.Json;
using MangaFlow.Native;
using MangaFlow.Native.Services;

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
    catch (InvalidOperationException e) { Check(e.Message.Contains("版本已更新") && e.Message.Contains("刷新"), "409 preserves actionable server detail"); }
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
    new Preferences { Width = 1111, RecentProject = "中文项目", SidebarCollapsed = true }.Save(temp);
    var loaded = Preferences.Load(temp);
    Check(loaded.Width == 1111 && loaded.RecentProject == "中文项目" && loaded.SidebarCollapsed, "window preferences persist");
    Check(Directory.GetFiles(temp).Length == 1, "atomic save leaves no temporary files");
    File.WriteAllText(Path.Combine(temp, "window.json"), "broken");
    Check(Preferences.Load(temp).Width == 1320, "corrupt preferences fall back safely");
}
finally { Directory.Delete(temp, true); }
Console.WriteLine($"Native client checks passed: {count}");

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

if (args.Contains("--render")) NativeVisualChecks.Run(args.Last());

sealed class FakeHandler(Func<HttpRequestMessage, CancellationToken, Task<HttpResponseMessage>> respond) : HttpMessageHandler
{
    protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken cancellationToken) => respond(request, cancellationToken);
}
