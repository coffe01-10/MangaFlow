using System.IO;
using System.Net;
using System.Net.Http;
using System.Reflection;
using System.Text.Json;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Controls.Primitives;
using MangaFlow.Native;
using MangaFlow.Native.Services;
using MangaFlow.Native.Views;

// PUB-01A/01C + DIR-01B/01C 桌面同步回归：
// ① StripSlices 条带切片几何与 web computeStrip / 后端 compute_slices 同语义
//    （页间优先收刀、超限单页等距硬切、片序号稳定、缺尺寸回退 4:3）。
// ② 素材库 WEBTOON 导出：请求体 export_type=WEBTOON、列表标签「条漫 ZIP」、
//    下载扩展名 .zip；DirectorScope.Dialogue 的 panel/dialogue id 不互换。
// ③ 导演台 AI 解析：utterance POST 契约（page_id/storyboard_version/selection/
//    client_request_id）、PARSING 轮询到终态、多命令预览逐条确认（accept 后
//    原地刷新）、NEEDS_CLARIFICATION/STALE/PARSE_FAILED 三种终态各自落面。
// ④ 手机预览窗口：就绪页按宽高比缩放排布、分片边界与单页硬切标记、
//    未达标页阻塞卡与导出门禁（预览只读，导出按钮仅在全达标时可用）。
//
// 【注册】NativeInteractionChecks.RunIsolated 的 await 链已追加本检查。
internal static class NativePubDirSyncChecks
{
    private const BindingFlags All = BindingFlags.Instance | BindingFlags.NonPublic | BindingFlags.Public;

    public static async Task Run()
    {
        if (Application.Current is null) await RunOnDedicatedStaThread();
        else await RunFrame();
    }

    private static Task RunOnDedicatedStaThread()
    {
        Exception? failure = null;
        var thread = new Thread(() =>
        {
            try
            {
                var app = new Application { ShutdownMode = ShutdownMode.OnExplicitShutdown };
                app.Resources.MergedDictionaries.Add(new ResourceDictionary { Source = new Uri("/MangaFlow.Native;component/Theme.xaml", UriKind.Relative) });
                app.Dispatcher.BeginInvoke(new Action(async () =>
                {
                    try { await RunFrame(); }
                    catch (Exception error) { failure = error; }
                    finally { app.Shutdown(); }
                }));
                app.Run();
            }
            catch (Exception error) { failure = error; }
        });
        thread.SetApartmentState(ApartmentState.STA);
        thread.Start();
        thread.Join();
        if (failure != null) throw new Exception("NativePubDirSyncChecks failed", failure);
        return Task.CompletedTask;
    }

    private static async Task RunFrame()
    {
        var store = Path.Combine(Path.GetTempPath(), "mangaflow-pubdir-store-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(store);
        var prefs = Path.Combine(store, "prefs.json");
        File.WriteAllText(prefs, "{}");
        var previousStore = (string)typeof(KeyValueStore).GetField("path", BindingFlags.Static | BindingFlags.NonPublic)!.GetValue(null)!;
        KeyValueStore.UseLocation(prefs);
        try
        {
            StripSlicesMatchContract();
            Console.WriteLine("PASS: 条带切片几何与 web/后端同语义——页间优先、超限硬切、序号稳定");
            ScopeDialogueIdsNotSwapped();
            Console.WriteLine("PASS: DirectorScope.Dialogue 的 panel/dialogue id 不再互换");
            await LibraryWebtoonExport();
            Console.WriteLine("PASS: 素材库 WEBTOON 导出请求体、条漫 ZIP 标签与 .zip 下载后缀");
            await DirectorNlParse();
            Console.WriteLine("PASS: 导演台 AI 解析——utterance 契约、PARSING 轮询、逐条确认、澄清/过期/失败态");
            await MobilePreviewWindowChecks();
            Console.WriteLine("PASS: 手机预览窗口——缩放排布、分片边界/硬切标记、未达标页诊断与导出门禁");
            await DirectorConfirmedLeaveRebuilds();
            Console.WriteLine("PASS: 导演台确认弃稿切章后重建面板——旧面板引用不得卡死渲染门");
        }
        finally
        {
            KeyValueStore.UseLocation(previousStore);
            try { Directory.Delete(store, true); } catch (IOException) { } catch (UnauthorizedAccessException) { }
        }
    }

    // ── ① StripSlices 契约 ──
    private static void StripSlicesMatchContract()
    {
        // 页间优先：p1@0..1000 间隙16 → p2@1016..2016 → p3@2032..2532（总高 2532）。
        // 1600 片高：p2 装不下时在 p2 顶边收刀 → [0,1016){p1} + [1016,2532){p2,p3}。
        var strip = StripSlices.ComputeStrip([("p1", 1000.0), ("p2", 1000.0), ("p3", 500.0)], 16, 1600);
        Require(strip.TotalHeight == 2532, "条带总高未含页间距");
        Require(strip.Items[1].Top == 1016, "第二页顶边应跳过页间距");
        Require(strip.Slices.Count == 2, "应在页间缝收刀成两片");
        Require(strip.Slices[0].Start == 0 && strip.Slices[0].End == 1016
            && strip.Slices[0].ItemIds.SequenceEqual(new[] { "p1" }), "首片应止于下一页顶边且只含 p1");
        Require(strip.Slices[1].Start == 1016 && strip.Slices[1].End == 2532
            && strip.Slices[1].ItemIds.SequenceEqual(new[] { "p2", "p3" }), "末片应收尾到条带底");
        Require(strip.Slices.All(s => !s.Oversized), "常规页不得标记硬切");

        // 超限硬切：a@0..500 间隙10 → b@510..3510 间隙10 → c@3520..4020。
        // [0,510){a} + b 等距硬切三片 + [3510,4020){c}，共 5 片。
        var hard = StripSlices.ComputeStrip([("a", 500.0), ("b", 3000.0), ("c", 500.0)], 10, 1000);
        Require(hard.Slices.Count == 5, "超限单页应等距硬切");
        var cut = hard.Slices.Where(s => s.Oversized).ToList();
        Require(cut.Count == 3 && cut.All(s => s.End - s.Start == 1000 && s.ItemIds.Single() == "b"),
            "硬切片必须每片 ≤ 片高上限且属于同一页");
        Require(hard.Slices[0].End == 510 && hard.Slices[4].Start == 3510 && hard.Slices[4].End == 4020,
            "硬切前后普通页的片边界应落在页间缝");

        // 空输入与缺尺寸回退（4:3 竖版占位）。
        Require(StripSlices.ComputeStrip([], 16, 1000).Slices.Count == 0, "空条带不得产生切片");
        Require(StripSlices.ScaledPageHeight(2000, 3000, 1080) == 1620, "缩放高度未按宽高比换算");
        Require(StripSlices.ScaledPageHeight(null, null, 1080) == 1440, "缺尺寸页应回退 4:3 占位高");
    }

    private static void ScopeDialogueIdsNotSwapped()
    {
        var scope = DirectorScope.Dialogue("panel-x", "dlg-y");
        Require(scope.PanelId == "panel-x" && scope.DialogueId == "dlg-y",
            "Dialogue 作用域的两个 id 不得互换（NL selection 负载与规则解析都依赖正确字段）");
    }

    // ── ② 素材库 WEBTOON ──
    private static async Task LibraryWebtoonExport()
    {
        var exportBodies = new List<JsonElement>();
        using var api = new ApiClient("http://127.0.0.1:12345", new Handler(async request =>
        {
            var path = request.RequestUri!.AbsolutePath;
            if (request.Method == HttpMethod.Post && path.EndsWith("/exports"))
            {
                exportBodies.Add(JsonSerializer.Deserialize<JsonElement>(await request.Content!.ReadAsStringAsync()));
                return Response("""{"id":"exp-webtoon"}""");
            }
            if (request.Method != HttpMethod.Get) return Response("{}");
            if (path.EndsWith("/chapters")) return Response("""[{"id":"ch-1","title":"第一章","ordinal":1,"page_count":2}]""");
            if (path.EndsWith("/characters")) return Response("[]");
            if (path.EndsWith("/models")) return Response("[]");
            if (path.EndsWith("/production-readiness"))
                return Response("""{"ready":true,"ready_pages":2,"total_pages":2,"pages":[]}""");
            if (path.EndsWith("/exports"))
                return Response("""[{"id":"exp-webtoon","export_type":"WEBTOON","byte_size":4096,"page_count":2,"download_url":"/api/v1/exports/exp-webtoon/download"}]""");
            if (path.EndsWith("/library"))
                return Response("""{"groups":[],"total_candidates":0,"favorite_count":0,"next_cursor":null}""");
            return Response("{}");
        }));
        Require(LibraryView.ExportExtension("WEBTOON") == ".zip" && LibraryView.ExportExtension("PNG") == ".zip"
            && LibraryView.ExportExtension("PDF") == ".pdf", "WEBTOON 下载后缀必须是 .zip");
        Require(LibraryView.ExportLabel("WEBTOON") == "条漫 ZIP" && LibraryView.ExportLabel("PNG") == "原图 ZIP",
            "导出列表的 WEBTOON 标签必须是「条漫 ZIP」");
        var view = new LibraryView();
        view.Activate(Context(api, "p1"));
        try
        {
            await Until(() => Field<string?>(view, "readyChapter") == "ch-1");
            // 导出列表渲染出条漫 ZIP 行。
            var buttons = Descendants(view).OfType<Button>().Select(b => b.Content?.ToString() ?? "").ToList();
            Require(buttons.Any(label => label.Contains("条漫 ZIP") && label.Contains("下载")),
                "导出记录里的 WEBTOON 应渲染为「条漫 ZIP · 下载」按钮");
            await Invoke(view, "ExportChapter", "ch-1", "WEBTOON");
            Require(exportBodies.Count == 1 && exportBodies[0].Text("export_type") == "WEBTOON",
                "条漫包按钮必须提交 export_type=WEBTOON");
        }
        finally { view.Deactivate(); }
    }

    // ── ③ 导演台 AI 解析 ──
    private static async Task DirectorNlParse()
    {
        var fixture = new DirectorParseFixture();
        using var api = new ApiClient("http://127.0.0.1:12345", fixture);
        var view = new GenerateView();
        typeof(WorkspaceView).GetProperty("Context", All)!.SetValue(view, Context(api, "p1"));
        Set(view, "currentPage", new PageItem("page-1", 1) { StoryboardVersion = 7 });
        Set(view, "workbench", JsonDocument.Parse("""
            {"page":{"id":"page-1"},"storyboard":{"panels":[{"id":"panel-1","reading_order":1,"characters":[],"dialogues":[{"id":"dlg-1","reading_order":1}]}]}}
            """).RootElement.Clone());
        var pane = new DirectorPane(view);
        var input = Field<TextBox>(pane, "commandInput");
        var preview = Field<StackPanel>(pane, "preview");
        var history = Field<StackPanel>(pane, "history");

        // PARSING → PREVIEWED：两命令逐条确认。
        input.Text = "把格 1 的台词改成「不要走」，格 1 改成近景";
        Set(pane, "selection", DirectorScope.Panel("panel-1"));
        await Invoke(pane, "ParseAsync");
        Require(fixture.Utterances == 1, "AI 解析必须 POST utterance");
        var body = fixture.UtteranceBody;
        Require(body.Text("utterance") == "把格 1 的台词改成「不要走」，格 1 改成近景"
            && body.Text("page_id") == "page-1" && body.Number("storyboard_version") == 7
            && body.Text("client_request_id").Length > 0
            && body.Element("selection").Text("kind") == "panel"
            && body.Element("selection").Text("panel_id") == "panel-1",
            "utterance 负载必须携带指令/页锚/分镜版本/作用域/幂等键");
        Require(fixture.PreviewPolls >= 2, "PARSING 组必须轮询到终态才落地预览");
        Require(Texts(preview).Any(t => t.Contains("AI 解析") && t.Contains("gpt-test")),
            "预览卡应标明 AI 解析与文本模型");
        Require(Texts(preview).Any(t => t.Contains("逐条确认")), "多命令组应提示逐条确认");
        Require(Texts(preview).Any(t => t.Contains("台词") && t.Contains("旧") && t.Contains("新")),
            "预览卡应渲染 diff 的前后值");
        var confirms = Buttons(preview, "确认执行").ToList();
        Require(confirms.Count == 2, "每条 PREVIEWED 命令都要有独立确认按钮");

        Click(confirms[0]);
        await Until(() => fixture.Accepts == 1 && !pane.Busy);
        await Settle();
        Require(fixture.LastAcceptPath.EndsWith("/director/commands/cmd-1/accept"), "确认必须打到对应命令");
        Require(Texts(preview).Any(t => t.Contains("已执行")), "accept 后预览应原地刷新 EXECUTED 态");
        Require(Buttons(preview, "确认执行").Count() == 1, "剩余 PREVIEWED 命令仍待确认");
        Require(pane.HasDraft, "预览未关闭时草稿判定必须保持（渲染门继续守住面板）");

        // NEEDS_CLARIFICATION：原因 + 可点目标项 + 禁用提示项。
        fixture.Mode = "clarify";
        input.Text = "把它改一下";
        await Invoke(pane, "ParseAsync");
        Require(Texts(preview).Any(t => t.Contains("指代不明")), "澄清态必须展示 first_result.reason");
        var chips = Descendants(preview).OfType<Button>().ToList();
        var target = chips.FirstOrDefault(b => b.Content as string == "格 1");
        var hint = chips.FirstOrDefault(b => b.Content as string == "数值提示项");
        Require(target is { IsEnabled: true }, "可定位的澄清目标必须可点选");
        Require(hint is { IsEnabled: false }, "非作用域/无 id 的澄清项必须禁用（防误选）");
        // 点选目标设置 selection 供下一次解析携带。
        Click(target!);
        var selection = Field<DirectorScope?>(pane, "selection");
        Require(selection is { Kind: "panel", PanelId: "panel-1" }, "澄清点选应写入 panel 作用域");

        // PARSE_FAILED / STALE 落到各自的阻塞文案。
        fixture.Mode = "failed";
        input.Text = "随便改";
        await Invoke(pane, "ParseAsync");
        Require(Texts(preview).Any(t => t.Contains("模型输出不符合命令结构")), "解析失败必须展示服务端错误");
        fixture.Mode = "stale";
        await Invoke(pane, "ParseAsync");
        Require(Texts(preview).Any(t => t.Contains("分镜已变更")), "版本过期必须展示 STALE 原因");

        // 历史区渲染无命令体的组（AI 指令 + 组级状态 + 原因行）。
        await Invoke(pane, "LoadHistoryAsync");
        var historyTexts = Texts(history).ToList();
        Require(historyTexts.Any(t => t.Contains("AI 指令") && t.Contains("分镜已变更")),
            "历史应渲染无命令组的 STALE 组状态");
        Require(historyTexts.Any(t => t.Contains("分镜已变更，请刷新分镜版本")), "历史应展示 STALE 组的 first_result.reason");
    }

    // ── ④ 手机预览窗口 ──
    private static async Task MobilePreviewWindowChecks()
    {
        var fixture = new MobilePreviewFixture();
        using var api = new ApiClient("http://127.0.0.1:12345", fixture);
        var window = new MobilePreviewWindow(Context(api, "p1"), new ChapterItem("ch-1", "第一章", "") { Ordinal = 1 });
        try
        {
            await Invoke(window, "LoadAsync");
            var strip = Field<Canvas>(window, "strip");
            var summary = Field<TextBlock>(window, "summary");
            var export = Field<Button>(window, "export");
            // 默认 1080 目标宽 / 16 间距 / 4096 片高：p1 1620 + 间隙 + p2 2160 → 单片。
            Require(strip.Children.Count >= 2 && summary.Text == "2 页 · 1 片", "就绪页应按宽高比排入条带");
            Require(export.IsEnabled, "全部达标时导出条漫包必须可用");

            // 收紧片高到 2048：p2 超限 → 页间缝一片 + 硬切两片。
            var sliceBox = Field<ComboBox>(window, "sliceBox");
            sliceBox.SelectedItem = sliceBox.Items.OfType<ComboBoxItem>().Single(i => (int)i.Tag == 2048);
            Require(strip.Children.OfType<System.Windows.Shapes.Line>().Count() == 2, "三片应画出两条分片边界线");
            Require(Texts(strip).Count(t => t.Contains("单页硬切")) == 2, "超限页应逐片标记单页硬切");
            Require(summary.Text == "2 页 · 3 片", "片数统计应与切片模型一致");

            // 未达标页：阻塞卡原位 + 导出禁用。
            fixture.Blocked = true;
            await Invoke(window, "LoadAsync");
            Require(Texts(strip).Any(t => t.Contains("未完成视觉检查")), "未达标页必须原位展示阻塞原因");
            Require(!export.IsEnabled && summary.Text.Contains("未达标"), "存在未达标页时导出必须禁用");
        }
        finally { window.Close(); }
    }

    private sealed class DirectorParseFixture : HttpMessageHandler
    {
        public int Utterances, PreviewPolls, Accepts;
        public string Mode = "preview";
        public string LastAcceptPath = "";
        public JsonElement UtteranceBody;

        protected override async Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken token)
        {
            var path = request.RequestUri!.AbsolutePath;
            if (request.Method == HttpMethod.Post && path.EndsWith("/director/utterances"))
            {
                Utterances++;
                UtteranceBody = JsonSerializer.Deserialize<JsonElement>(await request.Content!.ReadAsStringAsync(token));
                return Response("""{"command_group_id":"grp-nl","status":"PARSING"}""");
            }
            if (request.Method == HttpMethod.Get && path.EndsWith("/director/command-groups/grp-nl"))
                return Response(GroupPayload());
            if (request.Method == HttpMethod.Post && path.Contains("/director/commands/") && path.EndsWith("/accept"))
            {
                Accepts++;
                LastAcceptPath = path;
                return Response("""
                    {"command_group_id":"grp-nl","status":"PREVIEWED","first_result":{"model":{"model_id":"gpt-test"}},"commands":[
                    {"command_id":"cmd-1","status":"EXECUTED","operation":"update_dialogue","target":{"panel_id":"panel-1","dialogue_id":"dlg-1"},"source":{"user_prompt":"两句一起改"},"diff":{"target_text":{"before":"旧","after":"新"}}},
                    {"command_id":"cmd-2","status":"PREVIEWED","operation":"update_panel_shot","target":{"panel_id":"panel-1"},"source":{"user_prompt":"两句一起改"},"diff":{"shot_type":{"before":"medium_close_up","after":"close_up"}}}]}
                    """);
            }
            if (request.Method == HttpMethod.Get && path.EndsWith("/director/command-groups"))
                return Response("""[{"command_group_id":"grp-stale","status":"STALE","first_result":{"reason":"分镜已变更，请刷新分镜版本"},"commands":[]}]""");
            if (request.Method == HttpMethod.Get && path.EndsWith("/characters"))
                return Response("""[{"id":"c1","primary_name":"小满"}]""");
            if (request.Method == HttpMethod.Get && path.EndsWith("/generation-workbench"))
                return Response("""{"page":{"id":"page-1","page_number":1,"storyboard_version":7},"storyboard":{"panels":[{"id":"panel-1","reading_order":1,"characters":[],"dialogues":[{"id":"dlg-1","reading_order":1}]}]},"readiness":{"ready":true},"candidates":[]}""");
            return Response("{}");
        }

        private string GroupPayload()
        {
            PreviewPolls++;
            if (Mode == "preview" && PreviewPolls == 1)
                return """{"command_group_id":"grp-nl","status":"PARSING","commands":[]}""";
            return Mode switch
            {
                "clarify" => """{"command_group_id":"grp-nl","status":"NEEDS_CLARIFICATION","first_result":{"reason":"「它」指代不明，请指定目标","clarify_options":[{"kind":"panel","id":"panel-1","label":"格 1"},{"kind":"value","id":null,"label":"数值提示项"}]},"commands":[]}""",
                "failed" => """{"command_group_id":"grp-nl","status":"PARSE_FAILED","first_result":{"error":{"message":"模型输出不符合命令结构"}},"commands":[]}""",
                "stale" => """{"command_group_id":"grp-nl","status":"STALE","first_result":{"reason":"分镜已变更，当前为 V8"},"commands":[]}""",
                _ => """
                    {"command_group_id":"grp-nl","status":"PREVIEWED","first_result":{"model":{"model_id":"gpt-test"}},"commands":[
                    {"command_id":"cmd-1","status":"PREVIEWED","operation":"update_dialogue","target":{"panel_id":"panel-1","dialogue_id":"dlg-1"},"source":{"user_prompt":"两句一起改"},"diff":{"target_text":{"before":"旧","after":"新"}}},
                    {"command_id":"cmd-2","status":"PREVIEWED","operation":"update_panel_shot","target":{"panel_id":"panel-1"},"source":{"user_prompt":"两句一起改"},"diff":{"shot_type":{"before":"medium_close_up","after":"close_up"}}}]}
                    """,
            };
        }
    }

    // ── ⑥ 确认弃稿后的导演台重建 ──
    // 失败构造（原始缺陷）：ConfirmLeaveAsync 只「问」不「丢」——同意离开后
    // LoadWorkbenchAsync 的 body.Children.Clear() 把旧面板摘出视觉树，但
    // directorPane 字段仍引用它，HasDraft 残留让 DirectorDraftActive 继续成立：
    // Render() 渲染门（及 PollTick/RefreshAsync 的 keep-drafts 守卫）被永久挡住，
    // 导演台卡死在「正在载入生成工作台…」spinner。
    private static async Task DirectorConfirmedLeaveRebuilds()
    {
        var fixture = new DraftChapterFixture();
        using var api = new ApiClient("http://127.0.0.1:12345", fixture);
        var view = new GenerateView();
        view.Activate(Context(api, "p-pubdir"));
        try
        {
            await Until(() => fixture.WorkbenchReadsPage1 >= 1);
            await Settle();
            typeof(GenerateView).GetMethod("SwitchMode", All)!.Invoke(view, new object?[] { true });
            var pane = Field<DirectorPane>(view, "directorPane");
            Field<TextBox>(pane, "commandInput").Text = "切章后应当丢弃的指令";
            view.LeaveConfirmOverride = () => Task.FromResult(true);   // 同意弃稿

            var selector = Field<ComboBox>(view, "chapterSelector");
            selector.SelectedItem = selector.Items.Cast<ComboBoxItem>().Single(i => (string?)i.Tag == "ch-2");
            await Until(() => fixture.WorkbenchReadsPage2 >= 1);
            await Settle(120);
            var current = Field<DirectorPane?>(view, "directorPane");
            Require(!ReferenceEquals(current, pane),
                "同意弃稿切章后必须丢弃旧导演台引用并重建（残留引用会把渲染门永久挡住）");
            var body = Field<StackPanel>(view, "body");
            Require(body.Children.OfType<DirectorPane>().Any(),
                "切章完成后导演模式必须渲染新的导演台，而不是停在载入中");
        }
        finally { view.Deactivate(); }
    }

    private sealed class MobilePreviewFixture : HttpMessageHandler
    {
        public bool Blocked;

        protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken token)
        {
            var path = request.RequestUri!.AbsolutePath;
            if (path.EndsWith("/mobile-preview"))
                return Task.FromResult(Response(Payload()));
            return Task.FromResult(Response("{}"));

            string Payload()
            {
                var second = Blocked
                    ? """{"page_id":"pg-2","page_number":2,"state":"AWAITING_INSPECTION","ready":false,"width":null,"height":null,"blockers":[{"code":"QUALITY","message":"未完成视觉检查"}]}"""
                    : """{"page_id":"pg-2","page_number":2,"state":"READY","ready":true,"image_url":"/api/v1/assets/a2/thumbnail/640","width":1000,"height":2000,"blockers":[]}""";
                return $$"""
                    {"chapter_id":"ch-1","title":"第一章","pages":[
                    {"page_id":"pg-1","page_number":1,"state":"READY","ready":true,"image_url":"/api/v1/assets/a1/thumbnail/640","width":2000,"height":3000,"blockers":[]},
                    {{second}}]}
                    """;
            }
        }
    }

    // 两章 GenerateView 夹具：ch-1→pg-1、ch-2→pg-2，各自独立的工作台。
    private sealed class DraftChapterFixture : HttpMessageHandler
    {
        public int WorkbenchReadsPage1, WorkbenchReadsPage2;

        protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken token)
        {
            var path = request.RequestUri!.AbsolutePath;
            if (request.Method == HttpMethod.Get)
            {
                if (path.EndsWith("/pages/pg-1/generation-workbench")) { WorkbenchReadsPage1++; return Task.FromResult(Response(Workbench("pg-1"))); }
                if (path.EndsWith("/pages/pg-2/generation-workbench")) { WorkbenchReadsPage2++; return Task.FromResult(Response(Workbench("pg-2"))); }
                if (path.EndsWith("/pages/pg-1/batches") || path.EndsWith("/pages/pg-2/batches")) return Task.FromResult(Response("[]"));
                if (path.EndsWith("/chapters/ch-1/pages"))
                    return Task.FromResult(Response("""[{"id":"pg-1","chapter_id":"ch-1","page_number":1,"panel_count":1,"storyboard_version":4,"status":"","selected_candidate_id":"","continuity_status":""}]"""));
                if (path.EndsWith("/chapters/ch-2/pages"))
                    return Task.FromResult(Response("""[{"id":"pg-2","chapter_id":"ch-2","page_number":1,"panel_count":1,"storyboard_version":4,"status":"","selected_candidate_id":"","continuity_status":""}]"""));
                if (path.EndsWith("/projects/p-pubdir/chapters"))
                    return Task.FromResult(Response("""[{"id":"ch-1","title":"第一章","ordinal":1,"page_count":1},{"id":"ch-2","title":"第二章","ordinal":2,"page_count":1}]"""));
                if (path.EndsWith("/projects/p-pubdir/characters") || path.EndsWith("/projects/p-pubdir/outfits")) return Task.FromResult(Response("[]"));
                if (path.Contains("/character-packages") || path.EndsWith("/models")) return Task.FromResult(Response("[]"));
                if (path.Contains("/director/command-groups")) return Task.FromResult(Response("[]"));
            }
            return Task.FromResult(Response("{}"));
        }

        private static string Workbench(string pageId) =>
            """{"page":{"id":"PAGE","page_number":1,"storyboard_version":4},"storyboard":{"panels":[{"id":"panel-1","reading_order":1,"characters":[],"dialogues":[]}]},"readiness":{"ready":true},"current_batch":{"id":"batch"},"candidates":[]}"""
            .Replace("PAGE", pageId);
    }

    // ── 辅助 ──
    private static WorkspaceContext Context(ApiClient api, string projectId) => new()
    {
        Api = api, State = new WorkspaceState(), Window = null!,
        Project = new ProjectItem(projectId, "桌面同步检查", "", 0, 0),
        NavigateSection = (_, _) => Task.CompletedTask, OpenDashboard = () => Task.CompletedTask,
    };

    private static T Field<T>(object target, string name) => (T)target.GetType().GetField(name, All)!.GetValue(target)!;
    private static void Set(object target, string name, object? value) =>
        target.GetType().GetField(name, All)!.SetValue(target, value);
    private static Task Invoke(object target, string name, params object?[] args) =>
        (Task)target.GetType().GetMethod(name, All)!.Invoke(target, args)!;
    private static void Click(ButtonBase button) => button.RaiseEvent(new RoutedEventArgs(ButtonBase.ClickEvent));
    private static IEnumerable<DependencyObject> Descendants(DependencyObject root)
    {
        yield return root;
        foreach (var child in LogicalTreeHelper.GetChildren(root).OfType<DependencyObject>())
            foreach (var nested in Descendants(child)) yield return nested;
    }
    private static IEnumerable<string> Texts(DependencyObject root) =>
        Descendants(root).OfType<TextBlock>().Select(t => t.Text);
    private static IEnumerable<Button> Buttons(DependencyObject root, string label) =>
        Descendants(root).OfType<Button>().Where(b => b.Content as string == label);
    private static async Task Until(Func<bool> condition) => await Until(condition, 15);
    private static async Task Until(Func<bool> condition, int seconds)
    {
        using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(seconds));
        try { while (!condition()) await Task.Delay(5, timeout.Token); }
        catch (OperationCanceledException) { throw new Exception($"等待超时（{seconds}s）"); }
    }
    private static async Task Settle(int milliseconds = 60) => await Task.Delay(milliseconds);
    private static void Require(bool condition, string message) { if (!condition) throw new Exception(message); }
    private static HttpResponseMessage Response(string body) =>
        new(HttpStatusCode.OK) { Content = new StringContent(body) };
    private sealed class Handler(Func<HttpRequestMessage, Task<HttpResponseMessage>> respond) : HttpMessageHandler
    {
        protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken token) => respond(request);
    }
}
