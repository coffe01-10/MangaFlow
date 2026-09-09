using System.IO;
using System.Net.Http;
using System.Text.Json;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Controls.Primitives;
using System.Windows.Input;
using System.Windows.Media;
using Microsoft.Win32;
using MangaFlow.Native.Controls;
using MangaFlow.Native.Services;

namespace MangaFlow.Native.Views;

/// <summary>Native wardrobe editor, matching assets-section.tsx. Draft references never bind until save.</summary>
internal sealed class OutfitWorkspace : StackPanel
{
    private readonly AssetsView view;
    private readonly int epoch;
    private readonly string projectId;
    private readonly ModelPickerBand models;
    private readonly ComboBox characterSelector = new() { MinHeight = 40 };
    private readonly TextBox outfitName = new() { MinHeight = 40, MaxLength = 120 };
    private readonly TextBox lockedFields = new() { MinHeight = 40 };
    private readonly StackPanel records = new(), references = new(), liveResults = new(), library = new();
    private readonly Grid flow = new();
    private readonly TextBlock summaryCount = new() { FontSize = 26 }, summaryNames = Kit.Caption("");
    private readonly Button save, cancel, upload, import;
    private readonly HashSet<string> selected = [];
    private OutfitItem? editing;
    private bool busy, readingResults, pendingResults;
    private int libraryRequest, resultRequest;
    private string resultOutfit = "", resultBatch = "", nextCursor = "";
    private readonly List<JsonElement> libraryCandidates = [];
    // Draft baseline captured whenever the form is (re)filled, so DraftDirty can
    // distinguish "user edits since BeginEdit/Reset" from the pristine record.
    private string initialName = "", initialLocked = "";
    private readonly HashSet<string> initialSelected = [];
    private bool syncingSelector;
    private string lastCharacterId = "";
    internal int SessionEpoch => epoch;
    internal bool DraftDirty => editing != null
        ? outfitName.Text != initialName || lockedFields.Text != initialLocked || !selected.SetEquals(initialSelected)
        : outfitName.Text.Trim().Length > 0 || lockedFields.Text.Trim().Length > 0 || selected.Count > 0;
    internal string DraftLabel => editing != null ? outfitName.Text.Trim().Length > 0 ? outfitName.Text.Trim() : editing.Name : outfitName.Text.Trim();
    private bool Active => view.IsCurrent(epoch) && view.OwnsOutfits(this);
    private string CharacterId => (characterSelector.SelectedItem as ComboBoxItem)?.Tag as string ?? "";

    public OutfitWorkspace(AssetsView view)
    {
        this.view = view; epoch = view.Epoch; projectId = view.ProjectIdValue;
        Children.Add(AssetPageUi.Header("WARDROBE / 服装档案", "角色、服装与参考图逐一绑定", $"{view.outfits.Count} 份档案"));
        models = new ModelPickerBand(view, "本次服装预览模型");
        Children.Add(models); Children.Add(liveResults);
        characterSelector.Items.Add(new ComboBoxItem { Tag = "", Content = "选择角色后再绑定服装" });
        foreach (var c in view.characters) characterSelector.Items.Add(new ComboBoxItem { Tag = c.Id, Content = c.PrimaryName + (c.Aliases.Count > 0 ? "（" + string.Join(" / ", c.Aliases) + "）" : "") });
        characterSelector.SelectedIndex = 0;
        System.Windows.Automation.AutomationProperties.SetName(characterSelector, "服装所属角色");
        save = Kit.Act("建立并绑定（0 图）", async (_, _) => await SaveAsync(), "InkButton");
        cancel = Kit.Act("取消编辑", (_, _) => Reset(), "Outline");
        var editor = new StackPanel();
        editor.Children.Add(SectionTitle("服装档案", "明确绑定角色、服装名称与参考图"));
        flow.Margin = new Thickness(0, 0, 0, 12); editor.Children.Add(flow);
        var compose = new Grid { ColumnDefinitions = { new ColumnDefinition(), new ColumnDefinition { Width = new GridLength(135) } }, RowDefinitions = { new RowDefinition(), new RowDefinition { Height = GridLength.Auto } } };
        var fields = new StackPanel { Margin = new Thickness(0, 0, 12, 0) };
        fields.Children.Add(Field("所属角色", characterSelector));
        fields.Children.Add(Field("服装档案名称", AssetPageUi.Input(outfitName, "例如：校服 / 冬季便装", "服装档案名称")));
        fields.Children.Add(Field("一致性锁定项", AssetPageUi.Input(lockedFields, "颜色、鞋型、领结、配饰…", "服装锁定项")));
        compose.Children.Add(fields);
        var summary = new StackPanel { VerticalAlignment = VerticalAlignment.Center };
        summary.Children.Add(Kit.Caption("当前待绑定")); summary.Children.Add(summaryCount); summary.Children.Add(Kit.Caption("张参考图")); summaryNames.Margin = new Thickness(0, 8, 0, 0); summary.Children.Add(summaryNames);
        var summarySurface = Surface(summary, 10); summarySurface.Background = AssetPageUi.Brush("Paper"); Grid.SetColumn(summarySurface, 1); compose.Children.Add(summarySurface);
        var actions = new WrapPanel { HorizontalAlignment = HorizontalAlignment.Right, Margin = new Thickness(0, 12, 0, 0) };
        cancel.Margin = new Thickness(0, 0, 8, 0); actions.Children.Add(cancel); actions.Children.Add(save);
        Grid.SetRow(actions, 1); Grid.SetColumnSpan(actions, 2); compose.Children.Add(actions);
        editor.Children.Add(Surface(compose, 12));
        editor.Children.Add(new TextBlock { Text = "上传服装参考后会自动加入当前档案；也可以在下方素材卡中加入、移除，再点击保存绑定。", FontSize = 12, Foreground = AssetPageUi.Brush("Muted"), Margin = new Thickness(0, 10, 0, 0) });
        var saved = new StackPanel(); saved.Children.Add(SectionTitle("已保存服装", "在右侧集中管理角色已有的服装档案")); saved.Children.Add(records);
        var workbench = new TilePanel { MinimumTileWidth = 470, MaximumColumns = 2, Gap = 12, Margin = new Thickness(0, 0, 0, 28) };
        workbench.Children.Add(Surface(editor, 16)); workbench.Children.Add(Surface(saved, 16)); Children.Add(workbench);
        Children.Add(new TextBlock { Text = "服装参考", FontWeight = FontWeights.Bold, Margin = new Thickness(0, 0, 0, 12) });
        var stage = new StackPanel { HorizontalAlignment = HorizontalAlignment.Center };
        stage.Children.Add(SourceIcon.Create("upload", 30));
        stage.Children.Add(new TextBlock { Text = "拖拽图片到这里，或点击上传服装参考", FontWeight = FontWeights.Bold, FontSize = 16, TextAlignment = TextAlignment.Center, Margin = new Thickness(0, 14, 0, 10) });
        stage.Children.Add(new TextBlock { Text = "上传后自动加入当前服装档案，保存时绑定到上方所选角色。", TextAlignment = TextAlignment.Center, Foreground = AssetPageUi.Brush("Muted") });
        upload = new Button { Content = stage, Style = (Style)FindResource("Outline"), MinHeight = 165, AllowDrop = true, Background = AssetPageUi.Brush("Surface") };
        upload.Click += async (_, _) => { var dialog = new OpenFileDialog { Filter = "图片|*.png;*.jpg;*.jpeg;*.webp", Multiselect = true, Title = "上传服装参考图" }; if (dialog.ShowDialog(view.WindowHost()) == true) await UploadAsync(dialog.FileNames); };
        upload.DragOver += (_, e) => { e.Effects = !busy && CharacterId.Length > 0 && e.Data.GetDataPresent(DataFormats.FileDrop) ? DragDropEffects.Copy : DragDropEffects.None; e.Handled = true; };
        upload.Drop += async (_, e) => { if (e.Data.GetData(DataFormats.FileDrop) is string[] paths) await UploadAsync(paths); e.Handled = true; };
        Children.Add(upload);
        import = Kit.Act("从生成素材库导入", ToggleLibrary, "Outline");
        import.HorizontalAlignment = HorizontalAlignment.Left; import.Margin = new Thickness(0, 12, 0, 14); Children.Add(import);
        library.Visibility = Visibility.Collapsed; Children.Add(library); Children.Add(references);
        characterSelector.SelectionChanged += async (_, _) => await SelectionChangedAsync();
        outfitName.TextChanged += (_, _) => UpdateDraft();
        models.AddHandler(ButtonBase.ClickEvent, new RoutedEventHandler((_, _) => RenderRecords()));
        if (view.SelectedCharacter != null) SelectCharacter(view.SelectedCharacter.Id);
        if (view.outfits.FirstOrDefault(o => o.Id == view.SelectedOutfit?.Id) is { } initialOutfit) BeginEdit(initialOutfit);
        lastCharacterId = CharacterId; CaptureDraftBaseline();
        UpdateDraft(); RenderRecords(); RenderReferences();
        if (view.OutfitPreviewId.Length > 0) Dispatcher.BeginInvoke(new Action(async () => await LoadLatestResultAsync()));
    }

    private static Border Surface(UIElement child, double padding = 12) => new() { Child = child, Padding = new Thickness(padding), BorderBrush = AssetPageUi.Brush("Line"), BorderThickness = new Thickness(1), Background = AssetPageUi.Brush("Surface") };
    private static StackPanel SectionTitle(string title, string description)
    {
        var panel = new StackPanel { Margin = new Thickness(0, 0, 0, 14) };
        panel.Children.Add(new TextBlock { Text = title, FontWeight = FontWeights.Bold, FontSize = 15 }); panel.Children.Add(Kit.Caption(description)); return panel;
    }
    private static FrameworkElement Field(string label, UIElement input)
    {
        var panel = new StackPanel { Margin = new Thickness(0, 0, 0, 9) }; panel.Children.Add(new TextBlock { Text = label, FontSize = 12, Foreground = AssetPageUi.Brush("Muted"), Margin = new Thickness(0, 0, 0, 6) }); panel.Children.Add(input); return panel;
    }
    private void SelectCharacter(string id) => characterSelector.SelectedItem = characterSelector.Items.OfType<ComboBoxItem>().FirstOrDefault(i => Equals(i.Tag, id)) ?? characterSelector.Items[0];
    private void UpdateDraft()
    {
        var owner = view.characters.FirstOrDefault(c => c.Id == CharacterId);
        flow.Children.Clear(); flow.ColumnDefinitions.Clear();
        var steps = new[] { ("01", "所属角色", owner?.PrimaryName ?? "未选择", owner != null), ("02", "服装参考", $"{selected.Count} 张", selected.Count > 0), ("03", "服装档案", outfitName.Text.Trim().Length > 0 ? outfitName.Text.Trim() : "待命名", outfitName.Text.Trim().Length > 0) };
        foreach (var (number, label, value, done) in steps)
        {
            int column = flow.ColumnDefinitions.Count;
            if (column > 0) { flow.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(20) }); var arrow = Kit.Caption("→"); arrow.VerticalAlignment = VerticalAlignment.Center; arrow.HorizontalAlignment = HorizontalAlignment.Center; Grid.SetColumn(arrow, column++); flow.Children.Add(arrow); }
            flow.ColumnDefinitions.Add(new ColumnDefinition());
            var text = new StackPanel(); text.Children.Add(new TextBlock { Text = number + "  " + label, Foreground = AssetPageUi.Brush("Muted"), FontSize = 11 }); text.Children.Add(new TextBlock { Text = value, FontSize = 12, FontWeight = FontWeights.Bold, TextTrimming = TextTrimming.CharacterEllipsis, TextWrapping = TextWrapping.NoWrap, Margin = new Thickness(0, 4, 0, 0) });
            var card = Surface(text, 8); card.Background = AssetPageUi.Brush(done ? "SuccessBg" : "Paper"); card.BorderBrush = AssetPageUi.Brush(done ? "Success" : "Line"); Grid.SetColumn(card, column); flow.Children.Add(card);
        }
        summaryCount.Text = selected.Count.ToString();
        summaryNames.Text = selected.Count == 0 ? "在下方“服装参考”中选择图片" : string.Join("、", view.assets.Where(a => selected.Contains(a.Id)).Take(2).Select(a => a.Name)) + (selected.Count > 2 ? $" 等 {selected.Count} 张" : "");
        save.Content = (editing == null ? "建立并绑定" : "保存绑定") + $"（{selected.Count} 图）";
        save.IsEnabled = !busy && outfitName.Text.Trim().Length > 0 && (editing != null || CharacterId.Length > 0 && selected.Count > 0);
        cancel.Visibility = editing == null ? Visibility.Collapsed : Visibility.Visible;
        characterSelector.IsEnabled = !busy && editing == null;
        upload.IsEnabled = import.IsEnabled = !busy && CharacterId.Length > 0;
    }
    private void BeginEdit(OutfitItem outfit)
    {
        if (busy) return; editing = outfit; view.SelectedOutfit = outfit;
        syncingSelector = true; SelectCharacter(outfit.CharacterId); syncingSelector = false; lastCharacterId = CharacterId;
        selected.Clear(); selected.UnionWith(outfit.ReferenceAssetIds); outfitName.Text = outfit.Name; lockedFields.Text = outfit.LockedFields;
        CaptureDraftBaseline();
        UpdateDraft(); RenderRecords(); RenderReferences();
    }
    // Switching the edited record re-owns the form: unsaved edits get the same
    // save/discard/cancel guard as tab switches before the record loads.
    private async Task EditAsync(OutfitItem outfit)
    {
        if (busy) return;
        if (DraftDirty && editing?.Id != outfit.Id)
        {
            if (await view.GuardOutfitDraftAsync() == AssetsView.OutfitDraftDecision.Cancelled || DraftDirty) return;
        }
        BeginEdit(outfit);
    }
    internal void Reset()
    {
        editing = null; view.SelectedOutfit = null; selected.Clear(); outfitName.Clear(); lockedFields.Clear();
        libraryRequest++; library.Visibility = Visibility.Collapsed; import.Content = "从生成素材库导入";
        CaptureDraftBaseline();
        UpdateDraft(); RenderRecords(); RenderReferences();
    }
    private void CaptureDraftBaseline()
    {
        initialName = outfitName.Text; initialLocked = lockedFields.Text;
        initialSelected.Clear(); initialSelected.UnionWith(selected);
    }
    // A user-driven character change re-owns the draft: with unsaved edits, ask
    // save/discard/cancel first. Programmatic selections (BeginEdit/Reset) pass through.
    private async Task SelectionChangedAsync()
    {
        if (syncingSelector) return;
        var next = CharacterId;
        if (next == lastCharacterId) return;
        if (DraftDirty)
        {
            // Revert first so a cancelled guard leaves the draft's owner selected.
            syncingSelector = true; SelectCharacter(lastCharacterId); syncingSelector = false;
            var decision = await view.GuardOutfitDraftAsync();
            if (decision == AssetsView.OutfitDraftDecision.Cancelled || DraftDirty) return;
            syncingSelector = true; SelectCharacter(next); syncingSelector = false;
        }
        lastCharacterId = next;
        libraryRequest++; library.Visibility = Visibility.Collapsed; import.Content = "从生成素材库导入";
        UpdateDraft(); RenderReferences();
    }
    private async Task RunAsync(Func<Task> action)
    {
        if (busy || !Active) return;
        busy = true; IsEnabled = false;
        try { await action(); }
        catch (Exception ex) { if (Active) view.Notify(ex.Message); }
        finally { busy = false; IsEnabled = true; if (Active) { UpdateDraft(); RenderRecords(); } }
    }
    private async Task RefreshDataAsync()
    {
        var assets = await view.ApiSend($"assets?project_id={projectId}");
        var outfits = await view.ApiSend($"projects/{projectId}/outfits");
        if (!Active) return;
        view.assets = assets.EnumerateArray().Select(AssetItem.From).ToList(); view.outfits = outfits.EnumerateArray().Select(OutfitItem.From).ToList();
        AdoptReloaded();
    }
    // Absorbs already-refreshed shared rows (AssetsView.LoadAsync fetched them)
    // without rebuilding the editor: the draft, its baseline and the busy state
    // stay exactly as they were. Only externally-deleted reference ids leave the
    // baseline; typed text keeps marking the draft dirty.
    internal void AdoptReloaded()
    {
        if (!Active) return;
        var liveIds = view.assets.Where(a => a.Kind == "OUTFIT_REFERENCE").Select(a => a.Id).ToHashSet();
        selected.IntersectWith(liveIds);
        initialSelected.IntersectWith(liveIds);
        Children.RemoveAt(0); Children.Insert(0, AssetPageUi.Header("WARDROBE / 服装档案", "角色、服装与参考图逐一绑定", $"{view.outfits.Count} 份档案"));
        view.InvalidateOutfitDependents();
        RenderReferences(); RenderRecords(); UpdateDraft();
    }
    internal Task SaveAsync() => SaveDraftAsync();
    private Task SaveDraftAsync() => RunAsync(async () =>
    {
        if (outfitName.Text.Trim().Length == 0 || editing == null && (CharacterId.Length == 0 || selected.Count == 0)) return;
        var fields = lockedFields.Text.Split(new[] { '，', ',', '、' }, StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries);
        if (editing == null) await view.ApiSend($"projects/{projectId}/outfits", HttpMethod.Post, new { character_id = CharacterId, name = outfitName.Text.Trim(), reference_asset_ids = selected.ToArray(), locked_fields = fields });
        else await view.ApiSend($"outfits/{editing.Id}", HttpMethod.Patch, new { version = editing.Version, name = outfitName.Text.Trim(), reference_asset_ids = selected.ToArray(), locked_fields = fields });
        if (!Active) return; Reset(); await RefreshDataAsync(); if (Active) view.Notify("服装档案及参考图绑定已保存。");
    });
    internal Task UploadAsync(string[] paths) => RunAsync(async () =>
    {
        if (CharacterId.Length == 0) return;
        try
        {
            foreach (var path in paths)
            {
                var asset = await view.UploadAsset("OUTFIT_REFERENCE", path); if (!Active) return;
                selected.Add(asset.Text("id"));
                view.assets.RemoveAll(a => a.Id == asset.Text("id")); view.assets.Add(AssetItem.From(asset));
                RenderReferences(); UpdateDraft();
            }
            view.Notify("参考图已加入待绑定，点击保存后写入服装档案。");
        }
        finally { if (Active) { RenderReferences(); UpdateDraft(); } }
    });
    private void RenderRecords()
    {
        records.Children.Clear();
        foreach (var outfit in view.outfits)
        {
            var text = new StackPanel();
            text.Children.Add(new PageHeading(new TextBlock { Text = outfit.Name, FontSize = 16, FontWeight = FontWeights.Bold }, Kit.Caption("WARDROBE")));
            text.Children.Add(Kit.Caption($"{outfit.LockedFields.Split(new[] { '，', ',', '、' }, StringSplitOptions.RemoveEmptyEntries).Length} 项锁定"));
            var chain = new WrapPanel { Margin = new Thickness(0, 10, 0, 12) };
            foreach (var label in new[] { view.characters.FirstOrDefault(c => c.Id == outfit.CharacterId)?.PrimaryName ?? "未知角色", outfit.Name, $"{outfit.ReferenceCount} 张参考图" })
            { if (chain.Children.Count > 0) chain.Children.Add(new TextBlock { Text = "→", Margin = new Thickness(6), Foreground = AssetPageUi.Brush("Accent") }); chain.Children.Add(Surface(new TextBlock { Text = label, FontSize = 12 }, 6)); }
            text.Children.Add(chain);
            var actions = new WrapPanel();
            actions.Children.Add(Kit.Act("删除档案及图片", async (_, _) => await DeleteAsync(outfit), "CompactDanger"));
            actions.Children.Add(Kit.Act(editing?.Id == outfit.Id ? "编辑中" : "管理参考图", async (_, _) => await EditAsync(outfit), "Compact"));
            var generate = Kit.Act("生成穿着图", async (_, _) => await GenerateAsync(outfit), "Compact"); generate.IsEnabled = !busy && view.ImageEditModels.Any(m => m.Text("logical_alias") == models.Selected) && outfit.ReferenceCount > 0; actions.Children.Add(generate);
            foreach (FrameworkElement action in actions.Children) action.Margin = new Thickness(0, 0, 6, 6);
            text.Children.Add(actions); var surface = Surface(text); surface.Margin = new Thickness(0, 0, 0, 10); if (editing?.Id == outfit.Id) surface.BorderBrush = AssetPageUi.Brush("Accent"); records.Children.Add(surface);
        }
        if (view.outfits.Count == 0) records.Children.Add(Kit.Caption("还没有服装档案。完成上方 01–03 三步后建立。"));
    }
    private Task DeleteAsync(OutfitItem outfit)
    {
        if (busy || !Active || MessageBox.Show(view.WindowHost(), $"删除服装档案“{outfit.Name}”？\n\n将同时删除绑定的 {outfit.ReferenceCount} 张参考图、已生成的穿着图，并清除剧本与分镜中的服装绑定。被其他档案共用的图片会保留。", "删除服装档案", MessageBoxButton.YesNo, MessageBoxImage.Warning) != MessageBoxResult.Yes) return Task.CompletedTask;
        return RunAsync(async () => { await view.ApiSendOptional($"outfits/{outfit.Id}", HttpMethod.Delete); if (!Active) return; if (editing?.Id == outfit.Id) Reset(); if (resultOutfit == outfit.Id) { view.OutfitPreviewId = resultOutfit = resultBatch = ""; pendingResults = false; liveResults.Children.Clear(); } await RefreshDataAsync(); });
    }
    private Task GenerateAsync(OutfitItem outfit) => RunAsync(async () =>
    {
        var alias = models.Selected; if (!view.ImageEditModels.Any(m => m.Text("logical_alias") == alias) || outfit.ReferenceCount == 0) return;
        resultRequest++; view.OutfitPreviewId = resultOutfit = outfit.Id;
        var batch = await view.ApiSend("asset-generation-batches", HttpMethod.Post, new { target_type = "OUTFIT", target_id = outfit.Id, generation_kind = "OUTFIT" });
        if (!Active) return; resultBatch = batch.Text("id");
        await view.ApiSend($"asset-generation-batches/{resultBatch}/candidates", HttpMethod.Post, new { model_alias = alias, resolution = "1K", variant = "OUTFIT" });
        if (!Active) return; pendingResults = true; view.Notify("穿着图任务已创建，结果将在此更新。"); await RefreshResultsAsync();
    });
    private async Task LoadLatestResultAsync()
    {
        if (!Active) return;
        resultOutfit = view.OutfitPreviewId; var request = ++resultRequest;
        try
        {
            var batches = await view.ApiSend(QueryBuilder.Build("asset-generation-batches", ("target_type", "OUTFIT"), ("target_id", resultOutfit), ("limit", 1)));
            if (!Active || request != resultRequest) return;
            resultBatch = batches.EnumerateArray().FirstOrDefault().Text("id");
            await RefreshResultsAsync();
        }
        catch (Exception ex) { if (Active && request == resultRequest) { liveResults.Children.Clear(); liveResults.Children.Add(Kit.Caption("读取穿着图结果失败：" + ex.Message)); liveResults.Children.Add(Kit.Act("重新读取穿着图", async (_, _) => await LoadLatestResultAsync(), "Compact")); } }
    }
    internal void PollTick() { if (pendingResults && !readingResults && Active) _ = RefreshResultsAsync(); }
    internal async Task RefreshResultsAsync()
    {
        if (!Active || readingResults || resultBatch.Length == 0) return;
        readingResults = true; var batch = resultBatch;
        try
        {
            var response = await view.ApiSend($"batches/{batch}/candidates"); if (!Active || batch != resultBatch) return;
            liveResults.Children.Clear(); liveResults.Children.Add(SectionTitle("服装穿着图实时结果", view.outfits.FirstOrDefault(o => o.Id == resultOutfit)?.Name ?? "服装"));
            var rows = response.EnumerateArray().ToList(); pendingResults = rows.Count == 0 || rows.Any(r => r.Text("status") is "WAITING" or "QUEUED" or "PREPARING" or "UPLOADING_REFERENCES" or "GENERATING" or "OCR_CHECKING" or "CONSISTENCY_CHECKING" or "REPAIRING" or "RUNNING");
            var grid = new TilePanel { MinimumTileWidth = 220, MaximumColumns = 4, Gap = 12, Margin = new Thickness(0, 0, 0, 24) };
            foreach (var row in rows)
            {
                var text = new StackPanel(); text.Children.Add(Artwork(row.Text("thumbnail_url", row.Text("content_url")), row.Text("content_url"), "服装穿着预览", 170));
                text.Children.Add(new TextBlock { Text = "服装穿着预览", FontWeight = FontWeights.Bold, Margin = new Thickness(0, 8, 0, 4) }); text.Children.Add(Kit.Caption(Labels.Map(Labels.CandidateStatus, row.Text("status")) + " · " + row.Text("resolution")));
                var prompt = row.Element("prompt_snapshot");
                text.Children.Add(new Expander { Header = "实际提示词", Margin = new Thickness(0, 8, 0, 0), Content = new TextBox { Text = prompt.Text("prompt_preview", "任务排队后会在这里保存本次实际提示词。"), IsReadOnly = true, TextWrapping = TextWrapping.Wrap, MaxHeight = 180, VerticalScrollBarVisibility = ScrollBarVisibility.Auto } }); grid.Children.Add(Surface(text));
            }
            liveResults.Children.Add(grid); if (rows.Count == 0) liveResults.Children.Add(Kit.Caption("等待任务开始生成…"));
        }
        catch (Exception ex) { if (Active && batch == resultBatch) { liveResults.Children.Clear(); liveResults.Children.Add(Kit.Caption("读取结果失败：" + ex.Message)); liveResults.Children.Add(Kit.Act("重新读取结果", async (_, _) => await RefreshResultsAsync(), "Compact")); } }
        finally { readingResults = false; }
    }
    private FrameworkElement Artwork(string thumbnail, string url, string label, double height)
    {
        var box = new Button { Height = height, Padding = new Thickness(0), Style = (Style)FindResource("Outline"), Background = AssetPageUi.Brush("PaperDeep") };
        box.Content = thumbnail.Length > 0 ? new ImageBox { SourceUrl = view.OriginFor(thumbnail) } : new TextBlock { Text = "等待图片", Foreground = AssetPageUi.Brush("Muted") };
        if (url.Length > 0) box.Click += (_, _) => view.ShowImage(url, label); return box;
    }

    private async void ToggleLibrary(object sender, RoutedEventArgs args)
    {
        if (library.Visibility == Visibility.Visible) { libraryRequest++; library.Visibility = Visibility.Collapsed; import.Content = "从生成素材库导入"; }
        else { library.Visibility = Visibility.Visible; import.Content = "收起生成素材"; await LoadLibraryAsync(false); }
    }

    private async Task LoadLibraryAsync(bool more)
    {
        if (!Active || busy || CharacterId.Length == 0) return;
        var request = ++libraryRequest; var character = CharacterId;
        if (!more) { libraryCandidates.Clear(); nextCursor = ""; }
        library.Children.Clear(); library.Children.Add(Kit.Caption("正在读取生成素材…"));
        try
        {
            var response = await view.ApiSend(QueryBuilder.Build($"projects/{projectId}/library", ("group_by", "batch"), ("character_id", character), ("limit", 30), ("cursor", more ? nextCursor : null)));
            if (!Active || request != libraryRequest || character != CharacterId) return;
            nextCursor = response.Text("next_cursor");
            foreach (var item in response.Array("groups").SelectMany(g => g.Array("candidates")))
                if (item.Text("asset_id").Length > 0 && item.Text("content_url").Length > 0 && !libraryCandidates.Any(c => c.Text("asset_id") == item.Text("asset_id"))) libraryCandidates.Add(item);
            RenderLibrary();
        }
        catch (Exception ex) { if (Active && request == libraryRequest) { library.Children.Clear(); library.Children.Add(Kit.Caption("读取失败：" + ex.Message)); library.Children.Add(Kit.Act("重试读取素材", async (_, _) => await LoadLibraryAsync(more), "Compact")); } }
    }
    private void RenderLibrary()
    {
        library.Children.Clear(); library.Children.Add(SectionTitle("选择一张图加入待绑定服装参考", "只显示当前角色相关的已生成图片；导入后仍保留在生成素材库。"));
        var grid = new TilePanel { MinimumTileWidth = 210, MaximumColumns = 4, Gap = 12, Margin = new Thickness(0, 0, 0, 20) };
        foreach (var item in libraryCandidates)
        {
            var id = item.Text("asset_id"); var panel = new StackPanel(); panel.Children.Add(Artwork(item.Text("thumbnail_url", item.Text("content_url")), item.Text("content_url"), "生成素材", 155));
            panel.Children.Add(Kit.Caption(item.Text("variant", "生成候选") + " · " + item.Text("resolution")));
            var button = Kit.Act(selected.Contains(id) ? "已加入待绑定" : "加入待绑定", async (_, _) => await RunAsync(async () => { var asset = await view.ApiSend($"assets/{id}/adopt-reference", HttpMethod.Post); if (!Active) return; selected.Add(asset.Text("id")); await RefreshDataAsync(); if (Active) RenderLibrary(); }), "Compact"); button.IsEnabled = !selected.Contains(id); panel.Children.Add(button); grid.Children.Add(Surface(panel));
        }
        library.Children.Add(grid); if (libraryCandidates.Count == 0) library.Children.Add(Kit.Caption("当前角色还没有可导入的生成图片。"));
        if (nextCursor.Length > 0) library.Children.Add(Kit.Act("加载更多生成素材", async (_, _) => await LoadLibraryAsync(true), "Outline"));
    }
    private void RenderReferences()
    {
        references.Children.Clear(); var assets = view.assets.Where(a => a.Kind == "OUTFIT_REFERENCE").ToList();
        references.Children.Add(new PageHeading(new TextBlock { Text = "服装参考", FontWeight = FontWeights.Bold }, Kit.Caption($"{assets.Count} FILES")));
        references.Children.Add(new TextBlock { Text = "选择图片后，上方绑定流程会明确保存“角色 → 服装档案 → 参考图”的关系。", Foreground = AssetPageUi.Brush("Muted"), Margin = new Thickness(0, 8, 0, 18) });
        var grid = new TilePanel { MinimumTileWidth = 370, MaximumColumns = 2, Gap = 12 };
        foreach (var asset in assets) grid.Children.Add(ReferenceCard(asset)); references.Children.Add(grid);
        if (assets.Count == 0) references.Children.Add(Kit.Caption("尚无服装参考"));
    }
    private Border ReferenceCard(AssetItem asset)
    {
        var grid = new Grid { ColumnDefinitions = { new ColumnDefinition { Width = new GridLength(86) }, new ColumnDefinition() } };
        var thumb = Artwork(asset.ContentUrl, asset.ContentUrl, asset.Name, 74); thumb.Width = 74; thumb.VerticalAlignment = VerticalAlignment.Top; grid.Children.Add(thumb);
        var text = new StackPanel(); Grid.SetColumn(text, 1); grid.Children.Add(text);
        var title = new TextBlock { Text = asset.Name, FontWeight = FontWeights.Bold, FontSize = 14 };
        var name = new TextBox { Text = asset.Name, Visibility = Visibility.Collapsed };
        var rename = SourceIcon.Action("edit", "修改素材名称", (_, _) => { title.Visibility = Visibility.Collapsed; name.Visibility = Visibility.Visible; name.Focus(); });
        text.Children.Add(new PageHeading(title, rename)); text.Children.Add(name);
        name.KeyDown += async (_, e) => { if (e.Key == Key.Escape) { name.Text = asset.Name; name.Visibility = Visibility.Collapsed; title.Visibility = Visibility.Visible; } if (e.Key == Key.Enter && !string.IsNullOrWhiteSpace(name.Text)) await RunAsync(async () => { await view.ApiSend($"assets/{asset.Id}", HttpMethod.Patch, new { display_name = name.Text.Trim() }); if (Active) await RefreshDataAsync(); }); };
        text.Children.Add(Kit.Caption("服装参考 · " + asset.SizeLabel)); text.Children.Add(new TextBlock { Text = "✓ " + asset.StatusLabel, Foreground = AssetPageUi.Brush("Success"), Margin = new Thickness(0, 6, 0, 8) });
        var linked = view.outfits.Where(o => o.ReferenceAssetIds.Contains(asset.Id)).Select(o => (view.characters.FirstOrDefault(c => c.Id == o.CharacterId)?.PrimaryName ?? "未知角色") + " → " + o.Name).ToList();
        text.Children.Add(Surface(Kit.Caption(linked.Count == 0 ? "尚未写入服装档案" : "已绑定：" + string.Join("；", linked)), 6));
        var bind = Kit.Act(CharacterId.Length == 0 ? "先选择所属角色" : selected.Contains(asset.Id) ? "已选：保存后绑定" : "加入当前服装档案", (_, _) => { if (busy || CharacterId.Length == 0) return; if (!selected.Add(asset.Id)) selected.Remove(asset.Id); UpdateDraft(); RenderReferences(); if (library.Visibility == Visibility.Visible) RenderLibrary(); }, selected.Contains(asset.Id) ? "InkButton" : "Ghost"); bind.IsEnabled = CharacterId.Length > 0; bind.Margin = new Thickness(0, 8, 0, 8); text.Children.Add(bind);
        var actions = new Grid { ColumnDefinitions = { new ColumnDefinition(), new ColumnDefinition { Width = GridLength.Auto } } };
        var kinds = new ComboBox { MinHeight = 36, Margin = new Thickness(0, 0, 6, 0) };
        foreach (var kind in new[] { "CHARACTER_REFERENCE", "OUTFIT_REFERENCE", "SCENE_REFERENCE", "STYLE_REFERENCE" }) kinds.Items.Add(new ComboBoxItem { Tag = kind, Content = Labels.Map(Labels.AssetKinds, kind) });
        kinds.SelectedIndex = 1;
        kinds.SelectionChanged += async (_, _) => { var kind = (kinds.SelectedItem as ComboBoxItem)?.Tag as string; if (kind == null || kind == asset.Kind) return; kinds.SelectedIndex = 1; if (MessageBox.Show(view.WindowHost(), "修改素材用途可能解除已有绑定，确定继续吗？", "修改素材用途", MessageBoxButton.YesNo) == MessageBoxResult.Yes) await RunAsync(async () => { await view.ApiSend($"assets/{asset.Id}", HttpMethod.Patch, new { kind }); if (Active) await RefreshDataAsync(); }); };
        actions.Children.Add(kinds); var remove = SourceIcon.Action("trash", "删除素材", async (_, _) => { if (MessageBox.Show(view.WindowHost(), "删除该素材及其候选记录，并解除已有绑定？", "删除素材", MessageBoxButton.YesNo) == MessageBoxResult.Yes) await RunAsync(async () => { await view.ApiSendOptional($"assets/{asset.Id}", HttpMethod.Delete); if (Active) await RefreshDataAsync(); }); }); Grid.SetColumn(remove, 1); actions.Children.Add(remove); text.Children.Add(actions);
        var surface = Surface(grid, 10); if (selected.Contains(asset.Id)) surface.BorderBrush = AssetPageUi.Brush("Accent"); return surface;
    }
}
