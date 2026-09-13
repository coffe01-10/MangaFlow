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

// #427 回归（P2）：快速 A→B→C 切换工作流选择器会把一个工作流的图写进另一个。
// 三条确定性检查（TaskCompletionSource 门控假 HTTP 响应构造竞态，零真实网络）：
// ① 两次乱序完成的 LoadWorkflowAsync——迟到响应不得把画布/版本号翻回旧工作流；
// ② A→B→C 切换且离场 flush 挂起在途——任何 PATCH 的 (graph, id) 必须配对，
//    离场工作流的待存编辑必须照常落盘，终态画布与选择一致；
// ③ Activate 恰好发起一次工作流载入（旧代码：选择器处理器 + Activate 各载入一次）。
internal static class NativeIssue427Checks
{
    private const BindingFlags All = BindingFlags.Instance | BindingFlags.NonPublic | BindingFlags.Public;

    public static void Run()
    {
        // Application 是 AppDomain 级单例：链内（RunIsolated）已有 Application 时
        // 必须复用调用方线程，只有独立运行才自建 STA 线程 + Application
        // （NativeStoryboardEditChecks.Run 的同款双模式，否则链内 new Application()
        // 抛「不能在同一 AppDomain 中创建多个实例」）。
        if (Application.Current is null) RunOnDedicatedStaThread();
        else RunFrame();
    }

    // 独立运行（进程里还没有 Application）：自建 STA 线程 + Application/Theme。
    private static void RunOnDedicatedStaThread()
    {
        Exception? failure = null;
        var thread = new Thread(() =>
        {
            try
            {
                var app = new Application { ShutdownMode = ShutdownMode.OnExplicitShutdown };
                app.Resources.MergedDictionaries.Add(new ResourceDictionary { Source = new Uri("/MangaFlow.Native;component/Theme.xaml", UriKind.Relative) });
                RunFrame();
            }
            catch (Exception error) { failure = error; }
        });
        thread.SetApartmentState(ApartmentState.STA);
        thread.Start();
        thread.Join();
        if (failure != null) throw new Exception("NativeIssue427Checks failed", failure);
    }

    // STA + DispatcherFrame 泵（NativeInteractionChecks.RunIsolated 的模式）：
    // async 续体经由 DispatcherSynchronizationContext 回到泵上执行。
    private static void RunFrame()
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
        timeout.Tick += (_, _) => { failure ??= new TimeoutException("NativeIssue427Checks timed out"); frame.Continue = false; };
        timeout.Start();
        SynchronizationContext.SetSynchronizationContext(new DispatcherSynchronizationContext());
        Dispatcher.CurrentDispatcher.BeginInvoke(new Action(async () =>
        {
            try
            {
                await ReversedCompletionLoadRace();
                Console.WriteLine("PASS: #427 乱序完成的两次工作流载入——画布/workflowId/version 保持一致（画布显示最后请求的工作流）");
                await SwitchWithPendingFlushKeepsGraphOwnership();
                Console.WriteLine("PASS: #427 A→B→C 切换（离场 flush 挂起在途）——没有发出 (graph, id) 配错的 PATCH，离场编辑照常落盘");
                await ActivateLoadsExactlyOnce();
                Console.WriteLine("PASS: #427 Activate 恰好发起一次工作流载入");
            }
            catch (Exception error) { failure = error; }
            finally { frame.Continue = false; }
        }));
        Dispatcher.PushFrame(frame);
        timeout.Stop();
        if (failure != null) throw new Exception("NativeIssue427Checks failed", failure);
    }

    // ── ① 载入竞态：两次 LoadWorkflowAsync，后发起的先返回 ──
    // 失败构造（原始缺陷）：LoadWorkflowAsync 没有请求令牌，A 的迟到响应最后落地，
    // 画布翻回 A 而 workflowId 指向 B——下一次编辑就把 A 的图 PATCH 进 B。
    private static async Task ReversedCompletionLoadRace()
    {
        var gateA = NewGate();
        var gateB = NewGate();
        using var api = new ApiClient("http://127.0.0.1:12345", new Handler(async request =>
        {
            var path = request.RequestUri!.AbsolutePath;
            if (request.Method == HttpMethod.Get && path.EndsWith("/workflows/wf-a")) return await gateA.Task;
            if (request.Method == HttpMethod.Get && path.EndsWith("/workflows/wf-b")) return await gateB.Task;
            if (path.EndsWith("/runs")) return Response("[]");
            if (request.Method == HttpMethod.Patch) return Response(EmptyDefinition("wf-x"));
            throw new Exception("Unexpected load-race request: " + path);
        }));
        var view = new WorkflowView();
        try
        {
            WireContext(view, api);
            var workflowId = Field(view, "workflowId");
            var version = Field(view, "version");

            // 载入 A（响应挂起）→ 选择切到 B（第二次载入，响应挂起）→ 先放行 B 再放行 A。
            workflowId.SetValue(view, "wf-a");
            var loadA = InvokeLoad(view);
            workflowId.SetValue(view, "wf-b");
            var loadB = InvokeLoad(view);

            gateB.TrySetResult(Response(Definition("wf-b", 6, "b-node", "乙")));
            await loadB;   // B（最后请求的）先落地
            gateA.TrySetResult(Response(Definition("wf-a", 5, "a-node", "甲")));
            await loadA;   // A 的迟到响应——必须被载入令牌整份丢弃

            var canvas = CanvasNodeIds(view);
            Require((string?)workflowId.GetValue(view) == "wf-b", "迟到响应推进了 workflowId（应保持 wf-b）");
            Require(canvas.Count == 1 && canvas[0] == "b-node",
                $"迟到响应把画布翻回了旧工作流（画布节点：{Join(canvas)}，期望 b-node）");
            Require((int?)version.GetValue(view) == 6, "迟到响应改写了版本号（期望 B 的 version 6）");
            Require(((JsonElement)Field(view, "current").GetValue(view)!).Text("id") == "wf-b", "current 指向了旧工作流");
        }
        finally
        {
            gateA.TrySetResult(Response(Definition("wf-a", 5, "a-node", "甲")));
            gateB.TrySetResult(Response(Definition("wf-b", 6, "b-node", "乙")));
            view.Deactivate();
        }
    }

    // ── ② A→B→C 切换 + 挂起中的离场 flush/载入 ──
    // 失败构造（原始缺陷的两扇窗）：处理器1（A→B）悬挂在 await SaveNowAsync()（对 A
    // 的 PATCH 被门控挂起），越过 flush 后旧代码才把 workflowId 改成 B 并悬挂在 B 的
    // 门控载入上（画布仍是 A 的图）；此刻处理器2（B→C）的排队 flush（target 为空 →
    // 读"当前 workflowId"）PATCH workflows/B 携 A 的图（窗一）；随后 B 的载入响应
    // 最后落地，画布=B 而 workflowId=C（窗二）。
    // 门控：PATCH wf-a 挂起离场 flush；GET wf-b 挂起 B 的载入（保证处理器2 执行时
    // workflowId=B 而画布必然还是 A 的图，竞态完全确定）；GET wf-c 即时返回。
    private static async Task SwitchWithPendingFlushKeepsGraphOwnership()
    {
        var patches = new System.Collections.Concurrent.ConcurrentQueue<(string Path, JsonElement Body)>();
        var flushGate = NewGate();   // PATCH wf-a 的响应：挂起处理器1 的离场 flush
        var loadBGate = NewGate();   // GET wf-b 的响应：挂起 B 的载入（画布保持 A 的图）
        using var api = new ApiClient("http://127.0.0.1:12345", new Handler(async request =>
        {
            var path = request.RequestUri!.AbsolutePath;
            if (request.Method == HttpMethod.Patch && path.EndsWith("/workflows/wf-a"))
            {
                patches.Enqueue((path, await Body(request)));
                return await flushGate.Task;
            }
            if (request.Method == HttpMethod.Patch)
            {
                patches.Enqueue((path, await Body(request)));
                return Response(EmptyDefinition(path.EndsWith("/workflows/wf-b") ? "wf-b" : "wf-c"));
            }
            if (request.Method == HttpMethod.Get && path.EndsWith("/workflows/wf-a"))
                return Response(Definition("wf-a", 5, "a-node", "甲"));
            if (request.Method == HttpMethod.Get && path.EndsWith("/workflows/wf-b"))
                return await loadBGate.Task;
            if (request.Method == HttpMethod.Get && path.EndsWith("/workflows/wf-c"))
                return Response(Definition("wf-c", 7, "c-node", "丙"));
            if (path.EndsWith("/runs")) return Response("[]");
            throw new Exception("Unexpected switch-race request: " + path);
        }));
        var view = new WorkflowView();
        try
        {
            WireContext(view, api);
            var workflowId = Field(view, "workflowId");
            var selector = (ComboBox)Field(view, "workflowSelector").GetValue(view)!;
            selector.Items.Add(new ComboBoxItem { Tag = "wf-a", Content = "甲流程" });
            selector.Items.Add(new ComboBoxItem { Tag = "wf-b", Content = "乙流程" });
            selector.Items.Add(new ComboBoxItem { Tag = "wf-c", Content = "丙流程" });
            ComboBoxItem Item(string tag) => selector.Items.Cast<ComboBoxItem>().Single(item => (string?)item.Tag == tag);

            workflowId.SetValue(view, "wf-a");
            selector.SelectedItem = Item("wf-a");   // 同 id：切换处理器必须保持静默
            await InvokeLoad(view);                 // 画布载入 A（a-node, version 5）
            Require(CanvasNodeIds(view).Contains("a-node"), "前置失败：A 未载入画布");

            selector.SelectedItem = Item("wf-b");   // 处理器1（A→B）：对 A 的离场 flush 被门控挂起
            await Until(() => patches.Count == 1);  // A 的离场 PATCH 已发出（响应挂起中）
            flushGate.TrySetResult(Response(EmptyDefinition("wf-a")));   // 放行：处理器1 越过 flush
            // 未修复代码此刻才把 workflowId 改成 B（旧顺序：赋值在 await 之后），
            // 随即悬挂在 B 的门控载入上——画布仍是 A 的图；修复代码在切换起始就
            // 已提交 wf-b。两种实现都能观测到 workflowId=wf-b 再发起第二次切换，
            // 竞态构造不依赖任务延续顺序。
            await Until(() => (string?)workflowId.GetValue(view) == "wf-b");
            selector.SelectedItem = Item("wf-c");   // 处理器2（B→C）：此刻 workflowId=B、画布仍是 A 的图
            // 未修复：排队的 flush（target 为空 → 读“当前 workflowId”）PATCH
            // workflows/B 携 A 的图——跨界写；修复：flush 点名离开的 B，画布归属
            // 仍是 A → 弃权。
            await Until(() => CanvasNodeIds(view).Contains("c-node"));   // wf-c 落地（B 的载入仍被门控）
            loadBGate.TrySetResult(Response(Definition("wf-b", 6, "b-node", "乙")));   // 最后放行 B 的载入响应（构造乱序返回）
            await Until(() => (string?)workflowId.GetValue(view) == "wf-c"
                && CanvasNodeIds(view).Any(id => id is "b-node" or "c-node"));
            await Task.Delay(200);   // 让尚未落地的那份载入也提交完再断言

            // 核心：每个 PATCH 的 (graph, id) 必须配对——URL 指向谁，载荷节点就属于谁。
            foreach (var patch in patches.ToArray())
            {
                var expected = patch.Path.EndsWith("/workflows/wf-a") ? "a-"
                    : patch.Path.EndsWith("/workflows/wf-b") ? "b-" : "c-";
                var carried = patch.Body.Element("draft_graph").Array("nodes").Select(row => row.Text("id")).ToList();
                Require(carried.Count > 0 && carried.All(id => id.StartsWith(expected, StringComparison.Ordinal)),
                    $"#427 跨界 PATCH：{patch.Path} 携带了不属于自己的图（节点：{Join(carried)}，期望前缀 {expected}）");
            }
            // 离场工作流的待存编辑必须照常落盘（防止“以不保存来修复”）。
            Require(patches.ToArray().Any(p => p.Path.EndsWith("/workflows/wf-a")
                    && p.Body.Element("draft_graph").Array("nodes").Any(row => row.Text("id") == "a-node")),
                "离场工作流的待存编辑未落盘（切换路径应先 flush A）");
            // 终态一致：选择与画布都停在最后请求的 C。
            var finalCanvas = CanvasNodeIds(view);
            Require((string?)workflowId.GetValue(view) == "wf-c" && finalCanvas.Count == 1 && finalCanvas[0] == "c-node",
                $"切换完成后画布与选择不一致（workflowId={workflowId.GetValue(view)}，画布节点：{Join(finalCanvas)}）");
        }
        finally
        {
            flushGate.TrySetResult(Response(EmptyDefinition("wf-a")));
            loadBGate.TrySetResult(Response(Definition("wf-b", 6, "b-node", "乙")));
            view.Deactivate();
        }
    }

    // ── ③ Activate 恰好一次载入 ──
    // 失败构造（原始缺陷）：Activate 先拨 SelectedItem（选择器处理器载入一次）再自行
    // LoadWorkflowAsync（再载入一次）——每次激活双倍 GET。
    private static async Task ActivateLoadsExactlyOnce()
    {
        var loads = 0;
        using var api = new ApiClient("http://127.0.0.1:12345", new Handler(request =>
        {
            var path = request.RequestUri!.AbsolutePath;
            if (request.Method == HttpMethod.Get && path == "/api/v1/workflows/wf-act")
            {
                loads++;
                return Task.FromResult(Response(Definition("wf-act", 3, "act-node", "激活")));
            }
            if (path.EndsWith("/workflow-node-types")) return Task.FromResult(Response("[]"));
            if (path.EndsWith("/projects/p1/workflows"))
                return Task.FromResult(Response("""[{"id":"wf-act","name":"激活检查","version":3,"draft_version":1,"draft_graph":{"nodes":[],"edges":[]}}]"""));
            if (path.EndsWith("/projects/p1/chapters")) return Task.FromResult(Response("[]"));
            if (path.EndsWith("/models")) return Task.FromResult(Response("[]"));
            if (path.EndsWith("/runs")) return Task.FromResult(Response("[]"));
            throw new Exception("Unexpected activate-race request: " + path);
        }));
        var view = new WorkflowView();
        try
        {
            view.Activate(Context(api));
            var workflowId = Field(view, "workflowId");
            await Until(() => (string?)workflowId.GetValue(view) == "wf-act" && CanvasNodeIds(view).Contains("act-node"));
            await Task.Delay(300);   // 给可能存在的第二份（迟到）请求留出窗口
            Require(loads == 1, $"Activate 应恰好发起一次工作流载入（实际 {loads} 次）");
        }
        finally { view.Deactivate(); }
    }

    // ── 反射/断言辅助（沿用 NativeWorkflowChecks 的模式） ──

    // 反射不自动补可选参数（targetId = null），必须显式传 null。
    private static Task InvokeLoad(WorkflowView view) =>
        (Task)typeof(WorkflowView).GetMethod("LoadWorkflowAsync", All)!.Invoke(view, new object?[] { null })!;

    private static FieldInfo Field(WorkflowView view, string name) =>
        typeof(WorkflowView).GetField(name, All) ?? throw new Exception("反射入口缺失：WorkflowView." + name);

    private static void WireContext(WorkflowView view, ApiClient api) =>
        typeof(WorkspaceView).GetProperty("Context", BindingFlags.Instance | BindingFlags.NonPublic)!.SetValue(view, Context(api));

    private static WorkspaceContext Context(ApiClient api) => new()
    {
        Api = api, State = new WorkspaceState(), Window = null!,
        Project = new ProjectItem("p1", "#427 检查", "", 0, 0),
        NavigateSection = (_, _) => Task.CompletedTask, OpenDashboard = () => Task.CompletedTask,
    };

    private static List<string> CanvasNodeIds(WorkflowView view) =>
        ((System.Collections.IList)Field(view, "nodes").GetValue(view)!).Cast<object>()
            .Select(node => (string)node.GetType().GetProperty("Id")!.GetValue(node)!).ToList();

    private static async Task<JsonElement> Body(HttpRequestMessage request)
    {
        using var document = JsonDocument.Parse(await request.Content!.ReadAsStringAsync());
        return document.RootElement.Clone();
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

    private static string Join(IEnumerable<string> parts) => string.Join(",", parts);

    private static TaskCompletionSource<HttpResponseMessage> NewGate() =>
        new(TaskCreationOptions.RunContinuationsAsynchronously);

    private static HttpResponseMessage Response(string json, HttpStatusCode status = HttpStatusCode.OK) =>
        new(status) { Content = new StringContent(json) };

    private static string EmptyDefinition(string id) => JsonSerializer.Serialize(new
    {
        id, name = "占位", version = 9, draft_version = 2,
        draft_graph = new { nodes = Array.Empty<object>(), edges = Array.Empty<object>() },
    });

    private static string Definition(string id, int version, string nodeId, string name) => JsonSerializer.Serialize(new
    {
        id, name, version, draft_version = 1,
        draft_graph = new
        {
            nodes = new object[]
            {
                // inputs/outputs/config 必须给全：WorkflowNode.From 会把缺失项读成
                // Undefined JsonElement，BuildGraph 再进 JsonContent 会直接抛异常，
                // 载入的图就永远到不了 PATCH。
                new
                {
                    id = nodeId, type = "agent.parse", name, position = new { x = 10, y = 20 },
                    inputs = Array.Empty<object>(), outputs = Array.Empty<object>(),
                    config = new Dictionary<string, object>(),
                },
            },
            edges = Array.Empty<object>(),
        },
    });

    private sealed class Handler(Func<HttpRequestMessage, Task<HttpResponseMessage>> handler) : HttpMessageHandler
    {
        protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, System.Threading.CancellationToken token) => handler(request);
    }
}
