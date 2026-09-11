using System.Net.Http;
using System.Text.Json;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Controls.Primitives;
using System.Windows.Input;
using System.Windows.Media;
using System.Windows.Shapes;
using MangaFlow.Native.Controls;
using Microsoft.Win32;

namespace MangaFlow.Native.Views;

/// <summary>Reference intake: explicit purpose, real binding state and drafts awaiting save.</summary>
internal sealed class ReferencesPane : StackPanel
{
    internal static readonly string[] Kinds = ["CHARACTER_REFERENCE", "OUTFIT_REFERENCE", "STYLE_REFERENCE", "SCENE_REFERENCE"];
    internal readonly AssetsView View;
    internal int SessionEpoch { get; }
    internal bool Active => View.IsCurrent(SessionEpoch) && View.OwnsReferences(this);
    private readonly ContentControl header = new();
    private readonly StackPanel groups = new();
    private readonly WrapPanel chips = new();
    private readonly ComboBox character = new() { MinWidth = 190, MinHeight = 36 };
    private readonly TextBlock hint = Caption("");
    private readonly TextBlock uploadLabel = new() { FontSize = 17, FontWeight = FontWeights.SemiBold, TextAlignment = TextAlignment.Center, TextWrapping = TextWrapping.Wrap };
    private readonly TextBlock description = Caption("");
    private readonly TextBlock error = Caption("");
    private readonly Button retry = new() { Content = "重新读取素材与引用", Visibility = Visibility.Collapsed, HorizontalAlignment = HorizontalAlignment.Left, Margin = new Thickness(0, 8, 0, 0) };
    private readonly Button upload;
    private readonly Dictionary<string, ReferenceAssetCard> cards = new();
    private List<JsonElement> styles = [];
    internal bool StyleLinksReady { get; private set; }
    private bool busy, syncingCharacter;
    internal bool Busy => busy;
    internal Func<string, bool>? Confirm;
    internal string SelectedKind => View.ReferenceKind;
    internal ReferencesPane(AssetsView view)
    {
        View = view; SessionEpoch = view.Epoch;
        Background = AssetPageUi.Brush("Paper"); UseLayoutRounding = true; SnapsToDevicePixels = true;
        TextOptions.SetTextFormattingMode(this, TextFormattingMode.Display); RenderOptions.SetClearTypeHint(this, ClearTypeHint.Enabled);
        Children.Add(header);
        foreach (var kind in Kinds)
        {
            var chip = new ToggleButton { Tag = kind, Content = Label(kind), IsChecked = kind == SelectedKind, Style = (Style)FindResource("Pill"), MinHeight = 34, Margin = new Thickness(0, 0, 3, 0) };
            chip.Click += (_, _) => { View.ReferenceKind = kind; UpdateState(); };
            chips.Children.Add(chip);
        }
        System.Windows.Automation.AutomationProperties.SetName(character, "参考图绑定角色");
        character.SelectionChanged += (_, _) =>
        {
            if (syncingCharacter) return;
            var id = (character.SelectedItem as ComboBoxItem)?.Tag as string;
            var previousOwner = View.SelectedCharacter?.Id;
            View.SelectedCharacter = View.characters.FirstOrDefault(c => c.Id == id);
            if (previousOwner != null && previousOwner != id) View.PendingOutfitReferences.Clear();
            UpdateState();
            foreach (var card in cards.Values) card.UpdateBinding();
        };
        var toolbar = new PageHeading(chips, character) { Margin = new Thickness(0, 12, 0, 10) };
        Children.Add(toolbar); hint.Margin = new Thickness(0, 0, 0, 12); Children.Add(hint);
        var stage = new StackPanel { VerticalAlignment = VerticalAlignment.Center, Margin = new Thickness(18) };
        stage.Children.Add(new Border { Child = SourceIcon.Create("upload", 28), Width = 44, Height = 44, BorderBrush = AssetPageUi.Brush("Ink"), BorderThickness = new Thickness(1), Margin = new Thickness(0, 0, 0, 14) });
        uploadLabel.FontFamily = (FontFamily)FindResource("Serif"); stage.Children.Add(uploadLabel);
        description.TextAlignment = TextAlignment.Center; description.Margin = new Thickness(0, 8, 0, 0); stage.Children.Add(description);
        upload = new Button { Style = (Style)FindResource("Outline"), Background = AssetPageUi.Brush("Surface"), BorderThickness = new Thickness(0), MinHeight = 175, HorizontalContentAlignment = HorizontalAlignment.Stretch, Content = stage, AllowDrop = true };
        var area = new Grid(); area.Children.Add(upload);
        area.Children.Add(new Rectangle { Stroke = AssetPageUi.Brush("LineDark"), StrokeThickness = 1, StrokeDashArray = new DoubleCollection { 3, 2 }, IsHitTestVisible = false });
        upload.Click += async (_, _) => await Upload();
        upload.DragOver += (_, e) => { e.Effects = !busy && e.Data.GetDataPresent(DataFormats.FileDrop) ? DragDropEffects.Copy : DragDropEffects.None; e.Handled = true; if (e.Effects == DragDropEffects.Copy) uploadLabel.Text = "松开即可上传参考图片"; };
        upload.DragLeave += (_, _) => UpdateState();
        upload.Drop += async (_, e) => { e.Handled = true; if (e.Data.GetData(DataFormats.FileDrop) is string[] { Length: > 0 } paths) await Upload(paths[0]); UpdateState(); };
        Children.Add(area); error.Foreground = AssetPageUi.Brush("Danger"); error.Margin = new Thickness(0, 10, 0, 0); Children.Add(error);
        retry.Style = (Style)FindResource("Compact"); retry.Click += async (_, _) => { if (!busy && Active) { await View.ReloadAssets(); await ReloadStyleLinks(); } }; Children.Add(retry); Children.Add(groups);
        AdoptReloaded();
        Dispatcher.BeginInvoke(new Action(async () => await ReloadStyleLinks()));
    }
    internal static string Label(string kind) => Labels.Map(Labels.AssetKinds, kind);
    internal static TextBlock Caption(string text) => new() { Text = text, FontSize = 12, TextWrapping = TextWrapping.Wrap, Foreground = AssetPageUi.Brush("Muted") };
    private static string Purpose(string kind) => kind switch
    {
        "CHARACTER_REFERENCE" => "绑定主要姓名与绰号，用于保持脸、发型和体型一致。",
        "OUTFIT_REFERENCE" => "选择图片后，在服装档案中保存“角色 → 服装档案 → 参考图”的关系。",
        "STYLE_REFERENCE" => "选择后由默认视觉模型按所选的黑白或彩色模式总结可复用画面语言。",
        _ => "用于固定地点结构、空间透视和环境基调，请在场景资产中绑定到具体地点。",
    };
    internal void AdoptReloaded()
    {
        if (error.Text.StartsWith("素材刷新失败")) error.Text = "";
        retry.Visibility = Visibility.Collapsed;
        header.Content = AssetPageUi.Header("REFERENCE INTAKE / 原始素材", "上传、分类与追溯原始参考图", $"{View.assets.Count} 个文件");
        View.PendingOutfitReferences.RemoveWhere(id => !View.assets.Any(a => a.Id == id && a.Kind == "OUTFIT_REFERENCE"));
        View.PendingStyleReferences.RemoveWhere(id => !View.assets.Any(a => a.Id == id && a.Kind == "STYLE_REFERENCE"));
        syncingCharacter = true;
        character.Items.Clear(); character.Items.Add(new ComboBoxItem { Tag = "", Content = "选择角色后绑定参考图" });
        foreach (var c in View.characters) character.Items.Add(new ComboBoxItem { Tag = c.Id, Content = c.PrimaryName });
        character.SelectedItem = character.Items.OfType<ComboBoxItem>().FirstOrDefault(i => Equals(i.Tag, View.SelectedCharacter?.Id)) ?? character.Items[0];
        syncingCharacter = false;
        foreach (var id in cards.Keys.Where(id => !View.assets.Any(a => a.Id == id)).ToList()) cards.Remove(id);
        groups.Children.Clear();
        foreach (var kind in Kinds)
        {
            var members = View.assets.Where(a => a.Kind == kind).ToList();
            var title = new PageHeading(new TextBlock { Text = Label(kind), FontWeight = FontWeights.Bold, FontSize = 14 }, Caption($"{members.Count} FILES"));
            groups.Children.Add(new Border { Child = title, BorderBrush = AssetPageUi.Brush("Line"), BorderThickness = new Thickness(0, 0, 0, 1), Padding = new Thickness(0, 0, 0, 9), Margin = new Thickness(0, 26, 0, 0) });
            groups.Children.Add(Caption(Purpose(kind)));
            var grid = new TilePanel { MinimumTileWidth = 370, MaximumColumns = 2, Gap = 10, Margin = new Thickness(0, 14, 0, 0) };
            foreach (var asset in members)
            {
                if (!cards.TryGetValue(asset.Id, out var card) || card.Asset.Kind != asset.Kind) cards[asset.Id] = card = new ReferenceAssetCard(this, asset);
                else { if (card.Parent is Panel parent) parent.Children.Remove(card); card.Adopt(asset); }
                grid.Children.Add(card);
            }
            if (members.Count == 0) grid.Children.Add(new Border { Child = Caption($"尚无{Label(kind)}"), Padding = new Thickness(12), Background = AssetPageUi.Brush("Surface") });
            groups.Children.Add(grid);
        }
        UpdateState();
    }
    private void UpdateState()
    {
        foreach (var chip in chips.Children.OfType<ToggleButton>()) { chip.IsChecked = Equals(chip.Tag, SelectedKind); chip.IsEnabled = !busy; }
        character.IsEnabled = !busy;
        upload.IsEnabled = !busy;
        retry.IsEnabled = !busy;
        uploadLabel.Text = busy ? "正在处理，请稍候…" : $"拖拽图片到这里，或点击上传{Label(SelectedKind)}";
        description.Text = Purpose(SelectedKind);
        hint.Text = SelectedKind switch
        {
            "CHARACTER_REFERENCE" => View.SelectedCharacter is { } c ? $"上传后绑定到：{c.PrimaryName}" : "未选择角色，上传后可在素材卡中绑定。",
            "OUTFIT_REFERENCE" => $"已选 {View.PendingOutfitReferences.Count} 张服装参考；到服装档案命名并保存后生效。",
            "STYLE_REFERENCE" => $"已选 {View.PendingStyleReferences.Count} 张风格参考；到漫画风格创建并分析后生效。",
            _ => "上传后请到场景资产工作区绑定地点。",
        };
        foreach (var card in cards.Values) card.SetBusy(busy);
    }
    internal async Task<bool> Mutate(Func<Task> action)
    {
        if (!Active || busy) return false;
        busy = true; error.Text = ""; UpdateState();
        try { await action(); return Active; }
        catch (OperationCanceledException) { return false; }
        catch (Exception e) { if (Active) error.Text = e.Message; return false; }
        finally { busy = false; if (Active) UpdateState(); }
    }
    internal bool Ask(string text) => Confirm?.Invoke(text) ?? MessageBox.Show(View.WindowHost(), text, "参考素材", MessageBoxButton.YesNo, MessageBoxImage.Warning) == MessageBoxResult.Yes;
    private async Task Upload(string? path = null)
    {
        if (!Active || busy) return;
        if (path == null)
        {
            var picker = new OpenFileDialog { Filter = "图片|*.png;*.jpg;*.jpeg;*.webp", Title = "上传" + Label(SelectedKind) };
            if (picker.ShowDialog(View.WindowHost()) != true) return; path = picker.FileName;
        }
        var kind = SelectedKind; var owner = View.SelectedCharacter?.Id;
        await Mutate(async () =>
        {
            var asset = await View.UploadAsset(kind, path);
            if (!Active) return;
            if (asset.Text("kind") != kind) throw new InvalidOperationException("该图片已按其他参考用途上传，请改用原用途或先调整原图分类。");
            // Upload and bind are separate commits: retain and display a successful upload
            // even when the optional binding fails, so retrying cannot hide that partial result.
            string? bindingFailure = null;
            if (kind == "CHARACTER_REFERENCE" && owner != null)
            {
                try { await View.ApiSend($"characters/{owner}/references", HttpMethod.Post, new { asset_id = asset.Text("id"), angle = "unspecified", is_canonical = true }); }
                catch (Exception e) when (e is not OperationCanceledException) { bindingFailure = e.Message; }
            }
            if (!Active) return;
            if (kind == "OUTFIT_REFERENCE") View.PendingOutfitReferences.Add(asset.Text("id"));
            if (kind == "STYLE_REFERENCE") View.PendingStyleReferences.Add(asset.Text("id"));
            await View.ReloadAssets();
            if (Active) { if (bindingFailure != null) error.Text = "图片已上传，但角色绑定失败，请在素材卡重试：" + bindingFailure; else View.Notify("参考图片已上传。"); }
        });
    }
    internal void ToggleDraft(AssetItem asset)
    {
        var pending = asset.Kind == "OUTFIT_REFERENCE" ? View.PendingOutfitReferences : View.PendingStyleReferences;
        if (!pending.Add(asset.Id)) pending.Remove(asset.Id);
        UpdateState(); foreach (var card in cards.Values) card.UpdateBinding();
    }
    internal string StyleLinks(string id) => string.Join("、", styles.Where(s => s.GetProperty("profile").Strings("reference_asset_ids").Contains(id)).Select(s => s.Text("name")));
    internal void ReportReadFailure(string message)
    {
        if (!Active) return;
        error.Text = "素材刷新失败，已保留当前内容与输入：" + message;
        retry.Visibility = Visibility.Visible;
    }
    internal async Task ReloadStyleLinks()
    {
        if (!Active) return;
        try { var data = await View.ApiSend($"projects/{View.ProjectIdValue}/styles"); if (!Active) return; styles = data.EnumerateArray().Where(s => s.TryGetProperty("profile", out var p) && p.ValueKind == JsonValueKind.Object).ToList(); StyleLinksReady = true; if (error.Text.StartsWith("风格引用状态读取失败")) { error.Text = ""; retry.Visibility = Visibility.Collapsed; } foreach (var card in cards.Values) card.UpdateBinding(); }
        catch (OperationCanceledException) { }
        catch (Exception e) { if (Active) { StyleLinksReady = false; error.Text = "风格引用状态读取失败：" + e.Message; retry.Visibility = Visibility.Visible; foreach (var card in cards.Values) card.UpdateBinding(); } }
    }
}

internal sealed class ReferenceAssetCard : Border
{
    private readonly ReferencesPane pane;
    internal AssetItem Asset { get; private set; }
    private readonly ContentControl nameHost = new();
    private readonly StackPanel binding = new();
    private readonly TextBox nameInput = new() { MinHeight = 36, MaxLength = 255 };
    private readonly TextBlock status = ReferencesPane.Caption("");
    private readonly TextBlock message = ReferencesPane.Caption("");
    private readonly ComboBox kind = new() { MinHeight = 40 };
    private readonly Button remove;
    private bool editing, syncing;
    internal ReferenceAssetCard(ReferencesPane pane, AssetItem asset)
    {
        this.pane = pane; Asset = asset; Padding = new Thickness(8); Background = AssetPageUi.Brush("Surface"); BorderThickness = new Thickness(1); BorderBrush = AssetPageUi.Brush("Line");
        var grid = new Grid { ColumnDefinitions = { new ColumnDefinition { Width = new GridLength(74) }, new ColumnDefinition() } };
        var thumb = new Button { Width = 74, Height = 74, MinHeight = 74, Padding = new Thickness(0), VerticalAlignment = VerticalAlignment.Top, Style = (Style)FindResource("Outline"), ToolTip = "查看原图" };
        if (asset.ContentUrl.Length > 0) thumb.Content = new ImageBox { SourceUrl = pane.View.OriginFor(asset.ContentUrl.Replace("/content", "/thumbnail/640")) }; else thumb.Content = SourceIcon.Create("file", 24);
        thumb.IsEnabled = asset.ContentUrl.Length > 0; thumb.Click += (_, _) => pane.View.ShowImage(Asset.ContentUrl, Asset.Name); grid.Children.Add(thumb);
        var body = new StackPanel { Margin = new Thickness(10, 0, 0, 0) }; Grid.SetColumn(body, 1); grid.Children.Add(body);
        body.Children.Add(nameHost); body.Children.Add(ReferencesPane.Caption($"{ReferencesPane.Label(asset.Kind)} · {asset.SizeLabel}")); status.Margin = new Thickness(0, 5, 0, 7); body.Children.Add(status); body.Children.Add(binding);
        var actions = new Grid { ColumnDefinitions = { new ColumnDefinition(), new ColumnDefinition { Width = GridLength.Auto } }, Margin = new Thickness(0, 7, 0, 0) };
        foreach (var k in ReferencesPane.Kinds) kind.Items.Add(new ComboBoxItem { Content = ReferencesPane.Label(k), Tag = k });
        SelectKind(); System.Windows.Automation.AutomationProperties.SetName(kind, "修改素材用途");
        kind.SelectionChanged += async (_, _) => { if (syncing) return; var next = (kind.SelectedItem as ComboBoxItem)?.Tag as string; SelectKind(); if (next != null && next != Asset.Kind) await Reclassify(next); };
        actions.Children.Add(kind); remove = SourceIcon.Action("trash", "删除素材", async (_, _) => await Delete()); remove.Margin = new Thickness(6, 0, 0, 0); Grid.SetColumn(remove, 1); actions.Children.Add(remove); body.Children.Add(actions);
        message.Foreground = AssetPageUi.Brush("Danger"); body.Children.Add(message); Child = grid;
        nameInput.KeyDown += async (_, e) => { if (e.Key == Key.Enter) { e.Handled = true; await Rename(); } else if (e.Key == Key.Escape && !pane.Busy) { e.Handled = true; editing = false; RenderName(); } };
        Adopt(asset);
    }
    internal void Adopt(AssetItem asset)
    {
        Asset = asset;
        bool ready = asset.Status is "UPLOADED" or "ANALYZED" or "GENERATED";
        status.Text = (ready ? "✓ " : "") + asset.StatusLabel;
        status.Foreground = AssetPageUi.Brush(ready ? "Success" : "Muted");
        if (!editing) RenderName(); UpdateBinding();
    }
    private void RenderName()
    {
        if (editing)
        {
            var actions = new WrapPanel(); actions.Children.Add(Kit.Act("保存", async (_, _) => await Rename(), "Compact")); actions.Children.Add(Kit.Act("取消", (_, _) => { editing = false; RenderName(); }, "Compact"));
            nameHost.Content = new StackPanel { Children = { nameInput, actions } }; return;
        }
        var title = new TextBlock { Text = Asset.Name, ToolTip = Asset.Name, FontSize = 14, FontWeight = FontWeights.Bold, TextTrimming = TextTrimming.CharacterEllipsis, VerticalAlignment = VerticalAlignment.Center };
        nameHost.Content = new PageHeading(title, SourceIcon.Action("edit", "修改素材名称", (_, _) => { editing = true; nameInput.Text = Asset.Name; RenderName(); nameInput.Focus(); nameInput.SelectAll(); }));
    }
    internal async Task Rename()
    {
        var text = nameInput.Text.Trim(); if (text.Length == 0) { message.Text = "素材名称不能为空。"; return; }
        message.Text = "";
        if (await pane.Mutate(async () => { await pane.View.ApiSend($"assets/{Asset.Id}", HttpMethod.Patch, new { display_name = text }); if (pane.Active) { editing = false; await pane.View.ReloadAssets(); } })) RenderName();
    }
    private void SelectKind() { syncing = true; kind.SelectedItem = kind.Items.OfType<ComboBoxItem>().First(i => Equals(i.Tag, Asset.Kind)); syncing = false; }
    internal async Task Reclassify(string next)
    {
        if (pane.Busy || !pane.Active || next == Asset.Kind || !ReferencesPane.Kinds.Contains(next)) return;
        if (!pane.Ask($"将把「{Asset.Name}」的用途从「{ReferencesPane.Label(Asset.Kind)}」改为「{ReferencesPane.Label(next)}」，可能解除已有绑定。确定继续吗？")) return;
        await pane.Mutate(async () => { await pane.View.ApiSend($"assets/{Asset.Id}", HttpMethod.Patch, new { kind = next }); if (pane.Active) { pane.View.InvalidateOutfitDependents(); await pane.View.ReloadAssets(); await pane.ReloadStyleLinks(); } });
    }
    internal async Task Delete()
    {
        if (pane.Busy || !pane.Active || !pane.Ask("删除该素材及其候选记录，并解除已有绑定？")) return;
        await pane.Mutate(async () => { await pane.View.ApiSendOptional($"assets/{Asset.Id}", HttpMethod.Delete); if (pane.Active) { pane.View.InvalidateOutfitDependents(); await pane.View.ReloadAssets(); } });
    }
    internal void SetBusy(bool value) { nameHost.IsEnabled = !value; kind.IsEnabled = !value; remove.IsEnabled = !value; binding.IsEnabled = !value; }
    internal void UpdateBinding()
    {
        binding.Children.Clear(); var view = pane.View;
        void Note(string text) { var note = ReferencesPane.Caption(text); note.Foreground = AssetPageUi.Brush("Success"); binding.Children.Add(new Border { Child = note, Background = AssetPageUi.Brush("SuccessBg"), BorderBrush = AssetPageUi.Brush("Line"), BorderThickness = new Thickness(1), Padding = new Thickness(5), Margin = new Thickness(0, 0, 0, 6) }); }
        Button Action(string text, RoutedEventHandler handler) { var button = Kit.Act(text, handler, "Compact"); button.HorizontalContentAlignment = HorizontalAlignment.Center; button.MinHeight = 36; binding.Children.Add(button); return button; }
        if (Asset.Kind == "CHARACTER_REFERENCE")
        {
            var linked = view.characters.FirstOrDefault(c => c.References.Any(r => r.Text("asset_id") == Asset.Id));
            if (linked != null) Note("当前绑定：" + linked.PrimaryName);
            var owner = view.SelectedCharacter;
            Action(owner == null ? "先选择角色" : linked?.Id == owner.Id ? "解除与 " + owner.PrimaryName + " 的绑定" : linked == null ? "绑定到 " + owner.PrimaryName : "改绑到 " + owner.PrimaryName + "（自动解除 " + linked.PrimaryName + "）", async (_, _) => await Bind()).IsEnabled = owner != null;
        }
        else if (Asset.Kind is "OUTFIT_REFERENCE" or "STYLE_REFERENCE")
        {
            bool outfit = Asset.Kind == "OUTFIT_REFERENCE"; var pending = outfit ? view.PendingOutfitReferences : view.PendingStyleReferences;
            var links = outfit ? string.Join("、", view.outfits.Where(o => o.ReferenceAssetIds.Contains(Asset.Id)).Select(o => (view.characters.FirstOrDefault(c => c.Id == o.CharacterId)?.PrimaryName ?? "未知角色") + " → " + o.Name)) : pane.StyleLinks(Asset.Id);
            Note(!outfit && !pane.StyleLinksReady ? "风格引用状态尚未读取" : links.Length > 0 ? "已用于：" + links : "尚未写入" + (outfit ? "服装" : "风格") + "档案");
            Action(pending.Contains(Asset.Id) ? "已选：保存后绑定" : "加入当前" + (outfit ? "服装" : "风格") + "档案", (_, _) => pane.ToggleDraft(Asset)).IsEnabled = !outfit || view.SelectedCharacter != null;
            if (pending.Contains(Asset.Id)) Action("前往" + (outfit ? "服装档案" : "漫画风格") + " →", async (_, _) => await view.SwitchAsync(outfit ? AssetsView.Outfits : AssetsView.Style));
        }
        else binding.Children.Add(ReferencesPane.Caption("请到场景资产工作区绑定地点。"));
    }
    internal async Task Bind()
    {
        var owner = pane.View.SelectedCharacter; if (owner == null) return;
        var reference = owner.References.FirstOrDefault(r => r.Text("asset_id") == Asset.Id);
        await pane.Mutate(async () =>
        {
            if (reference.ValueKind == JsonValueKind.Object) await pane.View.ApiSendOptional($"character-references/{reference.Text("id")}", HttpMethod.Delete);
            else await pane.View.ApiSend($"characters/{owner.Id}/references", HttpMethod.Post, new { asset_id = Asset.Id, angle = "unspecified", is_canonical = true });
            if (pane.Active) await pane.View.ReloadAssets();
        });
    }
}
