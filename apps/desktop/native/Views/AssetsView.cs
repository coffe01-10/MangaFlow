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

/// <summary>NUI-3: reference assets — characters / outfits / scenes / style / references sub-views.</summary>
public sealed class AssetsView : WorkspaceView
{
    public const string Characters = "characters", Outfits = "outfits", Scenes = "scenes", Style = "style", References = "references";

    private readonly WrapPanel subnav = new();
    private readonly Grid host = new();
    private readonly Dictionary<string, ToggleButton> tabs = new();
    private string current = Characters;
    private int epoch, loadRequest;
    internal int Epoch => epoch;
    internal bool IsCurrent(int captured) => captured == epoch && !lifetime.IsCancellationRequested;

    internal List<CharacterItem> characters = [];
    internal List<OutfitItem> outfits = [];
    internal List<StyleItem> styles = [];
    internal List<AssetItem> assets = [];
    private List<JsonElement> models = [];
    internal CharacterItem? SelectedCharacter;
    internal OutfitItem? SelectedOutfit;
    internal string OutfitPreviewId = "";
    internal StyleItem? SelectedStyle;
    private readonly TextBlock notice = new() { Style = (Style)Application.Current.FindResource("Caption"), TextWrapping = TextWrapping.Wrap, Margin = new Thickness(0, 0, 0, 12) };

    public AssetsView()
    {
        var panel = new StackPanel { Margin = new Thickness(0, 0, 0, 70), Background = (Brush)Application.Current.FindResource("Paper") };
        RenderOptions.SetClearTypeHint(panel, ClearTypeHint.Enabled);
        foreach (var (key, label) in new[]
                 {
                     (Characters, "人物设定"), (Outfits, "服装档案"), (Scenes, "场景资产"), (Style, "漫画风格"), (References, "原始参考素材"),
                 })
        {
            var tab = new ToggleButton
            {
                Content = label, Style = (Style)Application.Current.FindResource("AssetTab"),
                Tag = key, Margin = new Thickness(0, 0, 7, 0),
            };
            tab.Click += (_, _) => { if (current == key) tab.IsChecked = true; else Switch(key); };
            tabs[key] = tab;
            subnav.Children.Add(tab);
        }
        panel.Children.Add(new Border { Child = subnav, BorderBrush = (Brush)FindResource("Line"), BorderThickness = new Thickness(0, 0, 0, 1), Margin = new Thickness(0, 0, 0, 22) });
        notice.SetBinding(VisibilityProperty, new System.Windows.Data.Binding("Text") { Source = notice, ConverterParameter = "collapse", Converter = (System.Windows.Data.IValueConverter)FindResource("EmptyToCollapsed") });
        panel.Children.Add(notice);
        panel.Children.Add(host);
        var scroller = new ScrollViewer { VerticalScrollBarVisibility = ScrollBarVisibility.Auto, Content = panel };
        Content = scroller;
    }

    public override async void Activate(WorkspaceContext context)
    {
        var changed = ProjectId != context.ProjectId;
        base.Activate(context); epoch++;
        if (changed) { SelectedCharacter = null; SelectedOutfit = null; OutfitPreviewId = ""; SelectedStyle = null; notice.Text = ""; }
        // Deactivation cancels in-flight reads; async void has no caller to observe the
        // cancellation, so swallow it here instead of crashing the dispatcher.
        try { await LoadAsync(); }
        catch (OperationCanceledException) { }
    }

    public void Switch(string view)
    {
        if (!tabs.ContainsKey(view)) return;
        if (current == view) return;
        current = view;
        foreach (var (key, tab) in tabs) tab.IsChecked = key == view;
        Render();
        if (view == Style) _ = LoadAsync();   // styles load lazily on first visit
    }

    public override void Deactivate() { epoch++; base.Deactivate(); }

    internal async Task LoadAsync()
    {
        var captured = epoch; var token = lifetime.Token; var request = ++loadRequest;
        try
        {
            var modelTask = Api.SendAsync("models", cancellation: token);
            var assetTask = Api.SendAsync($"assets?project_id={ProjectId}", cancellation: token);
            var characterTask = Api.SendAsync($"projects/{ProjectId}/characters", cancellation: token);
            var outfitTask = Api.SendAsync($"projects/{ProjectId}/outfits", cancellation: token);
            await Task.WhenAll(modelTask, assetTask, characterTask, outfitTask);
            if (!IsCurrent(captured) || request != loadRequest) return;
            models = (await modelTask).EnumerateArray().ToList();
            assets = (await assetTask).EnumerateArray().Select(AssetItem.From).ToList();
            characters = (await characterTask).EnumerateArray().Select(CharacterItem.From).ToList();
            SelectedCharacter = characters.FirstOrDefault(c => c.Id == SelectedCharacter?.Id);
            outfits = (await outfitTask).EnumerateArray().Select(OutfitItem.From).ToList();
            var keepPane = false;
            if (current == Style)
            {
                var styleRows = await Api.SendAsync($"projects/{ProjectId}/styles", cancellation: token);
                if (!IsCurrent(captured) || request != loadRequest) return;
                var next = styleRows.EnumerateArray().Select(StyleItem.From).ToList();
                // PollTick during ANALYZING reloads every 3s; only rebuild the pane
                // when a style actually changed, or the creation form loses input.
                keepPane = host.Children.Count > 0 && StyleFingerprint(next) == StyleFingerprint(styles);
                styles = next;
            }
            if (keepPane) return;
            foreach (var (key, tab) in tabs) tab.IsChecked = key == current;
            Render();
        }
         catch (OperationCanceledException) { }
        catch (Exception error) when (error is not OperationCanceledException)
        {
            if (!IsCurrent(captured) || request != loadRequest) return;
            host.Children.Clear();
            host.Children.Add(Kit.Caption($"资产读取失败：{error.Message}"));
        }
    }

    private static string StyleFingerprint(List<StyleItem> rows) =>
        string.Join("|", rows.OrderBy(s => s.Id).Select(s =>
            $"{s.Id}:{s.Status}:{s.PaletteConfirmed}:{string.Join(",", s.PaletteDraft ?? [])}"));

    internal List<JsonElement> ImageEditModels => models
        .Where(m => m.Text("model_type") == "IMAGE" && m.Array("operations").Any(o => o.ToString() == "image_edit") && m.Flag("enabled"))
        .ToList();

    internal string SelectedImageModel
    {
        get => KeyValueStore.Get("image-model:" + ProjectId);
        set => KeyValueStore.Set("image-model:" + ProjectId, value);
    }

    private void Render()
    {
        host.Children.Clear();
        switch (current)
        {
            case Characters: host.Children.Add(new CharactersPane(this)); break;
            case Outfits: host.Children.Add(new OutfitWorkspace(this)); break;
            case Scenes: host.Children.Add(new ScenesPane(this)); break;
            case Style: host.Children.Add(new StylePane(this)); break;
            default: host.Children.Add(new ReferencesPane(this)); break;
        }
    }

    internal void Notify(string message) => notice.Text = message;

    // Bridges for the nested panes (they are plain controls, not WorkspaceViews).
    internal Task<JsonElement> ApiSend(string path, HttpMethod? method = null, object? body = null) =>
        Api.SendAsync(path, method, body, lifetime.Token);
    internal Task<JsonElement?> ApiSendOptional(string path, HttpMethod? method = null, object? body = null) =>
        Api.SendOptionalAsync(path, method, body, lifetime.Token);
    internal string ProjectIdValue => ProjectId;
    internal Window WindowHost() => Host;
    internal string OriginFor(string path) => Api.OriginUrl(path);
    internal void ShowImage(string url, string label) => OpenImage(url, label);

    internal async Task<JsonElement> UploadAsset(string kind, string filePath)
    {
        var data = await File.ReadAllBytesAsync(filePath, lifetime.Token);
        var mime = Path.GetExtension(filePath).ToLowerInvariant() switch
        {
            ".png" => "image/png", ".jpg" or ".jpeg" => "image/jpeg", ".webp" => "image/webp", _ => "",
        };
        if (mime.Length == 0) throw new InvalidOperationException("只支持 PNG、JPEG 或 WebP 图片");
        if (data.Length > 20 * 1024 * 1024) throw new InvalidOperationException("图片不能超过 20 MB");
        return await Api.UploadAsync("assets/upload",
            new Dictionary<string, string> { ["project_id"] = ProjectId, ["kind"] = kind },
            ("file", Path.GetFileName(filePath), mime, data), lifetime.Token);
    }

    internal async Task ReloadAssets()
    {
        var captured = epoch;
        await LoadAsync();
        if (IsCurrent(captured)) Cache.Invalidate("assets:" + ProjectId, "dashboard");
    }

    public override Task RefreshAsync() => LoadAsync();

    internal void InvalidateOutfitDependents() => Cache.Invalidate("assets:" + ProjectId, "library:" + ProjectId, "jobs:" + ProjectId, "workbench:", "script:", "storyboard:", "pages:", "dashboard");

    internal bool OwnsOutfits(OutfitWorkspace pane) => host.Children.Contains(pane);

    public override void PollTick()
    {
        if (current == Outfits && host.Children.OfType<OutfitWorkspace>().FirstOrDefault() is { } outfitPane) outfitPane.PollTick();
        if (current == Style && styles.Any(s => s.Analyzing)) _ = LoadAsync();
    }
}

/// <summary>Model picker band shared by every asset sub-view (image edit models only).</summary>
internal sealed class ModelPickerBand : Border
{
    private readonly AssetsView view;
    public string Selected { get; private set; } = "";

    public ModelPickerBand(AssetsView view, string label)
    {
        this.view = view;
        BorderBrush = (Brush)Application.Current.FindResource("Line");
        BorderThickness = new Thickness(0);
        Padding = new Thickness(0);
        Margin = new Thickness(0, 14, 0, 24);
        var panel = new StackPanel();
        panel.Children.Add(new TextBlock { Text = label, Foreground = (Brush)Application.Current.FindResource("Muted"), FontSize = 13 });
        var options = view.ImageEditModels;
        if (options.Count == 0)
        {
            panel.Children.Add(new TextBlock
            {
                Text = "暂无已启用且支持参考图编辑的图片模型，请先到系统设置配置供应商。",
                Foreground = (Brush)Application.Current.FindResource("Danger"), FontSize = 12.5,
            });
        }
        else
        {
            var row = new TilePanel { MinimumTileWidth = 260, MaximumColumns = 2, Gap = 9, Margin = new Thickness(0, 15, 0, 4) };
            Selected = view.SelectedImageModel;
            foreach (var option in options)
            {
                var alias = option.Text("logical_alias");
                var description = new StackPanel();
                description.Children.Add(new TextBlock { Text = option.Text("display_name"), FontWeight = FontWeights.Bold, FontSize = 12, TextTrimming = TextTrimming.CharacterEllipsis, TextWrapping = TextWrapping.NoWrap });
                description.Children.Add(new TextBlock { Text = string.Join(" · ", new[] { option.Text("provider"), option.Text("model_id") }.Where(s => s.Length > 0)), FontSize = 11, FontWeight = FontWeights.Normal, FontFamily = (FontFamily)Application.Current.FindResource("Mono"), Foreground = (Brush)Application.Current.FindResource("Muted"), Margin = new Thickness(0, 4, 0, 0), TextWrapping = TextWrapping.Wrap });
                var toggle = new ToggleButton
                {
                    Content = description, Tag = alias,
                    Style = (Style)Application.Current.FindResource("ModelChoice"),
                    IsChecked = alias == Selected,
                };
                System.Windows.Automation.AutomationProperties.SetName(toggle, option.Text("display_name") + " · " + option.Text("model_id"));
                toggle.Click += (_, _) =>
                {
                    Selected = alias;
                    view.SelectedImageModel = alias;
                    foreach (var other in row.Children.OfType<ToggleButton>()) other.IsChecked = ReferenceEquals(other, toggle);
                };
                row.Children.Add(toggle);
            }
            panel.Children.Add(row);
        }
        Child = panel;
    }
}

/// <summary>Characters pane: strip + profile editor + concept panel + package workspace entry.</summary>
internal sealed class CharactersPane : StackPanel
{
    private readonly AssetsView view;
    private readonly StackPanel strip = new() { Orientation = Orientation.Horizontal };
    private readonly StackPanel concept = new();
    private readonly Button addButton = new() { Content = "＋ 添加角色", MinHeight = 44, Style = (Style)Application.Current.FindResource("InkButton") };
    private bool saving;
    private readonly StackPanel editor = new();
    private readonly TextBox nameInput = new() { Height = 42, FontSize = 13 };
    private readonly TextBox aliasInput = new() { Height = 42, FontSize = 13 };

    public CharactersPane(AssetsView view)
    {
        this.view = view;
        Margin = new Thickness(0);
        Children.Add(Header("C H A R A C T E R  B I B L E  /  角色资产", "姓名、绰号与参考图绑定", $"{view.characters.Count} 个角色"));
        var createRow = new Grid { Margin = new Thickness(0, 18, 0, 12), ColumnDefinitions = { new ColumnDefinition(), new ColumnDefinition(), new ColumnDefinition { Width = GridLength.Auto } } };
        createRow.Children.Add(AssetPageUi.Input(nameInput, "主要姓名（剧本默认使用）", "新角色主要姓名"));
        var aliases = AssetPageUi.Input(aliasInput, "绰号，用逗号分隔", "新角色绰号，用逗号分隔");
        aliases.Margin = new Thickness(8, 0, 8, 0); Grid.SetColumn(aliases, 1); createRow.Children.Add(aliases);
        Grid.SetColumn(addButton, 2); createRow.Children.Add(addButton);
        addButton.IsEnabled = false; nameInput.TextChanged += (_, _) => addButton.IsEnabled = !saving && nameInput.Text.Trim().Length > 0;
        addButton.Click += async (_, _) => await AddCharacter();
        Children.Add(createRow);
        Children.Add(new ScrollViewer { HorizontalScrollBarVisibility = ScrollBarVisibility.Auto, VerticalScrollBarVisibility = ScrollBarVisibility.Disabled, Content = strip, Margin = new Thickness(0, 4, 0, 12) });
        RenderStrip(); Children.Add(editor);
        Children.Add(new ModelPickerBand(view, "项目视觉模型（必须显式选择，并在各生成页面保持一致）"));
        Children.Add(concept);
        Children.Add(new CharacterPackagesWorkspace(view));
        Children.Add(new CharacterReferencesPane(view));
        RenderEditor();
    }

    private static Border Header(string kicker, string title, string count)
    {
        var border = new Border { BorderBrush = (Brush)Application.Current.FindResource("Ink"), BorderThickness = new Thickness(0, 0, 0, 1), Padding = new Thickness(0, 6, 0, 17) };
        var grid = new Grid();
        grid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        grid.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        var heading = new StackPanel();
        heading.Children.Add(new TextBlock { Text = kicker, FontSize = 9, FontWeight = FontWeights.Bold, Foreground = (Brush)Application.Current.FindResource("Muted") });
        heading.Children.Add(new TextBlock
        {
            Text = title, FontFamily = (FontFamily)Application.Current.FindResource("Serif"),
            FontSize = 23, FontWeight = FontWeights.Normal, Margin = new Thickness(0, 10, 0, 0),
        });
        grid.Children.Add(heading);
        var counter = new TextBlock { Text = count, Style = (Style)Application.Current.FindResource("Caption"), VerticalAlignment = VerticalAlignment.Bottom };
        Grid.SetColumn(counter, 1);
        grid.Children.Add(counter);
        border.Child = grid;
        return border;
    }

    private void RenderStrip()
    {
        strip.Children.Clear();
        foreach (var character in view.characters)
        {
            var chip = new ToggleButton
            {
                Content = new StackPanel
                {
                    Children =
                    {
                        new TextBlock { Text = character.PrimaryName, FontWeight = FontWeights.Bold },
                        new TextBlock { Text = character.AliasLabel, Style = (Style)Application.Current.FindResource("Micro") },
                        new TextBlock { Text = $"{character.ReferenceCount} 张参考图 · {character.LockedReferences} 项已锁定", Style = (Style)Application.Current.FindResource("Micro") },
                    },
                },
                Style = (Style)Application.Current.FindResource("CharacterChoice"),
                Tag = character.Id,
                IsChecked = view.SelectedCharacter?.Id == character.Id,
                Margin = new Thickness(0, 0, 8, 4), Width = 176, MinHeight = 80,
            };
            chip.Click += (_, _) => { view.SelectedCharacter = character; RenderStrip(); RenderEditor(); foreach (var references in Children.OfType<CharacterReferencesPane>()) references.Render(); };
            strip.Children.Add(chip);
        }
        if (view.characters.Count == 0)
            strip.Children.Add(Kit.Caption("还没有角色。填写姓名后点击“添加角色”。"));
    }

    private void RenderEditor()
    {
        editor.Children.Clear(); concept.Children.Clear();
        var character = view.SelectedCharacter;
        if (character == null) return;
        editor.Children.Add(new TextBlock
        {
            Text = "规范姓名与一致性锁 / 剧本统一使用主要姓名；固定特征和禁止改变项会进入每次生图提示。",
            Style = (Style)Application.Current.FindResource("Micro"), Margin = new Thickness(0, 4, 0, 12), TextWrapping = TextWrapping.Wrap,
        });
        var form = new StackPanel();
        // Fresh instances per render: a WPF element can only have one logical parent,
        // so the editor must not reuse the create-row inputs.
        var editName = new TextBox { Text = character.PrimaryName };
        var editAlias = new TextBox { Text = string.Join("，", character.Aliases) };
        var lockedFeatures = new TextBox { Text = character.LockedFeatures };
        var forbiddenChanges = new TextBox { Text = character.ForbiddenChanges };
        var row = new Grid { ColumnDefinitions = { new ColumnDefinition(), new ColumnDefinition(), new ColumnDefinition { Width = GridLength.Auto } } };
        row.Children.Add(editName);
        editAlias.Margin = new Thickness(8, 0, 8, 0);
        Grid.SetColumn(editAlias, 1); row.Children.Add(editAlias);
        var save = Kit.Act("保存角色规范", async (_, _) => await SaveCharacter(character, editName.Text, editAlias.Text, lockedFeatures.Text, forbiddenChanges.Text), "Outline");
        Grid.SetColumn(save, 2); row.Children.Add(save);
        form.Children.Add(row);
        form.Children.Add(Labelled("固定特征（生图时保持）", lockedFeatures));
        form.Children.Add(Labelled("禁止改变项", forbiddenChanges));
        editor.Children.Add(new Border { Style = (Style)Application.Current.FindResource("Card"), Padding = new Thickness(18), Child = form });
        concept.Children.Add(new ConceptPanel(view, character));
    }

    private static StackPanel Labelled(string label, TextBox box)
    {
        var panel = new StackPanel { Margin = new Thickness(0, 10, 0, 0) };
        panel.Children.Add(new TextBlock { Text = label, Style = (Style)Application.Current.FindResource("FieldLabel") });
        panel.Children.Add(box);
        return panel;
    }

    private async Task AddCharacter()
    {
        if (saving) return;
        var captured = view.Epoch;
        var name = nameInput.Text.Trim();
        if (name.Length == 0) { view.Notify("请填写姓名。"); return; }
        saving = true; addButton.IsEnabled = false; nameInput.IsEnabled = aliasInput.IsEnabled = false;
        try
        {
            var aliases = aliasInput.Text.Split(['，', ',', '、'], StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries);
            await view.ApiSend("projects/" + view.ProjectIdValue + "/characters", HttpMethod.Post,
                new { primary_name = name, aliases });
            if (!view.IsCurrent(captured)) return;
            nameInput.Clear(); aliasInput.Clear();
            await view.ReloadAssets();
            view.Notify($"角色「{name}」已创建。");
        }
        catch (Exception error) { if (view.IsCurrent(captured)) view.Notify("创建角色失败：" + error.Message); }
        finally { saving = false; addButton.IsEnabled = nameInput.Text.Trim().Length > 0; nameInput.IsEnabled = aliasInput.IsEnabled = true; }
    }

    private async Task SaveCharacter(CharacterItem character, string primaryName, string aliasText,
        string lockedFeatures, string forbiddenChanges)
    {
        if (saving || primaryName.Trim().Length == 0) return;
        saving = true; editor.IsEnabled = false; var captured = view.Epoch;
        try
        {
            var aliases = aliasText.Split(['，', ',', '、'], StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries);
            await view.ApiSend($"characters/{character.Id}", HttpMethod.Patch, new
            {
                version = character.Version,
                primary_name = primaryName.Trim(),
                aliases,
                locked_features = lockedFeatures.Split(['，', ',', '、'], StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries),
                forbidden_changes = forbiddenChanges.Split(['，', ',', '、'], StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries),
            });
            if (!view.IsCurrent(captured)) return;
            await view.ReloadAssets();
            if (view.IsCurrent(captured)) view.Notify("角色规范已保存。");
        }
        catch (Exception error) { if (view.IsCurrent(captured)) view.Notify("保存角色失败：" + error.Message); }
        finally { saving = false; editor.IsEnabled = true; }
    }
}

/// <summary>AI concept panel: one-sheet generation for character + outfit norms.</summary>
internal sealed class ConceptPanel : Border
{
    private readonly AssetsView view;
    private readonly CharacterItem character;
    private readonly TextBox appearance = new() { AcceptsReturn = true, MinHeight = 54 };
    private readonly TextBox outfitName = new() { Width = 260 };
    private readonly TextBox outfitDescription = new() { AcceptsReturn = true, MinHeight = 54 };
    private readonly TextBox lockedFields = new() { Width = 260 };
    private readonly StackPanel candidates = new();

    public ConceptPanel(AssetsView view, CharacterItem character)
    {
        this.view = view;
        this.character = character;
        Margin = new Thickness(0, 14, 0, 0);
        Style = (Style)Application.Current.FindResource("Card");
        Padding = new Thickness(18);
        var panel = new StackPanel();
        panel.Children.Add(new TextBlock { Text = "AI CONCEPT / 待确认草稿", Style = (Style)Application.Current.FindResource("SectionIndex") });
        panel.Children.Add(new TextBlock
        {
            Text = "一张综合设定页，同时建立人物与服装规范",
            FontFamily = (FontFamily)Application.Current.FindResource("Serif"), FontSize = 17, FontWeight = FontWeights.Bold, Margin = new Thickness(0, 4, 0, 2),
        });
        panel.Children.Add(new TextBlock
        {
            Text = "输出为 1 张彩色综合页：正面、侧面、背面、表情、剪裁与配饰细节。生成结果不会自动成为规范参考。",
            Style = (Style)Application.Current.FindResource("Micro"), Margin = new Thickness(0, 4, 0, 12), TextWrapping = TextWrapping.Wrap,
        });
        panel.Children.Add(Field("人物外观描述（可空）", appearance));
        panel.Children.Add(Field("服装档案名称", outfitName));
        panel.Children.Add(Field("服装描述", outfitDescription));
        panel.Children.Add(Field("确认后锁定项", lockedFields));
        var generate = Kit.Act("生成概念设定草稿", async (_, _) => await Generate(), "InkButton");
        generate.Margin = new Thickness(0, 6, 0, 12);
        generate.HorizontalAlignment = HorizontalAlignment.Left;
        panel.Children.Add(generate);
        panel.Children.Add(candidates);
        Child = panel;
        _ = LoadCandidatesAsync();
    }

    private static StackPanel Field(string label, TextBox box)
    {
        var panel = new StackPanel { Margin = new Thickness(0, 0, 0, 10) };
        panel.Children.Add(new TextBlock { Text = label, Style = (Style)Application.Current.FindResource("FieldLabel") });
        panel.Children.Add(box);
        return panel;
    }

    private async Task Generate()
    {
        var alias = new ModelPickerBand(view, "").Selected;
        if (alias.Length == 0) { view.Notify("请先选择一个支持参考图编辑的图片模型"); return; }
        if (outfitName.Text.Trim().Length == 0 || outfitDescription.Text.Trim().Length == 0)
        { view.Notify("请填写服装档案名称和服装描述。"); return; }
        try
        {
            await view.ApiSend($"characters/{character.Id}/complete-sheet", HttpMethod.Post, new
            {
                model_alias = alias, resolution = "1K", generation_mode = "CONCEPT",
                appearance_description = appearance.Text, outfit_name = outfitName.Text.Trim(),
                outfit_description = outfitDescription.Text,
            });
            view.Notify("概念设定任务已创建，候选生成后会显示在下方。");
            await LoadCandidatesAsync();
        }
        catch (Exception error) { view.Notify("生成失败：" + error.Message); }
    }

    private async Task LoadCandidatesAsync()
    {
        try
        {
            var batches = await view.ApiSend(QueryBuilder.Build("asset-generation-batches",
                ("target_type", "CHARACTER"), ("target_id", character.Id), ("limit", 10)));
            candidates.Children.Clear();
            foreach (var batch in batches.EnumerateArray().Reverse())
            {
                var rows = await view.ApiSend($"batches/{batch.Text("id")}/candidates");
                foreach (var row in rows.EnumerateArray())
                {
                    var candidate = CandidateItem.From(row);
                    var card = new StackPanel { Margin = new Thickness(0, 0, 12, 12) };
                    var url = candidate.ContentUrl.Length > 0 ? candidate.ContentUrl : (candidate.AssetId.Length > 0 ? $"assets/{candidate.AssetId}/content" : "");
                    Border artwork;
                    if (url.Length > 0)
                    {
                        artwork = new Border { Width = 128, Height = 128, BorderBrush = (Brush)Application.Current.FindResource("LineDark"), BorderThickness = new Thickness(1) };
                        var image = new ImageBox { SourceUrl = view.OriginFor(url) };
                        artwork.Child = image;
                        artwork.Cursor = Cursors.Hand;
                        artwork.MouseLeftButtonDown += (_, _) => view.ShowImage(view.OriginFor(url), $"{character.PrimaryName} 概念设定");
                    }
                    else
                    {
                        artwork = new Border
                        {
                            Width = 128, Height = 128, Background = (Brush)Application.Current.FindResource("PaperDeep"),
                            Child = new TextBlock
                            {
                                Text = candidate.Status == "FAILED" ? "生成失败" : "等待 Worker 生成",
                                Style = (Style)Application.Current.FindResource("Micro"),
                                HorizontalAlignment = HorizontalAlignment.Center, VerticalAlignment = VerticalAlignment.Center,
                            },
                        };
                    }
                    card.Children.Add(artwork);
                    card.Children.Add(new TextBlock { Text = candidate.StatusLabel, Style = (Style)Application.Current.FindResource("Micro"), Margin = new Thickness(0, 4, 0, 0) });
                    if (candidate.Status == "READY")
                    {
                        var approve = Kit.Act("确认为规范参考", async (_, _) => await Approve(candidate), "CompactInk");
                        approve.Margin = new Thickness(0, 6, 0, 0);
                        card.Children.Add(approve);
                    }
                    candidates.Children.Add(card);
                }
            }
            if (candidates.Children.Count == 0)
                candidates.Children.Add(Kit.Caption("第一张草稿生成后会实时出现在这里；确认前不会进入正式页面提示词。"));
        }
        catch (Exception) { candidates.Children.Add(Kit.Caption("候选读取失败。")); }
    }

    private async Task Approve(CandidateItem candidate)
    {
        try
        {
            await view.ApiSend($"asset-candidates/{candidate.Id}/approve-reference", HttpMethod.Post, new
            {
                character_id = character.Id,
                bind_character_reference = true,
                set_canonical = true,
                outfit_name = outfitName.Text.Trim().Length > 0 ? outfitName.Text.Trim() : null,
                outfit_description = outfitDescription.Text.Trim().Length > 0 ? outfitDescription.Text : null,
                outfit_locked_fields = lockedFields.Text.Trim().Length > 0 ? lockedFields.Text.Trim().Split('；', ';') : Array.Empty<string>(),
            });
            view.Notify("已绑定人物与服装。");
            await view.ReloadAssets();
        }
        catch (Exception error) { view.Notify("确认采用失败：" + error.Message); }
    }
}

/// <summary>Outfits pane: three-step binding flow + saved records.</summary>
internal sealed class OutfitsPane : StackPanel
{
    private readonly AssetsView view;
    private readonly ComboBox characterSelector = new() { Width = 240 };
    private readonly TextBox outfitName = new() { Width = 260 };
    private readonly TextBox lockedFields = new() { Width = 320 };
    private readonly StackPanel records = new();
    private readonly StackPanel liveResults = new();
    private readonly StackPanel pendingRefs = new();
    // 素材库候选按钮住在专用面板：待绑定计数的 RenderPending 会重建
    // pendingRefs，若按钮与其同住，导入一张后其余候选全部消失。
    private readonly StackPanel candidateRefs = new();
    private readonly List<string> pendingAssetIds = [];

    public OutfitsPane(AssetsView view)
    {
        this.view = view;
        Children.Add(PaneHeader("WARDROBE", "服装档案 · 角色、服装与参考图逐一绑定", $"{view.outfits.Count} 份档案"));
        foreach (var character in view.characters)
            characterSelector.Items.Add(new ComboBoxItem { Tag = character.Id, Content = character.PrimaryName });
        if (view.SelectedCharacter != null) SelectItem(characterSelector, view.SelectedCharacter.Id);
        characterSelector.SelectionChanged += (_, _) => RenderRecords();
        var form = new StackPanel();
        var step = new TextBlock
        {
            Text = "01 所属角色 → 02 服装参考 → 03 服装档案",
            Style = (Style)Application.Current.FindResource("Micro"), Margin = new Thickness(0, 0, 0, 10),
        };
        form.Children.Add(step);
        var row = new StackPanel { Orientation = Orientation.Horizontal };
        row.Children.Add(characterSelector);
        outfitName.Margin = new Thickness(8, 0, 8, 0);
        System.Windows.Automation.AutomationProperties.SetName(outfitName, "服装名称");
        row.Children.Add(outfitName);
        row.Children.Add(Kit.Act("上传参考图", async (_, _) => await UploadReference(), "Compact"));
        row.Children.Add(Kit.Act("从素材库导入", async (_, _) => await ImportFromLibrary(), "Compact"));
        form.Children.Add(row);
        lockedFields.Margin = new Thickness(0, 10, 0, 0);
        form.Children.Add(new TextBlock { Text = "锁定项", Style = (Style)Application.Current.FindResource("FieldLabel"), Margin = new Thickness(0, 10, 0, 6) });
        form.Children.Add(lockedFields);
        form.Children.Add(pendingRefs);
        form.Children.Add(candidateRefs);
        var actions = new StackPanel { Orientation = Orientation.Horizontal, Margin = new Thickness(0, 12, 0, 0) };
        actions.Children.Add(Kit.Act("建立并绑定", async (_, _) => await CreateOutfit(), "InkButton"));
        form.Children.Add(actions);
        Children.Add(new Border { Style = (Style)Application.Current.FindResource("Card"), Padding = new Thickness(18), Margin = new Thickness(0, 0, 0, 14), Child = form });
        Children.Add(liveResults);
        Children.Add(new TextBlock { Text = "已保存档案", Style = (Style)Application.Current.FindResource("SectionIndex"), Margin = new Thickness(0, 6, 0, 8) });
        Children.Add(records);
        RenderRecords();
    }

    private static void SelectItem(ComboBox box, string tag)
    {
        foreach (var item in box.Items.OfType<ComboBoxItem>())
            if ((string?)item.Tag == tag) { box.SelectedItem = item; return; }
        if (box.Items.Count > 0) box.SelectedIndex = 0;
    }

    private static Border PaneHeader(string kicker, string title, string count)
    {
        var border = new Border { Style = (Style)Application.Current.FindResource("CanvasHeader") };
        var grid = new Grid();
        grid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        grid.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        var heading = new StackPanel();
        heading.Children.Add(new TextBlock { Text = kicker, Style = (Style)Application.Current.FindResource("SectionIndex") });
        heading.Children.Add(new TextBlock
        {
            Text = title, FontFamily = (FontFamily)Application.Current.FindResource("Serif"),
            FontSize = 20, FontWeight = FontWeights.Bold, Margin = new Thickness(0, 5, 0, 0),
        });
        grid.Children.Add(heading);
        var counter = new TextBlock { Text = count, Style = (Style)Application.Current.FindResource("Caption"), VerticalAlignment = VerticalAlignment.Bottom };
        Grid.SetColumn(counter, 1);
        grid.Children.Add(counter);
        border.Child = grid;
        return border;
    }

    private async Task UploadReference()
    {
        if (characterSelector.SelectedItem is not ComboBoxItem { Tag: string characterId })
        { view.Notify("请先选择所属角色。"); return; }
        var picker = new OpenFileDialog { Filter = "图片|*.png;*.jpg;*.jpeg;*.webp", Title = "上传服装参考图" };
        if (picker.ShowDialog(view.WindowHost()) != true) return;
        try
        {
            var asset = await view.UploadAsset("OUTFIT_REFERENCE", picker.FileName);
            pendingAssetIds.Add(asset.Text("id"));
            RenderPending();
            view.Notify("参考图已上传，保存后绑定。");
        }
        catch (Exception error) { view.Notify(error.Message); }
    }

    private async Task ImportFromLibrary()
    {
        if (characterSelector.SelectedItem is not ComboBoxItem { Tag: string characterId }) { view.Notify("请先选择角色。"); return; }
        try
        {
            var rows = await view.ApiSend(QueryBuilder.Build($"projects/{view.ProjectIdValue}/library", ("character_id", characterId), ("limit", 30)));
            var adopted = rows.EnumerateArray().SelectMany(g => g.Array("candidates")).ToList();
            if (adopted.Count == 0) { view.Notify("当前角色还没有可导入的生成图片。"); return; }
            // 每次拉取都重建候选面板（重复点击不叠加旧按钮）；面板与
            // pendingRefs 分离，导入一张后其余候选按钮保持可见。
            candidateRefs.Children.Clear();
            foreach (var candidate in adopted.Take(30))
            {
                if (candidate.Text("asset_id").Length == 0) continue;
                var button = Kit.Act($"导入 {candidate.Text("display_name", "候选")}", async (_, _) =>
                {
                    try
                    {
                        var asset = await view.ApiSend($"assets/{candidate.Text("asset_id")}/adopt-reference", HttpMethod.Post);
                        pendingAssetIds.Add(asset.Text("id"));
                        RenderPending();
                        view.Notify("已加入待绑定。");
                    }
                    catch (Exception error) { view.Notify("导入失败：" + error.Message); }
                }, "Compact");
                candidateRefs.Children.Add(button);
            }
        }
        catch (Exception error) { view.Notify(error.Message); }
    }

    private void RenderPending()
    {
        pendingRefs.Children.Clear();
        if (pendingAssetIds.Count == 0) return;
        var label = new TextBlock
        {
            Text = $"待绑定 {pendingAssetIds.Count} 张（保存后绑定）",
            Style = (Style)Application.Current.FindResource("Micro"), Margin = new Thickness(0, 10, 0, 6),
        };
        pendingRefs.Children.Add(label);
    }

    private async Task CreateOutfit()
    {
        if (characterSelector.SelectedItem is not ComboBoxItem { Tag: string characterId }) { view.Notify("请先选择所属角色。"); return; }
        var name = outfitName.Text.Trim();
        if (name.Length == 0 || pendingAssetIds.Count == 0) { view.Notify("建档需要名称和至少一张参考图。"); return; }
        try
        {
            await view.ApiSend($"projects/{view.ProjectIdValue}/outfits", HttpMethod.Post, new
            {
                character_id = characterId,
                name = name,
                reference_asset_ids = pendingAssetIds.ToList(),
                locked_fields = lockedFields.Text,
                components = new Dictionary<string, object>(),
                state_rules = new Dictionary<string, object>(),
            });
            pendingAssetIds.Clear();
            outfitName.Clear();
            RenderPending();
            await view.ReloadAssets();
            view.Notify("服装档案已建立并绑定参考图。");
        }
        catch (Exception error) { view.Notify("建档失败：" + error.Message); }
    }

    private void RenderRecords()
    {
        records.Children.Clear();
        var characterId = (characterSelector.SelectedItem as ComboBoxItem)?.Tag as string ?? "";
        var mine = view.outfits.Where(o => o.CharacterId == characterId).ToList();
        if (mine.Count == 0)
        {
            records.Children.Add(Kit.Caption("还没有服装档案。完成上方 01–03 三步后建立。"));
            return;
        }
        foreach (var outfit in mine)
        {
            var card = new StackPanel();
            var header = new DockPanel();
            var actions = new StackPanel { Orientation = Orientation.Horizontal };
            var generate = Kit.Act("生成穿着图", async (_, _) => await GenerateWear(outfit), "Compact");
            var remove = Kit.Act("删除档案及图片", async (_, _) => await DeleteOutfit(outfit), "CompactDanger");
            remove.Margin = new Thickness(8, 0, 0, 0);
            actions.Children.Add(generate);
            actions.Children.Add(remove);
            DockPanel.SetDock(actions, Dock.Right);
            header.Children.Add(actions);
            header.Children.Add(new TextBlock
            {
                Text = outfit.Name, FontFamily = (FontFamily)Application.Current.FindResource("Serif"),
                FontSize = 16, FontWeight = FontWeights.Bold,
            });
            card.Children.Add(header);
            card.Children.Add(new TextBlock
            {
                Text = $"角色 → 服装 → {outfit.ReferenceCount} 张参考图" + (outfit.LockedFields.Length > 0 ? $" · 锁定：{outfit.LockedFields}" : ""),
                Style = (Style)Application.Current.FindResource("Micro"), Margin = new Thickness(0, 6, 0, 0),
            });
            records.Children.Add(new Border
            {
                Style = (Style)Application.Current.FindResource("Card"), Padding = new Thickness(16),
                Margin = new Thickness(0, 0, 0, 10), Child = card,
            });
        }
    }

    private async Task GenerateWear(OutfitItem outfit)
    {
        var alias = new ModelPickerBand(view, "").Selected;
        if (alias.Length == 0) { view.Notify("请先选择一个支持参考图编辑的图片模型"); return; }
        try
        {
            var batch = await view.ApiSend("asset-generation-batches", HttpMethod.Post, new
            {
                target_type = "OUTFIT", target_id = outfit.Id, generation_kind = "OUTFIT",
            });
            await view.ApiSend($"asset-generation-batches/{batch.Text("id")}/candidates", HttpMethod.Post, new
            {
                model_alias = alias, resolution = "1K", variant = "OUTFIT", instruction = "",
            });
            view.Notify("穿着图任务已创建，稍后在此查看结果。");
        }
        catch (Exception error) { view.Notify("生成失败：" + error.Message); }
    }

    private async Task DeleteOutfit(OutfitItem outfit)
    {
        if (MessageBox.Show(view.WindowHost(),
                $"删除服装档案“{outfit.Name}”？\n\n将同时删除绑定的 {outfit.ReferenceCount} 张参考图、已生成的穿着图，并清除剧本与分镜中的服装绑定。被其他档案共用的图片会保留。",
                "删除服装档案", MessageBoxButton.YesNo, MessageBoxImage.Warning) != MessageBoxResult.Yes) return;
        try
        {
            await view.ApiSendOptional($"outfits/{outfit.Id}", HttpMethod.Delete);
            await view.ReloadAssets();
            view.Notify("服装档案已删除。");
        }
        catch (Exception error) { view.Notify("删除失败：" + error.Message); }
    }
}

/// <summary>Style pane: creation + profiles with the 4-stage production pipeline.</summary>
internal sealed class StylePane : StackPanel
{
    private readonly AssetsView view;
    private readonly ToggleButton monoButton = new() { Content = "黑白漫画", Style = (Style)Application.Current.FindResource("Pill") };
    private readonly ToggleButton colorButton = new() { Content = "彩色漫画", Style = (Style)Application.Current.FindResource("Pill") };
    private readonly TextBox styleName = new() { Width = 260 };
    private readonly StackPanel profiles = new();

    public StylePane(AssetsView view)
    {
        this.view = view;
        Children.Add(PaneHeader("STYLE SYSTEM", "漫画风格 · 色板、画面语言与测试图", $"{view.styles.Count} 份档案"));
        var modeRow = new StackPanel { Orientation = Orientation.Horizontal, Margin = new Thickness(0, 0, 0, 10) };
        monoButton.IsChecked = true;
        monoButton.Click += (_, _) => { monoButton.IsChecked = true; colorButton.IsChecked = false; };
        colorButton.Click += (_, _) => { colorButton.IsChecked = true; monoButton.IsChecked = false; };
        modeRow.Children.Add(monoButton);
        colorButton.Margin = new Thickness(8, 0, 0, 0);
        modeRow.Children.Add(colorButton);
        var form = new StackPanel();
        form.Children.Add(modeRow);
        form.Children.Add(new TextBlock { Text = "名称", Style = (Style)Application.Current.FindResource("FieldLabel"), Margin = new Thickness(0, 6, 0, 6) });
        styleName.Text = "黑白网点风格";
        monoButton.Click += (_, _) => styleName.Text = "黑白网点风格";
        colorButton.Click += (_, _) => styleName.Text = "彩色漫画风格";
        form.Children.Add(styleName);
        var create = Kit.Act("创建并分析", async (_, _) => await CreateStyle(), "InkButton");
        create.Margin = new Thickness(0, 12, 0, 0);
        create.HorizontalAlignment = HorizontalAlignment.Left;
        form.Children.Add(create);
        Children.Add(new Border { Style = (Style)Application.Current.FindResource("Card"), Padding = new Thickness(18), Margin = new Thickness(0, 0, 0, 16), Child = form });
        Children.Add(new TextBlock { Text = "已保存档案 / 逐份修改与切换", Style = (Style)Application.Current.FindResource("SectionIndex"), Margin = new Thickness(0, 0, 0, 8) });
        Children.Add(profiles);
        RenderProfiles();
    }

    private static Border PaneHeader(string kicker, string title, string count)
    {
        var border = new Border { Style = (Style)Application.Current.FindResource("CanvasHeader") };
        var grid = new Grid();
        grid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        grid.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        var heading = new StackPanel();
        heading.Children.Add(new TextBlock { Text = kicker, Style = (Style)Application.Current.FindResource("SectionIndex") });
        heading.Children.Add(new TextBlock
        {
            Text = title, FontFamily = (FontFamily)Application.Current.FindResource("Serif"),
            FontSize = 20, FontWeight = FontWeights.Bold, Margin = new Thickness(0, 5, 0, 0),
        });
        grid.Children.Add(heading);
        var counter = new TextBlock { Text = count, Style = (Style)Application.Current.FindResource("Caption"), VerticalAlignment = VerticalAlignment.Bottom };
        Grid.SetColumn(counter, 1);
        grid.Children.Add(counter);
        border.Child = grid;
        return border;
    }

    private async Task CreateStyle()
    {
        try
        {
            var created = await view.ApiSend($"projects/{view.ProjectIdValue}/styles", HttpMethod.Post, new
            {
                name = styleName.Text.Trim().Length > 0 ? styleName.Text.Trim() : "新风格",
                color_mode = colorButton.IsChecked == true ? "color" : "monochrome",
                profile = new Dictionary<string, object>(),
                reference_asset_ids = Array.Empty<string>(),
                locked_fields = "",
            });
            try
            {
                await view.ApiSend($"styles/{created.Text("id")}/analyze", HttpMethod.Post);
            }
            catch (Exception)
            {
                view.Notify("风格分析任务启动失败；请在已保存档案中点击“重新分析画面语言”重试。");
            }
            await view.ReloadAssets();
            view.Notify("风格档案已创建。");
        }
        catch (Exception error) { view.Notify("创建风格失败：" + error.Message); }
    }

    private void RenderProfiles()
    {
        profiles.Children.Clear();
        if (view.styles.Count == 0)
        {
            profiles.Children.Add(Kit.Caption("还没有风格档案。用上方表单创建。"));
            return;
        }
        foreach (var style in view.styles)
            profiles.Children.Add(new StyleProfileCard(view, style));
    }
}

internal sealed class StyleProfileCard : Border
{
    private readonly AssetsView view;
    private readonly StyleItem style;

    public StyleProfileCard(AssetsView view, StyleItem style)
    {
        this.view = view;
        this.style = style;
        Style = (Style)Application.Current.FindResource("Card");
        Padding = new Thickness(18);
        Margin = new Thickness(0, 0, 0, 12);
        var panel = new StackPanel();
        var header = new DockPanel { Margin = new Thickness(0, 0, 0, 8) };
        var actions = new StackPanel { Orientation = Orientation.Horizontal };
        var analyze = Kit.Act("重新分析画面语言", async (_, _) => await Analyze(), "Compact");
        actions.Children.Add(analyze);
        DockPanel.SetDock(actions, Dock.Right);
        header.Children.Add(actions);
        var title = new StackPanel();
        title.Children.Add(new TextBlock
        {
            Text = style.Name, FontFamily = (FontFamily)Application.Current.FindResource("Serif"),
            FontSize = 16, FontWeight = FontWeights.Bold,
        });
        title.Children.Add(new TextBlock
        {
            Text = $"{style.StatusLabel} · {style.ReferenceCount} 参考 · {(style.LockedFields.Length > 0 ? style.LockedFields : "无锁定")}",
            Style = (Style)Application.Current.FindResource("Micro"),
        });
        header.Children.Add(title);
        panel.Children.Add(header);
        panel.Children.Add(BuildPipeline());
        Child = panel;
    }

    private async Task Analyze()
    {
        try
        {
            await view.ApiSend($"styles/{style.Id}/analyze", HttpMethod.Post);
            view.Notify("风格分析任务已创建。");
            await view.ReloadAssets();
        }
        catch (Exception error) { view.Notify("启动分析失败：" + error.Message); }
    }

    private FrameworkElement BuildPipeline()
    {
        var panel = new StackPanel { Margin = new Thickness(0, 8, 0, 0) };
        // Stage 01: palette draft proposal (color styles only).
        if (style.Status != "ANALYZING")
        {
            var atmosphere = new TextBox
            {
                Text = "葬礼后的克制情绪、潮湿京都、低饱和但保留人物识别色",
                AcceptsReturn = true, MinHeight = 44, Margin = new Thickness(0, 6, 0, 8),
            };
            var draft = Kit.Act("由默认文字模型提议色板", async (_, _) =>
            {
                try
                {
                    await view.ApiSend($"styles/{style.Id}/palette-draft", HttpMethod.Post, new { atmosphere = atmosphere.Text });
                    view.Notify("色板草稿任务已创建。");
                    await view.ReloadAssets();
                }
                catch (Exception error) { view.Notify(error.Message); }
            }, "Compact");
            panel.Children.Add(StageLabel("01 / AI 色板草稿"));
            panel.Children.Add(atmosphere);
            panel.Children.Add(draft);
        }
        // Stage 04: activation hint.
        var ready = style.PaletteConfirmed;
        var hint = ready
            ? "色板和测试图均已确认，可以激活为正式风格。"
            : style.PaletteDraft == null && style.Palette == null
                ? "还缺：先在第 01 步生成 AI 色板草稿。"
                : !style.PaletteConfirmed ? "还缺：在第 02 步检查并确认彩色色板。" : "还缺：生成 1K 风格测试图并点击人工通过。";
        panel.Children.Add(StageLabel("04 / 激活正式风格"));
        panel.Children.Add(new TextBlock { Text = hint, Style = (Style)Application.Current.FindResource("Micro"), Margin = new Thickness(0, 6, 0, 8) });
        if (ready)
        {
            var activate = Kit.Act("激活彩色风格", async (_, _) =>
            {
                try
                {
                    await view.ApiSend($"projects/{view.ProjectIdValue}/styles/{style.Id}/activate", HttpMethod.Post);
                    view.Notify("该档案已进入正式页面提示词。");
                    await view.ReloadAssets();
                }
                catch (Exception error) { view.Notify(error.Message); }
            }, "InkButton");
            activate.HorizontalAlignment = HorizontalAlignment.Left;
            panel.Children.Add(activate);
        }
        return panel;
    }

    private static TextBlock StageLabel(string text) => new()
    { Text = text, Style = (Style)Application.Current.FindResource("SectionIndex"), Margin = new Thickness(0, 10, 0, 4) };
}

/// <summary>Scenes pane: scene-asset list + detail (references, variants).</summary>
internal sealed class ScenesPane : StackPanel
{
    private readonly AssetsView view;
    private readonly TextBox searchInput = new() { Width = 220 };
    private readonly StackPanel list = new();
    private readonly StackPanel detail = new();
    private List<SceneAssetItem> sceneAssets = [];
    private SceneAssetItem? selected;

    public ScenesPane(AssetsView view)
    {
        this.view = view;
        Children.Add(PaneHeader("SCENE BIBLE", "场景资产 · 地点档案、环境变体与参考图绑定", ""));
        var toolbar = new StackPanel { Orientation = Orientation.Horizontal, Margin = new Thickness(0, 0, 0, 12) };
        toolbar.Children.Add(Kit.Act("新建场景", async (_, _) => await CreateScene(), "InkButton"));
        searchInput.Margin = new Thickness(12, 0, 0, 0);
        System.Windows.Automation.AutomationProperties.SetName(searchInput, "搜索场景");
        searchInput.TextChanged += (_, _) => RenderList();
        toolbar.Children.Add(searchInput);
        Children.Add(toolbar);
        var split = new Grid();
        split.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(264) });
        split.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        var listScroll = new ScrollViewer { VerticalScrollBarVisibility = ScrollBarVisibility.Auto, Content = list, MaxHeight = 640 };
        Grid.SetColumn(listScroll, 0);
        split.Children.Add(listScroll);
        var detailScroll = new ScrollViewer { VerticalScrollBarVisibility = ScrollBarVisibility.Auto, Content = detail };
        Grid.SetColumn(detailScroll, 1);
        split.Children.Add(detailScroll);
        Children.Add(split);
        _ = LoadScenesAsync();
    }

    private async Task LoadScenesAsync()
    {
        try
        {
            var rows = await view.ApiSend(QueryBuilder.Build($"projects/{view.ProjectIdValue}/scene-assets", ("limit", 200)));
            sceneAssets = rows.EnumerateArray().Select(SceneAssetItem.From).ToList();
            selected ??= sceneAssets.FirstOrDefault(s => !s.Deleted);
            RenderList();
            RenderDetail();
        }
        catch (Exception error) { detail.Children.Clear(); detail.Children.Add(Kit.Caption($"场景资产无法载入：{error.Message}")); }
    }

    private async Task CreateScene()
    {
        var dialog = new InputDialog(view.WindowHost(), "新建场景资产", "场景名称", "例如：京都老宅佛堂");
        if (dialog.ShowDialog() != true || dialog.Value.Trim().Length == 0) return;
        var name = dialog.Value.Trim();
        try
        {
            await view.ApiSend($"projects/{view.ProjectIdValue}/scene-assets", HttpMethod.Post, new { name });
            await LoadScenesAsync();
            view.Notify("场景资产已创建。");
        }
        catch (Exception error) { view.Notify("创建失败：" + error.Message); }
    }

    private static Border PaneHeader(string kicker, string title, string count)
    {
        var border = new Border { Style = (Style)Application.Current.FindResource("CanvasHeader") };
        var grid = new Grid();
        grid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        grid.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        var heading = new StackPanel();
        heading.Children.Add(new TextBlock { Text = kicker, Style = (Style)Application.Current.FindResource("SectionIndex") });
        heading.Children.Add(new TextBlock
        {
            Text = title, FontFamily = (FontFamily)Application.Current.FindResource("Serif"),
            FontSize = 20, FontWeight = FontWeights.Bold, Margin = new Thickness(0, 5, 0, 0),
        });
        grid.Children.Add(heading);
        var counter = new TextBlock { Text = count, Style = (Style)Application.Current.FindResource("Caption"), VerticalAlignment = VerticalAlignment.Bottom };
        Grid.SetColumn(counter, 1);
        grid.Children.Add(counter);
        border.Child = grid;
        return border;
    }

    private void RenderList()
    {
        list.Children.Clear();
        var keyword = searchInput.Text.Trim();
        var visible = sceneAssets
            .Where(s => s.Deleted == false || keyword.Length > 0)
            .Where(s => keyword.Length == 0 || s.Name.Contains(keyword, StringComparison.OrdinalIgnoreCase) || s.LocationHint.Contains(keyword, StringComparison.OrdinalIgnoreCase))
            .ToList();
        if (visible.Count == 0)
        {
            list.Children.Add(Kit.Caption("尚未创建场景资产"));
            return;
        }
        foreach (var asset in visible)
        {
            var card = new Border
            {
                BorderBrush = selected?.Id == asset.Id ? (Brush)Application.Current.FindResource("Accent") : (Brush)Application.Current.FindResource("Line"),
                BorderThickness = new Thickness(1), Padding = new Thickness(12), Margin = new Thickness(0, 0, 0, 8),
                Background = (Brush)Application.Current.FindResource("Surface"), Cursor = Cursors.Hand,
            };
            var content = new StackPanel();
            content.Children.Add(new TextBlock { Text = asset.Name, FontWeight = FontWeights.Bold, FontSize = 13.5 });
            content.Children.Add(new TextBlock { Text = $"{asset.InteriorLabel} · {asset.Variants.Count} 变体", Style = (Style)Application.Current.FindResource("Micro") });
            content.Children.Add(new TextBlock { Text = asset.StatusLabel, Style = (Style)Application.Current.FindResource("Micro") });
            card.Child = content;
            card.MouseLeftButtonDown += (_, _) => { selected = asset; RenderList(); RenderDetail(); };
            list.Children.Add(card);
        }
    }

    private void RenderDetail()
    {
        detail.Children.Clear();
        if (selected == null) return;
        var asset = selected;
        var header = new DockPanel { Margin = new Thickness(0, 0, 0, 10) };
        var actions = new StackPanel { Orientation = Orientation.Horizontal };
        actions.Children.Add(Kit.Act("设为规范参考", async (_, _) => await SetStatus("CANONICAL"), "CompactInk"));
        actions.Children.Add(Kit.Act("上传参考图", async (_, _) => await UploadReference(), "Compact"));
        var archive = Kit.Act(asset.Deleted ? "恢复" : "归档", async (_, _) => await ArchiveOrRestore(), "CompactDanger");
        archive.Margin = new Thickness(8, 0, 0, 0);
        actions.Children.Add(archive);
        DockPanel.SetDock(actions, Dock.Right);
        header.Children.Add(actions);
        var title = new StackPanel();
        title.Children.Add(new TextBlock { Text = "SCENE DETAIL", Style = (Style)Application.Current.FindResource("SectionIndex") });
        title.Children.Add(new TextBlock
        {
            Text = $"{asset.Name}（{asset.InteriorLabel}）",
            FontFamily = (FontFamily)Application.Current.FindResource("Serif"), FontSize = 19, FontWeight = FontWeights.Bold,
        });
        title.Children.Add(new TextBlock { Text = asset.StatusLabel, Style = (Style)Application.Current.FindResource("Micro") });
        header.Children.Add(title);
        detail.Children.Add(header);
        if (asset.Description.Length > 0 || asset.LocationHint.Length > 0)
            detail.Children.Add(Kit.Caption($"固定特征：{(asset.Description.Length > 0 ? asset.Description : asset.LocationHint)}"));
        detail.Children.Add(new TextBlock { Text = $"主参考图 · {asset.References.Count} 张", Style = (Style)Application.Current.FindResource("SectionIndex"), Margin = new Thickness(0, 14, 0, 8) });
        if (asset.References.Count == 0) detail.Children.Add(Kit.Caption("尚未绑定场景参考图"));
        var referenceGrid = new WrapPanel();
        foreach (var reference in asset.References)
        {
            var assetId = reference.Text("asset_id");
            var url = view.OriginFor($"assets/{assetId}/content");
            var thumb = new Border
            {
                Width = 96, Height = 96, Margin = new Thickness(0, 0, 10, 10),
                BorderBrush = reference.Flag("is_canonical") ? (Brush)Application.Current.FindResource("Success") : (Brush)Application.Current.FindResource("LineDark"),
                BorderThickness = new Thickness(1), Cursor = Cursors.Hand,
            };
            var image = new ImageBox { SourceUrl = view.OriginFor($"assets/{assetId}/thumbnail/640") };
            thumb.Child = image;
            thumb.MouseLeftButtonDown += (_, _) => view.ShowImage(url, $"{asset.Name} 参考图");
            var unbind = Kit.Act("解绑", async (_, _) =>
            {
                try
                {
                    await view.ApiSendOptional($"projects/{view.ProjectIdValue}/scene-assets/{asset.Id}/references/{assetId}", HttpMethod.Delete);
                    await LoadScenesAsync();
                }
                catch (Exception error) { view.Notify(error.Message); }
            }, "CompactDanger");
            var cell = new StackPanel();
            cell.Children.Add(thumb);
            cell.Children.Add(unbind);
            referenceGrid.Children.Add(cell);
        }
        detail.Children.Add(referenceGrid);
        detail.Children.Add(new TextBlock { Text = $"环境变体与专属参考（{asset.Variants.Count}）", Style = (Style)Application.Current.FindResource("SectionIndex"), Margin = new Thickness(0, 14, 0, 8) });
        if (asset.Variants.Count == 0) detail.Children.Add(Kit.Caption("还没有环境变体。变体只覆盖时间、天气、季节、光照和色调。"));
        foreach (var variant in asset.Variants)
        {
            var row = new StackPanel { Orientation = Orientation.Horizontal, Margin = new Thickness(0, 4, 0, 4) };
            row.Children.Add(new TextBlock
            {
                Text = variant.Text("name") + (variant.Flag("is_canonical") ? "（默认）" : ""),
                FontWeight = FontWeights.Bold, VerticalAlignment = VerticalAlignment.Center,
            });
            var remove = Kit.Act("删除", async (_, _) =>
            {
                if (MessageBox.Show(view.WindowHost(), $"删除环境变体“{variant.Text("name")}”？其专属参考图将解除绑定，绑定该变体的剧本场景回退到资产默认参考。",
                        "删除变体", MessageBoxButton.YesNo, MessageBoxImage.Warning) != MessageBoxResult.Yes) return;
                try
                {
                    await view.ApiSendOptional($"projects/{view.ProjectIdValue}/scene-assets/{asset.Id}/variants/{variant.Text("id")}", HttpMethod.Delete);
                    await LoadScenesAsync();
                }
                catch (Exception error) { view.Notify(error.Message); }
            }, "CompactDanger");
            remove.Margin = new Thickness(12, 0, 0, 0);
            row.Children.Add(remove);
            detail.Children.Add(row);
        }
    }

    private async Task SetStatus(string status)
    {
        if (selected == null) return;
        try
        {
            await view.ApiSend($"projects/{view.ProjectIdValue}/scene-assets/{selected.Id}", HttpMethod.Patch,
                new { version = selected.Version, status });
            await LoadScenesAsync();
            view.Notify("场景状态已更新。");
        }
        catch (Exception error) { view.Notify(error.Message); }
    }

    private async Task UploadReference()
    {
        if (selected == null) return;
        var picker = new OpenFileDialog { Filter = "图片|*.png;*.jpg;*.jpeg;*.webp", Title = "上传场景参考图" };
        if (picker.ShowDialog(view.WindowHost()) != true) return;
        try
        {
            var uploaded = await view.UploadAsset("SCENE_REFERENCE", picker.FileName);
            await view.ApiSend($"projects/{view.ProjectIdValue}/scene-assets/{selected.Id}/references", HttpMethod.Post,
                new { asset_id = uploaded.Text("id"), role = "main", is_canonical = selected.References.Count == 0 });
            await LoadScenesAsync();
            view.Notify("参考图已绑定。");
        }
        catch (Exception error) { view.Notify(error.Message); }
    }

    private async Task ArchiveOrRestore()
    {
        if (selected == null) return;
        if (selected.Deleted)
        {
            try
            {
                await view.ApiSend($"projects/{view.ProjectIdValue}/scene-assets/{selected.Id}/restore", HttpMethod.Post);
                await LoadScenesAsync();
            }
            catch (Exception error) { view.Notify(error.Message); }
            return;
        }
        if (MessageBox.Show(view.WindowHost(), $"归档场景“{selected.Name}”？归档后绑定它的剧本场景会失去场景参考消费，地点文本仍会保留。",
                "归档场景", MessageBoxButton.YesNo, MessageBoxImage.Warning) != MessageBoxResult.Yes) return;
        try
        {
            await view.ApiSendOptional($"projects/{view.ProjectIdValue}/scene-assets/{selected.Id}", HttpMethod.Delete);
            await LoadScenesAsync();
        }
        catch (Exception error) { view.Notify(error.Message); }
    }
}

/// <summary>References pane: upload stage + grouped asset grid with reclassify.</summary>
internal sealed class ReferencesPane : StackPanel
{
    private readonly AssetsView view;
    private readonly StackPanel groups = new();

    public ReferencesPane(AssetsView view)
    {
        this.view = view;
        Children.Add(PaneHeader("REFERENCE INTAKE", "原始素材 · 上传、分类与追溯原始参考图", $"{view.assets.Count} 个文件"));
        var upload = new Button
        {
            Content = "拖拽图片到这里，或点击上传参考图", MinHeight = 84,
            BorderBrush = (Brush)Application.Current.FindResource("LineDark"), BorderThickness = new Thickness(1),
            Background = new SolidColorBrush(Color.FromArgb(0x84, 0xFC, 0xFB, 0xF7)),
            Margin = new Thickness(0, 0, 0, 16), AllowDrop = true,
        };
        upload.Click += async (_, _) => await Upload();
        upload.Drop += async (_, e) =>
        {
            if (e.Data.GetData(DataFormats.FileDrop) is string[] { Length: > 0 } files)
                await Upload(files[0]);
        };
        Children.Add(upload);
        Children.Add(groups);
        RenderGroups();
    }

    private static Border PaneHeader(string kicker, string title, string count)
    {
        var border = new Border { Style = (Style)Application.Current.FindResource("CanvasHeader") };
        var grid = new Grid();
        grid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        grid.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        var heading = new StackPanel();
        heading.Children.Add(new TextBlock { Text = kicker, Style = (Style)Application.Current.FindResource("SectionIndex") });
        heading.Children.Add(new TextBlock
        {
            Text = title, FontFamily = (FontFamily)Application.Current.FindResource("Serif"),
            FontSize = 20, FontWeight = FontWeights.Bold, Margin = new Thickness(0, 5, 0, 0),
        });
        grid.Children.Add(heading);
        var counter = new TextBlock { Text = count, Style = (Style)Application.Current.FindResource("Caption"), VerticalAlignment = VerticalAlignment.Bottom };
        Grid.SetColumn(counter, 1);
        grid.Children.Add(counter);
        border.Child = grid;
        return border;
    }

    private async Task Upload(string? path = null)
    {
        var kind = view.SelectedCharacter != null ? "CHARACTER_REFERENCE" : "SCENE_REFERENCE";
        if (path == null)
        {
            var picker = new OpenFileDialog { Filter = "图片|*.png;*.jpg;*.jpeg;*.webp", Title = "上传参考图" };
            if (picker.ShowDialog(view.WindowHost()) != true) return;
            path = picker.FileName;
        }
        try
        {
            var asset = await view.UploadAsset(kind, path);
            if (kind == "CHARACTER_REFERENCE" && view.SelectedCharacter is { } character)
            {
                await view.ApiSend($"characters/{character.Id}/references", HttpMethod.Post,
                    new { asset_id = asset.Text("id"), angle = "unspecified", is_canonical = true });
            }
            await view.ReloadAssets();
            view.Notify("上传成功。");
        }
        catch (Exception error) { view.Notify(error.Message); }
    }

    private void RenderGroups()
    {
        groups.Children.Clear();
        foreach (var kind in new[] { "CHARACTER_REFERENCE", "OUTFIT_REFERENCE", "SCENE_REFERENCE", "STYLE_REFERENCE" })
        {
            var members = view.assets.Where(a => a.Kind == kind).ToList();
            var panel = new StackPanel { Margin = new Thickness(0, 0, 0, 16) };
            panel.Children.Add(new TextBlock
            {
                Text = $"{Labels.Map(Labels.AssetKinds, kind)} · {members.Count} FILES",
                Style = (Style)Application.Current.FindResource("SectionIndex"), Margin = new Thickness(0, 0, 0, 6),
            });
            if (members.Count == 0)
            {
                panel.Children.Add(Kit.Caption($"尚无{Labels.Map(Labels.AssetKinds, kind)}"));
            }
            else
            {
                var grid = new WrapPanel();
                foreach (var asset in members) grid.Children.Add(new AssetCard(view, asset));
                panel.Children.Add(grid);
            }
            groups.Children.Add(panel);
        }
    }
}

internal sealed class AssetCard : Border
{
    private readonly AssetsView view;
    private AssetItem asset;

    public AssetCard(AssetsView view, AssetItem asset)
    {
        this.view = view;
        this.asset = asset;
        BorderBrush = (Brush)Application.Current.FindResource("Line");
        BorderThickness = new Thickness(1);
        Background = (Brush)Application.Current.FindResource("Surface");
        Padding = new Thickness(10);
        Margin = new Thickness(0, 0, 10, 10);
        Width = 168;
        Render();
    }

    private void Render()
    {
        var panel = new StackPanel();
        var thumb = new Border
        {
            Height = 110, Background = (Brush)Application.Current.FindResource("PaperDeep"),
            Cursor = Cursors.Hand, Margin = new Thickness(0, 0, 0, 8),
        };
        if (asset.ContentUrl.Length > 0)
        {
            var image = new ImageBox { SourceUrl = view.OriginFor(asset.ContentUrl.Contains("/content") ? asset.ContentUrl.Replace("/content", "/thumbnail/640") : asset.ContentUrl) };
            thumb.Child = image;
            thumb.MouseLeftButtonDown += (_, _) => view.ShowImage(view.OriginFor(asset.ContentUrl), asset.Name);
        }
        panel.Children.Add(thumb);
        panel.Children.Add(new TextBlock { Text = asset.Name, FontWeight = FontWeights.Bold, FontSize = 12.5, TextTrimming = TextTrimming.CharacterEllipsis, TextWrapping = TextWrapping.NoWrap });
        panel.Children.Add(new TextBlock
        {
            Text = $"{Labels.AssetKindShort(asset.Kind)} · {asset.SizeLabel} · {asset.StatusLabel}",
            Style = (Style)Application.Current.FindResource("Micro"), Margin = new Thickness(0, 3, 0, 0),
        });
        var reclassify = new ComboBox { FontSize = 11, MinHeight = 28, Margin = new Thickness(0, 6, 0, 0) };
        foreach (var kind in new[] { "CHARACTER_REFERENCE", "OUTFIT_REFERENCE", "SCENE_REFERENCE", "STYLE_REFERENCE" })
            reclassify.Items.Add(new ComboBoxItem { Tag = kind, Content = Labels.Map(Labels.AssetKinds, kind) });
        SelectItem(reclassify, asset.Kind);
        reclassify.SelectionChanged += async (_, _) =>
        {
            var next = (reclassify.SelectedItem as ComboBoxItem)?.Tag as string;
            if (next == null || next == asset.Kind) return;
            if (MessageBox.Show(view.WindowHost(), $"将把「{asset.Name}」的用途从「{Labels.Map(Labels.AssetKinds, asset.Kind)}」改为「{Labels.Map(Labels.AssetKinds, next)}」，可能解除已有绑定。确定继续吗？",
                    "重分类", MessageBoxButton.YesNo, MessageBoxImage.Question) != MessageBoxResult.Yes)
            { SelectItem(reclassify, asset.Kind); return; }
            try
            {
                asset = AssetItem.From(await view.ApiSend($"assets/{asset.Id}", HttpMethod.Patch, new { kind = next }));
                await view.ReloadAssets();
            }
            catch (Exception error) { view.Notify("重分类失败：" + error.Message); }
        };
        panel.Children.Add(reclassify);
        var remove = Kit.Act("删除", async (_, _) =>
        {
            if (MessageBox.Show(view.WindowHost(), "删除该素材及其候选记录，并解除已有绑定？", "删除素材",
                    MessageBoxButton.YesNo, MessageBoxImage.Warning) != MessageBoxResult.Yes) return;
            try
            {
                await view.ApiSendOptional($"assets/{asset.Id}", HttpMethod.Delete);
                await view.ReloadAssets();
            }
            catch (Exception error) { view.Notify("删除失败：" + error.Message); }
        }, "CompactDanger");
        remove.Margin = new Thickness(0, 6, 0, 0);
        panel.Children.Add(remove);
        Child = panel;
    }

    private static void SelectItem(ComboBox box, string tag)
    {
        foreach (var item in box.Items.OfType<ComboBoxItem>())
            if ((string?)item.Tag == tag) { box.SelectedItem = item; return; }
    }
}
