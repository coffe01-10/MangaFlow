using System.Net.Http;
using System.Text.Json;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;
using System.Windows.Media;
using System.Windows.Threading;
using MangaFlow.Native.Controls;
using MangaFlow.Native.Services;
using Microsoft.Win32;

namespace MangaFlow.Native.Views;

/// <summary>Native counterpart of the web scene-workspace; all changes use the project API.</summary>
internal sealed class SceneWorkspace : StackPanel
{
    private readonly AssetsView view;
    private readonly int epoch;
    private readonly string project;
    private readonly ContentControl heading = new();
    private readonly TextBox search = new() { Width = 190, MinHeight = 40 };
    private readonly TextBox place = new() { Width = 145, MinHeight = 40 };
    private readonly ComboBox status = Select("按状态筛选场景", ("", "全部状态"), ("UPLOADED", "已上传"), ("ANALYZED", "已分析"), ("GENERATED", "已生成"), ("NEEDS_CONFIRMATION", "待确认"), ("CANONICAL", "已就绪"), ("ARCHIVED", "已归档"));
    private readonly ComboBox interior = Select("按室内外筛选", ("", "全部"), ("true", "室内"), ("false", "室外"));
    private readonly CheckBox archived = new() { Content = "显示已归档", MinHeight = 40, VerticalContentAlignment = VerticalAlignment.Center };
    private readonly ListBox list = new() { HorizontalContentAlignment = HorizontalAlignment.Stretch, BorderThickness = new Thickness(0), Background = Brushes.Transparent };
    private readonly StackPanel left = new();
    private readonly StackPanel detail = new();
    private readonly Grid split = new();
    private readonly StackPanel feedback = new();
    private readonly DispatcherTimer debounce = new() { Interval = TimeSpan.FromMilliseconds(250) };
    private List<JsonElement> rows = [];
    private string selected = "";
    private int request;
    private bool busy, rendering;
    private string Base => $"projects/{project}/scene-assets";
    private bool Active => view.IsCurrent(epoch) && view.OwnsScenes(this);
    internal Task InitialLoad { get; private set; } = Task.CompletedTask;

    internal SceneWorkspace(AssetsView view)
    {
        this.view = view; epoch = view.Epoch; project = view.ProjectIdValue;
        Background = AssetPageUi.Brush("Paper"); UseLayoutRounding = true; SnapsToDevicePixels = true;
        TextOptions.SetTextFormattingMode(this, TextFormattingMode.Display);
        RenderOptions.SetClearTypeHint(this, ClearTypeHint.Enabled);
        Children.Add(heading); UpdateHeading();
        var toolbar = new WrapPanel { Margin = new Thickness(0, 16, 0, 8) };
        var create = Kit.Act("＋ 新建场景", (_, _) => EditScene(null), "CompactInk"); create.MinHeight = 40;
        toolbar.Children.Add(Wrap(create));
        toolbar.Children.Add(Wrap(AssetPageUi.Input(search, "搜索场景 / 地点", "搜索场景或地点")));
        toolbar.Children.Add(Wrap(Field("状态", status))); toolbar.Children.Add(Wrap(Field("地点前缀", place)));
        toolbar.Children.Add(Wrap(Field("室内外", interior))); toolbar.Children.Add(Wrap(archived));
        Children.Add(toolbar); Children.Add(feedback);
        split.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(280) });
        split.ColumnDefinitions.Add(new ColumnDefinition());
        split.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto }); split.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        left.Children.Add(list); split.Children.Add(left); Grid.SetColumn(detail, 1); split.Children.Add(detail); Children.Add(split);
        list.SelectionChanged += (_, _) => { if (!rendering && list.SelectedItem is ListBoxItem item) { selected = (string)item.Tag; RenderDetail(); } };
        search.TextChanged += (_, _) => RenderList();
        place.TextChanged += (_, _) => ScheduleLoad(); status.SelectionChanged += (_, _) => ScheduleLoad(); interior.SelectionChanged += (_, _) => ScheduleLoad();
        archived.Checked += (_, _) => ScheduleLoad(); archived.Unchecked += (_, _) => ScheduleLoad();
        debounce.Tick += async (_, _) => { debounce.Stop(); if (Active) await ReloadAsync(); };
        Unloaded += (_, _) => { debounce.Stop(); request++; };
        SizeChanged += (_, _) => ArrangeColumns();
        Dispatcher.BeginInvoke(new Action(() => { if (Active) InitialLoad = ReloadAsync(); }));
    }

    private static FrameworkElement Wrap(FrameworkElement element) { element.Margin = new Thickness(0, 0, 8, 8); element.VerticalAlignment = VerticalAlignment.Bottom; return element; }
    internal static FrameworkElement Field(string label, FrameworkElement input)
    {
        var panel = new StackPanel(); panel.Children.Add(new TextBlock { Text = label, FontSize = 11, FontWeight = FontWeights.SemiBold, Foreground = AssetPageUi.Brush("Muted"), Margin = new Thickness(0, 0, 0, 4) }); panel.Children.Add(input);
        System.Windows.Automation.AutomationProperties.SetName(input, label); return panel;
    }
    internal static ComboBox Select(string name, params (string Id, string Label)[] options)
    {
        var box = new ComboBox { MinWidth = 100, MinHeight = 40 };
        foreach (var option in options) box.Items.Add(new ComboBoxItem { Tag = option.Id, Content = option.Label });
        box.SelectedIndex = 0; System.Windows.Automation.AutomationProperties.SetName(box, name); return box;
    }
    internal static string Value(ComboBox box) => (box.SelectedItem as ComboBoxItem)?.Tag as string ?? "";
    internal static bool Deleted(JsonElement row) => row.TryGetProperty("deleted_at", out var value) && value.ValueKind is not (JsonValueKind.Null or JsonValueKind.Undefined);
    internal static string Status(JsonElement row) => Deleted(row) ? "已归档" : row.Text("status") switch
    {
        "CANONICAL" => "已就绪 · 可直接用于剧本与分镜", "NEEDS_CONFIRMATION" => "待确认 · 尚未设置规范参考图",
        "UPLOADED" => "已上传", "ANALYZED" => "已分析", "GENERATED" => "已生成", "ARCHIVED" => "已归档", _ => "状态未知",
    };
    private static string Interior(JsonElement row) => row.Element("structured") is { ValueKind: JsonValueKind.Object } data && data.TryGetProperty("interior", out var v) ? v.ValueKind switch { JsonValueKind.True => "室内", JsonValueKind.False => "室外", _ => "空间未指定" } : "空间未指定";
    private static TextBlock Caption(string text) => new() { Text = text, FontSize = 12, Foreground = AssetPageUi.Brush("Muted"), TextWrapping = TextWrapping.Wrap };
    private static Border Surface(UIElement child, bool canonical = false, int padding = 10) => new() { Child = child, Padding = new Thickness(padding), BorderThickness = new Thickness(1), BorderBrush = AssetPageUi.Brush(canonical ? "Success" : "Line"), Background = AssetPageUi.Brush("Surface") };
    private void ArrangeColumns()
    {
        // Web switches to one column below a 1280 px viewport (about 990 px of content).
        bool narrow = ActualWidth < 990;
        split.ColumnDefinitions[0].Width = narrow ? new GridLength(1, GridUnitType.Star) : new GridLength(280);
        split.ColumnDefinitions[1].Width = narrow ? new GridLength(0) : new GridLength(1, GridUnitType.Star);
        Grid.SetColumn(detail, narrow ? 0 : 1); Grid.SetRow(detail, narrow ? 1 : 0);
        left.Margin = narrow ? new Thickness(0, 0, 0, 16) : new Thickness(0, 0, 16, 0);
    }
    private void ScheduleLoad() { debounce.Stop(); if (Active) debounce.Start(); }
    private void UpdateHeading() => heading.Content = AssetPageUi.Header("SCENE BIBLE / 场景资产", "地点档案、环境变体与参考图绑定", $"{rows.Count} 个场景");
    internal async Task ReloadAsync()
    {
        if (!Active) return;
        var ticket = ++request;
        if (rows.Count == 0) Message("正在载入场景资产…", false);
        try
        {
            var data = await view.ApiSend(QueryBuilder.Build(Base, ("limit", 200), ("status", Value(status)), ("place", place.Text.Trim()), ("interior", Value(interior)), ("include_deleted", archived.IsChecked == true ? "true" : "")));
            if (!Active || ticket != request) return;
            rows = data.EnumerateArray().Select(r => r.Clone()).ToList();
            if (!rows.Any(r => r.Text("id") == selected)) selected = "";
            feedback.Children.Clear(); UpdateHeading(); RenderList();
        }
        catch (OperationCanceledException) { }
        catch (Exception ex) { if (Active && ticket == request) Message("场景资产无法载入：" + ex.Message, true); }
    }
    private void Message(string text, bool retry = true)
    {
        feedback.Children.Clear(); feedback.Children.Add(new TextBlock { Text = text, TextWrapping = TextWrapping.Wrap, Foreground = AssetPageUi.Brush(retry ? "Danger" : "Muted"), Margin = new Thickness(0, 0, 0, 8) });
        if (retry) feedback.Children.Add(Kit.Act("刷新", async (_, _) => await ReloadAsync(), "Compact"));
    }
    private void RenderList()
    {
        rendering = true; list.Items.Clear();
        var keyword = search.Text.Trim();
        var visible = rows.Where(r => keyword.Length == 0 || string.Join(" ", r.Text("name"), r.Text("location_hint"), r.Element("structured").Text("place")).Contains(keyword, StringComparison.OrdinalIgnoreCase)).ToList();
        if (!visible.Any(r => r.Text("id") == selected)) selected = visible.FirstOrDefault().Text("id");
        foreach (var row in visible)
        {
            var text = new StackPanel();
            text.Children.Add(new TextBlock { Text = row.Text("name"), FontFamily = (FontFamily)FindResource("Serif"), FontWeight = FontWeights.Bold, Margin = new Thickness(0, 0, 0, 4) });
            text.Children.Add(Caption($"{Interior(row)} · {row.Array("variants").Count(v => !Deleted(v))} 变体"));
            var state = Caption(Status(row)); state.Foreground = AssetPageUi.Brush(Deleted(row) ? "Muted" : row.Text("status") == "CANONICAL" ? "Success" : "Warning"); state.Margin = new Thickness(0, 4, 0, 0); text.Children.Add(state);
            var card = Surface(text, padding: 12); card.BorderThickness = new Thickness(0);
            list.Items.Add(new ListBoxItem { Style = (Style)FindResource("SceneListItem"), Content = card, Tag = row.Text("id"), IsSelected = selected == row.Text("id"), Padding = new Thickness(0), Margin = new Thickness(0, 0, 0, 8) });
        }
        while (left.Children.Count > 1) left.Children.RemoveAt(1);
        if (visible.Count == 0)
        {
            var empty = new StackPanel { Margin = new Thickness(12) }; empty.Children.Add(new TextBlock { Text = rows.Count == 0 ? "尚未创建场景资产" : "未找到相关场景资产", FontWeight = FontWeights.Bold });
            empty.Children.Add(Caption("新建地点档案后，才能把参考图和环境变体绑定到剧本场景。"));
            empty.Children.Add(Kit.Act("清除筛选", async (_, _) => { search.Clear(); place.Clear(); status.SelectedIndex = interior.SelectedIndex = 0; archived.IsChecked = false; debounce.Stop(); await ReloadAsync(); }, "Compact")); left.Children.Add(Surface(empty));
        }
        rendering = false; RenderDetail();
    }
    private void RenderDetail()
    {
        detail.Children.Clear(); var row = rows.FirstOrDefault(r => r.Text("id") == selected);
        if (row.ValueKind != JsonValueKind.Object) return;
        var body = new StackPanel(); var title = new StackPanel(); title.Children.Add(Caption("SCENE DETAIL"));
        title.Children.Add(new TextBlock { Text = $"{row.Text("name")}（{Interior(row)}）", FontFamily = (FontFamily)FindResource("Serif"), FontSize = 19, FontWeight = FontWeights.Bold, Margin = new Thickness(0, 4, 0, 4), TextWrapping = TextWrapping.Wrap });
        var state = Caption(Status(row)); state.Foreground = AssetPageUi.Brush(row.Text("status") == "CANONICAL" && !Deleted(row) ? "Success" : "Muted"); title.Children.Add(state);
        var actions = new WrapPanel(); actions.Children.Add(Action("编辑基本信息", () => { EditScene(row); return Task.CompletedTask; })); actions.Children.Add(Action(Deleted(row) ? "恢复" : "归档", () => ArchiveAsync(row), "Compact"));
        body.Children.Add(new PageHeading(title, actions));
        var fixedProps = string.Join("、", row.Element("structured").Array("fixed_props").Select(v => v.ToString()));
        body.Children.Add(Spaced(Caption("固定特征：" + (fixedProps.Length > 0 ? fixedProps : row.Text("description", "尚未填写")))));
        if (row.Text("location_hint").Length > 0) body.Children.Add(Spaced(Caption("来源地点：" + row.Text("location_hint"))));
        if (!Deleted(row))
        {
            var states = new WrapPanel();
            var ready = Action("设为规范参考", () => ChangeAsync($"{Base}/{selected}", HttpMethod.Patch, new { version = row.Number("version"), status = "CANONICAL" }), "CompactInk");
            ready.IsEnabled = !busy && row.Text("status") != "CANONICAL" && row.Array("references").Any(r => r.Flag("is_canonical"));
            ready.ToolTip = "先为主空间选择一张规范参考图"; states.Children.Add(ready);
            var pending = Action("标为待确认", () => ChangeAsync($"{Base}/{selected}", HttpMethod.Patch, new { version = row.Number("version"), status = "NEEDS_CONFIRMATION" })); pending.IsEnabled = !busy && row.Text("status") != "NEEDS_CONFIRMATION"; states.Children.Add(pending); body.Children.Add(Spaced(states));
        }
        var upload = Action("上传参考图", () => UploadAsync(row), "Compact"); upload.IsEnabled &= !Deleted(row);
        body.Children.Add(Spaced(new PageHeading(new TextBlock { Text = "主参考图", FontWeight = FontWeights.Bold }, upload)));
        var refs = new TilePanel { MinimumTileWidth = 180, MaximumColumns = 5, Gap = 10, Margin = new Thickness(0, 14, 0, 0) };
        foreach (var reference in row.Array("references")) refs.Children.Add(Reference(row, reference));
        body.Children.Add(refs); if (refs.Children.Count == 0) body.Children.Add(Caption("尚未绑定场景参考图"));
        var variants = row.Array("variants").Where(v => !Deleted(v)).ToList();
        var addVariant = Action("＋ 添加变体", () => { EditVariant(row, null); return Task.CompletedTask; }); addVariant.IsEnabled &= !Deleted(row);
        body.Children.Add(Spaced(new PageHeading(new TextBlock { Text = $"环境变体与专属参考（{variants.Count}）", FontWeight = FontWeights.Bold }, addVariant)));
        var tiles = new TilePanel { MinimumTileWidth = 180, MaximumColumns = 4, Gap = 10, Margin = new Thickness(0, 14, 0, 0) };
        foreach (var variant in variants) tiles.Children.Add(Variant(row, variant)); body.Children.Add(tiles);
        if (variants.Count == 0) body.Children.Add(Caption("还没有环境变体。变体只覆盖时间、天气、季节、光照和色调。"));
        detail.Children.Add(Surface(body, padding: 16));
    }
    private static FrameworkElement Spaced(FrameworkElement item) { item.Margin = new Thickness(0, 14, 0, 0); return item; }
    private Button Action(string label, Func<Task> action, string style = "Compact")
    {
        var button = Kit.Act(label, async (_, _) => { if (!busy && Active) await action(); }, style); button.Margin = new Thickness(0, 0, 8, 4); button.IsEnabled = !busy; return button;
    }
    private FrameworkElement Reference(JsonElement scene, JsonElement reference, string? variant = null)
    {
        var cell = new StackPanel(); var id = reference.Text("asset_id"); var file = view.assets.FirstOrDefault(a => a.Id == id);
        if (file?.ContentUrl.Length > 0)
        {
            var image = new ImageBox { SourceUrl = view.OriginFor(file.ContentUrl.Replace("/content", "/thumbnail/640")), Height = variant == null ? 160 : 72 };
            var button = new Button { Content = image, Padding = new Thickness(0), BorderThickness = new Thickness(0), Background = AssetPageUi.Brush("Surface"), ToolTip = "放大参考图" };
            button.Click += (_, _) => { try { view.ShowImage(file.ContentUrl, scene.Text("name") + " 参考图"); } catch (Exception ex) { Message(ex.Message); } }; cell.Children.Add(button);
        }
        else cell.Children.Add(new Border { Height = 74, Child = Caption("参考图文件不可用"), BorderBrush = AssetPageUi.Brush("Line"), BorderThickness = new Thickness(1) });
        cell.Children.Add(Spaced(Caption(reference.Flag("is_canonical") ? "规范参考" : reference.Text("role", "main"))));
        var actions = new WrapPanel(); var path = $"{Base}/{scene.Text("id")}" + (variant == null ? "" : $"/variants/{variant}") + "/references";
        if (variant == null && !reference.Flag("is_canonical")) actions.Children.Add(Action("设为规范参考", () => CanonicalAsync(scene, reference)));
        actions.Children.Add(Action("解绑", () => ChangeAsync(path + "/" + id, HttpMethod.Delete))); actions.IsEnabled = !Deleted(scene); cell.Children.Add(actions);
        return Surface(cell, reference.Flag("is_canonical"));
    }
    private FrameworkElement Variant(JsonElement scene, JsonElement variant)
    {
        var cell = new StackPanel(); cell.Children.Add(new TextBlock { Text = variant.Text("name") + (variant.Flag("is_canonical") ? "（默认）" : ""), FontWeight = FontWeights.Bold });
        var data = variant.Element("structured_overrides"); var summary = string.Join(" · ", new[] { SceneEditor.TimeLabel(data.Text("time_of_day")), data.Text("weather"), data.Text("lighting") }.Where(s => s.Length > 0));
        cell.Children.Add(Spaced(Caption(summary.Length > 0 ? summary : "沿用主场景基调")));
        var id = variant.Text("id"); foreach (var reference in variant.Array("references")) cell.Children.Add(Reference(scene, reference, id));
        var actions = new WrapPanel(); actions.Children.Add(Action("绑定变体图", () => UploadAsync(scene, id)));
        if (!variant.Flag("is_canonical")) actions.Children.Add(Action("设为默认", () => ChangeAsync($"{Base}/{scene.Text("id")}/variants/{id}", HttpMethod.Patch, new { version = variant.Number("version"), is_canonical = true })));
        actions.Children.Add(Action("编辑", () => { EditVariant(scene, variant); return Task.CompletedTask; }));
        actions.Children.Add(Action("删除", async () => { if (MessageBox.Show(view.WindowHost(), $"删除环境变体“{variant.Text("name")}”？专属参考会解除绑定，剧本场景回退到资产默认参考。", "删除变体", MessageBoxButton.YesNo, MessageBoxImage.Warning) == MessageBoxResult.Yes) await ChangeAsync($"{Base}/{scene.Text("id")}/variants/{id}", HttpMethod.Delete); }, "CompactDanger"));
        actions.IsEnabled = !Deleted(scene); cell.Children.Add(actions); return Surface(cell, variant.Flag("is_canonical"));
    }
    private async Task RunAsync(Func<Task> action)
    {
        if (busy || !Active) return; busy = true; IsEnabled = false;
        string? error = null;
        try { await action(); }
        catch (Exception ex) { error = ex.Message; }
        finally { busy = false; IsEnabled = true; }
        if (!Active) return;
        view.InvalidateSceneDependents(); await ReloadAsync(); if (error != null) Message(error);
    }
    internal Task ChangeAsync(string path, HttpMethod method, object? payload = null) => RunAsync(async () => { await view.ApiSendOptional(path, method, payload); });
    private Task CanonicalAsync(JsonElement scene, JsonElement reference) => RunAsync(async () =>
    {
        var path = $"{Base}/{scene.Text("id")}/references";
        await view.ApiSendOptional(path + "/" + reference.Text("asset_id"), HttpMethod.Delete);
        try { await view.ApiSend(path, HttpMethod.Post, new { asset_id = reference.Text("asset_id"), role = reference.Text("role", "main"), is_canonical = true }); }
        catch (Exception ex) { throw new InvalidOperationException("原参考已解除，重新绑定失败，请上传并重新绑定：" + ex.Message, ex); }
    });
    private Task UploadAsync(JsonElement scene, string? variant = null)
    {
        var picker = new OpenFileDialog { Filter = "图片|*.png;*.jpg;*.jpeg;*.webp", Title = variant == null ? "上传场景参考图" : "绑定变体图" };
        if (picker.ShowDialog(view.WindowHost()) != true) return Task.CompletedTask;
        return RunAsync(async () =>
        {
            var file = await view.UploadAsset("SCENE_REFERENCE", picker.FileName);
            if (Active) view.assets.Add(AssetItem.From(file));
            var path = $"{Base}/{scene.Text("id")}" + (variant == null ? "" : $"/variants/{variant}") + "/references";
            try { await view.ApiSend(path, HttpMethod.Post, new { asset_id = file.Text("id"), role = "main", is_canonical = variant == null && scene.Array("references").Count == 0 }); }
            catch (Exception ex) { throw new InvalidOperationException("图片已上传，但绑定失败：" + ex.Message, ex); }
        });
    }
    private async Task ArchiveAsync(JsonElement scene)
    {
        if (Deleted(scene)) { await ChangeAsync($"{Base}/{scene.Text("id")}/restore", HttpMethod.Post); return; }
        busy = true; IsEnabled = false; Message("正在确认剧本引用…", false);
        var count = await CountBindingsAsync(scene.Text("id"));
        busy = false; IsEnabled = true;
        if (!Active) return;
        feedback.Children.Clear();
        var references = count == null ? "无法确认引用数量。相关剧本场景将失去场景参考消费，地点文本仍保留。"
            : count > 0 ? $"当前项目中有 {count} 个剧本场景绑定了该资产。归档后它们会失去场景参考消费，地点文本仍保留。"
            : "当前已加载的剧本中没有发现绑定。";
        if (MessageBox.Show(view.WindowHost(), $"归档场景“{scene.Text("name")}”？\n\n{references}\n之后可通过“显示已归档”恢复。", "归档场景", MessageBoxButton.YesNo, MessageBoxImage.Warning) == MessageBoxResult.Yes)
            await ChangeAsync($"{Base}/{scene.Text("id")}", HttpMethod.Delete);
    }
    internal async Task<int?> CountBindingsAsync(string sceneId)
    {
        try
        {
            var chapters = await view.ApiSend($"projects/{project}/chapters");
            int count = 0;
            foreach (var chapter in chapters.EnumerateArray())
            {
                if (!Active) return null;
                var script = await view.ApiSend($"chapters/{chapter.Text("id")}/script");
                count += script.Array("scenes").Count(s => s.Text("scene_asset_id") == sceneId);
            }
            return count;
        }
        // Without a typed HTTP status on ApiClient errors, a missing script cannot
        // safely be distinguished from an unavailable service: never report a false zero.
        catch (Exception) { return null; }
    }
    private void EditScene(JsonElement? scene)
    {
        var editor = new SceneEditor(scene, false, async payload =>
        {
            if (!Active) throw new InvalidOperationException("页面已切换，请重新打开编辑器。");
            var data = await view.ApiSend(scene == null ? Base : $"{Base}/{scene.Value.Text("id")}", scene == null ? HttpMethod.Post : HttpMethod.Patch, payload);
            if (Active) { selected = data.Text("id"); view.InvalidateSceneDependents(); await ReloadAsync(); }
        }); editor.Owner = view.WindowHost(); editor.ShowDialog();
    }
    private void EditVariant(JsonElement scene, JsonElement? variant)
    {
        var editor = new SceneEditor(variant, true, async payload =>
        {
            if (!Active) throw new InvalidOperationException("页面已切换，请重新打开编辑器。");
            var path = $"{Base}/{scene.Text("id")}/variants" + (variant == null ? "" : "/" + variant.Value.Text("id"));
            await view.ApiSend(path, variant == null ? HttpMethod.Post : HttpMethod.Patch, payload); if (Active) { view.InvalidateSceneDependents(); await ReloadAsync(); }
        }); editor.Owner = view.WindowHost(); editor.ShowDialog();
    }
}
