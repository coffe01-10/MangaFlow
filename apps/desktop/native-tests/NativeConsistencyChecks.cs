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

// 桌面端与网页端一致性杂项（7 项）的契约检查：
//  1. 轮询周期读 /settings/runtime 的 ui_poll_interval_seconds（SettingsView 保存后
//     发布进 PollInterval 并触发 RuntimeSaved，MainWindow 下个 tick 生效）。
//  2. 任务重试资格仅 FAILED（叠加 attempt 守卫，对齐 web jobs-section）。
//  3. PNG/导出下载走流式 SaveDownloadAsync：409/网络中断透出错误且不留半文件。
//  4. 用量页预算横幅（localStorage 同键持久化 + idle/near/over/ok 四态）与
//     供应商/模型/通道分解明细（字段以 apps/api schemas.py 用量 Read 模型为准）。
//  5. 参考图上传的显式用途选择（四个 AssetPurpose chips，kind 不再由所选角色猜测）。
//  6. 409 detail 中结构化 blockers 数组逐条透出（web describeActionError 语义）。
//  7. 深链带实体预选（?character=/?outfit=/?style=/?page=，含分镜缺服装格定位）。
//
// 【需 lead 注册】本文件按任务约束未接入运行入口：建议在
// NativeInteractionChecks.RunIsolated 的 await 链（或专用入口）追加
// `await NativeConsistencyChecks.Run(output);`——Run 内部自带 STA 调度，
// 也可独立调用（无 Application 时会自建 STA 线程 + Application/Theme）。
// 未覆盖边界：MainWindow NavigateSection 的 query 转发只经由 RouteForBlocker 的
// 输出与 AssetsView/StoryboardView 的消费端钉住，真实 shell 导航链路属于
// NativeVisualChecks 的渲染域，不在本文件重复。
internal static class NativeConsistencyChecks
{
    internal static void Run(string output)
    {
        var previous = (string)typeof(KeyValueStore).GetField("path", BindingFlags.Static | BindingFlags.NonPublic)!.GetValue(null)!;
        Directory.CreateDirectory(output);
        var prefs = Path.Combine(output, "consistency-test-prefs.json");
        File.WriteAllText(prefs, "{}");
        KeyValueStore.UseLocation(prefs);
        try
        {
            PollInterval.ResetForTests();
            if (Application.Current is null) RunOnDedicatedStaThread(output);
            else RunFrame(output);
        }
        finally
        {
            PollInterval.ResetForTests();
            KeyValueStore.UseLocation(previous);
            try { File.Delete(prefs); File.Delete(prefs + ".tmp"); } catch (IOException) { }
        }
    }

    // 独立运行（进程里还没有 Application）：自建 STA 线程 + Application/Theme
    //（NativeStoryboardEditChecks 的模式）。Application 是 AppDomain 级单例，本方法每
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
        if (failure != null) throw new Exception("Native consistency checks failed", failure);
    }

    // STA + DispatcherFrame 泵：async-void 的按钮处理经由 DispatcherSynchronizationContext
    // 回到泵上执行（NativeStoryboardEditChecks / NativeInteractionChecks 的模式）。
    private static void RunFrame(string output)
    {
        var frame = new DispatcherFrame();
        Exception? failure = null;
        var app = Application.Current!;
        app.Dispatcher.UnhandledException += (_, e) =>
        {
            failure ??= new Exception("Dispatcher unhandled: " + e.Exception.Message, e.Exception);
            e.Handled = true;
            frame.Continue = false;
        };
        var timeout = new DispatcherTimer { Interval = TimeSpan.FromSeconds(90) };
        timeout.Tick += (_, _) => { failure = new TimeoutException("Native consistency checks timed out"); frame.Continue = false; };
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
        if (failure != null) throw new Exception("Native consistency checks failed", failure);
    }

    private static async Task CheckAsync(string output)
    {
        await PollIntervalChecks();
        await SettingsPublishChecks();
        RetryEligibilityChecks();
        await DownloadStreamChecks(output);
        UsageBudgetChecks();
        UsageRenderChecks();
        await BlockerDetailChecks();
        DeepLinkRouteChecks();
        await AssetsDeepLinkChecks();
        await UploadPurposeChecks(output);
        await StoryboardDeepLinkChecks();
        Console.WriteLine("PASS: poll interval setting, FAILED-only retry, streaming downloads, usage budget/breakdown, upload purpose, 409 blockers and entity deep links all match the web contract");
    }

    // ---------- 1. ui_poll_interval_seconds 驱动轮询 ----------

    private static async Task PollIntervalChecks()
    {
        Require(PollInterval.CurrentMs == PollInterval.DefaultMs && PollInterval.DefaultMs == 3000,
            "shipped poll default is the API default 3000 ms");
        Require(PollInterval.MinMs == 1000 && PollInterval.MaxMs == 60000, "poll clamp bounds follow the web ClampedNumberInput");
        using (var json = JsonDocument.Parse("{\"ui_poll_interval_seconds\":5000}"))
            Require(PollInterval.Parse(json.RootElement) == 5000, "runtime settings expose ui_poll_interval_seconds");
        using (var json = JsonDocument.Parse("{\"ui_poll_interval_seconds\":\"broken\"}"))
            Require(PollInterval.Parse(json.RootElement) is null, "non-numeric poll setting is ignored");
        PollInterval.Apply(null);
        Require(PollInterval.CurrentMs == 3000, "null poll setting keeps the current period");
        PollInterval.Apply(400);
        Require(PollInterval.CurrentMs == PollInterval.MinMs, "poll period clamps to the web minimum");
        PollInterval.Apply(999_999);
        Require(PollInterval.CurrentMs == PollInterval.MaxMs, "poll period clamps to the web maximum");
        using var api = new ApiClient("http://127.0.0.1:12345", new Handler(_ =>
            Task.FromResult(Json("{\"version\":1,\"ui_poll_interval_seconds\":7000}"))));
        await PollInterval.LoadAsync(api);
        Require(PollInterval.CurrentMs == 7000 && PollInterval.Interval == TimeSpan.FromMilliseconds(7000),
            "GET settings/runtime drives the poll period");
        PollInterval.ResetForTests();
    }

    private static async Task SettingsPublishChecks()
    {
        var fixture = new SettingsFixture();
        using var api = new ApiClient("http://127.0.0.1:12345", fixture);
        var view = new SettingsView();
        var saved = 0;
        view.RuntimeSaved += () => saved++;
        view.Activate(Context(api, "consistency"));
        // Activate 是 async-void：等运行设置表单落地后再改输入。
        await Until(() => Inputs(view).ContainsKey("ui_poll_interval_seconds"));
        ((TextBox)Inputs(view)["ui_poll_interval_seconds"]).Text = "5000";
        view.SaveRuntimeSettings();
        await Until(() => saved == 1);
        Require(fixture.Patches == 1, "runtime save PATCHes settings/runtime exactly once");
        Require(fixture.PatchBody.ValueKind == JsonValueKind.Object
            && fixture.PatchBody.GetProperty("ui_poll_interval_seconds").GetInt32() == 5000,
            "saved form carries ui_poll_interval_seconds to the server");
        Require(PollInterval.CurrentMs == 5000, "successful save publishes the new poll period into PollInterval");
        PollInterval.ResetForTests();
        view.Deactivate();
    }

    private static Dictionary<string, FrameworkElement> Inputs(SettingsView view) =>
        Field<Dictionary<string, FrameworkElement>>(view, "runtimeInputs");

    private sealed class SettingsFixture : HttpMessageHandler
    {
        public int Patches;
        public JsonElement PatchBody;

        protected override async Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken token)
        {
            var path = request.RequestUri!.AbsolutePath;
            if (request.Method == HttpMethod.Patch && path.EndsWith("/settings/runtime"))
            {
                Patches++;
                PatchBody = JsonSerializer.Deserialize<JsonElement>(await request.Content!.ReadAsStringAsync(token));
                return Json("{\"version\":2,\"queue_mode\":\"rq\",\"default_concurrency\":2,\"max_auto_repairs\":2,"
                    + "\"health_check_interval_seconds\":300,\"ui_poll_interval_seconds\":5000,\"storage_root\":\"D:/x\",\"database_backend\":\"sqlite\"}");
            }
            if (path.EndsWith("/settings/runtime"))
                return Json("{\"version\":1,\"queue_mode\":\"rq\",\"default_concurrency\":2,\"max_auto_repairs\":2,"
                    + "\"health_check_interval_seconds\":300,\"ui_poll_interval_seconds\":3000,\"storage_root\":\"D:/x\",\"database_backend\":\"sqlite\"}");
            if (path.EndsWith("/settings/diagnostics"))
                return Json("{\"queue\":{\"actual_executor\":\"rq\"},\"checked_at\":null,\"checks\":[]}");
            return Json("[]");
        }
    }

    // ---------- 2. 重试资格仅 FAILED ----------

    private static void RetryEligibilityChecks()
    {
        foreach (var status in new[] { "NEEDS_REVIEW", "WAITING", "QUEUED", "RUNNING", "COMPLETED", "CANCELLED" })
            using (var json = JsonDocument.Parse($"{{\"status\":\"{status}\",\"attempt_count\":1,\"max_attempts\":3}}"))
                Require(!JobItem.From(json.RootElement).CanRetry, $"status {status} must not offer retry (web offers retry for FAILED only)");
        using (var json = JsonDocument.Parse("{\"status\":\"FAILED\",\"attempt_count\":1,\"max_attempts\":3}"))
            Require(JobItem.From(json.RootElement).CanRetry, "FAILED with attempts left offers retry");
        using (var json = JsonDocument.Parse("{\"status\":\"FAILED\",\"attempt_count\":3,\"max_attempts\":3}"))
            Require(!JobItem.From(json.RootElement).CanRetry, "exhausted task cannot retry");
    }

    // ---------- 3. 下载流式 + 错误透出（不留半文件） ----------

    private static async Task DownloadStreamChecks(string output)
    {
        var directory = Path.Combine(output, "download-fixture");
        Directory.CreateDirectory(directory);
        var target = Path.Combine(directory, "page.png");
        File.WriteAllText(target, "previous-export");

        using (var api = new ApiClient("http://127.0.0.1:12345", new Handler(_ =>
            Task.FromResult(new HttpResponseMessage(HttpStatusCode.Conflict)
            { Content = new StringContent("{\"detail\":{\"code\":\"EXPORT_BLOCKED\",\"message\":\"页面尚未通过生产检查\",\"blockers\":[{\"code\":\"A\",\"message\":\"第 1 页未确认版本\"},{\"code\":\"B\",\"message\":\"风格未激活\"}]}}") }))))
        {
            var error = await Capture(() => api.SaveDownloadAsync("pages/pg-1/export.png", target));
            Require(error is InvalidOperationException && error.Message.Contains("页面尚未通过生产检查")
                && error.Message.Contains("第 1 页未确认版本") && error.Message.Contains("风格未激活")
                && error.Message.Contains("数据已变化"), "409 download surfaces the structured blockers");
        }
        Require(File.ReadAllText(target) == "previous-export", "failed download leaves the previous file untouched");
        Require(Directory.GetFiles(directory, "*.download").Length == 0, "failed download leaves no partial temp file");

        using (var api = new ApiClient("http://127.0.0.1:12345", new Handler(_ =>
            Task.FromResult(new HttpResponseMessage(HttpStatusCode.OK) { Content = new StreamContent(new FailingStream()) }))))
        {
            var error = await Capture(() => api.SaveDownloadAsync("pages/pg-1/export.png", target));
            Require(error is not null, "mid-stream network drop must propagate");
        }
        Require(File.ReadAllText(target) == "previous-export", "interrupted download leaves the previous file untouched");
        Require(Directory.GetFiles(directory, "*.download").Length == 0, "interrupted download cleans its temp file");

        var payload = new byte[] { 1, 2, 3, 4, 5, 6, 7, 8, 9 };
        using (var api = new ApiClient("http://127.0.0.1:12345", new Handler(_ =>
            Task.FromResult(new HttpResponseMessage(HttpStatusCode.OK) { Content = new ByteArrayContent(payload) }))))
            await api.SaveDownloadAsync("pages/pg-1/export.png", target);
        Require(File.ReadAllBytes(target).SequenceEqual(payload), "successful download streams the exact payload");
        Require(Directory.GetFiles(directory, "*.download").Length == 0, "successful download leaves no temp file");
    }

    private sealed class FailingStream : Stream
    {
        private int reads;

        public override async ValueTask<int> ReadAsync(Memory<byte> buffer, CancellationToken token = default)
        {
            await Task.Yield();
            if (reads++ == 0)
            {
                "partial"u8.CopyTo(buffer.Span);
                return "partial"u8.Length;
            }
            throw new IOException("connection reset by peer");
        }

        public override int Read(byte[] buffer, int offset, int count) => ReadAsync(buffer.AsMemory(offset, count)).AsTask().GetAwaiter().GetResult();
        public override bool CanRead => true;
        public override bool CanSeek => false;
        public override bool CanWrite => false;
        public override long Length => throw new NotSupportedException();
        public override long Position { get => throw new NotSupportedException(); set => throw new NotSupportedException(); }
        public override void Flush() { }
        public override long Seek(long offset, SeekOrigin origin) => throw new NotSupportedException();
        public override void SetLength(long value) => throw new NotSupportedException();
        public override void Write(byte[] buffer, int offset, int count) => throw new NotSupportedException();
    }

    // ---------- 4. 用量预算横幅 + 按模型明细 ----------

    private static void UsageBudgetChecks()
    {
        Require(UsageView.ParseBudget("{\"currency\":\"CNY\",\"amount\":\"120\"}") is { } parsed
            && parsed.Currency == "CNY" && Math.Abs(parsed.Amount - 120) < 0.001, "valid budget round-trips (web mangaflow.usage-budget shape)");
        Require(UsageView.ParseBudget("{\"currency\":\"cny\",\"amount\":\"120\"}") is null, "lowercase currency rejected");
        Require(UsageView.ParseBudget("{\"currency\":\"CNY\",\"amount\":\"0\"}") is null, "zero amount rejected");
        Require(UsageView.ParseBudget("{\"currency\":\"CNY\",\"amount\":\"-5\"}") is null, "negative amount rejected");
        Require(UsageView.ParseBudget("{\"currency\":\"CNY\",\"amount\":\"abc\"}") is null, "non-numeric amount rejected");
        Require(UsageView.ParseBudget("not-json") is null && UsageView.ParseBudget("") is null, "corrupt/empty budget ignored");

        Require(UsageView.BudgetStatus(null, 0) is var idle && idle.Tone == "idle" && idle.Text.Contains("尚未设置预算提醒"),
            "no budget shows the idle banner");
        Require(UsageView.BudgetStatus(new("CNY", 100), 0).Text.Contains("无法对比预算"), "no spend in the budget currency stays idle");
        Require(UsageView.BudgetStatus(new("CNY", 100), 150) is var over && over.Tone == "over" && over.Text.Contains("已超出预算") && over.Text.Contains("¥150.00"),
            "over-budget banner (ratio > 1)");
        Require(UsageView.BudgetStatus(new("CNY", 100), 90) is var near && near.Tone == "near" && near.Text.Contains("已接近预算"),
            "near-budget banner (ratio >= 0.8)");
        Require(UsageView.BudgetStatus(new("CNY", 100), 50) is var ok && ok.Tone == "ok" && ok.Text.Contains("在预算") && ok.Text.Contains("内"),
            "within-budget banner（文案为「在预算 {金额} 内」，金额插在中间）");

        using var estimated = JsonDocument.Parse("{\"estimated_costs\":[{\"currency\":\"CNY\",\"amount\":\"1\"}]}");
        using var usageOnly = JsonDocument.Parse("{\"input_tokens\":5}");
        using var unknown = JsonDocument.Parse("{}");
        Require(UsageView.GroupCostMode(estimated.RootElement) == "ESTIMATED"
            && UsageView.GroupCostMode(usageOnly.RootElement) == "USAGE_ONLY"
            && UsageView.GroupCostMode(unknown.RootElement) == "UNKNOWN", "group cost mode follows web groupCostMode");
    }

    private static void UsageRenderChecks()
    {
        // 预算 50 CNY，HTTP_API 行估算 60 CNY → over；同一行两组成本语义不同 → 混合。
        KeyValueStore.Set("mangaflow.usage-budget", "{\"currency\":\"CNY\",\"amount\":\"50\"}");
        const string summary = """
            {"groups":[
             {"day":"2026-09-08","provider":"示例供应商","model_id":"gemini-2.5-pro","channel":"HTTP_API",
              "attempt_count":5,"succeeded_count":4,"failed_count":1,"pending_count":0,
              "input_tokens":1200,"output_tokens":800,"cached_input_tokens":null,"output_images":2,
              "usage_status_counts":{"COMPLETE":4},
              "estimated_costs":[{"currency":"CNY","amount":"60.00"}]},
             {"day":"2026-09-09","provider":"示例供应商","model_id":"gemini-2.5-pro","channel":"HTTP_API",
              "attempt_count":2,"succeeded_count":2,"failed_count":0,"pending_count":0,
              "input_tokens":300,"output_tokens":null,"cached_input_tokens":null,"output_images":null,
              "usage_status_counts":{},
              "estimated_costs":[]},
             {"day":"2026-09-09","provider":"示例供应商","model_id":"gemini-2.5-pro","channel":"CLI",
              "attempt_count":1,"succeeded_count":0,"failed_count":1,"pending_count":0,
              "input_tokens":null,"output_tokens":null,"cached_input_tokens":null,"output_images":null,
              "usage_status_counts":{},
              "estimated_costs":[]}],
            "billed":[]}
            """;
        var view = new UsageView();
        typeof(UsageView).GetField("summary", BindingFlags.Instance | BindingFlags.NonPublic)!.SetValue(view, JsonSerializer.Deserialize<JsonElement>(summary));
        typeof(UsageView).GetMethod("RenderSummary", BindingFlags.Instance | BindingFlags.NonPublic)!.Invoke(view, null);

        var banner = Descendants(Field<StackPanel>(view, "budgetHost")).OfType<Border>()
            .SingleOrDefault(border => border.Tag as string is { Length: > 0 });
        var hostDump = string.Join(",", Field<StackPanel>(view, "budgetHost").Children.Cast<System.Windows.UIElement>().Select(c => c.GetType().Name + (c is Border b ? "[" + (b.Tag as string ?? "-") + "]" : "")));
        var board = Field<JsonElement>(view, "summary");
        var groupsInfo = $"{board.ValueKind}/{board.Array("groups").Count}/{(board.Array("groups").Count > 0 ? board.Array("groups")[0].Array("estimated_costs").Count : -1)}";
        Require(banner?.Tag as string == "over", $"budget banner tone follows the over state（banner={(banner?.Tag as string ?? "null")}，summary={groupsInfo}，budgetHost=[{hostDump}]，budgetRaw={KeyValueStore.Get("mangaflow.usage-budget")}）");
        var bannerText = Text(banner!);
        Require(bannerText.Contains("已超出预算") && bannerText.Contains("¥60.00") && bannerText.Contains("仅对比估算支出，不含账单事实"),
            "budget banner shows spent vs budget with the billed-facts note");

        var breakdown = Text(Field<StackPanel>(view, "breakdownHost"));
        Require(breakdown.Contains("示例供应商 · gemini-2.5-pro") && breakdown.Contains("HTTP API") && breakdown.Contains("CLI"),
            "breakdown aggregates to provider/model/channel rows");
        Require(breakdown.Contains("6 / 1 / 0") && breakdown.Contains("0 / 1 / 0"),
            "breakdown carries call and outcome counts（HTTP 行 4+2 成功/1 失败，CLI 行 0 成功/1 失败）");
        Require(breakdown.Contains("1500 / 800"), "breakdown sums present tokens (1200 + 300)");
        Require(breakdown.Contains("未知") && !breakdown.Contains("0 / 0") && breakdown.Contains("无估算数据") && breakdown.Contains("成本未知"),
            "breakdown keeps unknown quantities as unknown, never 0（两空 token 列与网页同为「未知」）");
        Require(breakdown.Contains("≈ ¥60.0000"), "breakdown shows per-currency estimated amounts");
        Require(breakdown.Contains("混合") && breakdown.Contains("成本未知"),
            "breakdown labels mixed and single cost modes（HTTP 行两语义→混合，CLI 行无数据→成本未知）");
        Require(breakdown.Contains("不同币种永不相加"), "breakdown keeps the never-add-currencies footnote");

        KeyValueStore.Remove("mangaflow.usage-budget");
        typeof(UsageView).GetMethod("RenderSummary", BindingFlags.Instance | BindingFlags.NonPublic)!.Invoke(view, null);
        var idleBanner = Descendants(Field<StackPanel>(view, "budgetHost")).OfType<Border>()
            .SingleOrDefault(border => border.Tag as string is { Length: > 0 });
        Require(idleBanner?.Tag as string == "idle" && Text(idleBanner!).Contains("尚未设置预算提醒"),
            "cleared budget returns to the idle banner");
    }

    // ---------- 6. 409 结构化 blockers 透出 ----------

    private static async Task BlockerDetailChecks()
    {
        using (var api = new ApiClient("http://127.0.0.1:12345", new Handler(_ =>
            Task.FromResult(new HttpResponseMessage(HttpStatusCode.Conflict)
            { Content = new StringContent("{\"detail\":{\"code\":\"SELECT_BLOCKED\",\"message\":\"候选未全部达成采用标准\",\"blockers\":[{\"code\":\"X\",\"message\":\"第 3 页风格未激活\"},{\"message\":\"人物参考图缺失\"}]}}") }))))
        {
            var error = await Capture(() => api.SendAsync("pages/pg/jobs/jb/select-candidate", HttpMethod.Post, new { version = 1 }));
            Require(error is InvalidOperationException
                && error.Message.Contains("候选未全部达成采用标准")
                && error.Message.Contains("第 3 页风格未激活") && error.Message.Contains("人物参考图缺失")
                && error.Message.Contains("；"), "409 keeps the message and every blocker entry");
        }
        using (var api = new ApiClient("http://127.0.0.1:12345", new Handler(_ =>
            Task.FromResult(new HttpResponseMessage(HttpStatusCode.Conflict)
            { Content = new StringContent("{\"detail\":{\"blockers\":[{\"message\":\"仅剩一条阻塞\"}]}}") }))))
        {
            var error = await Capture(() => api.SendAsync("assets/a/adopt", HttpMethod.Post));
            Require(error is InvalidOperationException && error.Message.Contains("仅剩一条阻塞"),
                "409 without a header message still surfaces blockers");
        }
        using (var api = new ApiClient("http://127.0.0.1:12345", new Handler(_ =>
            Task.FromResult(new HttpResponseMessage(HttpStatusCode.NotFound) { Content = new StringContent("{\"detail\":\"未找到该资产\"}") }))))
        {
            var error = await Capture(() => api.SendAsync("assets/missing"));
            Require(error is InvalidOperationException && error.Message.Contains("未找到该资产") && !error.Message.Contains("数据已变化"),
                "non-409 detail strings pass through without the conflict prefix");
        }
    }

    // ---------- 7. 深链：路由 + 资产预选 + 上传用途 + 分镜定位 ----------

    private static void DeepLinkRouteChecks()
    {
        var route = GenerateView.RouteForBlocker(Blocker("MISSING_OUTFIT_ASSIGNMENT", "char-9", "STORYBOARD"), "pg-7");
        Require(route.Section == "storyboard" && route.Query.Contains("page=pg-7") && route.Query.Contains("character=char-9"),
            "outfit-assignment blocker deep link carries page and character (web routeForBlocker)");
        route = GenerateView.RouteForBlocker(Blocker("MISSING_CHARACTER_REFERENCE", "char-9", "ASSETS"), "pg-7");
        Require(route.Section == "assets" && route.Query.Contains("view=characters") && route.Query.Contains("character=char-9"),
            "character blocker preselects the characters view");
        route = GenerateView.RouteForBlocker(Blocker("MISSING_OUTFIT_REFERENCE", "outfit-9", "ASSETS"), "pg-7");
        Require(route.Section == "assets" && route.Query.Contains("view=outfits") && route.Query.Contains("outfit=outfit-9"),
            "outfit blocker preselects the outfits view");
        route = GenerateView.RouteForBlocker(Blocker("STYLE_NOT_ACTIVE", "style-9", "STYLE"), "pg-7");
        Require(route.Section == "assets" && route.Query.Contains("view=style") && route.Query.Contains("style=style-9"),
            "style blockers focus the style view");
        Require(GenerateView.RouteForBlocker(Blocker("SOURCE_INCOMPLETE", null, "SOURCE"), "pg-7").Section == "source"
            && GenerateView.RouteForBlocker(Blocker("PROVIDER_UNUSABLE", null, "PROVIDER"), "pg-7").Section == "settings-global"
            && GenerateView.RouteForBlocker(Blocker("WEIRD", null, "SOMEWHERE"), "pg-7").Section == "generate",
            "stage fallback mirrors the web stageRoutes map");
    }

    private static JsonElement Blocker(string code, string? target, string stage)
    {
        using var parsed = JsonDocument.Parse(
            $"{{\"code\":\"{code}\",\"target_id\":{(target is null ? "null" : $"\"{target}\"")},\"stage\":\"{stage}\",\"message\":\"阻塞\"}}");
        return JsonSerializer.Deserialize<JsonElement>(parsed.RootElement.GetRawText());
    }

    private static async Task AssetsDeepLinkChecks()
    {
        var fixture = new AssetsFixture();
        using var api = new ApiClient("http://127.0.0.1:12345", fixture);
        var view = new AssetsView();
        view.Activate(Context(api, "consistency"));
        await Until(() => Field<Grid>(view, "host").Children.OfType<CharactersPane>().Any());

        view.ApplyDeepLink("char-b", null, null);
        await Until(() => view.SelectedCharacter?.Id == "char-b");
        Require(view.SelectedCharacter?.PrimaryName == "小狼", "character deep link preselects the entity once its row loads");

        view.ApplyDeepLink(null, "outfit-b", null);
        await Until(() => view.SelectedOutfit?.Id == "outfit-b");
        Require(Field<string>(view, "current") == AssetsView.Outfits, "outfit deep link switches to the outfits workspace");

        var before = view.SelectedCharacter?.Id;
        view.ApplyDeepLink("missing-character", null, null);
        await Until(() => fixture.CharacterLists >= 4);
        Require(view.SelectedCharacter?.Id == before, "unknown deep link ids fall back to the plain view");
        view.Deactivate();
    }

    private static async Task UploadPurposeChecks(string output)
    {
        var fixture = new AssetsFixture();
        using var api = new ApiClient("http://127.0.0.1:12345", fixture);
        var view = new AssetsView();
        view.Activate(Context(api, "consistency"));
        await Until(() => Field<Grid>(view, "host").Children.OfType<CharactersPane>().Any());
        // 选中角色后上传：kind 仍由显式选择决定，角色只决定 CHARACTER_REFERENCE 是否绑定。
        view.SelectedCharacter = CharacterItem.From(JsonSerializer.Deserialize<JsonElement>(
            "{\"id\":\"char-a\",\"primary_name\":\"小樱\",\"aliases\":[],\"locked_features\":[],\"forbidden_changes\":[],\"references\":[]}"));
        view.Switch(AssetsView.References);
        await Until(() => Field<Grid>(view, "host").Children.OfType<ReferencesPane>().Any());
        var pane = Field<Grid>(view, "host").Children.OfType<ReferencesPane>().Single();

        var chips = Descendants(pane).OfType<ToggleButton>().Where(b => b.Tag as string is { Length: > 0 }).ToList();
        Require(chips.Select(c => c.Content as string).SequenceEqual(new[] { "人物参考", "服装参考", "漫画风格", "场景参考" }),
            "upload intake offers the four explicit AssetPurpose choices (web assetKind)");
        Require(chips.Single(c => c.Tag as string == "CHARACTER_REFERENCE").IsChecked == true,
            "CHARACTER_REFERENCE stays the default purpose (web default assetKind)");

        var image = Path.Combine(output, "upload-purpose.png");
        File.WriteAllBytes(image, new byte[] { 0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A, 1, 2, 3 });
        try
        {
            await Invoke(pane, "Upload", image);
            // HttpClient 的 MultipartFormDataContent 对简单 token 输出 name=kind（不带引号，
            // 浏览器表单才带引号），断言只钉 kind 字段与值确实进入 multipart。
            Require(fixture.Uploads.Count >= 1 && fixture.Uploads[^1].Contains("CHARACTER_REFERENCE")
                && fixture.Uploads[^1].Contains("kind"), "default purpose uploads as CHARACTER_REFERENCE");
            Require(fixture.BindPosts == 1, "CHARACTER_REFERENCE upload binds to the selected character (web mutationFn)");

            Click(chips.Single(c => c.Tag as string == "SCENE_REFERENCE"));
            await Invoke(pane, "Upload", image);
            Require(fixture.Uploads.Count >= 2 && fixture.Uploads[^1].Contains("SCENE_REFERENCE"),
                "explicit scene purpose wins over the selected character (kind is never guessed)");
            Require(fixture.BindPosts == 1, "non-character purposes never post a character binding");
        }
        finally { File.Delete(image); }
        view.Deactivate();
    }

    private static async Task StoryboardDeepLinkChecks()
    {
        var fixture = new StoryboardFixture();
        using var api = new ApiClient("http://127.0.0.1:12345", fixture);
        var view = new StoryboardView();
        view.Activate(Context(api, "consistency"));
        await Until(() => view.PanelCountForTest == 2);

        await view.LocatePageAsync("pg-1", "c1");
        var selected = Field<object?>(view, "selected");
        Require(selected is not null && (string)selected.GetType().GetProperty("Id")!.GetValue(selected)! == "panel-1",
            "outfit deep link focuses the first VISIBLE panel without an outfit");
        Require(Field<TextBlock>(view, "statusLine").Text.Contains("已定位到缺少服装的出镜格"),
            "focused panel points the user at the outfit picker (web notice)");

        await view.LocatePageAsync("pg-1", "c2");   // c2 只有已绑定服装的出镜格
        selected = Field<object?>(view, "selected");
        Require(selected is not null && (string)selected.GetType().GetProperty("Id")!.GetValue(selected)! == "panel-1",
            "character with outfits everywhere does not steal the selection");

        await view.LocatePageAsync("pg-elsewhere");  // 未知页：GET pages/{id} 归属本章后正常回落
        view.Deactivate();
    }

    private sealed class AssetsFixture : HttpMessageHandler
    {
        public int CharacterLists;
        public int BindPosts;
        public List<string> Uploads = [];

        protected override async Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken token)
        {
            var path = request.RequestUri!.AbsolutePath;
            if (request.Method == HttpMethod.Post && path.EndsWith("/assets/upload"))
            {
                Uploads.Add(await request.Content!.ReadAsStringAsync(token));
                var purpose = ((MultipartFormDataContent)request.Content).Single(p => p.Headers.ContentDisposition?.Name?.Trim('"') == "kind");
                return Json(JsonSerializer.Serialize(new { id = "asset-9", kind = await purpose.ReadAsStringAsync(token), status = "READY", display_name = "ref.png", original_name = "ref.png" }));
            }
            if (request.Method == HttpMethod.Post && path.EndsWith("/characters/char-a/references"))
            {
                BindPosts++;
                return Json("{}");
            }
            if (request.Method == HttpMethod.Get)
            {
                if (path.EndsWith("/projects/consistency/characters"))
                {
                    CharacterLists++;
                    return Json("[{\"id\":\"char-a\",\"primary_name\":\"小樱\",\"aliases\":[],\"locked_features\":[],\"forbidden_changes\":[],\"references\":[]},"
                        + "{\"id\":\"char-b\",\"primary_name\":\"小狼\",\"aliases\":[],\"locked_features\":[],\"forbidden_changes\":[],\"references\":[]}]");
                }
                if (path.EndsWith("/projects/consistency/outfits"))
                    return Json("[{\"id\":\"outfit-b\",\"character_id\":\"char-b\",\"name\":\"雨夜外套\",\"reference_asset_ids\":[],\"locked_fields\":[]}]");
            }
            return Json("[]");
        }
    }

    private sealed class StoryboardFixture : HttpMessageHandler
    {
        protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken token)
        {
            var path = request.RequestUri!.AbsolutePath;
            if (request.Method == HttpMethod.Get)
            {
                if (path.EndsWith("/chapters/ch-1/pages"))
                    return Task.FromResult(Json("[{\"id\":\"pg-1\",\"chapter_id\":\"ch-1\",\"page_number\":3,\"panel_count\":2,\"storyboard_version\":1,\"status\":\"\",\"selected_candidate_id\":\"\",\"continuity_status\":\"\"}]"));
                if (path.EndsWith("/pages/pg-1/storyboard"))
                    return Task.FromResult(Json(Storyboard()));
                if (path.EndsWith("/projects/consistency/chapters"))
                    return Task.FromResult(Json("[{\"id\":\"ch-1\",\"title\":\"第一章\",\"ordinal\":1,\"page_count\":1}]"));
                if (path.EndsWith("/pages/pg-elsewhere"))
                    return Task.FromResult(Json("{\"id\":\"pg-elsewhere\",\"chapter_id\":\"ch-1\"}"));
                if (path.EndsWith("/projects/consistency/characters"))
                    return Task.FromResult(Json("[{\"id\":\"c1\",\"primary_name\":\"樱\",\"aliases\":[],\"locked_features\":[],\"forbidden_changes\":[],\"references\":[]},"
                        + "{\"id\":\"c2\",\"primary_name\":\"狼\",\"aliases\":[],\"locked_features\":[],\"forbidden_changes\":[],\"references\":[]}]"));
                if (path.EndsWith("/projects/consistency/outfits"))
                    return Task.FromResult(Json("[{\"id\":\"outfit-1\",\"character_id\":\"c1\",\"name\":\"默认\",\"reference_asset_ids\":[],\"locked_fields\":[]}]"));
            }
            return Task.FromResult(Json("[]"));
        }

        private static string Storyboard() => """
            {"page":{"id":"pg-1","chapter_id":"ch-1","page_number":3,"storyboard_version":7,
              "canvas":{"width_mm":182,"height_mm":257,"bleed_mm":3,"safe_mm":5},"status":""},
             "panels":[
              {"id":"panel-1","page_id":"pg-1","reading_order":1,"version":4,
               "bounds":{"x":0.1,"y":0.1,"width":0.5,"height":0.4},
               "geometry":{"type":"rect","rect":{"x":0.1,"y":0.1,"width":0.5,"height":0.4},"rotation":0,"z_order":1},
               "shot_type":"medium_close_up","camera_angle":"eye_level","bleed":false,"borderless":false,
               "actions":{"script_action":"雨夜对望"},"background":"","props":[],"sound_effects":[],
               "characters":["c1"],"character_presence":{"c1":"VISIBLE"},"expressions":{},"outfits":{},
               "dialogues":[]},
              {"id":"panel-2","page_id":"pg-1","reading_order":2,"version":3,
               "bounds":{"x":0.1,"y":0.55,"width":0.5,"height":0.4},
               "geometry":{"type":"rect","rect":{"x":0.1,"y":0.55,"width":0.5,"height":0.4},"rotation":0,"z_order":2},
               "shot_type":"long_shot","camera_angle":"eye_level","bleed":false,"borderless":false,
               "actions":{"script_action":"并肩而行"},"background":"","props":[],"sound_effects":[],
               "characters":["c1","c2"],"character_presence":{"c1":"VISIBLE","c2":"VISIBLE"},"expressions":{},
               "outfits":{"c1":"outfit-1","c2":"outfit-2"},"dialogues":[]}],
             "candidate_count":0}
            """;
    }

    // ---------- helpers (NativeJobsChecks / NativeStoryboardEditChecks 模式) ----------

    private static WorkspaceContext Context(ApiClient api, string project) => new()
    {
        Api = api, Cache = new ApiCache(), State = new WorkspaceState(), Window = null!,
        Project = new ProjectItem(project, project, "", 0, 0),
        NavigateSection = (_, _) => Task.CompletedTask, OpenDashboard = () => Task.CompletedTask,
    };

    private static async Task<Exception?> Capture(Func<Task> action)
    {
        try { await action(); return null; }
        catch (Exception error) { return error; }
    }

    private static async Task Invoke(object target, string name, params object?[] args) =>
        await (Task)target.GetType().GetMethod(name, BindingFlags.Instance | BindingFlags.NonPublic)!.Invoke(target, args)!;

    private static void Click(ToggleButton button) => button.RaiseEvent(new RoutedEventArgs(ButtonBase.ClickEvent));

    private static IEnumerable<DependencyObject> Descendants(DependencyObject root)
    {
        yield return root;
        foreach (var child in LogicalTreeHelper.GetChildren(root).OfType<DependencyObject>())
            foreach (var nested in Descendants(child)) yield return nested;
    }

    private static string Text(DependencyObject root) => string.Join("\n", Descendants(root).OfType<TextBlock>().Select(t => t.Text));

    private static T Field<T>(object target, string name) => (T)target.GetType().GetField(name, BindingFlags.Instance | BindingFlags.NonPublic)!.GetValue(target)!;

    private static void Require(bool value, string message) { if (!value) throw new Exception(message); }

    private static async Task Until(Func<bool> condition)
    {
        using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(10));
        while (!condition()) await Task.Delay(5, timeout.Token);
    }

    private static HttpResponseMessage Json(string body) => new(HttpStatusCode.OK) { Content = new StringContent(body) };

    private sealed class Handler(Func<HttpRequestMessage, Task<HttpResponseMessage>> respond) : HttpMessageHandler
    {
        protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken token) => respond(request);
    }
}
