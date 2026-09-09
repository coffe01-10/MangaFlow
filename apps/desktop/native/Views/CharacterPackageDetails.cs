using System.Net.Http;
using System.Text.Json;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;
using Microsoft.Win32;
using MangaFlow.Native.Controls;

namespace MangaFlow.Native.Views;

internal sealed partial class CharacterPackagePane
{
    private string PackagePath => string.Format(Base, view.ProjectIdValue, character.Id);
    private void RenderCompleteness(JsonElement version)
    {
        var completeness = version.Element("completeness");
        if (completeness.ValueKind != JsonValueKind.Object) completeness = package.Element("completeness");
        if (completeness.ValueKind != JsonValueKind.Object) { content.Children.Add(Kit.Caption("完整度暂不可用")); return; }
        var score = Math.Clamp(completeness.Number("score"), 0, 100);
        var row = new StackPanel { Orientation = Orientation.Horizontal, Margin = new Thickness(0, 12, 0, 8) };
        var progress = new ProgressBar { Value = score, Maximum = 100, Width = 256, Height = 8, VerticalAlignment = VerticalAlignment.Center, Foreground = AssetPageUi.Brush("Success") };
        System.Windows.Automation.AutomationProperties.SetName(progress, "角色包完整度");
        row.Children.Add(progress); row.Children.Add(new TextBlock { Text = $"{score}%", Margin = new Thickness(9, 0, 0, 0) }); content.Children.Add(row);
        content.Children.Add(Kit.Caption($"V{version.Number("version_number")} 完整度（发布前补全引导，不阻断发布）"));
        foreach (var missing in completeness.Array("missing")) content.Children.Add(new TextBlock { Text = "ⓘ " + missing.Text("message") + "  " + missing.Text("suggestion"), Foreground = AssetPageUi.Brush("Warning"), FontSize = 12, Margin = new Thickness(0, 4, 0, 0), TextWrapping = TextWrapping.Wrap });
    }
    private void RenderMatrix(JsonElement draft)
    {
        matrix.Children.Clear();
        var editable = draft.Text("status") == "DRAFT";
        matrix.Children.Add(Title("封面图"));
        matrix.Children.Add(Slot(draft, "cover", "", "封面图", editable));
        matrix.Children.Add(Title("多角度视角矩阵"));
        matrix.Children.Add(Kit.Caption("正面 15 · 侧面 10 · 背面 10 · 3/4 侧 5 分"));
        var views = new TilePanel { MinimumTileWidth = 135, MaximumColumns = 4, Gap = 10, Margin = new Thickness(0, 10, 0, 10) };
        foreach (var (role, label) in new[] { ("front", "正面（主视）"), ("side", "右侧面"), ("back", "背面"), ("three_quarter", "3/4 侧面") }) views.Children.Add(Slot(draft, role, "", label, editable));
        matrix.Children.Add(views);
        matrix.Children.Add(Kit.Caption("换绑槽位前需先解绑；同一张图可同时作为封面与视图（正面优先参与默认继承）。"));
        matrix.Children.Add(Title("核心表情集"));
        matrix.Children.Add(Kit.Caption("每个表情标签 5 分，最多计 4 个（推荐 neutral / joy / anger / sorrow）"));
        var expressions = new TilePanel { MinimumTileWidth = 135, MaximumColumns = 4, Gap = 10, Margin = new Thickness(0, 10, 0, 10) };
        foreach (var reference in draft.Array("references").Where(r => r.Text("role") == "expression")) expressions.Children.Add(Slot(draft, "expression", reference.Text("label"), reference.Text("label"), editable));
        matrix.Children.Add(expressions);
        if (editable)
        {
            var label = new TextBox { MinHeight = 40 };
            var entry = new StackPanel(); entry.Children.Add(AssetPageUi.Input(label, "表情标签，例如 neutral", "表情标签"));
            var choose = AssetPicker(); entry.Children.Add(choose);
            var bind = Kit.Act("绑定表情参考", async (_, _) => { if (string.IsNullOrWhiteSpace(label.Text)) { view.Notify("请填写表情标签。"); return; } if (choose.SelectedItem is ComboBoxItem { Tag: string id } && id.Length > 0) await Bind(draft, "expression", label.Text.Trim(), id); }, "Outline");
            entry.Children.Add(bind);
            entry.Children.Add(Kit.Act("上传表情参考", async (_, _) => { if (string.IsNullOrWhiteSpace(label.Text)) { view.Notify("请填写表情标签。"); return; } await UploadSlot(draft, "expression", label.Text.Trim()); }, "Ghost"));
            matrix.Children.Add(entry);
        }
        RenderOutfits(draft, editable);
    }
    private static TextBlock Title(string text) => new() { Text = text, FontSize = 16, FontWeight = FontWeights.Bold, Margin = new Thickness(0, 20, 0, 12) };
    private ComboBox AssetPicker()
    {
        var picker = new ComboBox { MinHeight = 40, Margin = new Thickness(0, 8, 0, 0) };
        picker.Items.Add(new ComboBoxItem { Content = "绑定已有素材…", Tag = "" });
        foreach (var asset in view.assets.Where(a => a.Kind == "CHARACTER_REFERENCE")) picker.Items.Add(new ComboBoxItem { Content = asset.Name, Tag = asset.Id });
        picker.SelectedIndex = 0; return picker;
    }
    private Border Slot(JsonElement draft, string role, string label, string title, bool editable)
    {
        var reference = draft.Array("references").FirstOrDefault(r => r.Text("role") == role && r.Text("label") == label);
        var bound = reference.ValueKind == JsonValueKind.Object;
        var panel = new StackPanel();
        var image = new Border { Height = role == "cover" ? 96 : 120, BorderBrush = AssetPageUi.Brush("Line"), BorderThickness = new Thickness(1), Background = AssetPageUi.Brush("Surface") };
        if (bound) image.Child = new ImageBox { SourceUrl = view.OriginFor($"assets/{reference.Text("asset_id")}/thumbnail/640") };
        else image.Child = new TextBlock { Text = role == "cover" ? "未设置封面" : "＋", HorizontalAlignment = HorizontalAlignment.Center, VerticalAlignment = VerticalAlignment.Center, FontSize = role == "cover" ? 12 : 26, Foreground = AssetPageUi.Brush("Muted") };
        panel.Children.Add(image); panel.Children.Add(new TextBlock { Text = title, FontWeight = FontWeights.Bold, Margin = new Thickness(0, 8, 0, 5) });
        panel.Children.Add(Kit.Caption(bound ? "已绑定" : "空缺"));
        if (editable)
        {
            if (bound) panel.Children.Add(Kit.Act("解绑", async (_, _) => await Change(async () => { await view.ApiSendOptional($"{PackagePath}/versions/{draft.Text("id")}/references/{reference.Text("id")}", HttpMethod.Delete, new { version = draft.Number("version") }); }), "Ghost"));
            else
            {
                panel.Children.Add(Kit.Act(role == "cover" ? "上传封面" : "上传", async (_, _) => await UploadSlot(draft, role, label), "Ghost"));
                var picker = AssetPicker(); System.Windows.Automation.AutomationProperties.SetName(picker, "绑定已有素材 " + title);
                picker.SelectionChanged += async (_, _) => { if (picker.SelectedItem is ComboBoxItem { Tag: string id } && id.Length > 0) await Bind(draft, role, label, id); };
                panel.Children.Add(picker);
            }
        }
        if (role == "cover")
        {
            panel.Children.Remove(image); image.Width = image.Height = 96;
            // Cover actions sit beside the preview, as on the web; other views remain cards.
            panel.Children.RemoveAt(0); panel.Children.RemoveAt(0);
            var row = new Grid { ColumnDefinitions = { new ColumnDefinition { Width = new GridLength(110) }, new ColumnDefinition() } };
            var actions = new WrapPanel { VerticalAlignment = VerticalAlignment.Center };
            foreach (var child in panel.Children.Cast<FrameworkElement>().ToArray()) { panel.Children.Remove(child); child.Margin = new Thickness(0, 0, 8, 0); actions.Children.Add(child); }
            row.Children.Add(image); Grid.SetColumn(actions, 1); row.Children.Add(actions);
            return new Border { Child = row };
        }
        return new Border { BorderBrush = AssetPageUi.Brush("Line"), BorderThickness = new Thickness(1), Padding = new Thickness(10), Child = panel };
    }
    private Task Bind(JsonElement draft, string role, string label, string assetId) => Change(async () =>
    {
        if (role == "cover") await view.ApiSend($"{PackagePath}/versions/{draft.Text("id")}/cover", HttpMethod.Put, new { asset_id = assetId, version = draft.Number("version") });
        else await view.ApiSend($"{PackagePath}/versions/{draft.Text("id")}/references", HttpMethod.Post, new { asset_id = assetId, role, label, sort_order = 0, version = draft.Number("version") });
    });
    private async Task UploadSlot(JsonElement draft, string role, string label)
    {
        var picker = new OpenFileDialog { Filter = "图片|*.png;*.jpg;*.jpeg;*.webp" };
        if (picker.ShowDialog(view.WindowHost()) != true) return;
        await Change(async () =>
        {
            var asset = await view.UploadAsset("CHARACTER_REFERENCE", picker.FileName);
            if (!Showing) return;
            if (role == "cover") await view.ApiSend($"{PackagePath}/versions/{draft.Text("id")}/cover", HttpMethod.Put, new { asset_id = asset.Text("id"), version = draft.Number("version") });
            else await view.ApiSend($"{PackagePath}/versions/{draft.Text("id")}/references", HttpMethod.Post, new { asset_id = asset.Text("id"), role, label, sort_order = 0, version = draft.Number("version") });
        });
    }
    private async Task Change(Func<Task> action)
    {
        if (busy || !Showing) return; busy = true; IsEnabled = false;
        try { await action(); if (Showing) await LoadAsync(); }
        catch (Exception ex) { if (Showing) view.Notify(ex.Message); }
        finally { busy = false; IsEnabled = true; }
    }
    private void RenderOutfits(JsonElement draft, bool editable)
    {
        matrix.Children.Add(Title("服装集与默认服装"));
        foreach (var relation in draft.Array("outfits"))
        {
            var id = relation.Text("outfit_id");
            var row = new WrapPanel { Margin = new Thickness(0, 5, 0, 5) };
            row.Children.Add(new TextBlock { Text = (view.outfits.FirstOrDefault(o => o.Id == id)?.Name ?? "服装") + (relation.Flag("is_default") ? " · 默认服装" : ""), VerticalAlignment = VerticalAlignment.Center, Margin = new Thickness(0, 0, 12, 0) });
            if (editable)
            {
                if (!relation.Flag("is_default")) row.Children.Add(Kit.Act("设为默认", async (_, _) => await Change(async () => { await view.ApiSend($"{PackagePath}/versions/{draft.Text("id")}/outfits/{id}", HttpMethod.Patch, new { is_default = true, version = draft.Number("version") }); }), "Outline"));
                row.Children.Add(Kit.Act("解绑服装", async (_, _) => await Change(async () => { await view.ApiSendOptional($"{PackagePath}/versions/{draft.Text("id")}/outfits/{id}", HttpMethod.Delete, new { version = draft.Number("version") }); }), "Ghost"));
            }
            matrix.Children.Add(row);
        }
        if (!editable) return;
        var picker = new ComboBox { MinHeight = 40 };
        picker.Items.Add(new ComboBoxItem { Content = "选择要关联的服装…", Tag = "" });
        foreach (var outfit in view.outfits.Where(o => o.CharacterId == character.Id && !draft.Array("outfits").Any(r => r.Text("outfit_id") == o.Id))) picker.Items.Add(new ComboBoxItem { Content = outfit.Name, Tag = outfit.Id });
        picker.SelectedIndex = 0;
        picker.SelectionChanged += async (_, _) => { if (picker.SelectedItem is ComboBoxItem { Tag: string id } && id.Length > 0) await Change(async () => { await view.ApiSend($"{PackagePath}/versions/{draft.Text("id")}/outfits", HttpMethod.Post, new { outfit_id = id, is_default = false, sort_order = draft.Array("outfits").Count, version = draft.Number("version") }); }); };
        matrix.Children.Add(picker);
    }
    internal string DescribeChanges(string block, JsonElement diff)
    {
        var lines = new List<string>();
        string Field(string key) => key switch { "age_appearance" => "年龄段外观", "gender" => "性别", "personality" => "核心性格", "identity_notes" => "身份备注", "hair" => "发型", "hair_color" => "发色", "face" => "面部", "eyes" => "瞳色", "body" => "体型", "distinguishing_marks" => "标识性特征", _ => key };
        string Asset(string id) => id.Length == 0 ? "未绑定" : view.assets.FirstOrDefault(a => a.Id == id)?.Name ?? "素材已不可用";
        foreach (var (key, label) in new[] { ("added", "新增"), ("removed", "移除"), ("changed", "修改") })
        {
            var values = diff.Element(key);
            if (values.ValueKind == JsonValueKind.Object)
                foreach (var field in values.EnumerateObject()) lines.Add($"{label} · {Field(field.Name)}：{field.Value}");
            else if (values.ValueKind == JsonValueKind.Array)
                foreach (var item in values.EnumerateArray())
                {
                    if (item.ValueKind == JsonValueKind.String) lines.Add($"{label} · {item.GetString()}");
                    else if (block is "identity_spec" or "visual_spec") lines.Add($"{label} · {Field(item.Text("field"))}：{item.Text("base_value", "未填写")} → {item.Text("target_value", "未填写")}");
                    else if (block == "references") lines.Add($"{label} · {Labels.Map(Labels.PackageRole, item.Text("role"))} {item.Text("label")}：" + (key == "changed" ? Asset(item.Text("base_asset_id")) + " → " + Asset(item.Text("target_asset_id")) : Asset(item.Text("asset_id"))));
                    else if (block == "outfits") lines.Add($"{label} · {view.outfits.FirstOrDefault(o => o.Id == item.Text("outfit_id"))?.Name ?? "服装已不可用"}" + (item.Flag("is_default") ? "（默认服装）" : ""));
                }
        }
        return lines.Count == 0 ? "没有变化" : string.Join("\n", lines);
    }

    private async Task ShowHistory()
    {
        var rows = package.Array("versions").OrderBy(v => v.Number("version_number")).ToList(); if (rows.Count < 2) return;
        var content = new StackPanel { Margin = new Thickness(24) };
        var from = new ComboBox { MinHeight = 42 }; var to = new ComboBox { MinHeight = 42 };
        foreach (var v in rows) { from.Items.Add(new ComboBoxItem { Content = $"V{v.Number("version_number")}", Tag = v.Text("id") }); to.Items.Add(new ComboBoxItem { Content = $"V{v.Number("version_number")}", Tag = v.Text("id") }); }
        from.SelectedIndex = 0; to.SelectedIndex = rows.Count - 1;
        content.Children.Add(Kit.Caption("比较基准版本")); content.Children.Add(from); content.Children.Add(Kit.Caption("目标版本")); content.Children.Add(to);
        var result = new StackPanel { Margin = new Thickness(0, 18, 0, 0) };
        async Task Compare()
        {
            result.Children.Clear(); result.Children.Add(Kit.Caption("正在读取差异…"));
            try
            {
                var data = await view.ApiSend($"{PackagePath}/diff?base_version_id={Uri.EscapeDataString((string)((ComboBoxItem)from.SelectedItem).Tag)}&target_version_id={Uri.EscapeDataString((string)((ComboBoxItem)to.SelectedItem).Tag)}");
                result.Children.Clear();
                foreach (var (key, label) in new[] { ("identity_spec", "身份锚点"), ("visual_spec", "视觉规格"), ("negative_constraints", "负面约束"), ("references", "参考图"), ("outfits", "服装集") })
                {
                    result.Children.Add(Title(label));
                    result.Children.Add(new TextBlock { Text = DescribeChanges(key, data.Element(key)), FontSize = 13, TextWrapping = TextWrapping.Wrap, Foreground = AssetPageUi.Brush("Muted") });
                }
            }
            catch (Exception ex) { result.Children.Clear(); result.Children.Add(Kit.Caption(ex.Message)); }
        }
        content.Children.Add(Kit.Act("比较所选版本", async (_, _) => await Compare(), "Outline")); content.Children.Add(result);
        var dialog = new Window { Owner = view.WindowHost(), Title = "角色模型包 · 对比历史", Width = 720, Height = 720, Content = new ScrollViewer { Content = content, VerticalScrollBarVisibility = ScrollBarVisibility.Auto }, WindowStartupLocation = WindowStartupLocation.CenterOwner };
        await Compare(); dialog.ShowDialog();
    }
}
