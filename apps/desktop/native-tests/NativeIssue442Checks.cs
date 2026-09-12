using System.Net;
using System.Net.Http;
using MangaFlow.Native.Services;

// 注册说明（需 lead 注册）：本仓库的发现机制是 Program.cs / NativeInteractionChecks.RunIsolated
// 里的手动调用列表——需在该列表加入 NativeIssue442Checks.Run()。
// #442/#468: ApiClient.Validate 旧版对整个 `path?query` 做 `..` 子串检查，而
// HttpUtility.UrlEncode / Uri.EscapeDataString 都不转义 `.`，导致含 `..` 的合法用户值
// （项目名「序章..终章」的删除确认、场景地点筛选「东京..雨」）编码后仍触发
// 「无效的 API 路径」，请求永远发不出去。修复后查询值是编码数据不是路径段：
// `..` 只对 `?` 之前的路径部分检查；绝对路径与 `://` 仍检查整个字符串。
internal static class NativeIssue442Checks
{
    public static async Task Run()
    {
        string? deleteUri = null, sceneQuery = null, rawQuery = null;
        using var client = new ApiClient("http://127.0.0.1:12345", new Handler(request =>
        {
            var uri = request.RequestUri!;
            if (request.Method == HttpMethod.Delete) deleteUri = uri.AbsoluteUri;
            if (uri.AbsolutePath.EndsWith("/scene-assets")) sceneQuery = uri.Query;
            if (uri.AbsolutePath.EndsWith("/raw")) rawQuery = uri.Query;
            return Task.FromResult(new HttpResponseMessage(HttpStatusCode.OK) { Content = new StringContent("{}") });
        }));
        // #468 复现 A：删除项目的 confirm_name 含 `..` 必须能发出（中文已百分号编码）。
        await client.SendOptionalAsync($"projects/p1?confirm_name={Uri.EscapeDataString("序章..终章")}", HttpMethod.Delete);
        Require(deleteUri != null && deleteUri.Contains("/api/v1/projects/p1?")
            && deleteUri.Contains("confirm_name=%E5%BA%8F%E7%AB%A0..%E7%BB%88%E7%AB%A0"),
            "delete-project with '..' in confirm_name must be sent with the name percent-encoded");
        // #442 复现 B：QueryBuilder 的场景地点筛选「东京..雨」必须到达处理器。
        await client.SendAsync(QueryBuilder.Build("projects/p1/scene-assets", ("limit", 200), ("place", "东京..雨")));
        Require(sceneQuery != null && sceneQuery.Contains("place=%E4%B8%9C%E4%BA%AC..%E9%9B%A8"),
            "scene place filter containing '..' must reach the handler encoded");
        // 未编码的裸 `..` 查询值同样是数据，不是路径段。
        await client.SendAsync("raw?x=..");
        Require(rawQuery == "?x=..", "a raw '..' query value must survive validation");
        // 路径部分的遍历必须继续被拒绝——带不带查询串都一样。
        foreach (var invalid in new[] { "../health", "..?x=1", "../x?y=1", "a/../b" })
        {
            try { await client.SendAsync(invalid); throw new Exception("traversal path accepted: " + invalid); }
            catch (ArgumentException) { }
        }
        try { client.PublicUrl("../../escape/content"); throw new Exception("media traversal accepted"); }
        catch (ArgumentException) { }
        Console.WriteLine("PASS: encoded query values containing '..' are sent; '..' in the path portion is still rejected");
    }

    private static void Require(bool value, string message) { if (!value) throw new Exception(message); }

    private sealed class Handler(Func<HttpRequestMessage, Task<HttpResponseMessage>> respond) : HttpMessageHandler
    {
        protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken cancellationToken) => respond(request);
    }
}
