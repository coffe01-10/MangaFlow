using System.ComponentModel;
using System.Text.Json;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;
using MangaFlow.Native.Controls;

namespace MangaFlow.Native.Views;

/// <summary>Scene and environment forms share the web schema, with errors inside the dialog.</summary>
internal sealed class SceneEditor : Window
{
    private readonly JsonElement? original;
    private readonly bool variant;
    private readonly Func<object, Task> submit;
    private readonly Dictionary<string, TextBox> inputs = new();
    private readonly ComboBox time = SceneWorkspace.Select("时间", ("", "未指定"), ("dawn", "黎明"), ("day", "白天"), ("dusk", "黄昏"), ("night", "夜晚"));
    private readonly ComboBox interior = SceneWorkspace.Select("室内外", ("", "未指定"), ("true", "室内"), ("false", "室外"));
    private readonly CheckBox canonical = new() { Content = "设为默认变体", MinHeight = 40, VerticalContentAlignment = VerticalAlignment.Center };
    private readonly TextBlock error = new() { Foreground = AssetPageUi.Brush("Danger"), TextWrapping = TextWrapping.Wrap, Margin = new Thickness(0, 12, 0, 8) };
    private readonly Button save;
    private readonly StackPanel form = new();
    private bool busy;
    internal SceneEditor(JsonElement? original, bool variant, Func<object, Task> submit)
    {
        this.original = original; this.variant = variant; this.submit = submit;
        Title = variant ? original == null ? "添加环境变体" : "编辑环境变体" : original == null ? "新建场景资产" : "编辑场景资产";
        Width = variant ? 570 : 720; Height = Math.Min(variant ? 660 : 820, SystemParameters.WorkArea.Height - 48);
        MinWidth = 450; MinHeight = 380; WindowStartupLocation = WindowStartupLocation.CenterOwner;
        Background = AssetPageUi.Brush("Paper"); Foreground = AssetPageUi.Brush("Ink"); FontFamily = new FontFamily("Microsoft YaHei UI"); FontSize = 13;
        UseLayoutRounding = true; SnapsToDevicePixels = true;
        TextOptions.SetTextFormattingMode(this, TextFormattingMode.Display); TextOptions.SetTextRenderingMode(this, TextRenderingMode.ClearType); RenderOptions.SetClearTypeHint(this, ClearTypeHint.Enabled);
        var root = new DockPanel { Margin = new Thickness(22) };
        var heading = new Border { BorderBrush = AssetPageUi.Brush("Ink"), BorderThickness = new Thickness(0, 0, 0, 1), Padding = new Thickness(0, 0, 0, 14), Margin = new Thickness(0, 0, 0, 16), Child = new TextBlock { Text = Title, FontFamily = (FontFamily)FindResource("Serif"), FontSize = 23 } };
        DockPanel.SetDock(heading, Dock.Top); root.Children.Add(heading);
        var footer = new StackPanel(); footer.Children.Add(error);
        var buttons = new StackPanel { Orientation = Orientation.Horizontal, HorizontalAlignment = HorizontalAlignment.Right };
        var cancel = Kit.Act("取消", (_, _) => Close(), "Compact"); cancel.Margin = new Thickness(0, 0, 8, 0); buttons.Children.Add(cancel);
        save = Kit.Act(variant ? "保存变体" : "保存", async (_, _) => { if (await SaveAsync()) { DialogResult = true; } }, "CompactInk"); buttons.Children.Add(save);
        footer.Children.Add(buttons); DockPanel.SetDock(footer, Dock.Bottom); root.Children.Add(footer);
        var data = original ?? default;
        form.Children.Add(Input("name", variant ? "变体名称" : "名称", data.Text("name")));
        if (!variant)
        {
            if (original == null) form.Children.Add(Input("location_hint", "来源地点提示", ""));
            else if (data.Text("location_hint").Length > 0) form.Children.Add(new TextBlock { Text = "来源地点（只读）：" + data.Text("location_hint"), Foreground = AssetPageUi.Brush("Muted"), TextWrapping = TextWrapping.Wrap, Margin = new Thickness(0, 0, 0, 10) });
            form.Children.Add(Input("description", "描述", data.Text("description"), true));
        }
        var structured = data.Element(variant ? "structured_overrides" : "structured");
        var grid = new Grid(); grid.ColumnDefinitions.Add(new ColumnDefinition()); grid.ColumnDefinitions.Add(new ColumnDefinition());
        var fieldIndex = 0;
        void Add(FrameworkElement field, bool wide = false)
        {
            if (wide && fieldIndex % 2 == 1) fieldIndex++;
            int row = fieldIndex / 2, column = fieldIndex % 2;
            while (grid.RowDefinitions.Count <= row) grid.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
            Grid.SetRow(field, row); Grid.SetColumn(field, column); if (wide) Grid.SetColumnSpan(field, 2);
            field.Margin = new Thickness(0, 0, !wide && column == 0 ? 10 : 0, 10); grid.Children.Add(field); fieldIndex += wide ? 2 : 1;
        }
        if (!variant)
        {
            Add(Input("place", "地点 / 场所", structured.Text("place")));
            if (structured.ValueKind == JsonValueKind.Object && structured.TryGetProperty("interior", out var room)) Pick(interior, room.ValueKind == JsonValueKind.True ? "true" : room.ValueKind == JsonValueKind.False ? "false" : "");
            Add(SceneWorkspace.Field("室内外", interior));
        }
        Pick(time, structured.Text("time_of_day")); Add(SceneWorkspace.Field("时间", time));
        foreach (var (key, label) in new[] { ("weather", "天气"), ("season", "季节"), ("lighting", "光照") }) Add(Input(key, label, structured.Text(key)));
        if (!variant)
        {
            Add(Input("subareas", "子区域（用逗号分隔）", Join(structured.Array("subareas"))), true);
            Add(Input("fixed_props", "固定物件（用逗号分隔）", Join(structured.Array("fixed_props"))), true);
        }
        Add(Input("dominant", "主色", Join(structured.Element("palette").Array("dominant"))));
        Add(Input("mood", "色调情绪", structured.Element("palette").Text("mood")));
        if (!variant) Add(Input("spatial_relations", "空间关系（每行：起点 > 关系 > 终点）", string.Join("\n", structured.Array("spatial_relations").Select(r => $"{r.Text("from")} > {r.Text("relation")} > {r.Text("to")}")), true), true);
        else { canonical.IsChecked = data.Flag("is_canonical"); Add(canonical, true); }
        form.Children.Add(grid); root.Children.Add(new ScrollViewer { Content = form, VerticalScrollBarVisibility = ScrollBarVisibility.Auto, HorizontalScrollBarVisibility = ScrollBarVisibility.Disabled }); Content = root;
        inputs["name"].TextChanged += (_, _) => save.IsEnabled = !busy && inputs["name"].Text.Trim().Length > 0; save.IsEnabled = inputs["name"].Text.Trim().Length > 0;
        Loaded += (_, _) => inputs["name"].Focus(); Closing += (_, e) => { if (busy) e.Cancel = true; };
        PreviewKeyDown += (_, e) => { if (e.Key == System.Windows.Input.Key.Escape && !busy) { Close(); e.Handled = true; } };
    }
    private static void Pick(ComboBox box, string value) => box.SelectedItem = box.Items.OfType<ComboBoxItem>().FirstOrDefault(i => Equals(i.Tag, value)) ?? box.Items[0];
    private static string Join(IEnumerable<JsonElement> items) => string.Join("，", items.Select(v => v.ToString()));
    internal static string[] Split(string value) => value.Split(['，', ',', '、', '\n', '\r'], StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries);
    internal static string TimeLabel(string value) => value switch { "dawn" => "黎明", "day" => "白天", "dusk" => "黄昏", "night" => "夜晚", _ => "" };
    private FrameworkElement Input(string key, string label, string value, bool multiline = false)
    {
        var input = new TextBox { Text = value, MinHeight = multiline ? 76 : 40, AcceptsReturn = multiline, TextWrapping = multiline ? TextWrapping.Wrap : TextWrapping.NoWrap, VerticalScrollBarVisibility = multiline ? ScrollBarVisibility.Auto : ScrollBarVisibility.Hidden };
        if (key == "name") input.MaxLength = 120;
        if (key == "location_hint") input.MaxLength = 200;
        if (key == "description") input.MaxLength = 8000;
        inputs[key] = input; var field = SceneWorkspace.Field(label, input); field.Margin = new Thickness(0, 0, 0, 10); return field;
    }
    internal object Payload()
    {
        string Text(string key) => inputs[key].Text;
        var structured = new Dictionary<string, object?>
        {
            ["time_of_day"] = SceneWorkspace.Value(time), ["weather"] = Text("weather"), ["season"] = Text("season"), ["lighting"] = Text("lighting"),
            ["palette"] = new { dominant = Split(Text("dominant")), mood = Text("mood") },
        };
        if (!variant)
        {
            structured["place"] = Text("place"); structured["interior"] = SceneWorkspace.Value(interior) switch { "true" => true, "false" => false, _ => (bool?)null };
            structured["subareas"] = Split(Text("subareas")); structured["fixed_props"] = Split(Text("fixed_props"));
            structured["spatial_relations"] = Text("spatial_relations").Split('\n').Select(line => line.Split('>').Select(part => part.Trim()).ToArray())
                .Select(parts => new { from = parts.ElementAtOrDefault(0) ?? "", relation = parts.ElementAtOrDefault(1) ?? "", to = string.Join(" > ", parts.Skip(2)) })
                .Where(relation => relation.from.Length + relation.relation.Length + relation.to.Length > 0).ToArray();
        }
        var result = new Dictionary<string, object?> { ["name"] = Text("name").Trim(), [variant ? "structured_overrides" : "structured"] = structured };
        if (original != null) result["version"] = original.Value.Number("version");
        if (variant) result["is_canonical"] = canonical.IsChecked == true;
        else { result["description"] = Text("description"); if (original == null) result["location_hint"] = Text("location_hint"); }
        return result;
    }
    internal async Task<bool> SaveAsync()
    {
        if (busy || inputs["name"].Text.Trim().Length == 0) return false;
        busy = true; form.IsEnabled = save.IsEnabled = false; error.Text = "";
        try { await submit(Payload()); return true; }
        catch (Exception ex) { error.Text = ex.Message; return false; }
        finally { busy = false; form.IsEnabled = true; save.IsEnabled = inputs["name"].Text.Trim().Length > 0; }
    }
}
