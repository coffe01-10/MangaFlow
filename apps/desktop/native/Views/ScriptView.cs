using System.Net.Http;
using System.Text.Json;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Controls.Primitives;
using System.Windows.Media;
using MangaFlow.Native.Controls;
using MangaFlow.Native.Services;

namespace MangaFlow.Native.Views;

/// <summary>NUI-3: screenplay — coverage header, scene/beat editing, scene-asset binding, wardrobe.</summary>
public sealed class ScriptView : WorkspaceView
{
    private readonly ScrollViewer scroller = new() { VerticalScrollBarVisibility = ScrollBarVisibility.Auto };
    private readonly StackPanel body = new();
    private readonly ComboBox chapterSelector = Selector("章节选择", 260);
    private readonly TextBlock sceneCount = new() { Style = (Style)Application.Current.FindResource("Caption"), VerticalAlignment = VerticalAlignment.Center };
    private List<ChapterItem> chapters = [];
    private JsonElement script;
    private string chapterId = "";
    private readonly Dictionary<string, List<CharacterItem>> chapterCharacters = [];
    private List<OutfitItem> outfits = [];
    private List<SceneAssetItem> sceneAssets = [];
    private readonly TextBlock notice = new() { Style = (Style)Application.Current.FindResource("Caption"), TextWrapping = TextWrapping.Wrap, Margin = new Thickness(0, 0, 0, 12) };
    private bool loadingScript;
    private int scriptLoadVersion;
    // SC-2: quiet 轮询加载的独立在途标志 —— 不渲染 spinner，但也不能每拍叠加一组 4 并发请求。
    private bool quietLoadInFlight;
    // SC-3: 本章 SOURCE_PARSE 收敛判定的任务列表探测在途标志。
    private bool jobsProbeBusy;
    // SC-4: 当前已渲染数据（script/characters/outfits/sceneAssets 原文）的签名，
    // quiet 加载结果与之相同则跳过 Render，避免打爆正在阅读或编辑中的界面。
    private string renderedScriptSignature = "";

    // 测试缝：headless 回归检查用无模态实现替换离开确认（StoryboardView 的
    // LeaveConfirmOverride 同一模式）；生产路径为 null。
    internal Func<Task<bool>>? LeaveConfirmOverride;

    public ScriptView()
    {
        var panel = new StackPanel { Margin = new Thickness(4, 0, 24, 28) };
        var header = new Border { Style = (Style)Application.Current.FindResource("CanvasHeader") };
        var headerGrid = new Grid();
        headerGrid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        headerGrid.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        var heading = new StackPanel();
        heading.Children.Add(new TextBlock { Text = "SCREENPLAY", Style = (Style)Application.Current.FindResource("SectionIndex") });
        heading.Children.Add(new TextBlock
        {
            Text = "漫画剧本 · 先写场景与情节拍，再进入分页",
            FontFamily = (FontFamily)Application.Current.FindResource("Serif"),
            FontSize = 21, FontWeight = FontWeights.Bold, Margin = new Thickness(0, 5, 0, 0),
        });
        headerGrid.Children.Add(heading);
        var stage = new StackPanel { Orientation = Orientation.Horizontal };
        sceneCount.Margin = new Thickness(14, 0, 0, 0);
        stage.Children.Add(chapterSelector);
        stage.Children.Add(sceneCount);
        Grid.SetColumn(stage, 1);
        stage.VerticalAlignment = VerticalAlignment.Bottom;
        headerGrid.Children.Add(stage);
        headerGrid.Children.Clear();
        header.Child = new PageHeading(heading, stage);
        panel.Children.Add(header);
        chapterSelector.SelectionChanged += async (_, _) =>
        {
            if (chapterSelector.SelectedItem is ComboBoxItem { Tag: string id } && id != chapterId)
            {
                if (!await ConfirmLeaveAsync()) { SelectChapter(chapterId); return; }
                chapterId = id;
                await LoadScriptAsync();
            }
        };
        panel.Children.Add(notice);
        panel.Children.Add(body);
        scroller.Content = panel;
        Content = scroller;
    }

    private void SelectChapter(string id)
    {
        foreach (var item in chapterSelector.Items.OfType<ComboBoxItem>())
            if ((string?)item.Tag == id) chapterSelector.SelectedItem = item;
    }

    public override async void Activate(WorkspaceContext context)
    {
        base.Activate(context);
        try
        {
            var rows = await Api.SendAsync($"projects/{ProjectId}/chapters", cancellation: lifetime.Token);
            if (lifetime.Token.IsCancellationRequested) return;
            chapters = rows.EnumerateArray().Select(ChapterItem.From).ToList();
            chapterSelector.Items.Clear();
            foreach (var chapter in chapters)
                chapterSelector.Items.Add(new ComboBoxItem { Tag = chapter.Id, Content = $"{chapter.Ordinal}. {chapter.Title}" });
            if (chapters.Count > 0)
            {
                var selected = chapters.FirstOrDefault(c => c.Id == KeyValueStore.Get("workspace:chapter:" + ProjectId)) ?? chapters.FirstOrDefault(c => c.Id == chapterId) ?? chapters[0];
                // 激活不是章节切换：离开剧本区时 MainWindow 已运行过离开确认，这里
                // 必须先赋 chapterId 再拨选择器，让 SelectionChanged 的确认/加载
                // 旁路保持静默，由 Activate 自己恰好加载一次（StoryboardView.Activate
                // 同款修复）。旧顺序在处理器里先行 chapterId=id + LoadScriptAsync()，
                // Activate 随后再 LoadScriptAsync()——每次激活双倍发起一组 4 并发
                // 请求，迟到的第一轮结果被 scriptLoadVersion 守卫丢弃成纯浪费。
                chapterId = selected.Id;
                SelectChapter(selected.Id);
                await LoadScriptAsync();
            }
            else
            {
                body.Children.Clear();
                body.Children.Add(EmptyState("请先导入原作", "在“原作与修订”页导入章节后，才能生成漫画剧本。"));
            }
        }
        catch (OperationCanceledException) { }
        catch (Exception error) when (error is not OperationCanceledException)
        {
            body.Children.Clear();
            var host = Context!;
            body.Children.Add(ErrorCard(error.Message, () => { Activate(host); return Task.CompletedTask; }));
        }
    }

    private async Task LoadScriptAsync(bool quiet = false)
    {
        if (chapterId.Length == 0) return;
        // 迟到响应隔离:快速切换章节时,旧章节数据不得覆盖新章节的渲染
        // (与 SourceView 的 activation / StoryboardView 的守卫同一模式)。
        var requestVersion = ++scriptLoadVersion;
        if (quiet)
        {
            // SC-2: quiet 加载不置 loadingScript（不渲染 spinner），改用独立在途标志去重，
            // 慢网下 PollTick 不得每 3 秒叠加一组 4 并发请求。
            if (quietLoadInFlight) return;
            quietLoadInFlight = true;
        }
        else
        {
            loadingScript = true;
            // SC-5: 非静默加载即切换数据作用域：先清空上一章的 script 与场景计数（占位 "—"），
            // 失败分支才不会残留旧章节信息，PollTick 的未加载判定也不会失真。
            script = default;
            sceneCount.Text = "—";
            body.Children.Clear();
            var spinner = new StackPanel { Orientation = Orientation.Horizontal, Margin = new Thickness(0, 6, 0, 0) };
            spinner.Children.Add(new Spinner { Size = 18 });
            var hint = Kit.Caption("正在读取剧本…");
            hint.Margin = new Thickness(10, 0, 0, 0);
            spinner.Children.Add(hint);
            body.Children.Add(spinner);
        }
        try
        {
            var loadScript = Api.SendAsync($"chapters/{chapterId}/script", cancellation: lifetime.Token);
            var loadCharacters = Api.SendAsync($"projects/{ProjectId}/characters", cancellation: lifetime.Token);
            var loadOutfits = Api.SendAsync($"projects/{ProjectId}/outfits", cancellation: lifetime.Token);
            var loadSceneAssets = Api.SendAsync(QueryBuilder.Build($"projects/{ProjectId}/scene-assets", ("limit", 200)), cancellation: lifetime.Token);
            await Task.WhenAll(loadScript, loadCharacters, loadOutfits, loadSceneAssets);
            if (requestVersion != scriptLoadVersion || lifetime.Token.IsCancellationRequested) return;
            var scriptRow = await loadScript;
            var characterRows = await loadCharacters;
            var outfitRows = await loadOutfits;
            var sceneAssetRows = await loadSceneAssets;
            var signature = string.Join("\n",
                scriptRow.GetRawText(), characterRows.GetRawText(), outfitRows.GetRawText(), sceneAssetRows.GetRawText());
            // SC-4: 在途 quiet 完成时用户已打开编辑表单 —— 整份结果原地丢弃（字段与已渲染 UI 保持一致），
            // 绝不 Render 清空表单；表单关闭后的下一次加载会用新数据重绘。
            if (quiet && editingFormsOpen) return;
            script = scriptRow;
            chapterCharacters[chapterId] = characterRows.EnumerateArray().Select(CharacterItem.From).ToList();
            outfits = outfitRows.EnumerateArray().Select(OutfitItem.From).ToList();
            sceneAssets = sceneAssetRows.EnumerateArray().Select(SceneAssetItem.From).ToList();
            // SC-4: quiet 成功但数据序列化未变化 —— 跳过 Render（非 quiet 路径总是重绘，替换 spinner/错误卡）。
            if (quiet && signature == renderedScriptSignature) return;
            renderedScriptSignature = signature;
            Render();
        }
         catch (OperationCanceledException) { }
        catch (Exception error) when (error is not OperationCanceledException)
        {
            // SC-1: 旧请求的迟到失败既不能盖掉新请求已渲染的章节，也不属于 quiet 静默重试路径。
            if (quiet || requestVersion != scriptLoadVersion) return;
            body.Children.Clear();
            // 错误卡替换了画布：签名与已渲染内容不再对应，下次成功加载必须重绘。
            renderedScriptSignature = "";
            body.Children.Add(ErrorCard($"剧本读取失败：{error.Message}", async () => await LoadScriptAsync()));
        }
        finally
        {
            // SC-1: loadingScript 只属于最新一次非静默请求 —— 旧请求的 finally 不得提前清掉新请求的 loading 位。
            if (!quiet && requestVersion == scriptLoadVersion) loadingScript = false;
            if (quiet) quietLoadInFlight = false;
        }
    }

    /// <summary>
    /// #428: 重连（重新连接 → ConnectAsync → OpenProjectAsync 同项目分支）在用户
    /// 拒绝弃稿时的保真激活。重连已 Dispose 旧 ApiClient，视图必须重绑新上下文
    /// （否则下一次请求直接抛异常），但绝不能走 Activate 的整链重载——非 quiet 的
    /// LoadScriptAsync 会先 body.Children.Clear() 清掉打开中的编辑表单。守卫与
    /// #341 的 RefreshAsync 同源（editingFormsOpen）：表单关闭后的下一次加载
    /// （轮询/F5）会用新数据重绘，这里原地保留表单即可。
    /// </summary>
    internal void ActivatePreservingDrafts(WorkspaceContext context)
    {
        if (!editingFormsOpen)
        {
            Activate(context);
            return;
        }
        base.Activate(context);
    }

    private static Border EmptyState(string title, string description)
    {
        var stack = new StackPanel();
        stack.Children.Add(new TextBlock { Text = title, FontFamily = (FontFamily)Application.Current.FindResource("Serif"), FontSize = 18, FontWeight = FontWeights.Bold });
        stack.Children.Add(Kit.Caption(description));
        return new Border { Style = (Style)Application.Current.FindResource("Card"), Padding = new Thickness(26), Child = stack };
    }

    private static Border ErrorCard(string message, Func<Task> retry)
    {
        var stack = new StackPanel();
        stack.Children.Add(new TextBlock { Text = message, TextWrapping = TextWrapping.Wrap });
        var button = Kit.Act("重试", async (_, _) => await retry(), "Outline");
        button.Margin = new Thickness(0, 10, 0, 0);
        button.HorizontalAlignment = HorizontalAlignment.Left;
        stack.Children.Add(button);
        return new Border { Style = (Style)Application.Current.FindResource("Card"), Padding = new Thickness(20), Child = stack };
    }

    private void Render()
    {
        body.Children.Clear();
        notice.Text = "";
        var status = script.Text("status", "NOT_CREATED");
        var coverage = (int)Math.Round(script.Decimal("coverage_ratio") * 100);
        var scenes = script.Array("scenes");
        sceneCount.Text = $"{scenes.Count} 个场景";

        if (scenes.Count == 0)
        {
            var chapter = chapters.FirstOrDefault(c => c.Id == chapterId);
            var hasPages = chapter is { Pages: > 0 };
            var stack = new StackPanel();
            stack.Children.Add(new TextBlock { Text = "本章还没有漫画剧本", FontFamily = (FontFamily)Application.Current.FindResource("Serif"), FontSize = 18, FontWeight = FontWeights.Bold });
            if (hasPages)
            {
                stack.Children.Add(Kit.Caption("已有分页时不能重新生成剧本。请先删除分页，再生成剧本并重新计算分页。"));
                var remove = Kit.Act("删除分页", async (_, _) => await DeleteScript(), "DangerButton");
                remove.Margin = new Thickness(0, 14, 0, 0);
                remove.HorizontalAlignment = HorizontalAlignment.Left;
                stack.Children.Add(remove);
            }
            else
            {
                stack.Children.Add(Kit.Caption("点击“生成漫画剧本”，默认文字模型会逐段补充可视化动作、场景、对白、旁白、情绪和翻页悬念，不会压缩原文。"));
                var generate = Kit.Act("生成漫画剧本", async (_, _) => await ParseChapter(), "InkButton");
                generate.Margin = new Thickness(0, 14, 0, 0);
                generate.HorizontalAlignment = HorizontalAlignment.Left;
                stack.Children.Add(generate);
            }
            body.Children.Add(new Border { Style = (Style)Application.Current.FindResource("Card"), Padding = new Thickness(26), Child = stack });
            return;
        }

        // Coverage header + delete script.
        var coverageBar = new DockPanel { Margin = new Thickness(0, 0, 0, 14) };
        var removeScript = Kit.Act("删除剧本", async (_, _) => await DeleteScript(), "DangerButton");
        DockPanel.SetDock(removeScript, Dock.Right);
        coverageBar.Children.Add(removeScript);
        var coverageStack = new StackPanel { Orientation = Orientation.Horizontal };
        coverageStack.Children.Add(new TextBlock { Text = $"原文覆盖 {coverage}%", FontWeight = FontWeights.Bold });
        var fragments = script.Array("source_segments");
        var meta = Kit.Caption($" · {fragments.Count} 个原文片段 · {Labels.Map(Labels.ScriptStatus, status)}");
        meta.Margin = new Thickness(10, 0, 0, 0);
        coverageStack.Children.Add(meta);
        coverageBar.Children.Add(coverageStack);
        body.Children.Add(coverageBar);

        body.Children.Add(new Border
        {
            Background = (Brush)Application.Current.FindResource("WarningBg"),
            Padding = new Thickness(14, 9, 14, 9),
            Margin = new Thickness(0, 0, 0, 16),
            Child = new TextBlock { Text = "导演修订模式 / 场景与情节拍可直接修改；来源区间保持只读，避免剧情丢失。", FontSize = 12.5 },
        });

        var sceneIndex = 0;
        foreach (var scene in scenes)
        {
            sceneIndex++;
            body.Children.Add(new SceneSection(this, scene, sceneIndex, chapterCharacters.GetValueOrDefault(chapterId, []), outfits, sceneAssets));
        }
    }

    internal async Task LoadScene(SceneSection section, JsonElement scene, Dictionary<string, object?> changes)
    {
        try
        {
            changes["version"] = scene.Number("version");
            await Api.SendAsync($"scenes/{scene.Text("id")}", HttpMethod.Patch, changes, cancellation: lifetime.Token);
            section.ExitEdit();
            notice.Text = "场景修改已保存；相关页面已标记为待复查。";
            Cache.Invalidate("script:" + chapterId, "pages:" + chapterId);
            await LoadScriptAsync();
        }
         catch (OperationCanceledException) { }
        catch (Exception error) when (error is not OperationCanceledException)
        {
            MessageBox.Show(Host, error.Message, "保存未完成", MessageBoxButton.OK, MessageBoxImage.Warning);
        }
    }

    internal async Task SaveBeat(BeatRow beat, JsonElement beatValue, Dictionary<string, object?> changes)
    {
        try
        {
            changes["version"] = beatValue.Number("version");
            await Api.SendAsync($"beats/{beatValue.Text("id")}", HttpMethod.Patch, changes, cancellation: lifetime.Token);
            beat.ExitEdit();
            notice.Text = "情节拍修改已保存；分镜与历史候选保留，相关页面需复查。";
            Cache.Invalidate("script:" + chapterId, "pages:" + chapterId);
            await LoadScriptAsync();
        }
         catch (OperationCanceledException) { }
        catch (Exception error) when (error is not OperationCanceledException)
        {
            MessageBox.Show(Host, error.Message, "保存未完成", MessageBoxButton.OK, MessageBoxImage.Warning);
        }
    }

    /// <summary>
    /// #426: wardrobe save now sends ONE full-map PATCH. The caller merges the live
    /// selector state into the final assignment map before calling; this method must
    /// never re-derive assignments from its own stale snapshot (the old per-character
    /// loop re-parsed the same render-time scene per call, so a removal landed by
    /// PATCH #1 was resurrected by PATCH #2). The payload also carries the observed
    /// scene version — schemas.SceneOutfitUpdate's optional optimistic-lock token —
    /// so concurrent edits 409 instead of last-write-wins.
    /// </summary>
    internal async Task SaveOutfitAssignments(JsonElement scene, IReadOnlyDictionary<string, string> assignments)
    {
        try
        {
            // outfit_assignments 是 {characterId: outfitId} 字典(后端 SceneRead);
            // 按数组解析会得到空映射,后端全量替换会悄悄清掉其他角色的指定。
            await Api.SendAsync($"scenes/{scene.Text("id")}/outfits", HttpMethod.Patch,
                new { assignments, version = scene.Number("version") }, cancellation: lifetime.Token);
            notice.Text = "本场服装指定已保存；相关页面会标记为待复查。";
            Cache.Invalidate("script:" + chapterId, "pages:" + chapterId);
            await LoadScriptAsync();
        }
        catch (OperationCanceledException) { }
        catch (Exception error) when (error is not OperationCanceledException)
        {
            MessageBox.Show(Host, error.Message, "服装指定未保存", MessageBoxButton.OK, MessageBoxImage.Warning);
        }
    }

    internal async Task BindSceneAsset(JsonElement scene, string? assetId, string? variantId)
    {
        try
        {
            await Api.SendAsync($"scenes/{scene.Text("id")}/bind-asset", HttpMethod.Patch,
                new { scene_asset_id = assetId, scene_asset_variant_id = variantId }, cancellation: lifetime.Token);
            Cache.Invalidate("script:" + chapterId, "scene-assets:" + ProjectId);
            await LoadScriptAsync();
        }
         catch (OperationCanceledException) { }
        catch (Exception error) when (error is not OperationCanceledException)
        {
            MessageBox.Show(Host, "绑定场景资产失败：" + error.Message, "绑定未完成", MessageBoxButton.OK, MessageBoxImage.Warning);
        }
    }

    private async Task DeleteScript()
    {
        if (MessageBox.Show(Host, "删除本章漫画剧本？分页、分镜和页面候选会同时从工作区移除；原文、素材文件与任务记录保留，之后可重新生成。",
            "删除剧本", MessageBoxButton.YesNo, MessageBoxImage.Warning) != MessageBoxResult.Yes) return;
        try
        {
            await Api.SendOptionalAsync($"chapters/{chapterId}/script", HttpMethod.Delete, cancellation: lifetime.Token);
            Cache.Invalidate("script:" + chapterId, "pages:" + chapterId, "chapters:" + ProjectId);
            await LoadScriptAsync();
        }
         catch (OperationCanceledException) { }
        catch (Exception error) when (error is not OperationCanceledException)
        {
            MessageBox.Show(Host, error.Message, "删除未完成", MessageBoxButton.OK, MessageBoxImage.Warning);
        }
    }

    private async Task ParseChapter()
    {
        try
        {
            await Api.SendAsync($"chapters/{chapterId}/parse", HttpMethod.Post, cancellation: lifetime.Token);
            State.Status = "剧本解析任务已创建";
            await Context!.NavigateSection("jobs", "");
        }
         catch (OperationCanceledException) { }
        catch (Exception error) when (error is not OperationCanceledException)
        {
            MessageBox.Show(Host, error.Message, "生成剧本未完成", MessageBoxButton.OK, MessageBoxImage.Warning);
        }
    }

    private bool editingFormsOpen =>
        body.Children.OfType<SceneSection>().Any(s => s.IsEditing)
        || body.Children.OfType<SceneSection>().SelectMany(s => s.BeatRows).Any(b => b.IsEditing);

    private static bool TerminalJobStatus(string status) =>
        status is "COMPLETED" or "FAILED" or "CANCELLED" or "NEEDS_REVIEW";

    public override void PollTick()
    {
        if (loadingScript || chapterId.Length == 0 || editingFormsOpen) return;
        // 剧本未加载时直接补一次 quiet 读取；已加载时按本章的活跃 SOURCE_PARSE 收敛。
        if (script.ValueKind == JsonValueKind.Undefined)
        {
            _ = LoadScriptAsync(quiet: true);
            return;
        }
        _ = PollChapterParseAsync();
    }

    /// <summary>
    /// SC-3: 本章是否存在活跃 SOURCE_PARSE（对齐 web chapterParseJob：
    /// job_type == "SOURCE_PARSE" &amp;&amp; job.target_id == chapterId 且非终态）。
    /// 数据源选「自行轻量拉取全项目活跃任务列表」：State.DockJob 只暴露全项目第一项
    /// （他章任务会误判/漏判），ApiCache 的 jobs 键当前无人写入（只有 Invalidate 调用），
    /// 都无法按 target_id 判定；jobsProbeBusy 保证每拍最多一次探测，慢响应由下一拍重试。
    /// </summary>
    private async Task PollChapterParseAsync()
    {
        if (jobsProbeBusy) return;
        jobsProbeBusy = true;
        try
        {
            var chapter = chapterId;
            var rows = await Api.SendAsync(
                QueryBuilder.Build($"projects/{ProjectId}/jobs", ("archived", "false")),
                cancellation: lifetime.Token);
            if (lifetime.Token.IsCancellationRequested || chapter != chapterId) return;
            var parseRunning = rows.EnumerateArray().Any(job => job.Text("job_type") == "SOURCE_PARSE"
                && job.Text("target_id") == chapter && !TerminalJobStatus(job.Text("status")));
            if (parseRunning && !loadingScript && !editingFormsOpen)
                await LoadScriptAsync(quiet: true);
        }
        catch (OperationCanceledException) { }
        catch (Exception) { /* 任务列表瞬时失败只影响本轮收敛判定，下一拍重试 */ }
        finally { jobsProbeBusy = false; }
    }

    public override async Task<bool> ConfirmLeaveAsync()
    {
        // 测试缝优先：headless 检查无模态驱动拒绝/同意分支，否则卡死在 MessageBox。
        if (LeaveConfirmOverride is { } prompt) return await prompt();
        var sceneEditing = body.Children.OfType<SceneSection>().Any(s => s.IsEditing);
        var beatEditing = body.Children.OfType<SceneSection>().SelectMany(s => s.BeatRows).Any(b => b.IsEditing);
        if (sceneEditing || beatEditing)
        {
            var result = MessageBox.Show(Host, "当前场景 / 情节拍的修改尚未保存，切换章节会丢弃这些修改。仍要切换吗？",
                "离开确认", MessageBoxButton.YesNo, MessageBoxImage.Question);
            return result == MessageBoxResult.Yes;
        }
        return await Task.FromResult(true);
    }

    public override Task RefreshAsync()
    {
        // F5/用户刷新不得销毁打开中的场景/情节拍编辑表单（#341）：quiet 轮询路径
        // 早有同款守卫（LoadScriptAsync 的 editingFormsOpen 检查），用户显式刷新
        // 同样提前返回——表单关闭后的下一次加载（轮询或手动刷新）会用新数据重绘。
        if (editingFormsOpen) return Task.CompletedTask;
        _ = LoadScriptAsync();
        return Task.CompletedTask;
    }
}

/// <summary>One scene card: header, context line, asset binding, wardrobe, beats.</summary>
internal sealed class SceneSection : Border
{
    private readonly ScriptView view;
    private JsonElement scene;
    private readonly int index;
    private readonly List<CharacterItem> characters;
    private readonly List<OutfitItem> outfits;
    private readonly List<SceneAssetItem> sceneAssets;
    private readonly StackPanel content = new();
    private bool editing;
    private readonly List<BeatRow> beatRows = [];

    public bool IsEditing => editing;
    internal IEnumerable<BeatRow> BeatRows => beatRows;

    public SceneSection(ScriptView view, JsonElement scene, int index,
        List<CharacterItem> characters, List<OutfitItem> outfits, List<SceneAssetItem> sceneAssets)
    {
        this.view = view;
        this.scene = scene;
        this.index = index;
        this.characters = characters;
        this.outfits = outfits;
        this.sceneAssets = sceneAssets;
        Style = (Style)Application.Current.FindResource("Card");
        Padding = new Thickness(20);
        Margin = new Thickness(0, 0, 0, 14);
        Child = content;
        Render();
    }

    public void ExitEdit() { editing = false; }

    private void Render()
    {
        content.Children.Clear();
        var header = new DockPanel { Margin = new Thickness(0, 0, 0, 10) };
        var editButton = Kit.Act(editing ? "退出编辑" : "编辑场景", (_, _) => { editing = !editing; Render(); }, editing ? "Ghost" : "Compact");
        DockPanel.SetDock(editButton, Dock.Right);
        header.Children.Add(editButton);
        var title = new StackPanel();
        title.Children.Add(new TextBlock { Text = $"SCENE {index:D2} · {scene.Text("location", "未指定地点")} · {scene.Text("time_label", "时间未标注")}", Style = (Style)Application.Current.FindResource("SectionIndex") });
        title.Children.Add(new TextBlock { Text = scene.Text("purpose"), Style = (Style)Application.Current.FindResource("Caption"), Margin = new Thickness(0, 5, 0, 0) });
        header.Children.Add(title);
        content.Children.Add(header);

        if (editing)
            content.Children.Add(BuildEditForm());
        else
        {
            var emotion = scene.Text("emotional_arc");
            var weather = scene.Text("weather");
            if (emotion.Length > 0 || weather.Length > 0)
                content.Children.Add(new TextBlock
                {
                    Text = $"情绪线：{emotion} · {weather}",
                    FontStyle = FontStyles.Italic, FontSize = 13, Margin = new Thickness(0, 0, 0, 10),
                });
        }

        content.Children.Add(BuildAssetBinding());
        var cast = VisibleCast();
        if (cast.Count > 0) content.Children.Add(BuildWardrobe(cast));
        content.Children.Add(new TextBlock { Text = "情节拍", Style = (Style)Application.Current.FindResource("SectionIndex"), Margin = new Thickness(0, 12, 0, 6) });
        beatRows.Clear();
        var beatIndex = 0;
        foreach (var beat in scene.Array("beats"))
        {
            var row = new BeatRow(view, beat, ++beatIndex, characters, outfits);
            beatRows.Add(row);
            content.Children.Add(row);
        }
        if (scene.Array("beats").Count == 0)
            content.Children.Add(Kit.Caption("本场景还没有情节拍。"));
    }

    private List<CharacterItem> VisibleCast() =>
        // 与网页一致:列出所有拥有服装档案的角色(说话人匹配会漏掉未开口
        // 但已建服装的出镜角色,也让衣橱区依赖对白文本)。
        characters.Where(c => outfits.Any(o => o.CharacterId == c.Id)).ToList();

    private FrameworkElement BuildEditForm()
    {
        var location = new TextBox { Text = scene.Text("location"), Margin = new Thickness(0, 0, 0, 10) };
        var time = new TextBox { Text = scene.Text("time_label"), Margin = new Thickness(0, 0, 0, 10) };
        var weather = new TextBox { Text = scene.Text("weather"), Margin = new Thickness(0, 0, 0, 10) };
        var purpose = new TextBox { Text = scene.Text("purpose"), AcceptsReturn = true, MinHeight = 64, Margin = new Thickness(0, 0, 0, 10) };
        var emotionalArc = new TextBox { Text = scene.Text("emotional_arc"), AcceptsReturn = true, MinHeight = 64, Margin = new Thickness(0, 0, 0, 12) };
        var panel = new StackPanel();
        panel.Children.Add(SceneLabel("地点（历史兜底，绑定资产时不会清空）"));
        panel.Children.Add(location);
        panel.Children.Add(SceneLabel("时间"));
        panel.Children.Add(time);
        panel.Children.Add(SceneLabel("天气 / 氛围"));
        panel.Children.Add(weather);
        panel.Children.Add(SceneLabel("本场目的"));
        panel.Children.Add(purpose);
        panel.Children.Add(SceneLabel("情绪弧线"));
        panel.Children.Add(emotionalArc);
        var actions = new StackPanel { Orientation = Orientation.Horizontal };
        var cancel = Kit.Act("取消", (_, _) => { editing = false; Render(); }, "Ghost");
        cancel.Margin = new Thickness(0, 0, 10, 0);
        actions.Children.Add(cancel);
        // #448: 双击保存会用同一份捕获的 scene version 发出两个 PATCH，第二个必然
        // 409 并弹出假的「保存未完成」冲突框。在途标志挡住第二次进入；禁用按钮只是
        // 视觉反馈（RaiseEvent/合成点击仍会触达禁用按钮，标志才是守卫）。
        var saveBusy = false;
        var save = Kit.Act("保存场景", async (sender, _) =>
        {
            if (saveBusy) return;
            saveBusy = true;
            var button = (Button)sender!;
            button.IsEnabled = false;
            try
            {
                await view.LoadScene(this, scene, new Dictionary<string, object?>
                {
                    ["location"] = location.Text, ["time_label"] = time.Text, ["weather"] = weather.Text,
                    ["purpose"] = purpose.Text, ["emotional_arc"] = emotionalArc.Text,
                });
            }
            finally { saveBusy = false; button.IsEnabled = true; }
        }, "InkButton");
        actions.Children.Add(save);
        panel.Children.Add(actions);
        return panel;
    }

    private static TextBlock SceneLabel(string text) => new()
    { Text = text, Style = (Style)Application.Current.FindResource("FieldLabel"), Margin = new Thickness(0, 0, 0, 6) };

    private FrameworkElement BuildAssetBinding()
    {
        var panel = new StackPanel { Margin = new Thickness(0, 4, 0, 10) };
        panel.Children.Add(new TextBlock { Text = "本场场景资产", Style = (Style)Application.Current.FindResource("SectionIndex"), Margin = new Thickness(0, 0, 0, 4) });
        panel.Children.Add(Kit.Caption("地点文本会保留作历史兜底，绑定资产时不会被清空。"));
        var assetSelector = new ComboBox { Width = 320, Margin = new Thickness(0, 8, 0, 0) };
        assetSelector.Items.Add(new ComboBoxItem { Tag = "", Content = "不绑定场景资产" });
        var currentAssetId = scene.Text("scene_asset_id");
        var currentVariantId = scene.Text("scene_asset_variant_id");
        foreach (var asset in sceneAssets.Where(a => !a.Deleted))
        {
            var item = new ComboBoxItem { Tag = asset.Id, Content = $"{asset.Name} · {asset.InteriorLabel}" };
            assetSelector.Items.Add(item);
            if (asset.Id == currentAssetId) assetSelector.SelectedItem = item;
        }
        if (assetSelector.SelectedIndex == -1) assetSelector.SelectedIndex = 0;
        var variantSelector = new ComboBox { Width = 320, Margin = new Thickness(10, 0, 0, 0) };
        void RefreshVariants()
        {
            variantSelector.Items.Clear();
            variantSelector.Items.Add(new ComboBoxItem { Tag = "", Content = "使用资产默认变体" });
            var selectedAsset = (assetSelector.SelectedItem as ComboBoxItem)?.Tag as string;
            var asset = sceneAssets.FirstOrDefault(a => a.Id == selectedAsset);
            if (asset != null)
                // #440: 场景详情接口内嵌的 variants 含已归档变体（列表裁剪只发生在
                // 资产级），已归档变体不可选 —— 保存会 422「场景变体已归档」。
                // 与 SceneWorkspace.Deleted 同一判定：deleted_at 为时间戳字符串即归档。
                foreach (var variant in asset.Variants.Where(v => !v.FlagDate("deleted_at")))
                {
                    var name = variant.Text("name");
                    var item = new ComboBoxItem { Tag = variant.Text("id"), Content = variant.Flag("is_canonical") ? $"{name}（默认）" : name };
                    variantSelector.Items.Add(item);
                    if (variant.Text("id") == currentVariantId) variantSelector.SelectedItem = item;
                }
            if (variantSelector.SelectedIndex == -1) variantSelector.SelectedIndex = 0;
            variantSelector.IsEnabled = asset != null;
        }
        RefreshVariants();
        var row = new StackPanel { Orientation = Orientation.Horizontal };
        row.Children.Add(assetSelector);
        row.Children.Add(variantSelector);
        panel.Children.Add(row);
        var bind = Kit.Act("保存绑定", async (_, _) =>
        {
            var assetId = (assetSelector.SelectedItem as ComboBoxItem)?.Tag as string;
            var variantId = assetId.Length > 0 ? (variantSelector.SelectedItem as ComboBoxItem)?.Tag as string : null;
            await view.BindSceneAsset(scene, assetId.Length > 0 ? assetId : null, variantId);
        }, "Compact");
        bind.Margin = new Thickness(0, 10, 0, 0);
        panel.Children.Add(bind);
        return panel;
    }

    private FrameworkElement BuildWardrobe(List<CharacterItem> cast)
    {
        var panel = new StackPanel { Margin = new Thickness(0, 4, 0, 4) };
        panel.Children.Add(new TextBlock { Text = "本场服装指定", Style = (Style)Application.Current.FindResource("SectionIndex"), Margin = new Thickness(0, 0, 0, 6) });
        var selectors = new List<(string CharacterId, ComboBox Selector)>();
        foreach (var character in cast)
        {
            var row = new StackPanel { Orientation = Orientation.Horizontal, Margin = new Thickness(0, 4, 0, 4) };
            var name = new TextBlock { Text = character.PrimaryName, VerticalAlignment = VerticalAlignment.Center, FontWeight = FontWeights.Bold, Width = 110, TextTrimming = TextTrimming.CharacterEllipsis };
            row.Children.Add(name);
            var selector = new ComboBox { Width = 260 };
            selector.Items.Add(new ComboBoxItem { Tag = "", Content = "未指定" });
            var assigned = scene.StringMap("outfit_assignments").TryGetValue(character.Id, out var assignedId) ? assignedId : "";
            foreach (var outfit in outfits.Where(o => o.CharacterId == character.Id))
            {
                var item = new ComboBoxItem { Tag = outfit.Id, Content = outfit.Name };
                selector.Items.Add(item);
                if (outfit.Id == assigned) selector.SelectedItem = item;
            }
            if (selector.SelectedIndex == -1) selector.SelectedIndex = 0;
            row.Children.Add(selector);
            selectors.Add((character.Id, selector));
            panel.Children.Add(row);
        }
        // #426/#448: 终版 assignments 在保存瞬间从「实时选择器状态 + 渲染时快照」
        // 合并一次得到，单个 PATCH 全量提交。旧实现按角色循环、每次调用都从同一份
        // 过期快照重建全量映射，PATCH #1 刚删掉的指定会被 PATCH #2 原样复活。
        // 在途标志挡住双击（PATCH 现在携带 version，重复提交的后果从悄悄覆盖升级
        // 成真 409，必须像场景/情节拍保存一样挡住第二次进入）。
        var saveBusy = false;
        var save = Kit.Act("保存服装指定", async (sender, _) =>
        {
            if (saveBusy) return;
            var current = scene.StringMap("outfit_assignments");
            var dirty = selectors.Any(s =>
                ((s.Selector.SelectedItem as ComboBoxItem)?.Tag as string ?? "")
                != (current.TryGetValue(s.CharacterId, out var value) ? value : ""));
            if (!dirty) return;
            saveBusy = true;
            var button = (Button)sender!;
            button.IsEnabled = false;
            try
            {
                // 未在本衣橱区渲染的角色（无服装档案）保留服务端现状，只覆盖表单内
                // 可编辑的角色；选择「未指定」= 从终版映射里删除该角色。
                var final = current;
                foreach (var (characterId, selector) in selectors)
                {
                    var outfitId = (selector.SelectedItem as ComboBoxItem)?.Tag as string ?? "";
                    if (outfitId.Length == 0) final.Remove(characterId);
                    else final[characterId] = outfitId;
                }
                await view.SaveOutfitAssignments(scene, final);
            }
            finally { saveBusy = false; button.IsEnabled = true; }
        }, "Compact");
        save.Margin = new Thickness(0, 8, 0, 0);
        panel.Children.Add(save);
        return panel;
    }
}

/// <summary>One beat: read view or inline edit form.</summary>
internal sealed class BeatRow : Border
{
    private readonly ScriptView view;
    private readonly JsonElement beat;
    private readonly int index;
    private readonly List<CharacterItem> characters;
    private readonly List<OutfitItem> outfits;
    private bool editing;

    public bool IsEditing => editing;

    public BeatRow(ScriptView view, JsonElement beat, int index, List<CharacterItem> characters, List<OutfitItem> outfits)
    {
        this.view = view;
        this.beat = beat;
        this.index = index;
        this.characters = characters;
        this.outfits = outfits;
        BorderBrush = (Brush)Application.Current.FindResource("Line");
        BorderThickness = new Thickness(0, 0, 0, 1);
        Padding = new Thickness(0, 10, 0, 10);
        Render();
    }

    public void ExitEdit() { editing = false; }

    private void Render()
    {
        Child = editing ? BuildEditForm() : BuildReadView();
    }

    private FrameworkElement BuildReadView()
    {
        var grid = new Grid();
        grid.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        grid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        grid.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        var number = new TextBlock { Text = index.ToString("D2"), Style = (Style)Application.Current.FindResource("SectionIndex"), Margin = new Thickness(0, 0, 14, 0), VerticalAlignment = VerticalAlignment.Top };
        grid.Children.Add(number);
        var content = new StackPanel();
        content.Children.Add(new TextBlock { Text = beat.Text("action"), FontWeight = FontWeights.Bold, FontSize = 13.5, TextWrapping = TextWrapping.Wrap });
        var speaker = beat.Text("speaker_name");
        var dialogue = beat.Text("dialogue");
        if (dialogue.Length > 0)
        {
            var line = new TextBlock { Text = speaker.Length > 0 ? $"{speaker}：{dialogue}" : dialogue, TextWrapping = TextWrapping.Wrap, Margin = new Thickness(0, 5, 0, 0) };
            content.Children.Add(line);
        }
        var narration = beat.Text("narration");
        if (narration.Length > 0)
            content.Children.Add(new TextBlock { Text = $"旁白：{narration}", TextWrapping = TextWrapping.Wrap, Margin = new Thickness(0, 5, 0, 0), Foreground = (Brush)Application.Current.FindResource("Muted") });
        var emotion = beat.Text("emotion");
        var segments = beat.Array("source_segments");
        content.Children.Add(new TextBlock
        {
            Text = $"{emotion} · 来源 {segments.Count} 段 · V{beat.Number("version")}",
            Style = (Style)Application.Current.FindResource("Micro"), Margin = new Thickness(0, 6, 0, 0),
        });
        grid.Children.Add(content);
        var edit = Kit.Act("编辑", (_, _) => { editing = true; Render(); }, "Compact");
        edit.VerticalAlignment = VerticalAlignment.Top;
        Grid.SetColumn(edit, 2);
        grid.Children.Add(edit);
        return grid;
    }

    private FrameworkElement BuildEditForm()
    {
        var panel = new StackPanel { Margin = new Thickness(38, 6, 0, 0) };
        var action = new TextBox { Text = beat.Text("action"), AcceptsReturn = true, MinHeight = 56, Margin = new Thickness(0, 0, 0, 10) };
        var speaker = new TextBox { Text = beat.Text("speaker_name"), Width = 240, HorizontalAlignment = HorizontalAlignment.Left, Margin = new Thickness(0, 0, 0, 10) };
        var emotion = new TextBox { Text = beat.Text("emotion"), Width = 240, HorizontalAlignment = HorizontalAlignment.Left, Margin = new Thickness(0, 0, 0, 10) };
        var dialogue = new TextBox { Text = beat.Text("dialogue"), AcceptsReturn = true, MinHeight = 56, Margin = new Thickness(0, 0, 0, 10) };
        var narration = new TextBox { Text = beat.Text("narration"), AcceptsReturn = true, MinHeight = 48, Margin = new Thickness(0, 0, 0, 10) };
        var subtext = new TextBox { Text = beat.Text("subtext"), Margin = new Thickness(0, 0, 0, 10) };
        var mustVisualize = new CheckBox { Content = "必须画出", IsChecked = beat.Flag("must_visualize"), Margin = new Thickness(0, 0, 14, 10) };
        var mergeable = new CheckBox { Content = "允许合并", IsChecked = beat.Flag("mergeable"), Margin = new Thickness(0, 0, 14, 10) };
        var pageTurn = new CheckBox { Content = "翻页悬念", IsChecked = beat.Flag("page_turn_hook"), Margin = new Thickness(0, 0, 0, 10) };
        var importance = new Slider { Minimum = 0, Maximum = 1, Value = beat.Decimal("importance", 0.5), Width = 260, HorizontalAlignment = HorizontalAlignment.Left };

        panel.Children.Add(BeatLabel("可视化动作（必填）"));
        panel.Children.Add(action);
        panel.Children.Add(BeatLabel("说话人（可填绰号，保存后归一）"));
        panel.Children.Add(speaker);
        panel.Children.Add(BeatLabel("情绪"));
        panel.Children.Add(emotion);
        panel.Children.Add(BeatLabel("对白"));
        panel.Children.Add(dialogue);
        panel.Children.Add(BeatLabel("旁白"));
        panel.Children.Add(narration);
        panel.Children.Add(BeatLabel("潜台词 / 表演提示"));
        panel.Children.Add(subtext);
        var flags = new StackPanel { Orientation = Orientation.Horizontal };
        flags.Children.Add(mustVisualize);
        flags.Children.Add(mergeable);
        flags.Children.Add(pageTurn);
        panel.Children.Add(flags);
        panel.Children.Add(BeatLabel("重要度"));
        panel.Children.Add(importance);
        var actions = new StackPanel { Orientation = Orientation.Horizontal, Margin = new Thickness(0, 12, 0, 0) };
        var cancel = Kit.Act("取消", (_, _) => { editing = false; Render(); }, "Ghost");
        cancel.Margin = new Thickness(0, 0, 10, 0);
        actions.Children.Add(cancel);
        // #448: 与场景保存同一在途守卫 —— 双击会带同一份 beat version 发两个
        // PATCH，第二个 409 弹出假的「保存未完成」冲突框。
        var saveBusy = false;
        var save = Kit.Act("保存情节拍", async (sender, _) =>
        {
            if (saveBusy) return;
            if (action.Text.Trim().Length == 0) { MessageBox.Show(Window.GetWindow(this), "可视化动作不能为空。", "保存情节拍"); return; }
            saveBusy = true;
            var button = (Button)sender!;
            button.IsEnabled = false;
            try
            {
                await view.SaveBeat(this, beat, new Dictionary<string, object?>
                {
                    ["action"] = action.Text,
                    ["speaker_name"] = speaker.Text,
                    ["emotion"] = emotion.Text,
                    ["dialogue"] = dialogue.Text,
                    ["narration"] = narration.Text,
                    ["subtext"] = subtext.Text,
                    ["importance"] = Math.Round(importance.Value, 2),
                    ["must_visualize"] = mustVisualize.IsChecked == true,
                    ["mergeable"] = mergeable.IsChecked == true,
                    ["page_turn_hook"] = pageTurn.IsChecked == true,
                });
            }
            finally { saveBusy = false; button.IsEnabled = true; }
        }, "InkButton");
        actions.Children.Add(save);
        panel.Children.Add(actions);
        return panel;
    }

    private static TextBlock BeatLabel(string text) => new()
    { Text = text, Style = (Style)Application.Current.FindResource("FieldLabel"), Margin = new Thickness(0, 0, 0, 6) };
}
