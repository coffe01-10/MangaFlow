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
    private string? viewedBatchId;
    private List<JsonElement>? historicalCandidates;
    private int workbenchRead;
    private int pagesRead;

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
                chapterId = id;
                KeyValueStore.Set("generate:chapter:" + ProjectId, id);
                await LoadPagesAsync();
            }
        };
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

        // Production readiness card.
        var ready = readiness.Flag("ready");
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
                var code = blocker.Text("code");
                if (RouteForBlocker(code) is { } route)
                {
                    var go = Kit.Act("去处理", async (_, _) => await Context!.NavigateSection(route.section, route.query), "Compact");
                    go.Margin = new Thickness(10, 0, 0, 0);
                    row.Children.Add(go);
                }
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
        var editModels = models.Where(m => m.Text("model_type") == "IMAGE"
            && m.Array("operations").Any(o => o.ToString() == "image_edit") && m.Flag("enabled")).ToList();
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

        var references = EffectiveReferences();
        var referenceReady = referencesLoaded && GenerationReferences.Ready(references, referenceOutfits, referencePackages);
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
        var steps = new StackPanel { Margin = new Thickness(0, 8, 0, 0) };
        foreach (var step in new[] { "人工校对并暂选", "确认当前分镜版本", "视觉检查通过" })
            steps.Children.Add(new TextBlock { Text = "• " + step, FontSize = 12.5 });
        gateCard.Children.Add(steps);
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
                var bytes = await Api.DownloadAsync($"pages/{exportPage.Id}/export.png", lifetime.Token);
                await File.WriteAllBytesAsync(save.FileName, bytes);
                notice.Text = "单页 PNG 已保存。";
            }
             catch (OperationCanceledException) { }
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

    private static (string section, string query)? RouteForBlocker(string code) => code switch
    {
        "MISSING_OUTFIT_ASSIGNMENT" => ("storyboard", ""),
        "MISSING_CHARACTER_REFERENCE" => ("assets", "view=characters"),
        "MISSING_OUTFIT_REFERENCE" => ("assets", "view=outfits"),
        "STYLE_NOT_ACTIVE" or "STYLE_MISSING" => ("assets", "view=style"),
        "SOURCE_INCOMPLETE" => ("source", ""),
        "SCRIPT_INCOMPLETE" => ("script", ""),
        "STORYBOARD_STALE" => ("storyboard", ""),
        _ => null,
    };

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
                    await Api.SendAsync($"candidates/{candidate.Id}/inspect", HttpMethod.Post, new
                    {
                        categories = new[] { "SPEAKER", "CHARACTER", "OUTFIT", "PROP", "CONTINUITY" },
                    });
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
                    if (MessageBox.Show(Host, $"升至 {resolution} 会调用一次所选图片模型（可能计费），并基于该候选生成一个新批次。是否继续？",
                            "保持结构升清", MessageBoxButton.YesNo, MessageBoxImage.Question) != MessageBoxResult.Yes) return;
                    await Api.SendAsync($"candidates/{candidate.Id}/upscale", HttpMethod.Post, new
                    {
                        model_alias = modelAlias.Length > 0 ? modelAlias : (string?)null,
                        resolution,
                    });
                    break;
            }
            Cache.Invalidate("workbench:" + targetPage, "library:" + project, "jobs:" + project, "pages:" + chapterId);
            if (token.IsCancellationRequested || currentPage?.Id != targetPage || ProjectId != project) return;
            await LoadWorkbenchAsync();
        }
        catch (OperationCanceledException) { }
        catch (Exception error) when (error is not OperationCanceledException)
        {
            if (!token.IsCancellationRequested && currentPage?.Id == targetPage && ProjectId == project) notice.Text = error.Message;
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
        return action == "favorite" || candidate.HasImage;
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

    public override void PollTick()
    {
        if (currentPage == null) return;
        if (director && directorPane is { } pane && (pane.Busy || pane.HasDraft)) return;   // keep drafts alive
        var active = workbench.Array("candidates").Any(c => c.Text("status") is "QUEUED" or "GENERATING");
        if (active || directorPane?.Busy == true) _ = LoadWorkbenchAsync();
    }

    public override Task RefreshAsync()
    {
        if (currentPage != null) _ = LoadWorkbenchAsync();
        return Task.CompletedTask;
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
        actions.Children.Add(CardAction(view, candidate, "暂选", "select"));
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
