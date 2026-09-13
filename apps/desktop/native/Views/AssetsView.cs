using System.IO;
using System.Net.Http;
using System.Text.Json;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Controls.Primitives;
using System.Windows.Input;
using System.Windows.Media;
using System.Windows.Threading;
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
    internal string ReferenceKind = "CHARACTER_REFERENCE";
    internal readonly HashSet<string> PendingOutfitReferences = [], PendingStyleReferences = [];
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
        if (changed) { SelectedCharacter = null; SelectedOutfit = null; OutfitPreviewId = ""; SelectedStyle = null; notice.Text = ""; ReferenceKind = "CHARACTER_REFERENCE"; PendingOutfitReferences.Clear(); PendingStyleReferences.Clear(); }
        // Deactivation cancels in-flight reads; async void has no caller to observe the
        // cancellation, so swallow it here instead of crashing the dispatcher.
        try { await LoadAsync(); }
        catch (OperationCanceledException) { }
    }

    public void Switch(string view) => _ = SwitchAsync(view);

    /// <summary>Guards an unsaved outfit draft before the internal tab changes.</summary>
    public async Task SwitchAsync(string view)
    {
        if (!tabs.ContainsKey(view)) return;
        if (current == view) return;
        if (await GuardOutfitDraftAsync() != OutfitDraftDecision.Proceed) return;
        current = view;
        foreach (var (key, tab) in tabs) tab.IsChecked = key == view;
        Render();
        if (view == Style) _ = LoadAsync();   // styles load lazily on first visit
    }

    internal enum OutfitDraftDecision { Proceed, Cancelled }

    // Test seam: when set, replaces the interactive save/discard/cancel dialog.
    internal Func<string, Task<string>>? OutfitDraftPrompt;

    /// <summary>
    /// If the wardrobe tab owns an unsaved draft, ask the user to save, discard or
    /// keep editing. Saving runs the real save flow and stays on the tab when the
    /// server rejects it (e.g. a version conflict). Never auto-submits.
    /// </summary>
    internal async Task<OutfitDraftDecision> GuardOutfitDraftAsync()
    {
        if (current != Outfits || host.Children.OfType<OutfitWorkspace>().FirstOrDefault() is not { } pane || !pane.DraftDirty)
            return OutfitDraftDecision.Proceed;
        var answer = OutfitDraftPrompt != null
            ? await OutfitDraftPrompt(pane.DraftLabel)
            : await ShowOutfitDraftDialog(pane.DraftLabel);
        if (answer == "save")
        {
            await pane.SaveAsync();
            // A rejected save (e.g. version conflict) keeps the draft on screen and
            // cancels the navigation so nothing is lost.
            if (!pane.DraftDirty) return OutfitDraftDecision.Proceed;
        }
        else if (answer == "discard")
        {
            pane.Reset();
            return OutfitDraftDecision.Proceed;
        }
        foreach (var (key, tab) in tabs) tab.IsChecked = key == current;
        return OutfitDraftDecision.Cancelled;
    }

    private async Task<string> ShowOutfitDraftDialog(string label)
    {
        var completion = new TaskCompletionSource<string>(TaskCreationOptions.RunContinuationsAsynchronously);
        var dialog = new Window
        {
            Owner = Host, Title = "未保存的服装草稿", Width = 470, SizeToContent = SizeToContent.Height,
            ResizeMode = ResizeMode.NoResize, WindowStartupLocation = WindowStartupLocation.CenterOwner, ShowInTaskbar = false,
            Background = (Brush)Application.Current.FindResource("Paper"),
        };
        var panel = new StackPanel { Margin = new Thickness(26) };
        panel.Children.Add(new TextBlock { Text = "未保存的服装草稿", FontFamily = (FontFamily)Application.Current.FindResource("Serif"), FontSize = 19, FontWeight = FontWeights.SemiBold });
        panel.Children.Add(new TextBlock { Text = $"「{label}」还有未保存的名称、参考图或锁定项。切换前要如何处理这份草稿？", TextWrapping = TextWrapping.Wrap, Margin = new Thickness(0, 10, 0, 0), LineHeight = 23 });
        var actions = new StackPanel { Orientation = Orientation.Horizontal, HorizontalAlignment = HorizontalAlignment.Right, Margin = new Thickness(0, 22, 0, 0) };
        void Add(string text, string result, string style, double marginLeft = 0)
        {
            var button = new Button { Content = text, MinWidth = 104, Margin = new Thickness(marginLeft, 0, 0, 0) };
            button.Style = (Style)Application.Current.FindResource(style);
            button.Click += (_, _) => { completion.TrySetResult(result); dialog.Close(); };
            actions.Children.Add(button);
        }
        Add("继续编辑", "cancel", "Outline");
        Add("放弃修改", "discard", "Ghost", 10);
        Add("保存并继续", "save", "InkButton", 10);
        panel.Children.Add(actions);
        dialog.Content = panel;
        dialog.PreviewKeyDown += (_, e) => { if (e.Key == Key.Escape) { e.Handled = true; completion.TrySetResult("cancel"); dialog.Close(); } };
        dialog.Closed += (_, _) => completion.TrySetResult("cancel");
        dialog.Loaded += (_, _) => actions.Children.OfType<Button>().First().Focus();
        dialog.ShowDialog();
        return await completion.Task;
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
            // Consume a pending deep link BEFORE the panes refresh so open panes adopt
            // the preselection (fresh Render picks SelectedCharacter/SelectedOutfit up
            // from the pane constructors themselves).
            var deepLink = ConsumePendingDeepLink();
            if (deepLink.Character is { } linked) SelectedCharacter = linked;
            if (deepLink.Outfit is { } linkedOutfit) SelectedOutfit = linkedOutfit;
            var keepPane = false;
            if (current == References && host.Children.OfType<ReferencesPane>().FirstOrDefault() is { } referencePane && referencePane.SessionEpoch == captured)
            {
                referencePane.AdoptReloaded();
                return;
            }
            if (current == Style && host.Children.OfType<StyleWorkspace>().FirstOrDefault() is { } stylePane && stylePane.SessionEpoch == captured)
            {
                await stylePane.ReloadAsync();
                if (deepLink.StyleId is { } styleId) stylePane.FocusStyle(styleId);
                keepPane = true;
            }
            else if (current == Outfits && host.Children.OfType<OutfitWorkspace>().FirstOrDefault() is { } outfitPane && outfitPane.SessionEpoch == captured)
            {
                // A late reload (e.g. a reference upload completing after a tab switch)
                // must refresh shared data without rebuilding the wardrobe editor —
                // the pane adopts the fresh rows and keeps unsaved input.
                outfitPane.AdoptReloaded();
                // ?outfit= deep link: web's beginOutfitEdit seeds the edit form directly.
                if (deepLink.Outfit is { } outfit) outfitPane.BeginEdit(outfit);
                keepPane = true;
            }
            else if (current == Characters && host.Children.OfType<CharactersPane>().FirstOrDefault() is { } charactersPane && charactersPane.SessionEpoch == captured)
            {
                // Same contract for the characters pane: strip/reference surfaces refresh,
                // the profile editor and the concept panel keep their in-progress input.
                charactersPane.AdoptReloaded();
                // ?character= deep link additionally seeds the edit form (web seeds the
                // form once when the bound character arrives).
                if (deepLink.Character is { } character) charactersPane.ApplyCharacterDeepLink(character);
                keepPane = true;
            }
            if (keepPane) return;
            foreach (var (key, tab) in tabs) tab.IsChecked = key == current;
            Render();
            // Fresh panes: the outfit pane constructor already adopts SelectedOutfit
            // (BeginEdit) and the character strip reflects SelectedCharacter; the
            // character editor seed and the style focus need an explicit pass.
            if (deepLink.Character is { } fresh && current == Characters &&
                host.Children.OfType<CharactersPane>().FirstOrDefault() is { } freshPane)
                freshPane.ApplyCharacterDeepLink(fresh);
            if (deepLink.StyleId is { } freshStyle && current == Style &&
                host.Children.OfType<StyleWorkspace>().FirstOrDefault() is { } freshStylePane)
                freshStylePane.FocusStyle(freshStyle);
        }
         catch (OperationCanceledException) { }
        catch (Exception error) when (error is not OperationCanceledException)
        {
            if (!IsCurrent(captured) || request != loadRequest) return;
            if (current == References && host.Children.OfType<ReferencesPane>().FirstOrDefault() is { } referencePane && referencePane.SessionEpoch == captured)
            {
                referencePane.ReportReadFailure(error.Message);
                return;
            }
            host.Children.Clear();
            host.Children.Add(Kit.Caption($"资产读取失败：{error.Message}"));
        }
    }

    // ============ Web deep links (?character= / ?outfit= / ?style=) ============

    private string? pendingCharacterId, pendingOutfitId, pendingStyleId;

    /// <summary>
    /// Web project-workspace.tsx reads ?character=/?outfit=/?style= and hands them to
    /// use-assets-workspace (bindCharacterId preselect + edit-form seeding, outfit edit
    /// state, style record focus). Desktop equivalent: MainWindow's NavigateSection
    /// forwards the ids here; they are consumed by the next data load, when the rows
    /// they point at actually exist. Unknown ids simply fall back to the plain view.
    /// </summary>
    public void ApplyDeepLink(string? characterId, string? outfitId, string? styleId)
    {
        pendingCharacterId = NonEmpty(characterId);
        pendingOutfitId = NonEmpty(outfitId);
        pendingStyleId = NonEmpty(styleId);
        if (pendingCharacterId == null && pendingOutfitId == null && pendingStyleId == null) return;
        // Switch to the target sub-view first (same unsaved-draft guard as a user tab
        // click); the pending ids are applied when the load below lands.
        var target = pendingOutfitId != null ? Outfits : pendingStyleId != null ? Style : Characters;
        if (current != target) _ = SwitchAsync(target);
        _ = LoadAsync();
    }

    private static string? NonEmpty(string? value) => value is { Length: > 0 } ? value : null;

    private (CharacterItem? Character, OutfitItem? Outfit, string? StyleId) ConsumePendingDeepLink()
    {
        CharacterItem? character = null;
        OutfitItem? outfit = null;
        // Styles load lazily inside StyleWorkspace (not part of this view's shared
        // reads), so the style id is handed through and resolved by the pane itself.
        string? styleId = null;
        if (pendingCharacterId is { } characterId)
        {
            character = characters.FirstOrDefault(c => c.Id == characterId);
            pendingCharacterId = null;
        }
        if (pendingOutfitId is { } outfitId)
        {
            outfit = outfits.FirstOrDefault(o => o.Id == outfitId);
            pendingOutfitId = null;
        }
        if (pendingStyleId is { } pending)
        {
            styleId = pending;
            pendingStyleId = null;
        }
        return (character, outfit, styleId);
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
            case Scenes: host.Children.Add(new SceneWorkspace(this)); break;
            case Style: host.Children.Add(new StyleWorkspace(this)); break;
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

    internal async Task ReloadAssets() => await LoadAsync();

    public override Task RefreshAsync() => LoadAsync();

    // Leaving the whole assets view (page/project navigation, window close) gets the
    // same unsaved-draft protection as internal tab switches.
    public override async Task<bool> ConfirmLeaveAsync() =>
        await GuardOutfitDraftAsync() == OutfitDraftDecision.Proceed;

    internal bool OwnsOutfits(OutfitWorkspace pane) => host.Children.Contains(pane);
    internal bool OwnsScenes(SceneWorkspace pane) => host.Children.Contains(pane);
    internal bool OwnsStyle(StyleWorkspace pane) => host.Children.Contains(pane);
    internal bool OwnsReferences(ReferencesPane pane) => host.Children.Contains(pane);

    public override void PollTick()
    {
        if (current == Outfits && host.Children.OfType<OutfitWorkspace>().FirstOrDefault() is { } outfitPane) outfitPane.PollTick();
        if (current == Characters && host.Children.OfType<CharactersPane>().FirstOrDefault() is { } charactersPane) charactersPane.PollTick();
        if (current == Style && host.Children.OfType<StyleWorkspace>().FirstOrDefault() is { } stylePane) stylePane.PollTick();
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
    private readonly int epoch;
    private readonly StackPanel strip = new() { Orientation = Orientation.Horizontal };
    private readonly StackPanel concept = new();
    private ConceptPanel? conceptPanel;
    private readonly Button addButton = new() { Content = "＋ 添加角色", MinHeight = 44, Style = (Style)Application.Current.FindResource("InkButton") };
    private bool saving;
    private readonly StackPanel editor = new();
    private readonly TextBox nameInput = new() { Height = 42, FontSize = 13 };
    private readonly TextBox aliasInput = new() { Height = 42, FontSize = 13 };
    internal int SessionEpoch => epoch;
    private bool Attached => Parent != null && view.IsCurrent(epoch);

    public CharactersPane(AssetsView view)
    {
        this.view = view;
        epoch = view.Epoch;
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
        conceptPanel = new ConceptPanel(view, character);
        concept.Children.Add(conceptPanel);
    }

    internal void PollTick() => conceptPanel?.PollTick();

    /// <summary>
    /// ?character= deep link (web seeds the edit form once when the deep-linked
    /// character becomes bound): select the strip chip and open the seeded editor.
    /// </summary>
    internal void ApplyCharacterDeepLink(CharacterItem character)
    {
        if (!Attached) return;
        view.SelectedCharacter = character;
        RenderStrip();
        RenderEditor();
        foreach (var references in Children.OfType<CharacterReferencesPane>()) references.Render();
    }

    // Refresh shared data surfaces (strip counts, reference bindings) after a
    // background reload WITHOUT rebuilding the editor or the concept panel —
    // in-progress input must survive late responses from other panes.
    internal void AdoptReloaded()
    {
        if (!Attached) return;
        RenderStrip();
        foreach (var references in Children.OfType<CharacterReferencesPane>()) references.Render();
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
            // The data refresh always runs; the success toast only shows when the
            // saved character is still on screen — otherwise it reads as the wrong
            // object's confirmation after a quick re-selection.
            if (view.IsCurrent(captured) && view.SelectedCharacter?.Id == character.Id) view.Notify("角色规范已保存。");
        }
        catch (Exception error) { if (view.IsCurrent(captured)) view.Notify("保存角色失败：" + error.Message); }
        finally { saving = false; editor.IsEnabled = true; }
    }
}

/// <summary>AI concept panel: one-sheet generation for character + outfit norms.</summary>
internal sealed class ConceptPanel : Border
{
    // Non-terminal candidate statuses (mirrors the asset candidate job pipeline).
    private static readonly HashSet<string> PendingStatuses =
        ["WAITING", "QUEUED", "PREPARING", "UPLOADING_REFERENCES", "GENERATING", "OCR_CHECKING", "CONSISTENCY_CHECKING", "REPAIRING", "RUNNING"];
    private const int VisibleCandidates = 2;   // web shows the latest two concept drafts

    private readonly AssetsView view;
    private readonly CharacterItem character;
    private readonly int epoch;
    private readonly TextBox appearance = new() { AcceptsReturn = true, MinHeight = 54 };
    private readonly TextBox outfitName = new() { Width = 260 };
    private readonly TextBox outfitDescription = new() { AcceptsReturn = true, MinHeight = 54 };
    private readonly TextBox lockedFields = new() { Width = 260 };
    private readonly StackPanel candidates = new();
    private readonly Button generate;
    private readonly DispatcherTimer draftSave;
    private readonly HashSet<string> approving = [];
    private bool busy, reading, pollDue;
    private bool Active => view.IsCurrent(epoch) && view.SelectedCharacter?.Id == character.Id;
    private string DraftKey => "concept-draft:" + view.ProjectIdValue + ":" + character.Id;

    public ConceptPanel(AssetsView view, CharacterItem character)
    {
        this.view = view;
        this.character = character;
        epoch = view.Epoch;
        Margin = new Thickness(0, 14, 0, 0);
        Style = (Style)Application.Current.FindResource("Card");
        Padding = new Thickness(18);
        LoadDraft();
        // Web parity: the concept form is a per-(project, character) draft that must
        // survive re-entry, approval reloads and background data refreshes.
        draftSave = new DispatcherTimer { Interval = TimeSpan.FromMilliseconds(800) };
        draftSave.Tick += (_, _) => { draftSave.Stop(); SaveDraft(); };
        foreach (var box in new[] { appearance, outfitName, outfitDescription, lockedFields })
            box.TextChanged += (_, _) => draftSave.Start();
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
        generate = Kit.Act("生成概念设定草稿", async (_, _) => await Generate(), "InkButton");
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

    private void LoadDraft()
    {
        try
        {
            using var document = JsonDocument.Parse(KeyValueStore.Get(DraftKey));
            var draft = document.RootElement;
            appearance.Text = draft.Text("appearance");
            outfitName.Text = draft.Text("outfitName");
            outfitDescription.Text = draft.Text("outfitDescription");
            lockedFields.Text = draft.Text("lockedFields");
        }
        catch (JsonException) { /* absent or corrupt draft starts blank */ }
    }

    private void SaveDraft()
    {
        KeyValueStore.Set(DraftKey, JsonSerializer.Serialize(new
        {
            appearance = appearance.Text, outfitName = outfitName.Text,
            outfitDescription = outfitDescription.Text, lockedFields = lockedFields.Text,
        }));
    }

    private async Task Generate()
    {
        if (busy) return;
        var alias = view.SelectedImageModel;
        if (!view.ImageEditModels.Any(m => m.Text("logical_alias") == alias))
        { view.Notify("请先选择一个支持参考图编辑的图片模型"); return; }
        if (outfitName.Text.Trim().Length == 0 || outfitDescription.Text.Trim().Length == 0)
        { view.Notify("请填写服装档案名称和服装描述。"); return; }
        draftSave.Stop(); SaveDraft();
        busy = true; generate.IsEnabled = false;
        try
        {
            await view.ApiSend($"characters/{character.Id}/complete-sheet", HttpMethod.Post, new
            {
                model_alias = alias, resolution = "1K", generation_mode = "CONCEPT",
                appearance_description = appearance.Text, outfit_name = outfitName.Text.Trim(),
                outfit_description = outfitDescription.Text,
            });
            if (!Active) return;
            view.Notify("概念设定任务已创建，候选生成后会显示在下方。");
            pollDue = true;
            await LoadCandidatesAsync();
        }
        catch (Exception error) { if (Active) view.Notify("生成失败：" + error.Message); }
        finally { busy = false; if (Active) generate.IsEnabled = true; }
    }

    internal void PollTick()
    {
        if (!Active || reading || !pollDue) return;
        _ = LoadCandidatesAsync();
    }

    // Reads the latest CHARACTER-target batch, then its concept-sheet candidates.
    // Late responses are dropped: the panel may have been detached or the character
    // re-selected while the requests were in flight.
    private async Task LoadCandidatesAsync()
    {
        if (reading) return;
        reading = true;
        try
        {
            var batches = await view.ApiSend(QueryBuilder.Build("asset-generation-batches",
                ("target_type", "CHARACTER"), ("target_id", character.Id), ("limit", 1)));
            if (!view.IsCurrent(epoch)) return;
            var rows = new List<JsonElement>();
            var batchId = batches.EnumerateArray().FirstOrDefault().Text("id");
            if (batchId.Length > 0)
            {
                var response = await view.ApiSend($"batches/{batchId}/candidates");
                if (!view.IsCurrent(epoch)) return;
                // Concept adoption only applies to complete-sheet candidates; other
                // variants (e.g. redrawn reference views) must not enter this list.
                rows = response.EnumerateArray().Where(r => r.Text("variant") == "SHEET").ToList();
            }
            if (!Active) return;
            RenderCandidates(rows);
        }
        catch (OperationCanceledException) { }
        catch (Exception error)
        {
            if (!Active) return;
            candidates.Children.Clear();
            candidates.Children.Add(Kit.Caption("候选读取失败：" + error.Message));
        }
        finally { reading = false; }
    }

    private void RenderCandidates(List<JsonElement> rows)
    {
        candidates.Children.Clear();
        pollDue = rows.Any(r => PendingStatuses.Contains(r.Text("status")));
        foreach (var row in rows.Take(VisibleCandidates))
        {
            var candidate = CandidateItem.From(row);
            var approved = row.Element("prompt_snapshot").Element("reference_approval").Flag("approved");
            var card = new StackPanel { Margin = new Thickness(0, 0, 12, 12) };
            var url = candidate.ContentUrl.Length > 0 ? candidate.ContentUrl : (candidate.AssetId.Length > 0 ? $"assets/{candidate.AssetId}/content" : "");
            Border artwork;
            if (url.Length > 0)
            {
                artwork = new Border { Width = 128, Height = 128, BorderBrush = (Brush)Application.Current.FindResource("LineDark"), BorderThickness = new Thickness(1) };
                artwork.Child = new ImageBox { SourceUrl = view.OriginFor(url) };
                artwork.Cursor = Cursors.Hand;
                artwork.MouseLeftButtonDown += (_, _) => view.ShowImage(url, $"{character.PrimaryName} 概念设定");
            }
            else
            {
                artwork = new Border
                {
                    Width = 128, Height = 128, Background = (Brush)Application.Current.FindResource("PaperDeep"),
                    Child = new TextBlock
                    {
                        Text = candidate.Status == "FAILED" ? "生成失败" : PendingStatuses.Contains(candidate.Status) ? "排队/生成中…" : "等待图片",
                        Style = (Style)Application.Current.FindResource("Micro"),
                        HorizontalAlignment = HorizontalAlignment.Center, VerticalAlignment = VerticalAlignment.Center,
                    },
                };
            }
            card.Children.Add(artwork);
            var status = approved ? "已确认为规范参考" : candidate.StatusLabel;
            card.Children.Add(new TextBlock { Text = status, Style = (Style)Application.Current.FindResource("Micro"), Margin = new Thickness(0, 4, 0, 0) });
            if (candidate.Status == "READY" && !approved)
            {
                var id = candidate.Id;
                var approve = Kit.Act("确认为规范参考", async (_, _) => await Approve(id), "CompactInk");
                approve.Margin = new Thickness(0, 6, 0, 0);
                if (approving.Contains(id)) approve.IsEnabled = false;
                card.Children.Add(approve);
            }
            candidates.Children.Add(card);
        }
        if (rows.Count > VisibleCandidates)
            candidates.Children.Add(Kit.Caption($"另有 {rows.Count - VisibleCandidates} 个历史候选，可在生成素材库中查看。"));
        if (candidates.Children.Count == 0)
            candidates.Children.Add(Kit.Caption("第一张草稿生成后会实时出现在这里；确认前不会进入正式页面提示词。"));
    }

    private async Task Approve(string candidateId)
    {
        if (busy || approving.Contains(candidateId)) return;
        draftSave.Stop(); SaveDraft();
        approving.Add(candidateId);
        foreach (var button in candidates.Children.OfType<StackPanel>().SelectMany(DescendantButtons))
            if (Equals(button.Content, "确认为规范参考")) button.IsEnabled = false;
        try
        {
            await view.ApiSend($"asset-candidates/{candidateId}/approve-reference", HttpMethod.Post, new
            {
                character_id = character.Id,
                bind_character_reference = true,
                set_canonical = true,
                outfit_name = outfitName.Text.Trim().Length > 0 ? outfitName.Text.Trim() : null,
                outfit_description = outfitDescription.Text.Trim().Length > 0 ? outfitDescription.Text : null,
                outfit_locked_fields = lockedFields.Text.Trim().Length > 0 ? lockedFields.Text.Trim().Split('；', ';') : Array.Empty<string>(),
            });
            if (!Active) return;
            view.Notify("已绑定人物与服装。");
            await view.ReloadAssets();
            if (Active) await LoadCandidatesAsync();
        }
        catch (Exception error) { if (Active) view.Notify("确认采用失败：" + error.Message); }
        finally
        {
            approving.Remove(candidateId);
            if (Active) foreach (var button in candidates.Children.OfType<StackPanel>().SelectMany(DescendantButtons))
                if (Equals(button.Content, "确认为规范参考")) button.IsEnabled = true;
        }
    }

    private static IEnumerable<Button> DescendantButtons(DependencyObject root)
    {
        var count = System.Windows.Media.VisualTreeHelper.GetChildrenCount(root);
        for (var i = 0; i < count; i++)
        {
            var child = System.Windows.Media.VisualTreeHelper.GetChild(root, i);
            if (child is Button button) yield return button;
            foreach (var nested in DescendantButtons(child)) yield return nested;
        }
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
