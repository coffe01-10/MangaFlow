using System.Net.Http;
using System.Text.Json;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Controls.Primitives;
using System.Windows.Media;
using MangaFlow.Native.Controls;
using MangaFlow.Native.Services;
using Microsoft.Win32;

namespace MangaFlow.Native.Views;

internal sealed class StyleWorkspace : StackPanel
{
    internal readonly AssetsView View;
    private readonly int epoch;
    internal readonly string ProjectId;
    private readonly ContentControl header = new();
    private readonly StackPanel records = new();
    private readonly StackPanel references = new();
    private readonly TextBlock notice = new() { TextWrapping = TextWrapping.Wrap, Foreground = AssetPageUi.Brush("Danger") };
    private readonly Button retry = new() { Content = "重试读取档案", Visibility = Visibility.Collapsed, HorizontalAlignment = HorizontalAlignment.Left, Margin = new Thickness(0, 0, 0, 14) };
    private readonly TextBox name = new() { MinHeight = 42, MaxLength = 120 };
    private readonly TextBox locked = new() { MinHeight = 42 };
    private readonly TextBlock count = new() { FontSize = 28 };
    private readonly TextBlock summary = new() { TextWrapping = TextWrapping.Wrap, Foreground = AssetPageUi.Brush("Muted") };
    private readonly TextBlock modeHint = new() { Foreground = AssetPageUi.Brush("Muted"), TextWrapping = TextWrapping.Wrap };
    private readonly Button create;
    private readonly ModelPickerBand models;
    private readonly HashSet<string> selected = [];
    private readonly Dictionary<string, StyleProductionCard> cards = new();
    private string mode;
    private bool saving, reading;
    private int request;
    internal bool Active => View.IsCurrent(epoch) && View.OwnsStyle(this);
    internal int SessionEpoch => epoch;
    internal string Model => View.ImageEditModels.Any(m => m.Text("logical_alias") == models.Selected) ? models.Selected : "";
    internal Task InitialLoad { get; private set; } = Task.CompletedTask;
    internal StyleWorkspace(AssetsView view)
    {
        View = view; epoch = view.Epoch; ProjectId = view.ProjectIdValue;
        Background = AssetPageUi.Brush("Paper"); UseLayoutRounding = true; SnapsToDevicePixels = true;
        TextOptions.SetTextFormattingMode(this, TextFormattingMode.Display); RenderOptions.SetClearTypeHint(this, ClearTypeHint.Enabled);
        Children.Add(header); Heading(0);
        models = new ModelPickerBand(view, "本次风格测试模型"); Children.Add(models);
        models.AddHandler(ButtonBase.ClickEvent, new RoutedEventHandler((_, _) => { foreach (var card in cards.Values) card.UpdateState(); }));
        var workbench = new StackPanel();
        workbench.Children.Add(Title("创建新风格档案", "这里的模式只用于下面正在创建的新档案，并会记住本项目上次选择"));
        mode = KeyValueStore.Get("style-mode:" + ProjectId) == "color" ? "color" : "monochrome";
        name.Text = mode == "color" ? "彩色漫画风格" : "黑白网点风格";
        var modes = Modes(mode, value =>
        {
            mode = value; KeyValueStore.Set("style-mode:" + ProjectId, value);
            if (name.Text is "彩色漫画风格" or "黑白网点风格") name.Text = value == "color" ? "彩色漫画风格" : "黑白网点风格";
            UpdateDraft(); return Task.CompletedTask;
        });
        workbench.Children.Add(new PageHeading(new TextBlock { Text = "新档案色彩模式", FontWeight = FontWeights.Bold, VerticalAlignment = VerticalAlignment.Center }, modes));
        modeHint.Margin = new Thickness(0, 10, 0, 16); workbench.Children.Add(modeHint);
        var compose = new TilePanel { MinimumTileWidth = 310, MaximumColumns = 2, Gap = 16 };
        var fields = new StackPanel(); fields.Children.Add(Field("风格档案名称", name)); fields.Children.Add(Field("一致性锁定项", locked)); compose.Children.Add(fields);
        var selection = new StackPanel(); selection.Children.Add(Caption("当前待分析")); selection.Children.Add(count); selection.Children.Add(summary); compose.Children.Add(Surface(selection)); workbench.Children.Add(compose);
        create = Kit.Act("创建并分析", async (_, _) => await CreateAsync(), "InkButton"); create.HorizontalAlignment = HorizontalAlignment.Left; create.Margin = new Thickness(0, 12, 0, 22); workbench.Children.Add(create);
        workbench.Children.Add(Title("已保存档案 · 逐份修改与切换", "下方开关修改的是该档案本身，不会改变上方新档案表单。")); workbench.Children.Add(records);
        Children.Add(Surface(workbench, padding: 18)); notice.Margin = new Thickness(0, 14, 0, 14); Children.Add(notice);
        retry.Style = (Style)FindResource("Compact"); retry.Click += async (_, _) => await ReloadAsync(); Children.Add(retry);
        Children.Add(Title("漫画风格", "上传后选择参考页，用于分析画面语言与创建新档案。"));
        var uploadContent = new StackPanel { HorizontalAlignment = HorizontalAlignment.Center };
        uploadContent.Children.Add(new TextBlock { Text = "↑", FontSize = 30, HorizontalAlignment = HorizontalAlignment.Center });
        uploadContent.Children.Add(new TextBlock { Text = "拖拽图片到这里，或点击上传漫画风格", FontFamily = (FontFamily)FindResource("Serif"), FontSize = 17, Margin = new Thickness(0, 12, 0, 10), TextWrapping = TextWrapping.Wrap });
        uploadContent.Children.Add(Caption("上传后自动加入当前待分析参考，创建后由默认视觉模型分析。"));
        var upload = new Button { Content = uploadContent, Style = (Style)FindResource("Outline"), MinHeight = 180, AllowDrop = true, Margin = new Thickness(0, 0, 0, 24) };
        upload.Click += async (_, _) => { var picker = new OpenFileDialog { Filter = "图片|*.png;*.jpg;*.jpeg;*.webp", Multiselect = true }; if (picker.ShowDialog(View.WindowHost()) == true) await UploadAsync(picker.FileNames); };
        upload.DragOver += (_, e) => { e.Effects = !saving && e.Data.GetDataPresent(DataFormats.FileDrop) ? DragDropEffects.Copy : DragDropEffects.None; e.Handled = true; };
        upload.Drop += async (_, e) => { if (e.Data.GetData(DataFormats.FileDrop) is string[] paths) await UploadAsync(paths); e.Handled = true; };
        Children.Add(upload); Children.Add(references);
        selected.UnionWith(view.PendingStyleReferences.Where(id => view.assets.Any(a => a.Id == id && a.Kind == "STYLE_REFERENCE")));
        view.PendingStyleReferences.Clear();
        name.TextChanged += (_, _) => UpdateDraft(); UpdateDraft(); RenderReferences();
        Dispatcher.BeginInvoke(new Action(() => { if (Active) InitialLoad = ReloadAsync(); }));
    }
    internal static TextBlock Caption(string text) => new() { Text = text, FontSize = 12, TextWrapping = TextWrapping.Wrap, Foreground = AssetPageUi.Brush("Muted") };
    internal static StackPanel Title(string title, string description)
    {
        var panel = new StackPanel { Margin = new Thickness(0, 0, 0, 16) }; panel.Children.Add(new TextBlock { Text = title, FontSize = 15, FontWeight = FontWeights.Bold }); panel.Children.Add(Caption(description)); return panel;
    }
    internal static Border Surface(UIElement child, bool active = false, int padding = 12) => new() { Child = child, Padding = new Thickness(padding), BorderBrush = AssetPageUi.Brush(active ? "Success" : "Line"), BorderThickness = new Thickness(1), Background = AssetPageUi.Brush("Surface") };
    internal static FrameworkElement Field(string label, FrameworkElement input) { var field = SceneWorkspace.Field(label, input); field.Margin = new Thickness(0, 0, 0, 10); return field; }
    internal static WrapPanel Modes(string current, Func<string, Task> change)
    {
        var panel = new WrapPanel();
        foreach (var (id, label) in new[] { ("monochrome", "黑白漫画"), ("color", "彩色漫画") })
        {
            var toggle = new ToggleButton { Content = label, Tag = id, IsChecked = current == id, Style = (Style)Application.Current.FindResource("Pill"), MinHeight = 40, Padding = new Thickness(14, 8, 14, 8) };
            toggle.Click += async (_, _) => { foreach (var button in panel.Children.OfType<ToggleButton>()) button.IsChecked = Equals(button.Tag, id); await change(id); }; panel.Children.Add(toggle);
        }
        return panel;
    }
    private void Heading(int value) => header.Content = AssetPageUi.Header("STYLE SYSTEM / 漫画风格", "色板、画面语言与测试图", $"{value} 份档案");
    private void UpdateDraft()
    {
        if (create == null) return;
        count.Text = selected.Count + " 张参考页";
        summary.Text = selected.Count == 0 ? "在下方“漫画风格”中选择参考页" : string.Join("、", View.assets.Where(a => selected.Contains(a.Id)).Take(2).Select(a => a.Name));
        modeHint.Text = mode == "color" ? "分析色板、肤色发色、上色方式与光影。" : "分析线稿、网点、黑白对比与留白。";
        create.Content = $"创建并分析（{selected.Count} 图）"; create.IsEnabled = !saving && selected.Count > 0 && name.Text.Trim().Length > 0;
    }
    internal void Notify(string message) { if (Active) { notice.Text = message; retry.Visibility = message.StartsWith("风格档案读取失败") ? Visibility.Visible : Visibility.Collapsed; } }
    internal async Task ReloadAsync()
    {
        if (!Active) return; var ticket = ++request; reading = true;
        try
        {
            var styleTask = View.ApiSend($"projects/{ProjectId}/styles"); var projectTask = View.ApiSend($"projects/{ProjectId}"); await Task.WhenAll(styleTask, projectTask);
            if (!Active || ticket != request) return;
            if (retry.Visibility == Visibility.Visible) Notify("");
            var rows = (await styleTask).EnumerateArray().ToList(); var activeId = (await projectTask).Text("default_style_id");
            foreach (var id in cards.Keys.Where(id => !rows.Any(r => r.Text("id") == id)).ToList()) { records.Children.Remove(cards[id]); cards.Remove(id); }
            if (records.Children.OfType<TextBlock>().Any()) records.Children.Clear();
            foreach (var row in rows)
            {
                var id = row.Text("id");
                if (!cards.TryGetValue(id, out var card)) { card = new StyleProductionCard(this, row); cards[id] = card; records.Children.Add(card); }
                card.Adopt(row, activeId == id && row.Text("status") == "ACTIVE");
            }
            if (rows.Count == 0) records.Children.Add(Caption("选择色彩模式并绑定参考页，建立第一份漫画风格档案。")); Heading(rows.Count);
            ApplyPendingStyleFocus();
            await Task.WhenAll(cards.Values.Select(card => card.LoadCandidatesAsync()));
        }
        catch (OperationCanceledException) { }
        catch (Exception ex) { if (Active && ticket == request) Notify("风格档案读取失败：" + ex.Message); }
        finally { if (ticket == request) reading = false; }
    }
    internal void PollTick() { if (Active && !reading && cards.Values.Any(c => c.NeedsPoll)) _ = ReloadAsync(); }

    // ?style= deep link (web focusStyleId + deep-link-focus CSS class): highlight the
    // targeted record and scroll it into view. Styles load asynchronously here, so an
    /// id that has no card yet is queued and applied at the end of the next ReloadAsync.
    private string? pendingFocusStyleId;
    internal void FocusStyle(string styleId)
    {
        pendingFocusStyleId = styleId;
        ApplyPendingStyleFocus();
    }
    private void ApplyPendingStyleFocus()
    {
        if (pendingFocusStyleId is not { } id || !cards.TryGetValue(id, out var card)) return;
        pendingFocusStyleId = null;
        View.SelectedStyle = card.Item;
        card.MarkDeepLinkFocus();
    }
    internal async Task CreateAsync()
    {
        if (!Active || saving || selected.Count == 0 || name.Text.Trim().Length == 0) return;
        saving = true; IsEnabled = false; var submitted = selected.ToArray();
        try
        {
            var style = await View.ApiSend($"projects/{ProjectId}/styles", HttpMethod.Post, new { name = name.Text.Trim(), color_mode = mode, profile = new { }, reference_asset_ids = submitted, locked_fields = SceneEditor.Split(locked.Text) });
            if (!Active) return;
            selected.ExceptWith(submitted); locked.Clear(); RenderReferences(); UpdateDraft();
            Notify("风格档案已创建，正在提交分析。");
            try { var job = await View.ApiSend($"styles/{style.Text("id")}/analyze", HttpMethod.Post); Notify("风格档案已创建 · " + JobLabel(job)); }
            catch (Exception ex) { Notify("档案已保存，但分析未启动；可在下方重新分析。" + ex.Message); }
            if (Active) { await ReloadAsync(); }
        }
        catch (Exception ex) { Notify(ex.Message); }
        finally { saving = false; IsEnabled = true; UpdateDraft(); }
    }
    internal static string JobLabel(JsonElement job) => job.Text("status") switch { "WAITING" => "等待执行，请到任务中心检查队列", "FAILED" => "任务失败，请到任务中心查看原因", "COMPLETED" => "分析已完成", _ => "分析任务已提交" };
    internal async Task UploadAsync(string[] paths)
    {
        if (!Active || saving) return; saving = true; IsEnabled = false;
        try { foreach (var path in paths) { var file = await View.UploadAsset("STYLE_REFERENCE", path); if (!Active) return; View.assets.RemoveAll(a => a.Id == file.Text("id")); View.assets.Add(AssetItem.From(file)); selected.Add(file.Text("id")); } Notify("参考页已上传并加入待分析选择。"); }
        catch (Exception ex) { Notify("上传未全部完成：" + ex.Message); }
        finally { saving = false; IsEnabled = true; if (Active) { RenderReferences(); UpdateDraft(); } }
    }
    private void RenderReferences()
    {
        references.Children.Clear(); var assets = View.assets.Where(a => a.Kind == "STYLE_REFERENCE").ToList();
        references.Children.Add(new PageHeading(new TextBlock { Text = "漫画风格参考", FontWeight = FontWeights.Bold }, Caption($"{assets.Count} FILES")));
        var tiles = new TilePanel { MinimumTileWidth = 340, MaximumColumns = 2, Gap = 10, Margin = new Thickness(0, 14, 0, 0) };
        foreach (var asset in assets)
        {
            var body = new StackPanel(); body.Children.Add(new TextBlock { Text = asset.Name, FontSize = 14, FontWeight = FontWeights.Bold }); body.Children.Add(Caption(asset.SizeLabel + " · " + asset.StatusLabel));
            if (asset.ContentUrl.Length > 0)
            {
                var artwork = new Button { Content = new ImageBox { SourceUrl = View.OriginFor(asset.ContentUrl.Replace("/content", "/thumbnail/640")), Height = 140 }, Padding = new Thickness(0), ToolTip = "查看风格参考原图" };
                artwork.Click += (_, _) => { try { View.ShowImage(asset.ContentUrl, asset.Name); } catch (Exception ex) { Notify(ex.Message); } }; body.Children.Add(artwork);
            }
            var choose = Kit.Act(selected.Contains(asset.Id) ? "已加入当前风格档案" : "加入当前风格档案", (_, _) => { if (!selected.Add(asset.Id)) selected.Remove(asset.Id); UpdateDraft(); RenderReferences(); }, selected.Contains(asset.Id) ? "CompactInk" : "Compact"); choose.Margin = new Thickness(0, 10, 0, 0); body.Children.Add(choose);
            var actions = new WrapPanel { Margin = new Thickness(0, 8, 0, 0) };
            actions.Children.Add(Kit.Act("重命名", async (_, _) =>
            {
                var dialog = new InputDialog(View.WindowHost(), "修改素材名称", "素材名称", preset: asset.Name);
                if (dialog.ShowDialog() == true && dialog.Value.Trim().Length > 0) await UpdateAssetAsync(asset.Id, HttpMethod.Patch, new { display_name = dialog.Value.Trim() });
            }, "Compact"));
            var kind = SceneWorkspace.Select("参考用途", ("STYLE_REFERENCE", "漫画风格"), ("CHARACTER_REFERENCE", "人物参考"), ("OUTFIT_REFERENCE", "服装参考"), ("SCENE_REFERENCE", "场景参考"));
            kind.Margin = new Thickness(8, 0, 8, 0); kind.SelectionChanged += async (_, _) => { if (SceneWorkspace.Value(kind) != asset.Kind) await UpdateAssetAsync(asset.Id, HttpMethod.Patch, new { kind = SceneWorkspace.Value(kind) }); }; actions.Children.Add(kind);
            actions.Children.Add(Kit.Act("删除", async (_, _) => { if (MessageBox.Show(View.WindowHost(), $"删除“{asset.Name}”？这会影响使用该图的参考档案。", "删除参考页", MessageBoxButton.YesNo, MessageBoxImage.Warning) == MessageBoxResult.Yes) await UpdateAssetAsync(asset.Id, HttpMethod.Delete); }, "CompactDanger"));
            body.Children.Add(actions);
            tiles.Children.Add(Surface(body, selected.Contains(asset.Id)));
        }
        references.Children.Add(tiles); if (assets.Count == 0) references.Children.Add(Caption("还没有漫画风格参考页，请先上传。"));
    }
    private async Task UpdateAssetAsync(string id, HttpMethod method, object? body = null)
    {
        if (!Active || saving) return; saving = true; IsEnabled = false;
        try
        {
            await View.ApiSendOptional($"assets/{id}", method, body);
            var assets = await View.ApiSend($"assets?project_id={ProjectId}"); if (!Active) return;
            View.assets = assets.EnumerateArray().Select(AssetItem.From).ToList();
            selected.RemoveWhere(key => !View.assets.Any(a => a.Id == key && a.Kind == "STYLE_REFERENCE"));
            await ReloadAsync();
        }
        catch (Exception ex) { Notify(ex.Message); }
        finally { saving = false; IsEnabled = true; if (Active) { RenderReferences(); UpdateDraft(); } }
    }
}
