using System.Net.Http;
using System.IO;
using Microsoft.Win32;
using System.Text.Json;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Controls.Primitives;
using System.Windows.Input;
using System.Windows.Media;
using MangaFlow.Native.Controls;
using MangaFlow.Native.Services;

namespace MangaFlow.Native.Views;

/// <summary>
/// NUI-5: generation desk — draw mode (readiness, model, references, candidates,
/// inspection, production gate, next page) plus the director rule-stub loop.
/// </summary>
public sealed class GenerateView : WorkspaceView
{
    private readonly ComboBox chapterSelector = Selector("章节选择", 220);
    private readonly WrapPanel pageBar = new();
    private readonly ToggleButton drawMode = new() { Content = "抽卡", Style = (Style)Application.Current.FindResource("Pill"), IsChecked = true };
    private readonly ToggleButton directorMode = new() { Content = "导演", Style = (Style)Application.Current.FindResource("Pill") };
    private readonly StackPanel body = new();
    private readonly TextBlock notice = new() { Style = (Style)Application.Current.FindResource("Caption"), TextWrapping = TextWrapping.Wrap, Margin = new Thickness(0, 0, 0, 10) };

    private List<ChapterItem> chapters = [];
    private List<PageItem> pages = [];
    private string chapterId = "";
    private PageItem? currentPage;
    private JsonElement workbench;
    private List<JsonElement> models = [];
    private readonly Dictionary<string, ReferenceChoice> referenceSelections = new();
    private List<JsonElement> referenceCharacters = [], referenceOutfits = [], referencePackages = [];
    private bool referencesLoaded, referenceOverrideOpen;
    private readonly HashSet<string> pendingRows = new();
    private bool director;
    private DirectorPane? directorPane;
    private string selectedModel = "";
    private List<JsonElement> pageBatches = [];
    // 进行中的视觉检查任务 id：PAGE_INSPECT 不改变候选的 QUEUED/GENERATING 状态，
    // 只看候选状态会提前停止轮询，检查提交后的门禁/页面状态就永远刷不进来。
    private readonly HashSet<string> trackedInspectJobs = new();
    private bool jobsWatchBusy;
    private string? viewedBatchId;
    private List<JsonElement>? historicalCandidates;
    private int workbenchRead;
    private int pagesRead;
    // ── 检查结果面板（web InspectionPanel / reviewCandidateId 状态）──
    // 面板只跟随一个候选；切页/切批次/抽卡/修复/升清/删除都会收起（对齐 web 的
    // reviewCandidateId 生命周期），否则修复按钮会对已不在当前批次的旧候选提交。
    private string? reviewCandidateId;
    private List<JsonElement> reviewInspections = [];
    private string? inspectionsError;
    private int inspectionsRead;
    private JsonElement reviewInspectJob;
    private string reviewInspectJobIdSeen = "";
    private bool reviewChecking;
    private string? panelError;

    public GenerateView()
    {
        var root = new DockPanel { Margin = new Thickness(4, 0, 24, 24) };
        var scroller = new ScrollViewer { VerticalScrollBarVisibility = ScrollBarVisibility.Auto };
        var panel = new StackPanel();
        var header = new Border { Style = (Style)Application.Current.FindResource("CanvasHeader") };
        var headerGrid = new Grid();
        headerGrid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        headerGrid.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        var heading = new StackPanel();
        heading.Children.Add(new TextBlock { Text = "DRAW", Style = (Style)Application.Current.FindResource("SectionIndex") });
        heading.Children.Add(new TextBlock
        {
            Text = "单页抽卡 · 每次只生成 1 页",
            FontFamily = (FontFamily)Application.Current.FindResource("Serif"),
            FontSize = 21, FontWeight = FontWeights.Bold, Margin = new Thickness(0, 5, 0, 0),
        });
        headerGrid.Children.Add(heading);
        var modes = new StackPanel { Orientation = Orientation.Horizontal, VerticalAlignment = VerticalAlignment.Bottom };
        drawMode.Click += (_, _) => SwitchMode(false);
        directorMode.Click += (_, _) => SwitchMode(true);
        modes.Children.Add(drawMode);
        directorMode.Margin = new Thickness(8, 0, 12, 0);
        modes.Children.Add(directorMode);
        modes.Children.Add(chapterSelector);
        headerGrid.Children.Add(modes);
        headerGrid.Children.Clear();
        header.Child = new PageHeading(heading, modes);
        panel.Children.Add(header);
        panel.Children.Add(pageBar);
        pageBar.Margin = new Thickness(0, 0, 0, 14);
        panel.Children.Add(notice);
        panel.Children.Add(body);
        scroller.Content = panel;
        root.Children.Add(scroller);
        Content = root;
        chapterSelector.SelectionChanged += async (_, _) =>
        {
            if (chapterSelector.SelectedItem is ComboBoxItem { Tag: string id } && id != chapterId)
            {
                // #429-1: 切章对导演台是破坏性操作（换章重载会重建 DirectorPane，
                // 已输入的指令丢失）——先过离开确认（ScriptView/StoryboardView 的
                // 章节切换处理器同款契约），拒绝则选择器弹回原章节。
                if (!await ConfirmLeaveAsync()) { SelectChapter(chapterId); return; }
                chapterId = id;
                KeyValueStore.Set("generate:chapter:" + ProjectId, id);
                await LoadPagesAsync();
            }
        };
    }

    private void SelectChapter(string id)
    {
        foreach (var item in chapterSelector.Items.OfType<ComboBoxItem>())
            if ((string?)item.Tag == id) { chapterSelector.SelectedItem = item; return; }
    }

    private void SwitchMode(bool isDirector)
    {
        director = isDirector;
        drawMode.IsChecked = !isDirector;
        directorMode.IsChecked = isDirector;
        Render();
    }

    public override async void Activate(WorkspaceContext context)
    {
        base.Activate(context);
        referencesLoaded = false;
        try
        {
            var token = lifetime.Token;
            var chapterTask = Api.SendAsync($"projects/{ProjectId}/chapters", cancellation: token);
            var modelTask = Api.SendAsync("models", cancellation: token);
            var characterTask = Api.SendAsync($"projects/{ProjectId}/characters", cancellation: token);
            var outfitTask = Api.SendAsync($"projects/{ProjectId}/outfits", cancellation: token);
            var packagesTask = ReadPackages(token);
            await Task.WhenAll(chapterTask, modelTask, characterTask, outfitTask, packagesTask);
            if (token.IsCancellationRequested) return;
            var chapterRows = await chapterTask;
            var modelRows = await modelTask;
            referenceCharacters = (await characterTask).EnumerateArray().ToList();
            referenceOutfits = (await outfitTask).EnumerateArray().ToList();
            referencePackages = await packagesTask;
            referencesLoaded = true;
            chapters = chapterRows.EnumerateArray().Select(ChapterItem.From).ToList();
            models = modelRows.EnumerateArray().ToList();
            selectedModel = KeyValueStore.Get("image-model:" + ProjectId);
            chapterSelector.Items.Clear();
            foreach (var chapter in chapters)
                chapterSelector.Items.Add(new ComboBoxItem { Tag = chapter.Id, Content = $"第 {chapter.Ordinal} 章 · {chapter.Title}" });
            if (chapters.Count == 0)
            {
                body.Children.Clear();
                body.Children.Add(Kit.Caption("没有可抽卡页面。先完成动态分页。"));
                return;
            }
            var requestedChapter = KeyValueStore.Get("generate:chapter:" + ProjectId);
            var target = chapters.FirstOrDefault(c => c.Id == requestedChapter)
                ?? chapters.FirstOrDefault(c => c.Id == chapterId) ?? chapters[0];
            chapterId = target.Id;
            foreach (var item in chapterSelector.Items.OfType<ComboBoxItem>())
                if ((string?)item.Tag == target.Id) { chapterSelector.SelectedItem = item; break; }
            chapterId = target.Id;
            await LoadPagesAsync();
        }
        catch (OperationCanceledException) { }
        catch (Exception error) when (error is not OperationCanceledException)
        {
            body.Children.Clear();
            body.Children.Add(Kit.Caption($"页面列表读取失败：{error.Message}"));
        }
    }

    private async Task<List<JsonElement>> ReadPackages(CancellationToken token)
    {
        var result = new List<JsonElement>();
        var project = ProjectId;
        for (var offset = 0; ; offset += 200)
        {
            var rows = await Api.SendAsync($"projects/{project}/character-packages?limit=200&offset={offset}", cancellation: token);
            var items = rows.EnumerateArray().ToList();
            result.AddRange(items);
            if (items.Count < 200) return result;
        }
    }

    private Dictionary<string, ReferenceChoice> EffectiveReferences()
    {
        var defaults = GenerationReferences.Defaults(workbench.Element("storyboard").Array("panels"),
            referenceCharacters, referenceOutfits, referencePackages);
        foreach (var id in defaults.Keys.ToList())
            if (referenceSelections.TryGetValue(id, out var choice)) defaults[id] = choice;
        return defaults;
    }

    private Border BuildReferenceCard(Dictionary<string, ReferenceChoice> references, bool ready)
    {
        var panel = new StackPanel();
        var actions = new WrapPanel();
        actions.Children.Add(Kit.Act(referenceOverrideOpen ? "收起本次参考配置" : "调整本次参考", (_, _) =>
        { referenceOverrideOpen = !referenceOverrideOpen; Render(); }, "Compact"));
        actions.Children.Add(Kit.Act("恢复项目默认", (_, _) => { referenceSelections.Clear(); Render(); }, "Compact"));
        panel.Children.Add(new PageHeading(Kit.FieldLabel("人物与服装参考"), actions));
        panel.Children.Add(Kit.Caption(!referencesLoaded ? "正在读取参考配置…" : references.Count == 0
            ? "本页没有出场人物，无需人物参考。" : ready ? "参考配置已就绪；调整仅影响本次生成。" : "部分参考未就绪，请补全人物或服装素材。"));
        foreach (var (id, choice) in references)
        {
            var character = referenceCharacters.FirstOrDefault(c => c.Text("id") == id);
            var outfit = referenceOutfits.FirstOrDefault(o => o.Text("id") == choice.OutfitId);
            var hasPackage = GenerationReferences.HasPublishedPackage(id, referencePackages);
            var section = new StackPanel { Margin = new Thickness(0, 12, 0, 0) };
            section.Children.Add(Kit.FieldLabel(character.Text("primary_name", id) + (outfit.ValueKind == JsonValueKind.Object ? " · " + outfit.Text("name") : " · 默认服装")));
            section.Children.Add(Kit.Caption(choice.PackageVersionId != null ? "使用指定人物设定包版本" : hasPackage
                ? "继承当前发布的人物设定包" : choice.CharacterAssetId != null ? "继承人物标准参考图" : "缺少人物参考图"));
            if (referenceOverrideOpen)
            {
                if (referencePackages.Any(p => p.Text("character_id") == id))
                {
                    var versions = Selector("人物设定包版本", 280);
                    versions.Items.Add(new ComboBoxItem { Content = "继承当前发布版本", Tag = "" });
                    if (choice.PackageVersionId != null)
                        versions.Items.Add(new ComboBoxItem { Content = "已选版本", Tag = choice.PackageVersionId });
                    versions.SelectedIndex = choice.PackageVersionId == null ? 0 : 1;
                    var loading = false;
                    versions.DropDownOpened += async (_, _) =>
                    {
                        if (loading) return;
                        loading = true;
                        var token = lifetime.Token;
                        var page = currentPage?.Id;
                        try
                        {
                            var detail = await Api.SendAsync($"projects/{ProjectId}/characters/{id}/package", cancellation: token);
                            if (token.IsCancellationRequested || currentPage?.Id != page) return;
                            foreach (var version in detail.Array("versions").Where(v => v.Text("status") != "DRAFT"))
                            {
                                var versionId = version.Text("id");
                                var existing = versions.Items.OfType<ComboBoxItem>().FirstOrDefault(i => (string?)i.Tag == versionId);
                                var label = $"版本 {version.Number("version_number")} · {version.Text("status")}";
                                if (existing != null) existing.Content = label;
                                else versions.Items.Add(new ComboBoxItem { Content = label, Tag = versionId });
                            }
                        }
                        catch (OperationCanceledException) { }
                        catch (Exception error) { notice.Text = "版本读取失败：" + error.Message; }
                        finally { loading = false; }
                    };
                    versions.SelectionChanged += (_, _) =>
                    {
                        if (versions.SelectedItem is not ComboBoxItem { Tag: string version }) return;
                        referenceSelections[id] = choice with { PackageVersionId = version.Length == 0 ? null : version };
                        Render();
                    };
                    section.Children.Add(versions);
                }
                if (!hasPackage && choice.PackageVersionId == null)
                    AddImages("人物参考", character.Array("references").Select(r => r.Text("asset_id")), choice.CharacterAssetId,
                        asset => choice with { CharacterAssetId = asset });
                if (outfit.ValueKind == JsonValueKind.Object)
                    AddImages("服装参考", outfit.Strings("reference_asset_ids"), choice.OutfitAssetId,
                        asset => choice with { OutfitAssetId = asset });
                void AddImages(string title, IEnumerable<string> assets, string? selected, Func<string, ReferenceChoice> update)
                {
                    section.Children.Add(Kit.Caption(title));
                    var images = new WrapPanel();
                    foreach (var asset in assets.Where(a => a.Length > 0))
                    {
                        var chip = new ToggleButton { Style = (Style)Application.Current.FindResource("Chip"),
                            IsChecked = selected == asset, Margin = new Thickness(0, 4, 8, 4),
                            Content = new ImageBox { SourceUrl = Api.PublicUrl($"assets/{asset}/content"), Width = 68, Height = 80 },
                            ToolTip = title + (selected == asset ? " · 已选择" : " · 点击使用") };
                        System.Windows.Automation.AutomationProperties.SetName(chip, title + " " + (images.Children.Count + 1));
                        chip.Click += (_, _) => { referenceSelections[id] = update(asset); Render(); };
                        images.Children.Add(chip);
                    }
                    section.Children.Add(images.Children.Count > 0 ? images : Kit.Caption("尚未上传参考图"));
                }
            }
            panel.Children.Add(section);
        }
        return Wrap(null, panel);
    }

    private async Task LoadPagesAsync()
    {
        var token = lifetime.Token;
        var requestedChapter = chapterId;
        var read = ++pagesRead;
        try
        {
            var rows = await Api.SendAsync($"chapters/{requestedChapter}/pages", cancellation: token);
            if (token.IsCancellationRequested || requestedChapter != chapterId || read != pagesRead) return;
            pages = rows.EnumerateArray().Select(PageItem.From).ToList();
            RenderPageBar();
            var requested = KeyValueStore.Get("generate:page:" + ProjectId);
            var target = pages.FirstOrDefault(p => p.Id == requested) ?? pages.FirstOrDefault();
            if (target == null)
            {
                currentPage = null;
                workbench = default;
                body.Children.Clear();
                body.Children.Add(Kit.Caption("没有可抽卡页面。先完成动态分页。"));
                return;
            }
            await SelectPageAsync(target);
        }
        catch (OperationCanceledException) { }
        catch (Exception error) when (error is not OperationCanceledException)
        {
            if (token.IsCancellationRequested || read != pagesRead) return;
            body.Children.Add(Kit.Caption($"页面列表读取失败：{error.Message}"));
        }
    }

    private void RenderPageBar()
    {
        pageBar.Children.Clear();
        foreach (var item in pages)
        {
            var chip = new ToggleButton
            {
                Content = new StackPanel
                {
                    Children =
                    {
                        new TextBlock { Text = $"第 {item.PageNumber} 页", FontWeight = FontWeights.Bold },
                        new TextBlock { Text = item.StateLabel, Style = (Style)Application.Current.FindResource("Micro") },
                    },
                },
                Style = (Style)Application.Current.FindResource("Chip"),
                IsChecked = currentPage?.Id == item.Id, Margin = new Thickness(0, 0, 6, 0), MinWidth = 78,
            };
            var captured = item;
            chip.Click += async (_, _) => await SelectPageAsync(captured);
            pageBar.Children.Add(chip);
        }
    }

    private async Task SelectPageAsync(PageItem item)
    {
        currentPage = item;
        KeyValueStore.Set("generate:page:" + ProjectId, item.Id);
        referenceSelections.Clear();
        viewedBatchId = null;
        historicalCandidates = null;
        // web pageScopeRef：换页清空检查面板——reviewCandidateId 指向旧页候选时，
        // 面板会在新页渲染且修复按钮以错误分辨率对旧候选提交计费修复。
        reviewCandidateId = null;
        panelError = null;
        reviewInspectJob = default;
        reviewInspectJobIdSeen = "";
        reviewChecking = false;
        // G-3: 检查任务的看护是页作用域的 —— 旧页的在途 PAGE_INSPECT 不得继续驱动新页的轮询/终态刷新。
        //（在途的 Watch 在 currentPage 守卫处返回，不会把旧任务 id 写回。）
        trackedInspectJobs.Clear();
        RenderPageBar();
        await LoadWorkbenchAsync();
    }

    private async Task LoadWorkbenchAsync()
    {
        if (currentPage == null) return;
        var requestedPage = currentPage.Id;
        var requestedProject = ProjectId;
        var request = ++workbenchRead;
        var token = lifetime.Token;
        var requestedBatch = viewedBatchId;
        if (workbench.ValueKind != JsonValueKind.Object || workbench.Element("page").Text("id") != requestedPage)
        {
        body.Children.Clear();
        var spinner = new StackPanel { Orientation = Orientation.Horizontal, Margin = new Thickness(0, 8, 0, 0) };
        spinner.Children.Add(new Spinner { Size = 18 });
        var hint = Kit.Caption("正在载入生成工作台…");
        hint.Margin = new Thickness(10, 0, 0, 0);
        spinner.Children.Add(hint);
        body.Children.Add(spinner);
        }
        try
        {
            var nextTask = Api.SendAsync($"pages/{requestedPage}/generation-workbench", cancellation: token);
            var batchesTask = Api.SendAsync($"pages/{requestedPage}/batches", cancellation: token);
            await Task.WhenAll(nextTask, batchesTask);
            if (token.IsCancellationRequested || request != workbenchRead || currentPage?.Id != requestedPage || ProjectId != requestedProject) return;
            var next = await nextTask;
            var batches = (await batchesTask).EnumerateArray().ToList();
            var unchanged = workbench.ValueKind == JsonValueKind.Object && workbench.GetRawText() == next.GetRawText()
                && string.Join("", pageBatches.Select(b => b.GetRawText())) == string.Join("", batches.Select(b => b.GetRawText()));
            if (requestedBatch != null)
            {
                var history = await Api.SendAsync($"batches/{requestedBatch}/candidates", cancellation: token);
                if (token.IsCancellationRequested || request != workbenchRead || currentPage?.Id != requestedPage || viewedBatchId != requestedBatch) return;
                var rows = history.EnumerateArray().ToList();
                unchanged &= historicalCandidates != null && string.Join("", historicalCandidates.Select(c => c.GetRawText())) == string.Join("", rows.Select(c => c.GetRawText()));
                historicalCandidates = rows;
            }
            pageBatches = batches;
            workbench = next;
            if (next.Element("page").ValueKind == JsonValueKind.Object) currentPage = PageItem.From(next.Element("page"));
            notice.Text = "";
            if (!unchanged || body.Children.Count == 0) Render();
        }
        catch (OperationCanceledException) { }
        catch (Exception error) when (error is not OperationCanceledException)
        {
            if (request == workbenchRead && currentPage?.Id == requestedPage)
                notice.Text = $"生成工作台读取失败：{error.Message}";
        }
    }

    private async Task ViewBatchAsync(string id)
    {
        var pageId = currentPage?.Id;
        var token = lifetime.Token;
        viewedBatchId = id;
        // web 的批次切换（上一批/下一批/下拉选择）都会 setReviewCandidateId(null)：
        // 面板与修复动作是批次作用域的，不能跨批次对旧候选提交。
        reviewCandidateId = null;
        panelError = null;
        try
        {
            var rows = await Api.SendAsync($"batches/{id}/candidates", cancellation: token);
            if (token.IsCancellationRequested || viewedBatchId != id || currentPage?.Id != pageId) return;
            historicalCandidates = rows.EnumerateArray().ToList();
            Render();
        }
        catch (OperationCanceledException) { }
        catch (Exception error) { if (!token.IsCancellationRequested && viewedBatchId == id) notice.Text = error.Message; }
    }

    private void Render()
    {
        body.Children.Clear();
        if (currentPage == null) return;
        if (director)
        {
            directorPane = new DirectorPane(this);
            body.Children.Add(directorPane);
            return;
        }
        directorPane = null;
        var storyboard = workbench.Element("storyboard");
        var readiness = workbench.Element("readiness");
        var production = workbench.Element("production");
        var candidates = historicalCandidates ?? workbench.Array("candidates");
        var batches = pageBatches;
        // 工作台快照里的已暂选候选：横幅（沿用并重新检查）与生产门禁的步骤判定都以它为准。
        var selectedCandidate = workbench.Element("selected_candidate");
        // 就绪状态与参考配置提前算好：旧版本横幅的「重新生成」按钮与生成条共用同一判定。
        var ready = readiness.Flag("ready");
        var references = EffectiveReferences();
        var referenceReady = referencesLoaded && GenerationReferences.Ready(references, referenceOutfits, referencePackages);

        // 旧版本横幅（web stale-candidate-banner）：旧候选可以继续查看，但必须先决定版本
        // ——沿用并重新检查，或按当前分镜重新抽卡——才能进入下一页或导出。
        if (selectedCandidate.ValueKind == JsonValueKind.Object
            && selectedCandidate.Text("version_state", "CURRENT") is "STALE" or "LEGACY_UNKNOWN")
        {
            var banner = new StackPanel();
            banner.Children.Add(new TextBlock { Text = "版本需要决定", Style = (Style)Application.Current.FindResource("SectionIndex") });
            var basedOn = selectedCandidate.Number("based_on_storyboard_version");
            banner.Children.Add(new TextBlock
            {
                Text = $"旧候选基于 {(basedOn > 0 ? $"V{basedOn}" : "未知版本")}，当前分镜为 V{currentPage.StoryboardVersion}",
                FontWeight = FontWeights.Bold, Margin = new Thickness(0, 6, 0, 0),
            });
            banner.Children.Add(new TextBlock
            {
                Text = "旧图可以继续查看，但必须确认版本并重新完成视觉检查后，才能进入下一页或导出。",
                Style = (Style)Application.Current.FindResource("Caption"), TextWrapping = TextWrapping.Wrap, Margin = new Thickness(0, 4, 0, 0),
            });
            var staleRow = new StackPanel { Orientation = Orientation.Horizontal, Margin = new Thickness(0, 10, 0, 0) };
            // 捕获渲染时的候选快照：工作台稍后会被整体替换，闭包不能引用可变字段。
            var staleCandidate = selectedCandidate;
            staleRow.Children.Add(Kit.Act("沿用并重新检查", async (_, _) => await KeepSelectedCandidateAsync(staleCandidate), "InkButton"));
            // G-2: web 横幅的第二个动作 —— 按当前分镜版本重新抽卡（generate.mutate），
            // 复用现有生成路径；禁用条件对齐 web：提交中 / 生产准备未就绪 / 参考未就绪 / 正在查看历史批次。
            var currentBatchId = workbench.Element("current_batch").ValueKind == JsonValueKind.Object
                ? workbench.Element("current_batch").Text("id") : "";
            var latestOrdered = batches.OrderByDescending(b => b.Number("ordinal")).FirstOrDefault();
            var latestBatchId = currentBatchId.Length > 0 ? currentBatchId
                : latestOrdered.ValueKind == JsonValueKind.Object ? latestOrdered.Text("id") : "";
            var viewingHistory = viewedBatchId != null && viewedBatchId != latestBatchId;
            var regenerate = Kit.Act(viewingHistory ? "先切回最新批次" : $"按当前 V{currentPage.StoryboardVersion} 重新生成",
                async (_, _) => await GenerateAsync(), "Compact");
            regenerate.Margin = new Thickness(10, 0, 0, 0);
            regenerate.IsEnabled = !pendingRows.Contains("generate") && ready && referenceReady && !viewingHistory;
            staleRow.Children.Add(regenerate);
            banner.Children.Add(staleRow);
            body.Children.Add(Wrap(null, banner));
        }

        // Production readiness card.
        var readinessCard = new StackPanel();
        readinessCard.Children.Add(new TextBlock { Text = "PRODUCTION CHECK / 页面生产准备", Style = (Style)Application.Current.FindResource("SectionIndex") });
        var blockers = readiness.Array("blockers");
        if (!ready && blockers.Count > 0)
        {
            readinessCard.Children.Add(new TextBlock
            {
                Text = $"{blockers.Count} 项准备工作未完成",
                FontWeight = FontWeights.Bold, Foreground = (Brush)Application.Current.FindResource("Warning"), Margin = new Thickness(0, 6, 0, 0),
            });
            foreach (var blocker in blockers)
            {
                var row = new StackPanel { Orientation = Orientation.Horizontal, Margin = new Thickness(0, 5, 0, 0) };
                row.Children.Add(new TextBlock { Text = "• " + blocker.Text("message"), TextWrapping = TextWrapping.Wrap });
                var route = RouteForBlocker(blocker, currentPage.Id);
                var go = Kit.Act("去处理", async (_, _) => await Context!.NavigateSection(route.Section, route.Query), "Compact");
                go.Margin = new Thickness(10, 0, 0, 0);
                row.Children.Add(go);
                readinessCard.Children.Add(row);
            }
        }
        else
        {
            readinessCard.Children.Add(new TextBlock
            {
                Text = "页面生产条件已全部满足，可以确认参考图后生成 1 个 1K 彩色候选。",
                Style = (Style)Application.Current.FindResource("Caption"), Margin = new Thickness(0, 6, 0, 0),
            });
        }
        body.Children.Add(Wrap("页面生产准备", readinessCard));

        // Model picker.
        var editModels = UsableEditModels();
        var modelCard = new StackPanel();
        modelCard.Children.Add(new TextBlock { Text = "本次页面生成模型（仅显示支持图片编辑的已启用模型）", Style = (Style)Application.Current.FindResource("FieldLabel") });
        if (editModels.Count == 0)
            modelCard.Children.Add(new TextBlock { Text = "暂无已启用且支持参考图编辑的图片模型，请先到系统设置配置供应商。", Foreground = (Brush)Application.Current.FindResource("Danger"), FontSize = 12.5 });
        else
        {
            var row = new WrapPanel { Margin = new Thickness(0, 6, 0, 0) };
            foreach (var model in editModels)
            {
                var alias = model.Text("logical_alias");
                var chip = new ToggleButton
                {
                    Content = $"{model.Text("display_name")} · {model.Text("model_id")}",
                    Style = (Style)Application.Current.FindResource("Chip"),
                    IsChecked = alias == selectedModel, Margin = new Thickness(0, 0, 8, 6),
                };
                chip.Click += (_, _) =>
                {
                    selectedModel = alias;
                    KeyValueStore.Set("image-model:" + ProjectId, alias);
                    foreach (var other in row.Children.OfType<ToggleButton>()) other.IsChecked = ReferenceEquals(other, chip);
                    Render();
                };
                row.Children.Add(chip);
            }
            modelCard.Children.Add(row);
        }
        // The card is added in both cases: it explains either the choice or why generation is blocked.
        body.Children.Add(Wrap(null, modelCard));

        body.Children.Add(BuildReferenceCard(references, referenceReady));

        // Generate bar.
        var generateBar = new StackPanel { Orientation = Orientation.Horizontal, Margin = new Thickness(0, 4, 0, 14) };
        var generate = new Button { Content = GenerateButtonLabel(ready, editModels.Count > 0), Style = (Style)Application.Current.FindResource("InkButton"),
            IsEnabled = ready && referenceReady && editModels.Any(m => m.Text("logical_alias") == selectedModel) && !pendingRows.Contains("generate") };
        if (!referenceReady) generate.Content = referencesLoaded ? "先确认人物与服装参考" : "正在读取参考配置";
        generate.Click += async (_, _) => await GenerateAsync();
        generateBar.Children.Add(generate);
        body.Children.Add(generateBar);

        if (batches.Count > 0)
        {
            var picker = Selector("浏览生成批次", 270);
            foreach (var batch in batches.OrderByDescending(b => b.Number("ordinal")))
                picker.Items.Add(new ComboBoxItem { Tag = batch.Text("id"), Content = $"批次 {batch.Number("ordinal")} · {batch.Text("status")}" });
            picker.SelectedItem = picker.Items.OfType<ComboBoxItem>().FirstOrDefault(i => (string?)i.Tag ==
                (viewedBatchId ?? workbench.Element("current_batch").Text("id")));
            picker.SelectionChanged += async (_, _) =>
            {
                if (picker.SelectedItem is ComboBoxItem { Tag: string id }) await ViewBatchAsync(id);
            };
            picker.Margin = new Thickness(0, 0, 0, 12);
            body.Children.Add(picker);
        }

        // Candidates.
        var displayedBatch = batches.FirstOrDefault(b => b.Text("id") == (viewedBatchId ?? workbench.Element("current_batch").Text("id")));
        var batchLabel = displayedBatch.ValueKind == JsonValueKind.Object ? $"批次 {displayedBatch.Number("ordinal")}" : "当前批次";
        body.Children.Add(new TextBlock { Text = $"BATCH / {batchLabel} · 每个候选记录实际供应商与模型 · 收藏不等于采用", Style = (Style)Application.Current.FindResource("SectionIndex"), Margin = new Thickness(0, 10, 0, 8) });
        if (candidates.Count == 0)
        {
            var empty = new StackPanel();
            empty.Children.Add(new TextBlock { Text = "这个批次还没有候选", FontFamily = (FontFamily)Application.Current.FindResource("Serif"), FontSize = 16, FontWeight = FontWeights.Bold });
            empty.Children.Add(Kit.Caption("完成生产准备并确认参考图后，使用本次选择的图片模型生成 1 张彩色页面。"));
            body.Children.Add(new Border { Style = (Style)Application.Current.FindResource("Card"), Padding = new Thickness(20), Child = empty });
        }
        else
        {
            var grid = new TilePanel { MinimumTileWidth = 230 };
            foreach (var row in candidates)
                grid.Children.Add(new GenerateCandidateCard(this, CandidateItem.From(row)));
            body.Children.Add(grid);
        }

        // 候选视觉检查面板（web generate-section 里候选网格之后、生产门禁之前渲染 InspectionPanel）。
        if (reviewCandidateId is { } reviewTarget)
            body.Children.Add(BuildInspectionCard(reviewTarget));

        // Production gate + next page.
        var gateCard = new StackPanel();
        var productionReady = production.Flag("ready");
        gateCard.Children.Add(new TextBlock { Text = "页面生产门禁", Style = (Style)Application.Current.FindResource("SectionIndex") });
        gateCard.Children.Add(new TextBlock
        {
            Text = productionReady ? "当前页已通过，可以进入下一页" : "当前页尚未生产通过",
            FontWeight = FontWeights.Bold,
            Foreground = productionReady ? (Brush)Application.Current.FindResource("Success") : (Brush)Application.Current.FindResource("Warning"),
            Margin = new Thickness(0, 6, 0, 0),
        });
        // 三步的完成判定与 web generate-section 一致：已暂选（selected_candidate 存在）、
        // 分镜版本已确认（非 STALE/LEGACY_UNKNOWN）、门禁 ready。完成的步骤打勾并着色，
        // 让用户看到"还差哪一步"，而不是三行永远无状态的清单。
        var steps = new StackPanel { Margin = new Thickness(0, 8, 0, 0) };
        var adopted = selectedCandidate.ValueKind == JsonValueKind.Object;
        var versionConfirmed = adopted && selectedCandidate.Text("version_state", "CURRENT") is not ("STALE" or "LEGACY_UNKNOWN");
        foreach (var (step, done) in new[] { ("人工校对并暂选", adopted), ("确认当前分镜版本", versionConfirmed), ("视觉检查通过", productionReady) })
        {
            steps.Children.Add(new TextBlock
            {
                Text = (done ? "✓ " : "• ") + step,
                FontSize = 12.5,
                Foreground = done ? (Brush)Application.Current.FindResource("Success") : (Brush)Application.Current.FindResource("Muted"),
            });
        }
        gateCard.Children.Add(steps);
        if (!productionReady)
        {
            // 未通过时露出第一个阻塞原因（web 的 gateBlockerMessage 取 production.blockers[0].message）。
            var blocker = production.Array("blockers").FirstOrDefault();
            gateCard.Children.Add(new TextBlock
            {
                Text = blocker.ValueKind == JsonValueKind.Object && blocker.Text("message").Length > 0
                    ? blocker.Text("message") : "正在读取当前页生产状态",
                Foreground = (Brush)Application.Current.FindResource("Warning"),
                FontSize = 12.5, TextWrapping = TextWrapping.Wrap, Margin = new Thickness(0, 8, 0, 0),
            });
        }
        var gateRow = new StackPanel { Orientation = Orientation.Horizontal, Margin = new Thickness(0, 12, 0, 0) };
        if (productionReady)
        {
            var next = Kit.Act("生成下一页 →", async (_, _) => await NextPageAsync(), "InkButton");
            gateRow.Children.Add(next);
        }
        var download = Kit.Act("单页 PNG 下载", async (_, _) =>
        {
            if (Context == null) return;
            var exportPage = currentPage;
            var save = new SaveFileDialog { Filter = "PNG 图片|*.png", FileName = $"第{exportPage.PageNumber:D3}页.png" };
            if (save.ShowDialog(Host) != true) return;
            try
            {
                // Streaming save (web: <a href={selectedPagePngUrl}> lets the browser
                // stream): big pages never buffer in memory and a failure (non-2xx such
                // as the 409 production blockers, or a network drop mid-copy) leaves the
                // previous file intact and surfaces the server detail here.
                await Api.SaveDownloadAsync($"pages/{exportPage.Id}/export.png", save.FileName, lifetime.Token);
                notice.Text = "单页 PNG 已保存。";
            }
            catch (OperationCanceledException) when (lifetime.Token.IsCancellationRequested) { }
            catch (OperationCanceledException) { notice.Text = "下载超时，请重试；原有文件未被替换。"; }
            catch (Exception error) when (error is not OperationCanceledException) { notice.Text = error.Message; }
        }, "Compact");
        download.Margin = new Thickness(10, 0, 0, 0);
        if (productionReady) gateRow.Children.Add(download);
        gateCard.Children.Add(gateRow);
        body.Children.Add(Wrap(null, gateCard));
    }

    private string GenerateButtonLabel(bool ready, bool hasModel)
    {
        if (pendingRows.Contains("generate")) return "正在加入 1 个正式任务";
        if (!hasModel || selectedModel.Length == 0) return "先选择图片模型";
        if (!ready) return "先完成页面生产准备";
        return "生成 1 个 1K 彩色候选";
    }

    /// <summary>
    /// 支持图片编辑且对创作者可见的模型（与 web 的 modelOptions 同源：先按 IMAGE + image_edit
    /// 过滤，再过 creatorVisibleModels —— (enabled &amp;&amp; display_enabled)，当前已选 alias 豁免）。
    /// display_enabled 缺失按可见处理（与 ModelOption.From 同约定；/models 接口恒返回该字段）。
    /// </summary>
    private List<JsonElement> UsableEditModels() => models.Where(m => m.Text("model_type") == "IMAGE"
        && m.Array("operations").Any(o => o.ToString() == "image_edit")
        && ((m.Flag("enabled") && m.Element("display_enabled").ValueKind is not JsonValueKind.False)
            || m.Text("logical_alias") == selectedModel)).ToList();

    /// <summary>
    /// 当前是否选定了可用的抽卡模型（web 的 activeDrawModel 判定）：
    /// 生成、升清都必须先有模型，否则后端只会收到 null model_alias 并以 422 拒绝。
    /// </summary>
    internal bool HasUsableDrawModel() => selectedModel.Length > 0
        && UsableEditModels().Any(m => m.Text("logical_alias") == selectedModel);

    // Web routeForBlocker (production-readiness.tsx): the 去处理 jump carries the
    // blocker's target entity — ?character=/?outfit=/?style= preselect the assets view,
    // ?page= locates the storyboard page — instead of dropping the user on a bare tab.
    // The stage fallback (SOURCE/SCRIPT/STORYBOARD/…/PROVIDER/WORKER) mirrors the web's
    // stageRoutes map; unknown stages land on generate exactly like the web.
    internal static (string Section, string Query) RouteForBlocker(JsonElement blocker, string pageId)
    {
        var code = blocker.Text("code");
        var target = blocker.TextOrNull("target_id") ?? "";
        var escaped = Uri.EscapeDataString(target);
        if (code == "MISSING_OUTFIT_ASSIGNMENT")
            // Web also carries the character (?page=…&character=…&edit=outfit) so the
            // storyboard can focus the first VISIBLE panel of that character with no
            // outfit assigned (storyboard-editor focusCharacterId).
            return ("storyboard", $"page={Uri.EscapeDataString(pageId)}" + (escaped.Length > 0 ? $"&character={escaped}" : ""));
        if (code == "MISSING_CHARACTER_REFERENCE")
            return ("assets", $"view=characters&character={escaped}");
        if (code == "MISSING_OUTFIT_REFERENCE")
            return ("assets", $"view=outfits&outfit={escaped}");
        if (code.StartsWith("STYLE_", StringComparison.Ordinal))
            return ("assets", "view=style" + (escaped.Length > 0 ? $"&style={escaped}" : ""));
        var stage = blocker.TextOrNull("stage")?.ToLowerInvariant() ?? "";
        var section = stage switch
        {
            "source" => "source",
            "script" => "script",
            "storyboard" => "storyboard",
            "assets" or "style" => "assets",
            "settings" or "provider" or "worker" => "settings-global",
            _ => "generate",
        };
        return (section, "");
    }

    private static Border Wrap(string? title, StackPanel card)
    {
        if (title != null)
            card.Children.Insert(0, new TextBlock
            {
                Text = title, FontWeight = FontWeights.Bold, FontSize = 14, Margin = new Thickness(0, 0, 0, 4),
            });
        return new Border { Style = (Style)Application.Current.FindResource("Card"), Padding = new Thickness(18), Margin = new Thickness(0, 0, 0, 14), Child = card };
    }

    private async Task GenerateAsync()
    {
        var references = EffectiveReferences();
        if (currentPage == null || selectedModel.Length == 0 || !referencesLoaded || !workbench.Element("readiness").Flag("ready") ||
            !GenerationReferences.Ready(references, referenceOutfits, referencePackages) || !pendingRows.Add("generate")) return;
        var targetPage = currentPage;
        var modelAlias = selectedModel;
        var project = ProjectId;
        var token = lifetime.Token;
        Render();
        try
        {
            JsonElement batch = workbench.Element("current_batch");
            if (batch.ValueKind != JsonValueKind.Object)
                batch = await Api.SendAsync($"pages/{targetPage.Id}/batches", HttpMethod.Post, cancellation: token);
            await Api.SendAsync($"batches/{batch.Text("id")}/candidates", HttpMethod.Post, new
            {
                model_alias = modelAlias,
                resolution = "1K",
                storyboard_version = targetPage.StoryboardVersion,
                reference_selections = references,
            }, cancellation: token);
            Cache.Invalidate("workbench:" + targetPage.Id, "library:" + project, "jobs:" + project);
            if (token.IsCancellationRequested || currentPage?.Id != targetPage.Id || ProjectId != project) return;
            State.Status = "已加入 1 个生成任务";
            viewedBatchId = null;
            historicalCandidates = null;
            // web generate.onSuccess：新批次（或新候选）会替换当前查看的批次；
            // 旧 reviewCandidateId 不清理，检查面板会在新批次下继续渲染且其修复
            // 按钮会对旧候选提交。
            reviewCandidateId = null;
            panelError = null;
            await LoadWorkbenchAsync();
        }
        catch (OperationCanceledException) { }
        catch (Exception error) when (error is not OperationCanceledException)
        {
            if (token.IsCancellationRequested || currentPage?.Id != targetPage.Id) return;
            notice.Text = error.Message;
            pendingRows.Remove("generate");
            Render();
        }
        finally
        {
            pendingRows.Remove("generate");
            if (!token.IsCancellationRequested && currentPage?.Id == targetPage.Id) Render();
        }
    }

    internal async Task CandidateActionAsync(CandidateItem candidate, string action)
    {
        if (currentPage == null || !CanCandidateAction(candidate, action) || !pendingRows.Add(candidate.Id)) return;
        var targetPage = currentPage.Id;
        var project = ProjectId;
        var token = lifetime.Token;
        var modelAlias = selectedModel;
        try
        {
            switch (action)
            {
                case "favorite":
                    await Api.SendAsync($"candidates/{candidate.Id}/favorite", HttpMethod.Patch,
                        new { is_favorite = !candidate.Favorite });
                    break;
                case "delete":
                    if (MessageBox.Show(Host, "删除这个候选？收藏状态也会一并移除。", "删除候选",
                            MessageBoxButton.YesNo, MessageBoxImage.Question) != MessageBoxResult.Yes) return;
                    await Api.SendOptionalAsync($"candidates/{candidate.Id}", HttpMethod.Delete);
                    break;
                case "inspect":
                    // web：点击「视觉检查」先打开检查面板（立即展示已有结果与任务状态），
                    // 再提交新一轮检查；提交失败（如其他客户端已有进行中任务 409）时
                    // 面板保持打开并在页脚展示错误。
                    OpenInspectionPanel(candidate.Id);
                    var inspectJob = await Api.SendAsync($"candidates/{candidate.Id}/inspect", HttpMethod.Post, new
                    {
                        categories = new[] { "SPEAKER", "CHARACTER", "OUTFIT", "PROP", "CONTINUITY" },
                    });
                    // 记下进行中的检查任务：PollTick 靠它继续轮询，终态后再补刷一次门禁。
                    TrackInspectJob(inspectJob);
                    State.Status = "视觉检查任务已创建";
                    break;
                case "select":
                    if (MessageBox.Show(Host, "请确认页面文字已人工校对。暂选后还需要完成视觉检查，才能进入下一页或导出。是否继续？",
                            "人工校对并暂选", MessageBoxButton.YesNo, MessageBoxImage.Question) != MessageBoxResult.Yes) return;
                    await Api.SendAsync($"pages/{targetPage}/select-candidate", HttpMethod.Post, new
                    {
                        candidate_id = candidate.Id,
                        manual_text_confirmed = true,
                        accept_stale = candidate.VersionState != "CURRENT",
                    });
                    break;
                case "upscale2k" or "upscale4k":
                    var resolution = action == "upscale2k" ? "2K" : "4K";
                    // 与 web 的 requireDrawModel 一致：无可用模型时不提交，绝不发送 null model_alias。
                    if (!HasUsableDrawModel())
                    {
                        notice.Text = "请先选择图片模型，再执行升清。";
                        return;
                    }
                    if (MessageBox.Show(Host, $"升至 {resolution} 会调用一次所选图片模型（可能计费），并基于该候选生成一个新批次。是否继续？",
                            "保持结构升清", MessageBoxButton.YesNo, MessageBoxImage.Question) != MessageBoxResult.Yes) return;
                    await Api.SendAsync($"candidates/{candidate.Id}/upscale", HttpMethod.Post, new
                    {
                        model_alias = modelAlias,
                        resolution,
                    });
                    break;
            }
            // web 的 delete/upscale onSuccess 会收起检查面板：删除的候选不再有结果可看；
            // 升清会关闭当前批次并新开 UPSCALE 批次，旧候选不在新批次里。
            var closePanel = (action == "delete" && reviewCandidateId == candidate.Id) || action is "upscale2k" or "upscale4k";
            if (closePanel) { reviewCandidateId = null; panelError = null; }
            Cache.Invalidate("workbench:" + targetPage, "library:" + project, "jobs:" + project, "pages:" + chapterId);
            if (token.IsCancellationRequested || currentPage?.Id != targetPage || ProjectId != project) return;
            await LoadWorkbenchAsync();
            if (closePanel) Render();
        }
        catch (OperationCanceledException) { }
        catch (Exception error) when (error is not OperationCanceledException)
        {
            if (!token.IsCancellationRequested && currentPage?.Id == targetPage && ProjectId == project)
            {
                notice.Text = error.Message;
                // web 把 inspect/repair/upscale 的失败聚合展示在 InspectionPanel 页脚；
                // 面板打开且正是该候选时，镜像到面板内，其余情况仍走顶部通知。
                if (reviewCandidateId == candidate.Id && action is "inspect" or "upscale2k" or "upscale4k")
                {
                    panelError = error.Message;
                    Render();
                }
            }
        }
        finally
        {
            pendingRows.Remove(candidate.Id);
        }
    }

    internal bool CanCandidateAction(CandidateItem candidate, string action)
    {
        if (pendingRows.Contains(candidate.Id)) return false;
        if (action == "delete") return currentPage?.SelectedCandidateId != candidate.Id;
        // 已暂选的候选不能重复暂选（web 显示禁用的"已暂选"按钮）。
        if (action == "select") return candidate.HasImage && !candidate.IsSelected;
        // 升清按 web 的 activeDrawModel 门控：未选模型时按钮禁用并给提示，防止 null model_alias。
        if (action is "upscale2k" or "upscale4k") return candidate.HasImage && HasUsableDrawModel();
        return action == "favorite" || candidate.HasImage;
    }

    /// <summary>
    /// 沿用旧候选（STALE/LEGACY_UNKNOWN）并立即重新检查 — web 的 keepSelectedCandidate 接
    /// POST /pages/{pageId}/selected-candidate/keep（candidate_id + 当前分镜版本 + manual_text_confirmed），
    /// 成功后接着发起一次视觉检查。
    /// </summary>
    internal async Task KeepSelectedCandidateAsync(JsonElement candidate)
    {
        if (currentPage == null || !pendingRows.Add(candidate.Text("id"))) return;
        var targetPage = currentPage;
        var project = ProjectId;
        var token = lifetime.Token;
        try
        {
            // keep 与暂选一样要确认人工文字校对（manual_text_confirmed=true）；确认文案沿用 web 的暂选确认。
            if (MessageBox.Show(Host, "请确认页面文字已人工校对。暂选后还需要完成视觉检查，才能进入下一页或导出。是否继续？",
                    "沿用并重新检查", MessageBoxButton.YesNo, MessageBoxImage.Question) != MessageBoxResult.Yes) return;
            await Api.SendAsync($"pages/{targetPage.Id}/selected-candidate/keep", HttpMethod.Post, new
            {
                candidate_id = candidate.Text("id"),
                storyboard_version = targetPage.StoryboardVersion,
                manual_text_confirmed = true,
            }, cancellation: token);
            // web keepSelectedCandidate.onSuccess：沿用成功即打开检查面板，再提交新一轮检查
            // （inspect 失败时面板保持打开并展示错误，如其他客户端已有进行中的检查任务）。
            OpenInspectionPanel(candidate.Text("id"));
            var inspectJob = await Api.SendAsync($"candidates/{candidate.Text("id")}/inspect", HttpMethod.Post, new
            {
                categories = new[] { "SPEAKER", "CHARACTER", "OUTFIT", "PROP", "CONTINUITY" },
            }, cancellation: token);
            TrackInspectJob(inspectJob);
            State.Status = "已沿用旧候选并创建视觉检查任务";
            Cache.Invalidate("workbench:" + targetPage.Id, "pages:" + chapterId, "jobs:" + project);
            if (token.IsCancellationRequested || currentPage?.Id != targetPage.Id || ProjectId != project) return;
            await LoadWorkbenchAsync();
        }
        catch (OperationCanceledException) { }
        catch (Exception error) when (error is not OperationCanceledException)
        {
            if (!token.IsCancellationRequested && currentPage?.Id == targetPage.Id && ProjectId == project)
            {
                notice.Text = error.Message;
                if (reviewCandidateId == candidate.Text("id")) { panelError = error.Message; Render(); }
            }
        }
        finally
        {
            pendingRows.Remove(candidate.Text("id"));
        }
    }

    private async Task NextPageAsync()
    {
        if (currentPage == null) return;
        try
        {
            var next = await Api.SendAsync($"pages/{currentPage.Id}/next", HttpMethod.Post, cancellation: lifetime.Token);
            Cache.Invalidate("pages:" + chapterId, "workbench:", "library:" + ProjectId);
            var nextPage = pages.FirstOrDefault(p => p.Id == next.Text("id"));
            if (nextPage != null) await SelectPageAsync(nextPage);
            else await LoadPagesAsync();
        }
         catch (OperationCanceledException) { }
        catch (Exception error) when (error is not OperationCanceledException)
        {
            notice.Text = error.Message;
        }
    }

    internal JsonElement Workbench => workbench;
    internal PageItem? CurrentPage => currentPage;
    internal string SelectedModelAlias => selectedModel;
    internal string CurrentChapterId => chapterId;

    // Bridges for the nested director pane and candidate cards.
    internal WorkspaceContext? Ctx => Context;
    internal ApiClient Api2() => Api;
    internal ApiCache Cache2() => Cache;
    internal string ProjectId2 => ProjectId;
    internal void OpenImageExternal(string url, string label) => OpenImage(url, label);
    internal async Task OpenLocalEdit(CandidateItem candidate)
    {
        if (Context == null || currentPage == null || !candidate.HasImage) return;
        new LocalEditWindow(Context, currentPage, candidate, models).ShowDialog();
        await LoadWorkbenchAsync();
    }

    internal async Task ReloadWorkbench() => await LoadWorkbenchAsync();

    private static bool TerminalJobStatus(string status) =>
        status is "COMPLETED" or "FAILED" or "CANCELLED" or "NEEDS_REVIEW";

    private void TrackInspectJob(JsonElement job)
    {
        if (job.ValueKind == JsonValueKind.Object && job.Text("id").Length > 0 && !TerminalJobStatus(job.Text("status")))
            trackedInspectJobs.Add(job.Text("id"));
    }

    /// <summary>打开检查面板（web setReviewCandidateId）：清掉上一个候选的残留结果，立即拉取并重画。</summary>
    internal void OpenInspectionPanel(string candidateId)
    {
        reviewCandidateId = candidateId;
        panelError = null;
        reviewInspections = [];
        inspectionsError = null;
        reviewInspectJob = default;
        reviewInspectJobIdSeen = "";
        reviewChecking = false;
        _ = LoadInspectionsAsync(candidateId);
        Render();
    }

    /// <summary>
    /// 检查结果数据源：GET /candidates/{id}/inspections（web api.inspections）。带页/项目/
    /// 取消令牌与序号守卫，迟到响应不得写入已切换候选/页面的面板。
    /// </summary>
    internal async Task LoadInspectionsAsync(string candidateId)
    {
        var requestedPage = currentPage?.Id;
        var requestedProject = ProjectId;
        var request = ++inspectionsRead;
        var token = lifetime.Token;
        try
        {
            var rows = await Api.SendAsync($"candidates/{candidateId}/inspections", cancellation: token);
            if (token.IsCancellationRequested || request != inspectionsRead || reviewCandidateId != candidateId
                || currentPage?.Id != requestedPage || ProjectId != requestedProject) return;
            var list = rows.EnumerateArray().ToList();
            inspectionsError = null;
            var unchanged = reviewInspections.Count == list.Count
                && string.Join("", reviewInspections.Select(item => item.GetRawText())) == string.Join("", list.Select(item => item.GetRawText()));
            reviewInspections = list;
            if (!unchanged) Render();
        }
        catch (OperationCanceledException) { }
        catch (Exception error) when (error is not OperationCanceledException)
        {
            if (token.IsCancellationRequested || request != inspectionsRead || reviewCandidateId != candidateId) return;
            inspectionsError = error.Message;
            Render();
        }
    }

    /// <summary>
    /// web latestInspections：按当前分镜版本过滤（item.storyboard_version 与页不符的旧结果
    /// 不展示），再每个维度（category）取最新一条（接口按 created_at 倒序，首次出现的即最新）。
    /// </summary>
    internal List<JsonElement> LatestInspections()
    {
        var latest = new Dictionary<string, JsonElement>();
        var currentVersion = currentPage?.StoryboardVersion ?? 0;
        foreach (var item in reviewInspections)
        {
            if (item.ValueKind != JsonValueKind.Object) continue;
            var version = item.Element("storyboard_version");
            if (currentVersion > 0 && version.ValueKind == JsonValueKind.Number
                && version.TryGetInt32(out var itemVersion) && itemVersion != currentVersion) continue;
            var category = item.Text("category");
            if (!latest.ContainsKey(category)) latest[category] = item;
        }
        return latest.Values.ToList();
    }

    /// <summary>web display.recommendedRepairType：按检查维度推导修复范围（提交载荷的一部分）。</summary>
    internal static string RecommendedRepairType(string category) => category switch
    {
        "SPEAKER" => "BUBBLE_REGION",
        "CHARACTER" or "OUTFIT" or "PROP" => "PANEL",
        _ => "PAGE",
    };

    /// <summary>
    /// web display.inspectionSummary：details 带 expected/observed 时给「应为/实为」对照，
    /// 否则逐键罗列；完全为空时给固定文案。
    /// </summary>
    internal static string InspectionSummary(JsonElement details)
    {
        if (details.ValueKind != JsonValueKind.Object) return "模型未补充说明";
        var expected = details.TextOrNull("expected");
        var observed = details.TextOrNull("observed");
        if ((expected ?? "").Length > 0 || (observed ?? "").Length > 0)
        {
            return string.Join("；", new[]
            {
                string.IsNullOrEmpty(expected) ? null : $"应为：{expected}",
                string.IsNullOrEmpty(observed) ? null : $"实为：{observed}",
            }.Where(part => part != null));
        }
        var parts = details.EnumerateObject()
            .Select(property => $"{property.Name}: {DetailValue(property.Value)}")
            .ToList();
        return parts.Count > 0 ? string.Join("；", parts) : "模型未补充说明";
    }

    private static string DetailValue(JsonElement value) => value.ValueKind switch
    {
        JsonValueKind.String => value.ToString(),
        JsonValueKind.Number or JsonValueKind.True or JsonValueKind.False => value.ToString(),
        JsonValueKind.Null or JsonValueKind.Undefined => "",
        _ => value.GetRawText(),
    };

    /// <summary>web display.inspectionBubbleDiffs：details.bubble_diffs 只保留对象项。</summary>
    internal static List<JsonElement> InspectionBubbleDiffs(JsonElement details)
    {
        var diffs = details.Element("bubble_diffs");
        return diffs.ValueKind == JsonValueKind.Array
            ? diffs.EnumerateArray().Where(item => item.ValueKind == JsonValueKind.Object).ToList()
            : [];
    }

    /// <summary>候选视觉检查面板（web inspection-panel.tsx 的桌面移植）：结论、逐项 issue 与修复入口。</summary>
    private Border BuildInspectionCard(string candidateId)
    {
        var panel = new StackPanel();
        var heading = new StackPanel();
        heading.Children.Add(new TextBlock { Text = "AI QUALITY CHECK", Style = (Style)Application.Current.FindResource("SectionIndex") });
        heading.Children.Add(new TextBlock
        {
            Text = "候选视觉检查",
            FontFamily = (FontFamily)Application.Current.FindResource("Serif"),
            FontSize = 18, FontWeight = FontWeights.Bold, Margin = new Thickness(0, 5, 0, 0),
        });
        var actions = new StackPanel { Orientation = Orientation.Horizontal };
        actions.Children.Add(Kit.Act("关闭", (_, _) =>
        {
            reviewCandidateId = null;
            panelError = null;
            Render();
        }, "Compact"));
        panel.Children.Add(new PageHeading(heading, actions));
        panel.Children.Add(Kit.Caption("检查说话人归属、角色、服装、道具和连续性；文字由人工校对。"));

        var latest = LatestInspections();
        if (latest.Count == 0)
        {
            // web 等待行：检查任务进行中时显示任务状态与进度，否则显示读取文案。
            var wait = new StackPanel { Orientation = Orientation.Horizontal, Margin = new Thickness(0, 10, 0, 0) };
            wait.Children.Add(new Spinner { Size = 18 });
            var jobText = reviewInspectJob.ValueKind == JsonValueKind.Object
                ? $"检查任务 {Labels.Map(Labels.JobStatus, reviewInspectJob.Text("status"))} · {reviewInspectJob.Number("progress")}%"
                : null;
            var hint = Kit.Caption(inspectionsError != null ? $"检查结果读取失败：{inspectionsError}" : jobText ?? "正在读取检查结果");
            hint.Margin = new Thickness(10, 0, 0, 0);
            wait.Children.Add(hint);
            panel.Children.Add(wait);
        }
        else
        {
            foreach (var inspection in latest)
                panel.Children.Add(BuildInspectionRow(inspection));
        }
        if (panelError != null)
            panel.Children.Add(new TextBlock
            {
                Text = panelError, Foreground = (Brush)Application.Current.FindResource("Danger"),
                FontSize = 12.5, TextWrapping = TextWrapping.Wrap, Margin = new Thickness(0, 8, 0, 0),
            });
        var card = Wrap(null, panel);
        System.Windows.Automation.AutomationProperties.SetName(card, "候选视觉检查面板");
        return card;
    }

    /// <summary>单个检查结论（web inspection-panel 的 article）：维度、结论、分数、摘要、气泡差异与修复入口。</summary>
    private Border BuildInspectionRow(JsonElement inspection)
    {
        var category = inspection.Text("category");
        var outcome = inspection.Text("outcome");
        // web 的通过集合：PASS / ACCEPTABLE / MATCH；其余（MISMATCH/MISSING/EXTRA/未知）按未通过呈现。
        var passed = outcome is "PASS" or "ACCEPTABLE" or "MATCH";
        var details = inspection.Element("details");
        var content = new StackPanel();
        content.Children.Add(new TextBlock
        {
            Text = $"{Labels.Map(Labels.InspectionCategory, category)} · {Labels.Map(Labels.InspectionOutcome, outcome)} · {ScoreLabel(inspection)}",
            FontWeight = FontWeights.Bold,
            Foreground = passed ? (Brush)Application.Current.FindResource("Success") : (Brush)Application.Current.FindResource("Warning"),
        });
        var summary = Kit.Caption(InspectionSummary(details));
        summary.TextWrapping = TextWrapping.Wrap;
        summary.Margin = new Thickness(0, 4, 0, 0);
        content.Children.Add(summary);
        var diffs = InspectionBubbleDiffs(details);
        for (var index = 0; index < diffs.Count; index++)
        {
            var diff = diffs[index];
            var raw = diff.Element("balloon_index");
            var balloon = raw.ValueKind == JsonValueKind.Number && raw.TryGetInt32(out var number) ? number : index + 1;
            var line = new TextBlock
            {
                // web：气泡编号缺省用序号；相似度仅数字时给百分比。
                Text = $"气泡 {balloon:D2} · 目标：{diff.Text("target_text")} · 识别：{diff.Text("recognized_text")} · {SimilarityLabel(diff)}",
                Style = (Style)Application.Current.FindResource("Micro"), Margin = new Thickness(0, 3, 0, 0),
            };
            content.Children.Add(line);
        }
        if (!passed)
        {
            if (category == "TEXT")
            {
                var manual = Kit.Caption("请人工校对；确认后可直接采用");
                manual.Margin = new Thickness(0, 8, 0, 0);
                content.Children.Add(manual);
            }
            else
            {
                var repair = Kit.Act("修复" + Labels.Map(Labels.RepairType, RecommendedRepairType(category)),
                    async (_, _) => await RepairCandidateAsync(inspection), "Compact");
                repair.Margin = new Thickness(0, 8, 0, 0);
                repair.IsEnabled = !pendingRows.Contains("repair");
                content.Children.Add(repair);
            }
        }
        return new Border
        {
            BorderBrush = passed ? (Brush)Application.Current.FindResource("Success") : (Brush)Application.Current.FindResource("Warning"),
            BorderThickness = new Thickness(3, 0, 0, 0),
            Padding = new Thickness(8, 4, 0, 4),
            Margin = new Thickness(0, 10, 0, 0),
            Child = content,
        };
    }

    private static string ScoreLabel(JsonElement inspection)
    {
        var score = inspection.Element("score");
        return score.ValueKind == JsonValueKind.Number && score.TryGetDouble(out var value)
            ? $"{Math.Round(value * 100)}%" : "—";
    }

    private static string SimilarityLabel(JsonElement diff)
    {
        var similarity = diff.Element("similarity");
        return similarity.ValueKind == JsonValueKind.Number && similarity.TryGetDouble(out var value)
            ? $"{Math.Round(value * 100)}%" : "—";
    }

    /// <summary>
    /// 修复动作的分辨率来源（web repairCandidate）：优先工作台已暂选候选（同 id，独立于批次列表），
    /// 否则取当前查看批次里的该候选；都找不到时不得静默回退 1K（2K/4K 候选会被后端 422/409 拒绝）。
    /// </summary>
    private string ResolutionForReviewCandidate(string candidateId)
    {
        var selected = workbench.Element("selected_candidate");
        if (selected.ValueKind == JsonValueKind.Object && selected.Text("id") == candidateId)
            return selected.Text("resolution");
        var row = (historicalCandidates ?? workbench.Array("candidates")).FirstOrDefault(c => c.Text("id") == candidateId);
        return row.ValueKind == JsonValueKind.Object ? row.Text("resolution") : "";
    }

    /// <summary>
    /// 逐项修复（web repairCandidate）：按 issue 的检查结果 id 与推荐修复范围提交
    /// POST /candidates/{id}/repairs；成功后收起面板（修复会关闭当前批次并新开 REPAIR 批次，
    /// 旧候选不在新批次里），并失效工作台/候选/任务/页面数据。
    /// </summary>
    internal async Task RepairCandidateAsync(JsonElement inspection)
    {
        if (currentPage == null || reviewCandidateId is not { } candidateId)
        {
            panelError = "请先选择要修复的候选";
            Render();
            return;
        }
        if (!pendingRows.Add("repair")) return;
        var targetPage = currentPage;
        var project = ProjectId;
        var token = lifetime.Token;
        panelError = null;
        Render();
        try
        {
            // 与 web 的 requireDrawModel 一致：无可用模型时不提交，绝不发送空 model_alias。
            if (!HasUsableDrawModel())
            {
                panelError = "请先选择一个支持参考图编辑的图片模型";
                return;
            }
            var resolution = ResolutionForReviewCandidate(candidateId);
            if (resolution.Length == 0)
            {
                panelError = "候选分辨率未知，请刷新后重试";
                return;
            }
            await Api.SendAsync($"candidates/{candidateId}/repairs", HttpMethod.Post, new
            {
                inspection_result_id = inspection.Text("id"),
                repair_type = RecommendedRepairType(inspection.Text("category")),
                target_regions = inspection.Element("regions").ValueKind == JsonValueKind.Array
                    ? inspection.Element("regions") : (object)Array.Empty<object>(),
                target_fields = Array.Empty<string>(),
                model_alias = selectedModel,
                resolution,
            }, cancellation: token);
            reviewCandidateId = null;
            panelError = null;
            State.Status = "已创建修复任务";
            Cache.Invalidate("workbench:" + targetPage.Id, "library:" + project, "jobs:" + project, "pages:" + chapterId);
            if (token.IsCancellationRequested || currentPage?.Id != targetPage.Id || ProjectId != project) return;
            await LoadWorkbenchAsync();
        }
        catch (OperationCanceledException) { }
        catch (Exception error) when (error is not OperationCanceledException)
        {
            if (!token.IsCancellationRequested && currentPage?.Id == targetPage.Id && ProjectId == project) panelError = error.Message;
        }
        finally
        {
            pendingRows.Remove("repair");
            if (!token.IsCancellationRequested && currentPage?.Id == targetPage.Id) Render();
        }
    }

    /// <summary>
    /// 视觉检查任务的看护（对应 web use-generation-workspace 的轮询与终态失效）：
    /// 有活动 PAGE_INSPECT 时持续刷新工作台；跟踪中的任务转为终态后，再补刷一次
    /// 工作台/门禁 —— Worker 的最终页面状态提交可能晚于最后一次轮询。
    /// </summary>
    private async Task WatchInspectJobsAsync()
    {
        if (jobsWatchBusy || currentPage == null || Context == null) return;
        jobsWatchBusy = true;
        try
        {
            var page = currentPage.Id;
            var project = ProjectId;
            var token = lifetime.Token;
            var jobs = await Api.SendAsync(QueryBuilder.Build($"projects/{project}/jobs", ("archived", "false")), cancellation: token);
            if (token.IsCancellationRequested || currentPage?.Id != page || ProjectId != project) return;
            var candidateIds = workbench.Array("candidates").Select(c => c.Text("id")).ToHashSet();
            var activeIds = new HashSet<string>();
            foreach (var job in jobs.EnumerateArray())
            {
                if (job.Text("job_type") != "PAGE_INSPECT" || TerminalJobStatus(job.Text("status"))) continue;
                // 与 web 一致只关心当前工作台候选的检查任务；已跟踪的任务（如历史批次候选）继续跟到终态。
                if (candidateIds.Contains(job.Text("target_id")) || trackedInspectJobs.Contains(job.Text("id")))
                    activeIds.Add(job.Text("id"));
            }
            var turned = trackedInspectJobs.Except(activeIds).ToList();
            trackedInspectJobs.Clear();
            foreach (var id in activeIds) trackedInspectJobs.Add(id);
            if (activeIds.Count > 0 || turned.Count > 0) _ = LoadWorkbenchAsync();
            if (turned.Count > 0)
            {
                // G-4: web 在检查终态同时失效 pages —— 页面生产状态（continuity/门禁）来自 pages
                // 数据，只刷工作台会让页面栏停在检查前的状态。
                Cache.Invalidate("pages:" + chapterId);
                _ = RefreshPageBarAsync();
            }
            // ── 检查面板的数据看护（web reviewJob + inspections 轮询 + 终态 invalidation）──
            // 面板打开期间按 target_id 看护该候选的 PAGE_INSPECT，不限提交来源（其他客户端
            // 提交的外部任务同样驱动面板刷新）；进行中每拍重读检查结果（web 2500ms 轮询），
            // 任务身份变化或活动→终态翻转时补拉结果并刷新工作台/页面 —— 对齐 web 终态
            // invalidation 集合：inspections、generation-workbench、candidates、chapter-production
            //（桌面的门禁/候选来自工作台，章节生产状态来自 pages，两者都在这里覆盖）。
            var reviewId = reviewCandidateId;
            var reviewJob = default(JsonElement);
            if (reviewId != null)
            {
                foreach (var job in jobs.EnumerateArray())
                {
                    if (job.Text("job_type") != "PAGE_INSPECT" || job.Text("target_id") != reviewId) continue;
                    reviewJob = job;
                    break;
                }
            }
            reviewInspectJob = reviewJob;
            var reviewActive = reviewJob.ValueKind == JsonValueKind.Object && !TerminalJobStatus(reviewJob.Text("status"));
            if (reviewId == null)
            {
                reviewInspectJobIdSeen = "";
                reviewChecking = false;
                return;
            }
            var jobId = reviewJob.ValueKind == JsonValueKind.Object ? reviewJob.Text("id") : "";
            var jobChanged = jobId != reviewInspectJobIdSeen;
            reviewInspectJobIdSeen = jobId;
            var turnedTerminal = reviewChecking && !reviewActive;
            reviewChecking = reviewActive;
            if (reviewActive || jobChanged || turnedTerminal) _ = LoadInspectionsAsync(reviewId);
            if (jobChanged || turnedTerminal)
            {
                // G-4 的 turned 只覆盖 trackedInspectJobs（本会话提交）；外部来源的检查任务
                // 转终态时，这里补上同一组工作台/页面刷新。
                if (!(turnedTerminal && reviewJob.ValueKind == JsonValueKind.Object && turned.Contains(reviewJob.Text("id"))))
                {
                    _ = LoadWorkbenchAsync();
                    Cache.Invalidate("pages:" + chapterId);
                    _ = RefreshPageBarAsync();
                }
            }
        }
        catch (OperationCanceledException) { }
        catch (Exception) { /* 任务列表读取失败只影响本轮看护，下一拍重试，不打断抽卡轮询 */ }
        finally
        {
            jobsWatchBusy = false;
        }
    }

    /// <summary>检查终态后的轻量页面栏刷新：只重读 pages 列表并重画页签，不触发选页/工作台重载。</summary>
    private async Task RefreshPageBarAsync()
    {
        var chapter = chapterId;
        try
        {
            var rows = await Api.SendAsync($"chapters/{chapter}/pages", cancellation: lifetime.Token);
            if (lifetime.Token.IsCancellationRequested || chapter != chapterId) return;
            pages = rows.EnumerateArray().Select(PageItem.From).ToList();
            if (currentPage != null) currentPage = pages.FirstOrDefault(p => p.Id == currentPage.Id) ?? currentPage;
            RenderPageBar();
        }
        catch (OperationCanceledException) { }
        catch (Exception) { /* 页面栏刷新失败不影响工作台轮询，用户刷新/切页时会重新拉取 */ }
    }

    public override void PollTick()
    {
        if (currentPage == null) return;
        if (director && directorPane is { } pane && (pane.Busy || pane.HasDraft)) return;   // keep drafts alive
        var active = workbench.Array("candidates").Any(c => c.Text("status") is "QUEUED" or "GENERATING");
        if (active || directorPane?.Busy == true) _ = LoadWorkbenchAsync();
        // 轮询条件同时覆盖生成中的候选与进行中的视觉检查（web 的 refetchInterval 两者都看）。
        // G-1: checking 不限于本会话提交的任务 —— 任意来源（其他客户端/重启前）的非终态
        // PAGE_INSPECT，只要 target 属当前候选集就要继续看护。入口数据源即 Watch 自己拉取的
        // 全项目活跃任务列表（State/DockQueue 只暴露第一项，无法按 target 判定）；
        // Watch 自带 jobsWatchBusy 防重入，每拍最多 spawn 一次。当前页完全没有候选时无检查可言，
        // 跳过探测以免空转请求；检查面板打开时除外 —— 面板要按 target 看护该候选的检查任务
        //（含历史批次候选与外部来源），并在终态后补拉结果。
        if (active || trackedInspectJobs.Count > 0 || workbench.Array("candidates").Count > 0 || reviewCandidateId != null)
            _ = WatchInspectJobsAsync();
    }

    public override Task RefreshAsync()
    {
        // #429-1: PollTick 对有草稿的导演台早已「keep drafts alive」（HasDraft 早退），
        // 用户刷新同样不得丢稿——LoadWorkbenchAsync 在数据变化时 Render() 会重建
        // DirectorPane，已输入的指令随之消失。守卫复用同一 HasDraft 判定；F5 在
        // 无草稿时照常重载。
        if (DirectorDraftActive) return Task.CompletedTask;
        if (currentPage != null) _ = LoadWorkbenchAsync();
        // 手动刷新同样覆盖检查面板的数据源（web 的失效会让 inspections 查询重新拉取）。
        if (reviewCandidateId is { } reviewId) _ = LoadInspectionsAsync(reviewId);
        return Task.CompletedTask;
    }

    // #429-1/#428: 导演台草稿判定——PollTick 的 keep-drafts 谓词复用到这里
    // （RefreshAsync/切章确认/保真激活三条路径共用同一份语义）。
    private bool DirectorDraftActive => director && directorPane is { HasDraft: true };

    // 测试缝：headless 回归检查用无模态实现替换离开确认（StoryboardView 的
    // LeaveConfirmOverride 同一模式）；生产路径为 null。
    internal Func<Task<bool>>? LeaveConfirmOverride;

    public override Task<bool> ConfirmLeaveAsync()
    {
        // 测试缝优先：headless 检查无模态驱动拒绝/同意分支，否则卡死在 MessageBox。
        if (LeaveConfirmOverride is { } prompt) return prompt();
        if (!DirectorDraftActive) return Task.FromResult(true);
        var result = MessageBox.Show(Host, "导演指令尚未提交，离开会丢弃已输入的指令与预览选择。仍要离开吗？",
            "离开确认", MessageBoxButton.YesNo, MessageBoxImage.Question);
        return Task.FromResult(result == MessageBoxResult.Yes);
    }

    /// <summary>
    /// #428: 重连（重新连接 → ConnectAsync → OpenProjectAsync 同项目分支）在用户
    /// 拒绝弃稿时的保真激活。重连已 Dispose 旧 ApiClient，必须重绑新上下文，但
    /// 不能走 Activate 的整链重载——换章/重激活路径的 Render() 会 new 一个
    /// DirectorPane，已输入的指令（HasDraft）直接消失。无草稿时照常全量激活。
    /// </summary>
    internal void ActivatePreservingDrafts(WorkspaceContext context)
    {
        if (!DirectorDraftActive)
        {
            Activate(context);
            return;
        }
        base.Activate(context);
    }
}

internal sealed class GenerateCandidateCard : Border
{
    public GenerateCandidateCard(GenerateView view, CandidateItem candidate)
    {
        BorderBrush = (Brush)Application.Current.FindResource("Line");
        BorderThickness = new Thickness(1);
        Background = (Brush)Application.Current.FindResource("Surface");
        Padding = new Thickness(12);
        Margin = new Thickness(0, 0, 12, 12);
        HorizontalAlignment = HorizontalAlignment.Stretch;
        Margin = new Thickness(0);
        var panel = new StackPanel();
        var artwork = new Border
        {
            Height = 180, Background = (Brush)Application.Current.FindResource("PaperDeep"),
            Cursor = Cursors.Hand, Margin = new Thickness(0, 0, 0, 8),
        };
        var url = candidate.ContentUrl.Length > 0 ? candidate.ContentUrl
            : candidate.AssetId.Length > 0 ? $"assets/{candidate.AssetId}/content" : "";
        if (url.Length > 0 && view.Ctx != null)
        {
            artwork.Child = new ImageBox { SourceUrl = view.Ctx.Api.PublicUrl(url) };
            artwork.MouseLeftButtonDown += (_, _) => view.OpenImageExternal(url, $"候选 {candidate.Ordinal}");
        }
        else
            artwork.Child = new TextBlock
            {
                Text = candidate.Status == "FAILED" ? "生成失败" : "等待 Worker 生成",
                Style = (Style)Application.Current.FindResource("Micro"),
                HorizontalAlignment = HorizontalAlignment.Center, VerticalAlignment = VerticalAlignment.Center,
            };
        panel.Children.Add(artwork);
        panel.Children.Add(new TextBlock
        {
            Text = $"候选 {candidate.Ordinal} · {candidate.ModelAlias}",
            FontWeight = FontWeights.Bold, FontSize = 12.5, TextTrimming = TextTrimming.CharacterEllipsis, TextWrapping = TextWrapping.NoWrap,
        });
        panel.Children.Add(new TextBlock
        {
            Text = $"{candidate.Resolution} · {candidate.StatusLabel} · {candidate.VersionLabel}",
            Style = (Style)Application.Current.FindResource("Micro"), Margin = new Thickness(0, 3, 0, 6),
        });
        var actions = new WrapPanel();
        actions.Children.Add(CardAction(view, candidate, candidate.Favorite ? "♥ 已收藏" : "♥ 收藏", "favorite"));
        // 已暂选的候选显示禁用的"已暂选"，与 web 一致，防止重复提交暂选；
        // 旧版本候选的暂选文案区分版本语义（web："人工校对并暂选" vs "确认旧版本并暂选"），
        // accept_stale 提交行为保持不变（CandidateActionAsync 里按 VersionState 判定）。
        actions.Children.Add(CardAction(view, candidate, candidate.IsSelected ? "已暂选"
            : candidate.VersionState == "CURRENT" ? "人工校对并暂选" : "确认旧版本并暂选", "select"));
        actions.Children.Add(CardAction(view, candidate, "视觉检查", "inspect"));
        if (candidate.HasImage)
            actions.Children.Add(Kit.Act("局部修改", async (_, _) => await view.OpenLocalEdit(candidate), "Compact"));
        if (candidate.Resolution == "1K") actions.Children.Add(CardAction(view, candidate, "升 2K", "upscale2k"));
        if (candidate.Resolution != "4K") actions.Children.Add(CardAction(view, candidate, "升 4K", "upscale4k"));
        actions.Children.Add(CardAction(view, candidate, "删除", "delete"));
        panel.Children.Add(actions);
        Child = panel;
    }

    private static Button CardAction(GenerateView view, CandidateItem candidate, string label, string action)
    {
        var button = Kit.Act(label, async (sender, _) =>
        {
            var source = (Button)sender;
            source.IsEnabled = false;
            try { await view.CandidateActionAsync(candidate, action); }
            finally { source.IsEnabled = view.CanCandidateAction(candidate, action); }
        },
            action == "delete" ? "CompactDanger" : "Compact");
        button.IsEnabled = view.CanCandidateAction(candidate, action);
        if (action is "upscale2k" or "upscale4k" && !view.HasUsableDrawModel())
        {
            // 未选模型时按钮禁用，但要把原因说出来（web 的按钮 title 同款文案）。
            button.ToolTip = "先选择图片模型；升清会调用一次所选图片模型（可能计费）并生成新候选";
        }
        button.Margin = new Thickness(0, 0, 6, 6);
        button.MinHeight = 30;
        button.FontSize = 11.5;
        return button;
    }
}

/// <summary>Director pane: scope chips, rule-stub compile, preview, journal.</summary>
internal sealed class DirectorPane : Border
{
    private readonly GenerateView view;
    private readonly TextBox commandInput = new() { AcceptsReturn = true, MinHeight = 54 };
    private readonly StackPanel scopes = new() { Orientation = Orientation.Horizontal };
    private readonly StackPanel preview = new();
    private readonly StackPanel history = new();
    private DirectorScope? selection;
    private DirectorPlanResult? plan;
    private JsonElement previewGroup;
    private bool busy;
    private string? retryOfCommandId;

    public bool Busy => busy;

    /// <summary>True while the user has an unconfirmed draft; polling must not rebuild the pane then.</summary>
    public bool HasDraft => commandInput.Text.Trim().Length > 0 || selection != null || previewGroup.ValueKind == JsonValueKind.Object;

    public DirectorPane(GenerateView view)
    {
        this.view = view;
        Style = (Style)Application.Current.FindResource("Card");
        Padding = new Thickness(18);
        var panel = new StackPanel();
        panel.Children.Add(new TextBlock { Text = "DIRECTOR / 导演台 · 规则解析，非模型", Style = (Style)Application.Current.FindResource("SectionIndex") });
        panel.Children.Add(new TextBlock
        {
            Text = "先点作用域，再写短指令。预览确认后才会执行；导演台不会自动调用图片模型，也不会整页重绘。",
            Style = (Style)Application.Current.FindResource("Micro"), Margin = new Thickness(0, 6, 0, 10), TextWrapping = TextWrapping.Wrap,
        });
        BuildScopes();
        panel.Children.Add(scopes);
        var commandRow = new DockPanel { Margin = new Thickness(0, 10, 0, 0) };
        var propose = Kit.Act("预览", async (_, _) => await ProposeAsync(), "InkButton");
        propose.MinWidth = 96;
        DockPanel.SetDock(propose, Dock.Right);
        commandRow.Children.Add(propose);
        System.Windows.Automation.AutomationProperties.SetName(commandInput, "导演指令");
        commandInput.Margin = new Thickness(0, 0, 10, 0);
        commandRow.Children.Add(commandInput);
        panel.Children.Add(commandRow);
        panel.Children.Add(preview);
        panel.Children.Add(new TextBlock { Text = "HISTORY / 命令历史", Style = (Style)Application.Current.FindResource("SectionIndex"), Margin = new Thickness(0, 16, 0, 8) });
        panel.Children.Add(history);
        Child = panel;
        _ = LoadHistoryAsync();
    }

    private void BuildScopes()
    {
        scopes.Children.Clear();
        var storyboard = view.Workbench.Element("storyboard");
        var chips = new List<ToggleButton>();
        void Add(string label, DirectorScope scope)
        {
            var chip = new ToggleButton
            {
                Content = label, Style = (Style)Application.Current.FindResource("Chip"),
                IsChecked = selection?.Kind == scope.Kind && selection?.PanelId == scope.PanelId,
                Margin = new Thickness(0, 0, 8, 6),
            };
            chip.Click += (_, _) =>
            {
                selection = selection is { } current && current.Kind == scope.Kind && current.PanelId == scope.PanelId && current.DialogueId == scope.DialogueId
                    ? null : scope;
                BuildScopes();
            };
            chips.Add(chip);
            scopes.Children.Add(chip);
        }
        Add("整页", DirectorScope.Page());
        foreach (var panelRow in storyboard.Array("panels").OrderBy(p => p.Number("reading_order")))
        {
            Add($"格 {panelRow.Number("reading_order")}", DirectorScope.Panel(panelRow.Text("id")));
            foreach (var dialogue in panelRow.Array("dialogues"))
                Add($"格{panelRow.Number("reading_order")}·气泡{dialogue.Number("reading_order")}", DirectorScope.Dialogue(panelRow.Text("id"), dialogue.Text("id")));
        }
    }

    private async Task ProposeAsync()
    {
        if (busy || view.CurrentPage == null) return;
        busy = true;
        try
        {
            var storyboard = view.Workbench.Element("storyboard");
            var characters = await view.Api2().SendAsync($"projects/{view.ProjectId2}/characters");
            var visibleIds = storyboard.Array("panels").SelectMany(p => p.Strings("characters")).Distinct().ToList();
            var visible = characters.EnumerateArray().Where(c => visibleIds.Contains(c.Text("id"))).ToList();
            // Scene-level commands need the real scene version (web reads it from the script).
            int? sceneVersion = null;
            var primarySceneId = view.Workbench.Element("page").Array("scene_ids").FirstOrDefault().ToString();
            if (primarySceneId.Length > 0)
            {
                var script = await view.Api2().SendAsync($"chapters/{view.CurrentChapterId}/script");
                sceneVersion = script.Array("scenes").FirstOrDefault(s => s.Text("id") == primarySceneId).Number("version");
                if (sceneVersion == 0) sceneVersion = null;
            }
            plan = DirectorRules.Compile(view.Workbench.Element("page"), view.ProjectId2, storyboard, visible, selection,
                commandInput.Text, retryOfCommandId,
                view.Workbench.Array("candidates").Any(c => c.Text("status") is "QUEUED" or "GENERATING"),
                sceneVersion);
            if (plan.Kind == "command" && plan.Envelope is Dictionary<string, object?> envelope)
            {
                var proposed = await view.Api2().SendAsync($"projects/{view.ProjectId2}/director/command-groups", HttpMethod.Post,
                    new { command_group_id = envelope["command_group_id"], commands = new[] { envelope } });
                previewGroup = proposed;
                RenderPreview();
            }
            else RenderPreview();
        }
        catch (Exception error)
        {
            preview.Children.Clear();
            preview.Children.Add(Kit.Caption("预览失败：" + error.Message));
        }
        finally { busy = false; }
    }

    private void RenderPreview()
    {
        preview.Children.Clear();
        preview.Margin = new Thickness(0, 12, 0, 0);
        if (plan == null) return;
        var card = new Border
        {
            BorderBrush = (Brush)Application.Current.FindResource("Ink"),
            BorderThickness = new Thickness(1),
            Padding = new Thickness(14),
            Background = (Brush)Application.Current.FindResource("Surface"),
        };
        var content = new StackPanel();
        switch (plan.Kind)
        {
            case "clarify":
                content.Children.Add(new TextBlock { Text = plan.Reason, TextWrapping = TextWrapping.Wrap, FontWeight = FontWeights.Bold });
                if (plan.Options is { } options)
                {
                    var row = new WrapPanel { Margin = new Thickness(0, 10, 0, 0) };
                    foreach (var (kind, id, label) in options)
                    {
                        var chip = Kit.Act(label, (_, _) =>
                        {
                            selection = kind switch
                            {
                                "page" => DirectorScope.Page(),
                                "panel" => DirectorScope.Panel(id!),
                                "dialogue" => DirectorScope.Dialogue(
                                    view.Workbench.Element("storyboard").Array("panels")
                                        .FirstOrDefault(p => p.Array("dialogues").Any(d => d.Text("id") == id)).Text("id"), id!),
                                _ => DirectorScope.Character(id!),
                            };
                            BuildScopes();
                            commandInput.Focus();
                        }, "Ghost");
                        chip.Margin = new Thickness(0, 0, 8, 6);
                        row.Children.Add(chip);
                    }
                    content.Children.Add(row);
                }
                break;
            case "blocked" or "unsupported":
                content.Children.Add(new TextBlock { Text = plan.Reason, TextWrapping = TextWrapping.Wrap, Foreground = (Brush)Application.Current.FindResource("Danger") });
                break;
            case "command":
                content.Children.Add(new TextBlock { Text = $"命令预览 · 规则解析 · {plan.IntentLabel}", FontWeight = FontWeights.Bold });
                content.Children.Add(new TextBlock { Text = $"作用域：{plan.ScopeLabel}", Style = (Style)Application.Current.FindResource("Micro"), Margin = new Thickness(0, 4, 0, 0) });
                content.Children.Add(new Border
                {
                    BorderBrush = (Brush)Application.Current.FindResource("Accent"),
                    BorderThickness = new Thickness(3, 0, 0, 0),
                    Padding = new Thickness(8, 0, 0, 0),
                    Margin = new Thickness(0, 6, 0, 0),
                    Child = new TextBlock { Text = plan.Summary, TextWrapping = TextWrapping.Wrap },
                });
                var risk = plan.Risk switch
                {
                    "high" => "高：整页命令，候选将过期", "medium" => "中：影响本页后续抽卡", _ => "低：局部字段修改",
                };
                content.Children.Add(new TextBlock
                {
                    Text = $"模型：规则解析，非模型调用 · 抽卡模型：{(view.SelectedModelAlias.Length > 0 ? view.SelectedModelAlias : "未选择")}\n费用：分镜字段修改 · 本次不调用图片模型 · 重新抽卡费用暂不可估算\n风险：{risk}",
                    Style = (Style)Application.Current.FindResource("Micro"), Margin = new Thickness(0, 8, 0, 0),
                });
                var actions = new StackPanel { Orientation = Orientation.Horizontal, Margin = new Thickness(0, 12, 0, 0) };
                actions.Children.Add(Kit.Act("确认执行", async (_, _) => await JournalAsync("accept"), "InkButton"));
                var reject = Kit.Act("拒绝", async (_, _) => await JournalAsync("reject"), "Outline");
                reject.Margin = new Thickness(8, 0, 0, 0);
                actions.Children.Add(reject);
                content.Children.Add(actions);
                break;
        }
        card.Child = content;
        preview.Children.Add(card);
    }

    private async Task JournalAsync(string action)
    {
        if (busy) return;
        if (previewGroup.ValueKind != JsonValueKind.Object || previewGroup.Array("commands").Count == 0) return;
        busy = true;
        try
        {
            var commandId = previewGroup.Array("commands").FirstOrDefault().Text("command_id");
            var groupId = previewGroup.Text("command_group_id");
            previewGroup = await view.Api2().SendAsync(
                $"projects/{view.ProjectId2}/director/commands/{commandId}/{action}", HttpMethod.Post);
            view.Cache2().Invalidate("director:" + view.ProjectId2, "workbench:", "pages:");
            await view.ReloadWorkbench();
            RenderPreview();
            await LoadHistoryAsync();
        }
        catch (Exception error)
        {
            preview.Children.Add(Kit.Caption("执行失败：" + error.Message));
        }
        finally { busy = false; }
    }

    private async Task LoadHistoryAsync()
    {
        try
        {
            var groups = await view.Api2().SendAsync(
                QueryBuilder.Build($"projects/{view.ProjectId2}/director/command-groups", ("page_id", view.CurrentPage?.Id)));
            history.Children.Clear();
            var rows = groups.EnumerateArray().ToList();
            if (rows.Count == 0)
            {
                history.Children.Add(Kit.Caption("本页还没有导演命令。"));
                return;
            }
            foreach (var group in rows)
            {
                var command = group.Array("commands").FirstOrDefault();
                var item = new StackPanel { Margin = new Thickness(0, 6, 0, 10) };
                var status = command.Text("status", "PROPOSED");
                item.Children.Add(new TextBlock
                {
                    Text = $"{Labels.Map(Labels.DirectorOperation, command.Text("operation"))} · {Labels.Map(Labels.DirectorCommandStatus, status)}",
                    FontWeight = FontWeights.Bold, FontSize = 12.5,
                });
                item.Children.Add(new TextBlock
                {
                    Text = command.Element("source").Text("user_prompt"), FontStyle = FontStyles.Italic,
                    Style = (Style)Application.Current.FindResource("Micro"), Margin = new Thickness(0, 3, 0, 0),
                });
                if (status is "FAILED" or "REJECTED")
                {
                    // 改口令重发：沿用原指令与作用域，把 retry_of_command_id 链到原命令
                    var retry = Kit.Act("改口令重发", (_, _) =>
                    {
                        var chained = command.Text("retry_of_command_id");
                        retryOfCommandId = chained.Length > 0 ? chained : command.Text("command_id");
                        commandInput.Text = command.Element("source").Text("user_prompt");
                        selection = SelectionFromTarget(command.Element("target"));
                        plan = null;
                        previewGroup = default;
                        preview.Children.Clear();
                        BuildScopes();
                        commandInput.Focus();
                    }, "Outline");
                    retry.Margin = new Thickness(0, 6, 0, 0);
                    item.Children.Add(retry);
                }
                history.Children.Add(item);
            }
        }
        catch (Exception) { }
    }

    private static DirectorScope? SelectionFromTarget(JsonElement target)
    {
        var dialogueId = target.Text("dialogue_id");
        var panelId = target.Text("panel_id");
        if (dialogueId.Length > 0 && panelId.Length > 0) return DirectorScope.Dialogue(panelId, dialogueId);
        if (panelId.Length > 0) return DirectorScope.Panel(panelId);
        return null;
    }
}
