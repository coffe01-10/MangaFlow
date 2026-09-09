using System.IO;
using System.Net.Http;
using System.Text.Json;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Controls.Primitives;
using System.Windows.Media;
using Microsoft.Win32;
using MangaFlow.Native.Controls;

namespace MangaFlow.Native.Views;

internal static class AssetPageUi
{
    internal static Brush Brush(string key) => (Brush)Application.Current.FindResource(key);
    internal static FrameworkElement Input(TextBox input, string hint, string name)
    {
        System.Windows.Automation.AutomationProperties.SetName(input, name);
        var panel = new Grid(); panel.Children.Add(input);
        var placeholder = new TextBlock { Text = hint, Margin = new Thickness(13, 0, 13, 0), Foreground = Brush("Muted"), FontSize = 12, VerticalAlignment = VerticalAlignment.Center, IsHitTestVisible = false, TextTrimming = TextTrimming.CharacterEllipsis, TextWrapping = TextWrapping.NoWrap };
        panel.Children.Add(placeholder);
        void Update() => placeholder.Visibility = input.Text.Length == 0 ? Visibility.Visible : Visibility.Collapsed;
        input.TextChanged += (_, _) => Update(); Update(); return panel;
    }
    internal static Border Header(string kicker, string title, string count)
    {
        var heading = new StackPanel();
        heading.Children.Add(new TextBlock { Text = kicker, FontSize = 10, FontWeight = FontWeights.Bold, Foreground = Brush("Muted") });
        heading.Children.Add(new TextBlock { Text = title, FontFamily = (FontFamily)Application.Current.FindResource("Serif"), FontSize = 23, Margin = new Thickness(0, 10, 0, 0) });
        return new Border { BorderBrush = Brush("Ink"), BorderThickness = new Thickness(0, 0, 0, 1), Padding = new Thickness(0, 0, 0, 17), Child = new PageHeading(heading, Kit.Caption(count)) };
    }
}

internal sealed class CharacterPackagesWorkspace : StackPanel
{
    private readonly AssetsView view;
    private readonly StackPanel content = new();
    private string selected = "";
    private List<JsonElement> packages = [];
    internal CharacterPackagesWorkspace(AssetsView view) { this.view = view; Margin = new Thickness(0, 24, 0, 32); Children.Add(content); _ = LoadAsync(); }
    internal async Task LoadAsync()
    {
        var epoch = view.Epoch; content.Children.Clear(); content.Children.Add(Kit.Caption("正在载入角色模型包…"));
        try
        {
            var all = new List<JsonElement>();
            for (var offset = 0; ; offset += 200)
            {
                var page = await view.ApiSend($"projects/{view.ProjectIdValue}/character-packages?limit=200&offset={offset}");
                if (!view.IsCurrent(epoch)) return;
                var rows = page.EnumerateArray().ToList(); all.AddRange(rows); if (rows.Count < 200) break;
            }
            packages = all;
            if (!packages.Any(p => p.Text("character_id") == selected)) selected = packages.FirstOrDefault().Text("character_id");
            Render();
        }
        catch (OperationCanceledException) { }
        catch (Exception ex)
        {
            if (!view.IsCurrent(epoch)) return;
            content.Children.Clear(); content.Children.Add(Kit.Caption("角色模型包无法载入：" + ex.Message));
            content.Children.Add(Kit.Act("重试", async (_, _) => await LoadAsync(), "Outline"));
        }
    }
    private void Render()
    {
        content.Children.Clear();
        content.Children.Add(AssetPageUi.Header("CHARACTER MODEL PACKAGE / 角色模型包", "版本化角色资产：规格、矩阵与服装集", $"{packages.Count} 个模型包"));
        var createRow = new WrapPanel { Margin = new Thickness(0, 18, 0, 16) };
        var selector = new ComboBox { MinWidth = 185, MinHeight = 42, Margin = new Thickness(0, 0, 8, 0) };
        selector.Items.Add(new ComboBoxItem { Content = "选择角色（还没有模型包）", Tag = "" });
        foreach (var c in view.characters.Where(c => !packages.Any(p => p.Text("character_id") == c.Id))) selector.Items.Add(new ComboBoxItem { Content = c.PrimaryName, Tag = c.Id });
        selector.SelectedIndex = 0; System.Windows.Automation.AutomationProperties.SetName(selector, "选择要创建模型包的角色");
        var create = Kit.Act("＋ 创建角色模型包", async (sender, _) =>
        {
            var id = (selector.SelectedItem as ComboBoxItem)?.Tag as string;
            if (string.IsNullOrEmpty(id)) return;
            var epoch = view.Epoch; ((Button)sender).IsEnabled = selector.IsEnabled = false;
            try { await view.ApiSend($"projects/{view.ProjectIdValue}/characters/{id}/package", HttpMethod.Post, new { }); if (!view.IsCurrent(epoch)) return; selected = id; await LoadAsync(); }
            catch (Exception ex) { if (view.IsCurrent(epoch)) { view.Notify(ex.Message); ((Button)sender).IsEnabled = selector.IsEnabled = true; } }
        }, "InkButton");
        create.IsEnabled = false; create.MinHeight = 44;
        selector.SelectionChanged += (_, _) => create.IsEnabled = selector.SelectedIndex > 0;
        createRow.Children.Add(selector); createRow.Children.Add(create);
        createRow.Children.Add(new TextBlock { Text = "新建角色不会自动创建模型包；只有发布过的版本才会进入生成默认继承。", FontSize = 12, Foreground = AssetPageUi.Brush("Muted"), Margin = new Thickness(8, 0, 0, 0), VerticalAlignment = VerticalAlignment.Center });
        content.Children.Add(createRow);
        var split = new Grid { ColumnDefinitions = { new ColumnDefinition { Width = new GridLength( .24, GridUnitType.Star) }, new ColumnDefinition { Width = new GridLength(.76, GridUnitType.Star) } } };
        var list = new ListBox { Margin = new Thickness(0, 0, 16, 0), HorizontalContentAlignment = HorizontalAlignment.Stretch };
        System.Windows.Automation.AutomationProperties.SetName(list, "角色模型包列表");
        foreach (var package in packages)
        {
            var id = package.Text("character_id"); var character = view.characters.FirstOrDefault(c => c.Id == id);
            var text = new StackPanel();
            text.Children.Add(new TextBlock { Text = character?.PrimaryName ?? package.Element("character").Text("primary_name"), FontWeight = FontWeights.Bold });
            text.Children.Add(Kit.Caption(package.Number("published_version_number") > 0 ? $"已发布 V{package.Number("published_version_number")} · 完整度 {package.Element("published_completeness").Number("score")}%" : "尚未发布 · 完整度 —"));
            text.Children.Add(new TextBlock { Text = package.Text("status") == "ARCHIVED" ? "已归档" : "启用中", Foreground = AssetPageUi.Brush("Success"), FontSize = 12 });
            list.Items.Add(new ListBoxItem { Tag = id, Content = new Border { BorderThickness = new Thickness(1), BorderBrush = AssetPageUi.Brush("Line"), Background = AssetPageUi.Brush("Surface"), Padding = new Thickness(12), Child = text }, IsSelected = id == selected, Padding = new Thickness(0), Margin = new Thickness(0, 0, 0, 7) });
        }
        list.SelectedItem = list.Items.OfType<ListBoxItem>().FirstOrDefault(item => Equals(item.Tag, selected));
        var detail = new ContentControl { HorizontalContentAlignment = HorizontalAlignment.Stretch };
        void Select()
        {
            var id = (list.SelectedItem as ListBoxItem)?.Tag as string;
            var character = view.characters.FirstOrDefault(c => c.Id == id);
            if (character == null) return;
            selected = character.Id; detail.Content = new CharacterPackagePane(view, character) { Margin = new Thickness(0) };
        }
        list.SelectionChanged += (_, _) => Select(); Select();
        split.Children.Add(list); Grid.SetColumn(detail, 1); split.Children.Add(detail);
        if (packages.Count == 0) content.Children.Add(Kit.Caption("尚未创建角色模型包。为角色创建模型包后，可以维护四视图矩阵、表情集与默认服装。"));
        else content.Children.Add(split);
    }
}

internal sealed class CharacterReferencesPane : StackPanel
{
    private readonly AssetsView view;
    private bool busy;
    internal CharacterReferencesPane(AssetsView view) { this.view = view; Render(); }
    internal void Render()
    {
        Children.Clear();
        Children.Add(new TextBlock { Text = "人物参考", FontWeight = FontWeights.Bold, FontSize = 16, Margin = new Thickness(0, 0, 0, 12) });
        var stage = new StackPanel { HorizontalAlignment = HorizontalAlignment.Center };
        stage.Children.Add(new TextBlock { Text = "↑", FontSize = 32, HorizontalAlignment = HorizontalAlignment.Center });
        stage.Children.Add(new TextBlock { Text = "拖拽图片到这里，或点击上传人物参考", FontFamily = (FontFamily)Application.Current.FindResource("Serif"), FontSize = 17, HorizontalAlignment = HorizontalAlignment.Center, Margin = new Thickness(0, 10, 0, 10) });
        stage.Children.Add(new TextBlock { Text = view.SelectedCharacter == null ? "请先选择要绑定的角色" : $"人物图会和 {view.SelectedCharacter.PrimaryName} 绑定，用于保持脸、发型和体型一致。", Foreground = AssetPageUi.Brush("Muted"), FontSize = 13, TextWrapping = TextWrapping.Wrap });
        var upload = new Button { Content = stage, MinHeight = 160, Background = AssetPageUi.Brush("Surface"), Style = (Style)Application.Current.FindResource("Outline"), AllowDrop = true, IsEnabled = view.SelectedCharacter != null && !busy, Margin = new Thickness(0, 0, 0, 28) };
        upload.Click += async (_, _) => { var picker = new OpenFileDialog { Filter = "图片|*.png;*.jpg;*.jpeg;*.webp", Multiselect = true }; if (picker.ShowDialog(view.WindowHost()) == true) await UploadAsync(picker.FileNames); };
        upload.Drop += async (_, e) => { if (e.Data.GetData(DataFormats.FileDrop) is string[] paths) await UploadAsync(paths); };
        Children.Add(upload);
        var members = view.assets.Where(a => a.Kind == "CHARACTER_REFERENCE").ToList();
        Children.Add(new PageHeading(new TextBlock { Text = "人物参考", FontWeight = FontWeights.Bold }, Kit.Caption($"{members.Count} FILES")));
        Children.Add(new TextBlock { Text = "绑定主要姓名与绰号，用于保持脸、发型和体型一致。", Foreground = AssetPageUi.Brush("Muted"), Margin = new Thickness(0, 8, 0, 20) });
        var grid = new TilePanel { MinimumTileWidth = 370, MaximumColumns = 2, Gap = 10 };
        foreach (var asset in members) grid.Children.Add(Card(asset));
        Children.Add(grid); if (members.Count == 0) Children.Add(Kit.Caption("尚无人物参考"));
    }
    internal async Task UploadAsync(string[] paths)
    {
        var character = view.SelectedCharacter; if (character == null || busy) return;
        var epoch = view.Epoch; busy = true; Render();
        try
        {
            foreach (var path in paths)
            {
                var result = await view.UploadAsset("CHARACTER_REFERENCE", path);
                if (!view.IsCurrent(epoch)) return;
                await view.ApiSend($"characters/{character.Id}/references", HttpMethod.Post, new { asset_id = result.Text("id"), angle = "unspecified", is_canonical = true });
            }
            if (view.IsCurrent(epoch)) await view.ReloadAssets();
        }
        catch (Exception ex) { if (view.IsCurrent(epoch)) view.Notify("上传或绑定失败：" + ex.Message); }
        finally { busy = false; if (view.IsCurrent(epoch)) Render(); }
    }
    private Border Card(AssetItem asset)
    {
        var grid = new Grid { ColumnDefinitions = { new ColumnDefinition { Width = new GridLength(84) }, new ColumnDefinition() } };
        var thumb = new Button { Width = 74, Height = 74, Padding = new Thickness(0), VerticalAlignment = VerticalAlignment.Top, BorderThickness = new Thickness(0) };
        if (asset.ContentUrl.Length > 0) { thumb.Content = new ImageBox { SourceUrl = view.OriginFor(asset.ContentUrl.Replace("/content", "/thumbnail/640")) }; thumb.Click += (_, _) => view.ShowImage(view.OriginFor(asset.ContentUrl), asset.Name); }
        else thumb.Content = SourceIcon.Create("file", 26);
        grid.Children.Add(thumb);
        var info = new StackPanel(); Grid.SetColumn(info, 1); grid.Children.Add(info);
        var name = new TextBox { Text = asset.Name, Visibility = Visibility.Collapsed };
        var heading = new TextBlock { Text = asset.Name, FontWeight = FontWeights.Bold, FontSize = 14, TextWrapping = TextWrapping.Wrap };
        var rename = SourceIcon.Action("edit", "修改素材名称", (_, _) => { heading.Visibility = Visibility.Collapsed; name.Visibility = Visibility.Visible; name.Focus(); });
        var nameRow = new DockPanel(); DockPanel.SetDock(rename, Dock.Right); nameRow.Children.Add(rename); nameRow.Children.Add(heading);
        info.Children.Add(nameRow); info.Children.Add(name);
        name.KeyDown += async (_, e) =>
        {
            if (e.Key == System.Windows.Input.Key.Escape) { name.Text = asset.Name; name.Visibility = Visibility.Collapsed; heading.Visibility = Visibility.Visible; }
            if (e.Key != System.Windows.Input.Key.Enter || string.IsNullOrWhiteSpace(name.Text)) return;
            await RunAsync(async () => { await view.ApiSend($"assets/{asset.Id}", HttpMethod.Patch, new { display_name = name.Text.Trim() }); });
        };
        info.Children.Add(Kit.Caption($"人物参考 · {asset.SizeLabel}"));
        info.Children.Add(new TextBlock { Text = "✓ " + asset.StatusLabel, Foreground = AssetPageUi.Brush("Success"), Margin = new Thickness(0, 6, 0, 8) });
        var character = view.SelectedCharacter;
        var reference = character?.References.FirstOrDefault(r => r.Text("asset_id") == asset.Id) ?? default;
        var linked = view.characters.FirstOrDefault(c => c.References.Any(r => r.Text("asset_id") == asset.Id));
        if (linked != null) info.Children.Add(new Border { Background = AssetPageUi.Brush("SuccessBg"), Padding = new Thickness(6), Margin = new Thickness(0, 0, 0, 6), Child = Kit.Caption("当前绑定：" + linked.PrimaryName) });
        var bound = reference.ValueKind == JsonValueKind.Object;
        var bind = Kit.Act(character == null ? "先选择角色" : bound ? "解除与 " + character.PrimaryName + " 的绑定" : "绑定到 " + character.PrimaryName, async (_, _) =>
        {
            if (character == null) return;
            await RunAsync(async () => { if (bound) await view.ApiSendOptional($"character-references/{reference.Text("id")}", HttpMethod.Delete); else await view.ApiSend($"characters/{character.Id}/references", HttpMethod.Post, new { asset_id = asset.Id, angle = "unspecified", is_canonical = true }); });
        }, "Ghost");
        bind.IsEnabled = character != null && !busy; info.Children.Add(bind);
        var actions = new Grid { Margin = new Thickness(0, 8, 0, 0), ColumnDefinitions = { new ColumnDefinition(), new ColumnDefinition { Width = GridLength.Auto } } };
        var kinds = new ComboBox { MinHeight = 40, Margin = new Thickness(0, 0, 6, 0), IsEnabled = !busy };
        foreach (var kind in new[] { "CHARACTER_REFERENCE", "OUTFIT_REFERENCE", "SCENE_REFERENCE", "STYLE_REFERENCE" }) kinds.Items.Add(new ComboBoxItem { Tag = kind, Content = Labels.Map(Labels.AssetKinds, kind), IsSelected = kind == asset.Kind });
        kinds.SelectionChanged += async (_, _) =>
        {
            var kind = (kinds.SelectedItem as ComboBoxItem)?.Tag as string; if (kind == null || kind == asset.Kind) return;
            kinds.SelectedIndex = 0;
            if (MessageBox.Show(view.WindowHost(), "修改素材用途可能解除已有绑定，确定继续吗？", "重分类", MessageBoxButton.YesNo) != MessageBoxResult.Yes) return;
            await RunAsync(async () => { await view.ApiSend($"assets/{asset.Id}", HttpMethod.Patch, new { kind }); });
        };
        actions.Children.Add(kinds);
        var remove = SourceIcon.Action("trash", "删除素材", async (_, _) => { if (MessageBox.Show(view.WindowHost(), "删除该素材及其候选记录，并解除已有绑定？", "删除素材", MessageBoxButton.YesNo) == MessageBoxResult.Yes) await RunAsync(async () => { await view.ApiSendOptional($"assets/{asset.Id}", HttpMethod.Delete); }); });
        Grid.SetColumn(remove, 1); actions.Children.Add(remove); info.Children.Add(actions);
        return new Border { BorderBrush = AssetPageUi.Brush("Line"), BorderThickness = new Thickness(1), Background = AssetPageUi.Brush("Surface"), Padding = new Thickness(10), Child = grid };
    }
    private async Task RunAsync(Func<Task> action)
    {
        if (busy) return; var epoch = view.Epoch; busy = true; IsEnabled = false;
        try { await action(); if (view.IsCurrent(epoch)) await view.ReloadAssets(); }
        catch (Exception ex) { if (view.IsCurrent(epoch)) view.Notify(ex.Message); }
        finally { busy = false; IsEnabled = true; }
    }
}
