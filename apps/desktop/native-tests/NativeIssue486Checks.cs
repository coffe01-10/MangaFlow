using System.IO;
using System.Net;
using System.Net.Http;
using System.Reflection;
using System.Text.Json;
using System.Threading;
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

// #486 回归（P4 ×3）+ #449 第 2 项（P3）：四条同属「静默降级」类的桌面缺陷。
// ① #486-1 死批次闩锁：候选 POST 失败后 pendingBatch 保留（防重复计费，正确），
//    但没有任何路径清它——批次在服务端死亡（缺席 / 非 OPEN）后，每次生成都反复
//    打死批次 id，直到重启应用。修复：重试前用批次列表核实，确认死亡即换新；
//    瞬时失败且批次仍 OPEN 时必须继续复用（防重复计费不得回归）。
// ② #486-2 仪表盘封面：record 相等只覆盖 (Id,Name,Summary,Pending,Failed)，
//    漏掉绑定的 ModeAndResolution 与点击导航的 NextSection——计数不变的模式/
//    分辨率变更不再触发重绘，封面停留旧值。
// ③ #486-3 灯箱吞错：原图加载失败 catch{} → 永久空白黑窗；应像 ImageBox 一样
//    给出失败文案与重试入口。
// ④ #449-2：图片直连 HttpClient 绕过 ApiClient.ThrowResponseError——404/409 的
//    服务端 detail 到达不了任何图片界面，只剩生硬英文。修复：Ui.cs 内镜像同一
//    错误映射（JSON detail 优先 / 本地化回退带状态码 / 409 冲突前缀）。
//
// 【需 lead 注册】本文件按任务约束未接入运行入口：建议在
// NativeInteractionChecks.RunIsolated 的 await 链（或专用入口）追加
// `NativeIssue486Checks.Run(output);`——Run 内部自带 STA 调度，
// 也可独立调用（无 Application 时会自建 STA 线程 + Application/Theme）。
// 注册调用点必须在 STA/Dispatcher 线程上下文中（WPF 视觉树访问要求）。
internal static class NativeIssue486Checks
{
    internal static void Run(string output)
    {
        var previous = (string)typeof(KeyValueStore).GetField("path", BindingFlags.Static | BindingFlags.NonPublic)!.GetValue(null)!;
        Directory.CreateDirectory(output);
        var prefs = Path.Combine(output, "issue486-test-prefs.json");
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
    //（NativeStoryboardEditChecks.Run 的同款双模式；Application 是 AppDomain 级
    // 单例，链内复用调用方线程，避免「不能创建多个实例」）。
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
        if (failure != null) throw new Exception("Issue 486/449 checks failed", failure);
    }

    // STA + DispatcherFrame 泵（NativeInteractionChecks.RunIsolated 的模式）：
    // async 续体经由 DispatcherSynchronizationContext 回到泵上执行；等待一律用
    // Until 轮询，绝不在 dispatcher 任务上同步阻塞。
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
        var timeout = new DispatcherTimer { Interval = TimeSpan.FromSeconds(90) };
        timeout.Tick += (_, _) => { failure = new TimeoutException("Issue 486/449 checks timed out"); frame.Continue = false; };
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
        if (failure != null) throw new Exception("Issue 486/449 checks failed", failure);
    }

    private static async Task CheckAsync(string output)
    {
        DashboardCoversDisplayedFields();
        Console.WriteLine("PASS: #486-2 模式/分辨率/下一步变更（计数不变）触发仪表盘封面重绘，无变化时不重复重建");
        await DeadBatchRetryReplacesConfirmedDeadBatches();
        Console.WriteLine("PASS: #486-1 候选失败保留批次闩锁；重试核实死批次（缺席/非 OPEN）后换新批次，活批次仍复用");
        await MediaSurfacesSurfaceBackendDetail();
        Console.WriteLine("PASS: #486-3 + #449-2 灯箱失败可见且可重试；409 detail 与非 JSON 的带状态码本地化回退到达图片界面");
    }

    // ── ② #486-2：显示字段参与重绘判定（回归护栏） ──
    // 实测结论：C# record 合成等值包含 body 声明的 init 属性——原始缺陷描述
    // （「record 相等只覆盖主构造参数」）不成立，未修复代码同样重绘（本检查在
    // 恢复 SequenceEqual 的突变下仍通过，见任务报告）。检查保留为回归护栏：
    // `original with { ModeLabel = ... }` 必须触发封面重建，防止将来把
    // ProjectItem 重构成 class 或把显示字段挪出等值面时静默回归。
    private static void DashboardCoversDisplayedFields()
    {
        var state = new WorkspaceState();
        var original = new ProjectItem("p1", "雨夜来信", "3 章 · 9 页 · 2 已采用", 1, 0)
        {
            ModeLabel = "半自动", Resolution = "1K", NextSection = "script", NextLabel = "撰写脚本",
        };
        state.Projects.Add(original);
        state.ChangeDashboardPage(0);
        Require(ReferenceEquals(state.DashboardProjects[0], original), "前置失败：初始封面未渲染");
        // 只有显示字段变化：record 相等仍成立，但 HomeView 绑定 模式·分辨率、
        // 点击按 NextSection 导航——封面必须换新。
        state.Projects[0] = original with { ModeLabel = "导演", Resolution = "2K", NextSection = "generate", NextLabel = "生成页面" };
        state.ChangeDashboardPage(0);
        Require(ReferenceEquals(state.DashboardProjects[0], state.Projects[0])
            && state.DashboardProjects[0].ModeAndResolution == "导演 · 2K"
            && state.DashboardProjects[0].NextSection == "generate",
            "#486-2 模式/分辨率变更后仪表盘封面仍是旧值（显示字段未参与重绘判定）");
        // 不变量：完全相同的数据不重建集合（防止修复成「每次都重建」）。
        state.ChangeDashboardPage(0);
        Require(ReferenceEquals(state.DashboardProjects[0], state.Projects[0]), "无变化时不应重复重建封面集合");
    }

    // ── ① #486-1：死批次闩锁 ──
    // 失败构造（原始缺陷）：候选 POST 失败后 pendingBatch 永远保留——批次在服务端
    // 被清除或关闭后（create_asset_candidate 只接受 OPEN 批次，其余 409「资产生成
    // 批次不存在或已关闭」），每次重试都重新 POST 死批次 id，直到重启应用。
    private static async Task DeadBatchRetryReplacesConfirmedDeadBatches()
    {
        var fixture = new StyleFixture();
        using var api = new ApiClient("http://127.0.0.1:12345", fixture);
        var view = new AssetsView();
        try
        {
            KeyValueStore.Set("image-model:p", "model-a");
            view.Activate(new WorkspaceContext
            {
                Api = api, State = new(), Window = null!,
                Project = new ProjectItem("p", "#486 死批次检查", "", 0, 0),
                NavigateSection = (_, _) => Task.CompletedTask, OpenDashboard = () => Task.CompletedTask,
            });
            await view.RefreshAsync();
            await view.SwitchAsync(AssetsView.Style);
            await Task.Delay(30);
            Layout(view, 1100, 1500);
            var pane = NativeParityChecks.Descendants(view).OfType<StyleWorkspace>().Single();
            await pane.InitialLoad;
            var card = Field<Dictionary<string, StyleProductionCard>>(pane, "cards")["style1"];

            // ① 候选提交失败（后端真实副本的 409）→ 闩锁保留，不盲目重建。
            fixture.FailNextCandidates = true;
            await card.GenerateAsync();
            Require(fixture.CreateCount == 1, "①候选失败后不应另建批次（防重复计费）");
            Require(Field<string>(card, "pendingBatch") == "batch1", "①候选失败后批次闩锁应保留");
            Require(Field<TextBlock>(card, "error").Text.Contains("资产生成批次不存在或已关闭"),
                "①服务端 409 detail 应进入错误文案");

            // ② 批次被服务端清除（列表缺席）→ 重试必须核实并换新批次。
            fixture.RemoveBatch("batch1");
            await card.GenerateAsync();
            Require(fixture.CreateCount == 2, "#486-1 重试未检测到缺席的死批次——仍会反复打死批次 id");
            Require(fixture.LastCandidatesPost().EndsWith("/asset-generation-batches/batch2/candidates", StringComparison.Ordinal),
                "#486-1 重试的候选 POST 仍指向死批次 id，而不是新批次");
            Require(Field<string>(card, "pendingBatch") == "", "②成功提交后闩锁应清空");

            // ③ 瞬时失败但批次仍 OPEN → 核实后复用原批次（防重复计费不得回归）。
            fixture.FailNextCandidates = true;
            await card.GenerateAsync();
            Require(fixture.CreateCount == 3 && Field<string>(card, "pendingBatch") == "batch3",
                "③前置失败：瞬时失败后活批次闩锁未保留");
            await card.GenerateAsync();
            Require(fixture.CreateCount == 3, "③修复过度：核实的活批次也被重建（重复建批计费回归）");
            Require(fixture.LastCandidatesPost().EndsWith("/asset-generation-batches/batch3/candidates", StringComparison.Ordinal),
                "③核实的活批次应被复用而不是换新");

            // ④ 批次存在但已 CLOSED（非 OPEN 同样不可用）→ 换新批次。
            fixture.FailNextCandidates = true;
            await card.GenerateAsync();
            Require(fixture.CreateCount == 4 && Field<string>(card, "pendingBatch") == "batch4",
                "④前置失败：候选失败后批次闩锁未保留");
            fixture.CloseBatch("batch4");
            await card.GenerateAsync();
            Require(fixture.CreateCount == 5, "#486-1 重试未检测到已关闭（非 OPEN）的死批次");
            Require(fixture.LastCandidatesPost().EndsWith("/asset-generation-batches/batch5/candidates", StringComparison.Ordinal),
                "④重试仍指向已关闭批次，而不是新批次");
        }
        finally { view.Deactivate(); }
    }

    // ── ③④ #486-3 + #449-2：媒体界面失败可见、可重试、透出服务端 detail ──
    // 失败构造（原始缺陷）：Lightbox.Load 的 catch{} 吞掉一切（含 null 位图）→
    // 永久空白黑窗；ImageStore 用 EnsureSuccessStatusCode → 生成的英文行，
    // 后端 404/409 detail 与状态码回退都到不了界面。
    private static async Task MediaSurfacesSurfaceBackendDetail()
    {
        var media = new MediaFixture { Png = RenderPng() };
        ImageStore.TestResponder = media.Respond;
        try
        {
            // 直连层：409 + JSON detail → 冲突前缀 + detail 原文。
            media.On("/api/v1/assets/dead9/content", () => new HttpResponseMessage(HttpStatusCode.Conflict)
            { Content = new StringContent("{\"detail\":\"素材已归档，无法读取原图\"}") });
            try { await ImageStore.LoadAsync("http://127.0.0.1:12345/api/v1/assets/dead9/content", CancellationToken.None); throw new Exception("409 被当作图片成功"); }
            catch (InvalidOperationException e)
            {
                Require(e.Message.Contains("素材已归档") && e.Message.Contains("刷新"), "#449-2 409 的服务端 detail 未到达图片直连层");
            }
            // 直连层：非 JSON 失败 → 本地化回退带状态码。
            media.On("/api/v1/assets/boom7/content", () => new HttpResponseMessage(HttpStatusCode.InternalServerError)
            { Content = new StringContent("Internal Server Error") });
            try { await ImageStore.LoadAsync("http://127.0.0.1:12345/api/v1/assets/boom7/content", CancellationToken.None); throw new Exception("500 被当作图片成功"); }
            catch (InvalidOperationException e)
            {
                Require(e.Message.Contains("图片加载失败") && e.Message.Contains("500"), "#449-2 非 JSON 失败缺少带状态码的本地化回退");
            }

            // 灯箱：失败文案可见（不再是空白黑窗）+ 重试入口可用 + 重试成功路径。
            var lightbox = new Lightbox(null!, "http://127.0.0.1:12345/api/v1/assets/dead9/content", "#486-3 灯箱检查");
            try
            {
                var failurePanel = Field<StackPanel>(lightbox, "failurePanel");
                var failureText = Field<TextBlock>(lightbox, "failureText");
                await Until(() => failurePanel.Visibility == Visibility.Visible && failureText.Text.Length > 0,
                    "#486-3 原图加载失败后失败 UI 未出现（仍是空白黑窗）");
                Require(failureText.Text.Contains("素材已归档"), "#486-3/#449-2 灯箱未透出服务端 detail：" + failureText.Text);
                Require(Field<Image>(lightbox, "image").Source == null, "#486-3 失败态不应残留旧图");
                // 未 Show 的窗口不套模板：直接从失败面板取重试按钮（Decorator 子级
                // 无需模板即挂视觉树）。
                var retry = failurePanel.Children.OfType<Button>()
                    .Single(b => b.Content as string == "重试加载");
                Require(retry.IsEnabled, "#486-3 灯箱缺少可用的重试入口");
                // 重试成功：同一 URL 改回 200 PNG → 失败收起、原图呈现。
                media.On("/api/v1/assets/dead9/content", media.Image);
                retry.RaiseEvent(new RoutedEventArgs(ButtonBase.ClickEvent));
                await Until(() => Field<Image>(lightbox, "image").Source != null && failurePanel.Visibility == Visibility.Collapsed,
                    "#486-3 重试成功后未呈现原图/未收起失败面板");
            }
            finally { lightbox.Close(); }

            // 非 JSON 失败在灯箱同样给出带状态码的本地化回退。
            var rawBox = new Lightbox(null!, "http://127.0.0.1:12345/api/v1/assets/boom7/content", "#449-2 灯箱回退检查");
            try
            {
                var text = Field<TextBlock>(rawBox, "failureText");
                await Until(() => Field<StackPanel>(rawBox, "failurePanel").Visibility == Visibility.Visible,
                    "#449-2 非 JSON 失败在灯箱未出现失败 UI");
                Require(text.Text.Contains("图片加载失败") && text.Text.Contains("500"), "#449-2 灯箱回退缺少状态码：" + text.Text);
            }
            finally { rawBox.Close(); }

            // 缩略图路径（ImageBox）同样带映射后的失败信息。
            var thumb = new ImageBox { SourceUrl = "http://127.0.0.1:12345/api/v1/assets/boom7/content" };
            await Until(() => NativeParityChecks.Descendants((DependencyObject)thumb.Content!).OfType<TextBlock>()
                .Any(t => t.Text.Contains("图片加载失败") && t.Text.Contains("500")),
                "#449-2 缩略图失败占位未携带映射后的失败信息");
        }
        finally { ImageStore.TestResponder = null; }
    }

    // ── 辅助（沿用 NativeStyleChecks / NativeStoryboardEditChecks 的模式） ──

    private static T Field<T>(object value, string name) =>
        (T)(value.GetType().GetField(name, BindingFlags.Instance | BindingFlags.NonPublic)?.GetValue(value)
            ?? throw new Exception("反射入口缺失：" + value.GetType().Name + "." + name));

    private static void Layout(FrameworkElement element, int width, int height)
    {
        element.Measure(new Size(width, height));
        element.Arrange(new Rect(0, 0, width, height));
        element.UpdateLayout();
    }

    private static async Task Until(Func<bool> condition, string message)
    {
        using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(3));
        try { while (!condition()) await Task.Delay(5, timeout.Token); }
        catch (OperationCanceledException) { throw new Exception("等待超时（3s）：" + message); }
    }

    private static void Require(bool condition, string message)
    {
        if (!condition) throw new Exception(message);
    }

    private static byte[] RenderPng()
    {
        var source = new RenderTargetBitmap(8, 6, 96, 96, PixelFormats.Pbgra32);
        var encoder = new PngBitmapEncoder();
        encoder.Frames.Add(BitmapFrame.Create(source));
        using var stream = new MemoryStream();
        encoder.Save(stream);
        return stream.ToArray();
    }

    /// <summary>风格生成链路的假后端（NativeStyleChecks.Fixture 的裁剪版）：
    /// 建批递增 id 并进入可配置的批次列表；FailNextCandidates 单次失败；
    /// 候选失败返回后端真实副本 409「资产生成批次不存在或已关闭」。</summary>
    private sealed class StyleFixture : HttpMessageHandler
    {
        internal int CreateCount;
        internal bool FailNextCandidates;
        internal readonly List<(string Path, JsonElement Body)> Writes = [];
        internal readonly List<Dictionary<string, object>> Batches = [];

        protected override async Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken token)
        {
            var path = request.RequestUri!.AbsolutePath;
            if (request.Method != HttpMethod.Get)
            {
                Writes.Add((path, JsonSerializer.Deserialize<JsonElement>(
                    request.Content == null ? "{}" : await request.Content.ReadAsStringAsync(token))));
                if (path == "/api/v1/asset-generation-batches")
                {
                    CreateCount++;
                    var id = "batch" + CreateCount;
                    Batches.Add(new Dictionary<string, object>
                    { ["id"] = id, ["generation_kind"] = "STYLE_TEST", ["status"] = "OPEN", ["target_type"] = "STYLE", ["target_id"] = "style1" });
                    return Json("{\"id\":\"" + id + "\"}");
                }
                if (path.EndsWith("/candidates", StringComparison.Ordinal))
                {
                    if (FailNextCandidates)
                    {
                        FailNextCandidates = false;
                        return new HttpResponseMessage(HttpStatusCode.Conflict)
                        { Content = new StringContent("{\"detail\":\"资产生成批次不存在或已关闭\"}") };
                    }
                    return Json("{\"job_id\":\"job1\",\"job_status\":\"WAITING\"}");
                }
                return Json("{}");
            }
            if (path == "/api/v1/asset-generation-batches") return Json(JsonSerializer.Serialize(Batches));
            if (path.EndsWith("/candidates", StringComparison.Ordinal)) return Json("[]");
            if (path.EndsWith("/projects/p/styles", StringComparison.Ordinal)) return Json("[" + Style + "]");
            if (path.EndsWith("/projects/p", StringComparison.Ordinal)) return Json("{\"default_style_id\":\"\"}");
            if (path.EndsWith("/models", StringComparison.Ordinal))
                return Json("[{\"model_type\":\"IMAGE\",\"enabled\":true,\"operations\":[\"image_edit\"],\"logical_alias\":\"model-a\",\"display_name\":\"Codex CLI ImageGen\",\"provider\":\"Codex CLI\",\"model_id\":\"codex-imagegen\"}]");
            if (path.EndsWith("/assets", StringComparison.Ordinal))
                return Json("[{\"id\":\"ref1\",\"kind\":\"STYLE_REFERENCE\",\"original_name\":\"京都色彩参考.png\",\"status\":\"UPLOADED\"}]");
            return Json("[]");
        }

        internal void RemoveBatch(string id) => Batches.RemoveAll(b => (string)b["id"] == id);
        internal void CloseBatch(string id) => Batches.Single(b => (string)b["id"] == id)["status"] = "CLOSED";
        internal string LastCandidatesPost() =>
            Writes.Last(w => w.Path.EndsWith("/candidates", StringComparison.Ordinal)).Path;

        private string Style => JsonSerializer.Serialize(new
        {
            id = "style1", name = "京都雨夜 · 暖灰彩色", color_mode = "color", status = "DRAFT", version = 7,
            locked_fields = new[] { "肤色", "低饱和" },
            profile = new
            {
                reference_asset_ids = new[] { "ref1" },
                palette = new Dictionary<string, string> { ["肤色"] = "#F0C2A0", ["阴影"] = "#59443F" },
                palette_confirmed = true, test_image_approved = false, test_candidate_id = "",
            },
        });

        private static HttpResponseMessage Json(string value) =>
            new(HttpStatusCode.OK) { Content = new StringContent(value) };
    }

    /// <summary>ImageStore.TestResponder 的回放器：按绝对路径返回可控响应，
    /// 缺省成功（PNG 字节由 STA 线程预渲染）。</summary>
    private sealed class MediaFixture
    {
        internal byte[] Png = [];
        private readonly Dictionary<string, Func<HttpResponseMessage>> routes = new(StringComparer.Ordinal);

        internal void On(string absolutePath, Func<HttpResponseMessage> respond) => routes[absolutePath] = respond;
        internal HttpResponseMessage Image() => new(HttpStatusCode.OK) { Content = new ByteArrayContent(Png) };
        internal Task<HttpResponseMessage> Respond(HttpRequestMessage request, CancellationToken _)
        {
            var path = request.RequestUri!.AbsolutePath;
            return Task.FromResult(routes.TryGetValue(path, out var respond) ? respond() : Image());
        }
    }
}
