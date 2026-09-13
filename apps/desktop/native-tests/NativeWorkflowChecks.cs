using System.Net;
using System.Net.Http;
using System.Reflection;
using System.Text.Json;
using System.Windows.Controls;
using MangaFlow.Native;
using MangaFlow.Native.Services;
using MangaFlow.Native.Views;

// NUI-4 regression: navigating away with an ARMED autosave debounce must flush
// the draft. ConfirmLeaveAsync used to check Timer.Enabled AFTER Stop() —
// always false — so the last ≤800ms of edits were silently discarded the
// moment Deactivate() cancelled the view lifetime token.
internal static class NativeWorkflowChecks
{
    public static async Task Run()
    {
        await LeavingFlushesArmedAutosave();
        Console.WriteLine("PASS: workflow navigation flushes an armed autosave debounce exactly once");
    }

    private static async Task LeavingFlushesArmedAutosave()
    {
        var patches = 0;
        var view = new WorkflowView();
        using var api = new ApiClient("http://127.0.0.1:12345", new Handler(request =>
        {
            var path = request.RequestUri!.AbsolutePath;
            if (request.Method == HttpMethod.Patch && path.EndsWith("/workflows/wf-1"))
            {
                patches++;
                return Task.FromResult(Response("""{"id":"wf-1","version":4,"draft_version":2,"draft_graph":{"nodes":[],"edges":[]}}"""));
            }
            if (path.EndsWith("/workflow-node-types")) return Task.FromResult(Response("[]"));
            if (path.EndsWith("/projects/p1/workflows"))
                return Task.FromResult(Response("""[{"id":"wf-1","name":"流程"}]"""));
            if (path.EndsWith("/projects/p1/chapters")) return Task.FromResult(Response("[]"));
            if (path.EndsWith("/workflows/wf-1"))
                return Task.FromResult(Response("""{"id":"wf-1","version":3,"draft_version":1,"draft_graph":{"nodes":[],"edges":[]}}"""));
            return Task.FromResult(Response("{}"));
        }));
        var context = new WorkspaceContext
        {
            Api = api, State = new WorkspaceState(), Window = null!,
            Project = new ProjectItem("p1", "工作流测试", "", 0, 0),
            NavigateSection = (_, _) => Task.CompletedTask, OpenDashboard = () => Task.CompletedTask,
        };

        view.Activate(context);
        var viewType = typeof(WorkflowView);
        const BindingFlags flags = BindingFlags.Instance | BindingFlags.NonPublic;
        var workflowIdField = viewType.GetField("workflowId", flags);
        Require(workflowIdField != null, "反射入口缺失：WorkflowView.workflowId");
        await Until(() => (string?)workflowIdField!.GetValue(view) == "wf-1");

        // Arm the 800ms debounce (what a node rename / config edit does) and
        // navigate away immediately — before the timer fires.
        viewType.GetMethod("ScheduleSave", flags)!.Invoke(view, null);
        var autosave = viewType.GetField("autosave", flags)!.GetValue(view) as System.Timers.Timer;
        Require(autosave is { Enabled: true }, "ScheduleSave 未武装防抖计时器");

        await view.ConfirmLeaveAsync();
        Require(patches == 1, $"导航离开应冲刷已武装的防抖草稿（PATCH 次数 {patches}，期望 1；0 = 恢复死代码行为）");

        // The flushed timer must not fire again afterwards (no double save).
        await Task.Delay(1200);
        Require(patches == 1, $"冲刷后防抖计时器又触发了一次保存（PATCH 次数 {patches}）");
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
