using System.Net;
using System.Net.Http;
using System.Reflection;
using System.Text.Json;
using MangaFlow.Native;
using MangaFlow.Native.Services;
using MangaFlow.Native.Views;

// Storyboard activation regression: Activate must load the remembered chapter
// EXACTLY once. The old order (SelectChapter before assigning chapterId) let
// the SelectionChanged side channel load the chapter and then Activate loaded
// it a second time; a user's chapter-switch refusal was overridden the same way.
internal static class NativeStoryboardChecks
{
    public static async Task Run()
    {
        await ActivateLoadsRememberedChapterExactlyOnce();
        Console.WriteLine("PASS: storyboard activation loads the remembered chapter exactly once (no side-channel double fetch)");
    }

    private static async Task ActivateLoadsRememberedChapterExactlyOnce()
    {
        var ch1Loads = 0;
        var ch2Loads = 0;
        using var api = new ApiClient("http://127.0.0.1:12345", new Handler(request =>
        {
            var path = request.RequestUri!.AbsolutePath;
            if (path.EndsWith("/chapters/ch-1/pages")) { ch1Loads++; return Task.FromResult(Response("[]")); }
            if (path.EndsWith("/chapters/ch-2/pages")) { ch2Loads++; return Task.FromResult(Response("[]")); }
            if (path.EndsWith("/projects/p1/chapters"))
                return Task.FromResult(Response(
                    """[{"id":"ch-1","title":"一","ordinal":1,"page_count":0},{"id":"ch-2","title":"二","ordinal":2,"page_count":0}]"""));
            if (path.EndsWith("/projects/p1/characters") || path.EndsWith("/projects/p1/outfits"))
                return Task.FromResult(Response("[]"));
            return Task.FromResult(Response("{}"));
        }));
        var view = new StoryboardView();
        var viewType = typeof(StoryboardView);
        const BindingFlags flags = BindingFlags.Instance | BindingFlags.NonPublic;

        // The view already sits on ch-1 while the remembered chapter is ch-2:
        // activation must switch to ch-2 with exactly one page-list request.
        KeyValueStore.Set("workspace:chapter:p1", "ch-2");
        viewType.GetField("chapterId", flags)!.SetValue(view, "ch-1");
        view.Activate(new WorkspaceContext
        {
            Api = api, Cache = new ApiCache(), State = new WorkspaceState(), Window = null!,
            Project = new ProjectItem("p1", "分镜测试", "", 0, 0),
            NavigateSection = (_, _) => Task.CompletedTask, OpenDashboard = () => Task.CompletedTask,
        });

        await Until(() => ch1Loads + ch2Loads >= 1);
        // The duplicate load of the old ordering starts right after the first
        // completes; any window past that exposes it.
        await Task.Delay(300);
        Require(ch2Loads == 1, $"激活应恰好加载一次 ch-2 页面列表（实际 {ch2Loads} 次；>1 = SelectionChanged 旁路重复加载回归）");
        Require(ch1Loads == 0, $"激活不应加载已被替换的 ch-1（实际 {ch1Loads} 次）");
        KeyValueStore.Remove("workspace:chapter:p1");
    }

    private static async Task Until(Func<bool> condition)
    {
        using var timeout = new System.Threading.CancellationTokenSource(TimeSpan.FromSeconds(3));
        while (!condition()) await Task.Delay(5, timeout.Token);
    }

    private static void Require(bool condition, string message)
    {
        if (!condition) throw new Exception(message);
    }

    private static HttpResponseMessage Response(string json, HttpStatusCode status = HttpStatusCode.OK) =>
        new(status) { Content = new StringContent(json) };

    private sealed class Handler(Func<HttpRequestMessage, Task<HttpResponseMessage>> handler) : HttpMessageHandler
    {
        protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, System.Threading.CancellationToken token) => handler(request);
    }
}
