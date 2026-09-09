using System.Net.Http;
using System.Text.Json;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;
using Microsoft.Win32;
using MangaFlow.Native.Controls;

namespace MangaFlow.Native.Views;

/// <summary>
/// Character model package workspace (V02-23B native port): draft spec editor,
/// cover + four-view matrix, expression set, completeness, versions and publish.
/// </summary>
internal sealed partial class CharacterPackagePane : Border
{
    private const string Base = "projects/{0}/characters/{1}/package";

    private readonly AssetsView view;
    private readonly CharacterItem character;
    private JsonElement package;
    private readonly int epoch;
    private bool busy;
    private readonly StackPanel content = new();
    // Fresh on every RenderDraft(): these sit nested inside FieldGrid cards, so a
    // reused instance would still belong to the previous (detached) grid and throw.
    private TextBox age = null!;
    private TextBox gender = null!;
    private TextBox personality = null!;
    private TextBox identityNotes = null!;
    private TextBox hair = null!;
    private TextBox hairColor = null!;
    private TextBox face = null!;
    private TextBox eyes = null!;
    private TextBox body = null!;
    private TextBox marks = null!;
    private TextBox negative = null!;
    private readonly StackPanel versions = new();
    private readonly StackPanel matrix = new();
    // Async guard: the pane is only allowed to paint/notify while it is still the
    // attached package view for this character. Internal tab switches and package
    // list selections detach the pane WITHOUT bumping the parent view's epoch.
    private bool Showing => view.IsCurrent(epoch) && Parent != null;

    public CharacterPackagePane(AssetsView view, CharacterItem character)
    {
        this.view = view;
        this.character = character;
        epoch = view.Epoch;
        Margin = new Thickness(0, 14, 0, 0);
        Style = (Style)Application.Current.FindResource("Card");
        Padding = new Thickness(18);
        Child = content;
        _ = LoadAsync();
    }

    private async Task<JsonElement?> GetAsync(string path) =>
        await view.ApiSendOptional(path);

    private async Task LoadAsync()
    {
        content.Children.Clear();
        var spinner = new Spinner { Size = 16, HorizontalAlignment = HorizontalAlignment.Left };
        content.Children.Add(spinner);
        try
        {
            var loaded = await GetAsync(string.Format(Base, view.ProjectIdValue, character.Id));
            // Epoch only: during the constructor the pane is not parented yet, so the
            // first load must not depend on attachment. Late repaints of a detached
            // pane are invisible and harmless; user-facing notifies use Showing.
            if (!view.IsCurrent(epoch)) return;
            package = loaded is { ValueKind: JsonValueKind.Object } value ? value : default;
            if (package.ValueKind == JsonValueKind.Undefined)
            {
                RenderCreateEntry();
                return;
            }
            Render();
        }
        catch (Exception error)
        {
            if (!Showing) return;
            content.Children.Clear();
            content.Children.Add(Kit.Caption($"角色模型包暂不可用：{error.Message}"));
        }
    }

    private void RenderCreateEntry()
    {
        content.Children.Clear();
        content.Children.Add(new TextBlock { Text = "PACKAGE DETAIL", FontSize = 13 });
        content.Children.Add(Kit.Caption("未启用角色模型包（沿用人物参考图路径）。创建后可维护四视图矩阵、表情集与默认服装，并发布不可变版本用于生成。"));
        var create = Kit.Act("创建角色模型包", async (_, _) =>
        {
            try
            {
                await view.ApiSend(string.Format(Base, view.ProjectIdValue, character.Id), HttpMethod.Post, new { });
                await LoadAsync();
            }
            catch (Exception error) { view.Notify("创建失败：" + error.Message); }
        }, "InkButton");
        create.Margin = new Thickness(0, 10, 0, 0);
        create.HorizontalAlignment = HorizontalAlignment.Left;
        content.Children.Add(create);
    }

    private JsonElement? Draft()
    {
        foreach (var version in package.Array("versions"))
            if (version.Text("status") == "DRAFT") return version;
        return null;
    }

    // Web §9.2 parity: with no DRAFT, the frozen view shows the published pointer,
    // falling back to the newest locked version when no published pointer exists.
    private JsonElement FrozenVersion()
    {
        var versions = package.Array("versions");
        var published = versions.FirstOrDefault(v => v.Text("id") == package.Text("published_version_id"));
        if (published.ValueKind == JsonValueKind.Object) return published;
        return versions.FirstOrDefault(v => v.Text("status") != "DRAFT");
    }

    private void Render()
    {
        content.Children.Clear();
        var header = new DockPanel { Margin = new Thickness(0, 0, 0, 10) };
        var actions = new WrapPanel { HorizontalAlignment = HorizontalAlignment.Right };
        var draft = Draft();
        {
            var derive = Kit.Act("派生新版本", async (_, _) =>
            {
                try
                {
                    var published = package.Array("versions").FirstOrDefault(v => v.Text("id") == package.Text("published_version_id"));
                    await view.ApiSend(string.Format(Base, view.ProjectIdValue, character.Id) + "/versions", HttpMethod.Post,
                        new { base_version_id = published.ValueKind == JsonValueKind.Object ? published.Text("id") : (string?)null });
                    await LoadAsync();
                }
                catch (Exception error) { view.Notify("派生失败：" + error.Message); }
            }, "Compact");
            derive.IsEnabled = draft == null;
            derive.MinHeight = 44;
            derive.ToolTip = "从已发布版本派生新的可编辑草稿";
            actions.Children.Add(derive);
        }
        var compare = Kit.Act("对比历史", async (_, _) => await ShowHistory(), "Outline");
        compare.IsEnabled = package.Array("versions").Count > 1; compare.Margin = new Thickness(8, 0, 0, 0); actions.Children.Add(compare);
        var archive = Kit.Act(package.Text("status") == "ARCHIVED" ? "恢复角色包" : "归档角色包", async (_, _) =>
        {
            var message = package.Text("status") == "ARCHIVED"
                ? "恢复角色模型包？该角色将重新进入生成默认继承。"
                : "归档角色模型包后，该角色将退出生成默认继承，既有分镜与候选不受影响。确认归档？";
            if (MessageBox.Show(view.WindowHost(), message, "角色模型包", MessageBoxButton.YesNo, MessageBoxImage.Question) != MessageBoxResult.Yes) return;
            try
            {
                var action = package.Text("status") == "ARCHIVED" ? "restore" : "archive";
                await view.ApiSend($"{string.Format(Base, view.ProjectIdValue, character.Id)}/{action}", HttpMethod.Post);
                await LoadAsync();
            }
            catch (Exception error) { view.Notify(error.Message); }
        }, "Ghost");
        archive.Margin = new Thickness(8, 0, 0, 0);
        actions.Children.Add(archive);
        DockPanel.SetDock(actions, Dock.Right);
        header.Children.Add(actions);
        var title = new StackPanel();
        title.Children.Add(new TextBlock { Text = "PACKAGE DETAIL", FontSize = 13 });
        title.Children.Add(new TextBlock
        {
            Text = $"{character.PrimaryName} 的角色模型包",
            FontFamily = (FontFamily)Application.Current.FindResource("Serif"), FontSize = 17, FontWeight = FontWeights.Bold,
        });
        title.Children.Add(new TextBlock
        {
            Text = package.Text("status") == "ARCHIVED" ? "已归档 · 退出默认继承，Character 不受影响" : "启用中 · 已发布版本进入默认继承",
            Style = (Style)Application.Current.FindResource("Micro"),
        });
        header.Children.Clear();
        content.Children.Add(new PageHeading(title, actions) { Margin = new Thickness(0, 0, 0, 10) });
        RenderCompleteness(draft ?? FrozenVersion());

        if (draft is { } current)
        {
            RenderDraft(current);
        }
        else
        {
            content.Children.Add(Kit.Caption("当前版本已冻结，只读查看；如需调整，请先派生新版本。"));
            var frozen = FrozenVersion();
            if (frozen.ValueKind == JsonValueKind.Object)
            {
                RenderFrozenSpec(frozen);
                content.Children.Add(matrix); RenderMatrix(frozen);
            }
        }
        content.Children.Add(new TextBlock { Text = "版本历史", Style = (Style)Application.Current.FindResource("SectionIndex"), Margin = new Thickness(0, 14, 0, 6) });
        content.Children.Add(versions);
        RenderVersions();
    }

    private static readonly (string Key, string Label)[] SpecFields =
    [
        ("age_appearance", "年龄段外观"), ("gender", "性别"), ("personality", "核心性格"), ("identity_notes", "身份备注"),
        ("hair", "发型"), ("hair_color", "发色"), ("face", "面部"), ("eyes", "瞳色"), ("body", "体型"), ("distinguishing_marks", "标识性特征"),
    ];

    /// <summary>
    /// Read-only spec readout for a frozen version, mirroring the web's
    /// FrozenSpecReadout: identity + visual anchors and negative constraints come
    /// from THAT version's spec_snapshot — never from the package's editable
    /// working spec, which may already have drifted to a newer draft.
    /// </summary>
    private void RenderFrozenSpec(JsonElement version)
    {
        var header = new StackPanel { Margin = new Thickness(0, 10, 0, 8) };
        header.Children.Add(new TextBlock
        {
            Text = $"已冻结版本 V{version.Number("version_number")} · {Labels.Map(Labels.PackageVersionStatus, version.Text("status"))} · 只读",
            FontWeight = FontWeights.Bold,
        });
        header.Children.Add(Kit.Caption("发布版本冻结不可编辑；如需修改请派生新版本。"));
        content.Children.Add(new Border
        {
            BorderBrush = AssetPageUi.Brush("Line"), BorderThickness = new Thickness(1), Background = AssetPageUi.Brush("Surface"),
            Padding = new Thickness(14), Margin = new Thickness(0, 6, 0, 10), Child = header,
        });
        var snapshot = version.Element("spec_snapshot");
        var rows = new List<(string Label, string Value)>();
        foreach (var (key, label) in SpecFields)
        {
            var value = snapshot.Element("identity_spec").Text(key);
            if (value.Length == 0) value = snapshot.Element("visual_spec").Text(key);
            if (value.Length > 0) rows.Add((label, value));
        }
        // Unknown/extra snapshot keys still display (raw key as label), like the web.
        foreach (var block in new[] { snapshot.Element("identity_spec"), snapshot.Element("visual_spec") })
            if (block.ValueKind == JsonValueKind.Object)
                foreach (var property in block.EnumerateObject())
                    if (property.Value.ToString().Length > 0 && !SpecFields.Any(f => f.Key == property.Name))
                        rows.Add((property.Name, property.Value.ToString()));
        var readout = new StackPanel();
        if (rows.Count == 0)
        {
            readout.Children.Add(Kit.Caption("该版本未填写规格文字。"));
        }
        else
        {
            var grid = new Grid();
            for (var i = 0; i < 2; i++) grid.ColumnDefinitions.Add(new ColumnDefinition());
            for (var i = 0; i < rows.Count; i++)
            {
                var cell = new StackPanel { Margin = new Thickness(0, 0, 12, 8) };
                cell.Children.Add(new TextBlock { Text = rows[i].Label, FontSize = 12, FontWeight = FontWeights.Bold });
                cell.Children.Add(new TextBlock { Text = rows[i].Value, FontSize = 12.5, TextWrapping = TextWrapping.Wrap, Foreground = AssetPageUi.Brush("Muted") });
                Grid.SetRow(cell, i / 2);
                Grid.SetColumn(cell, i % 2);
                if (grid.RowDefinitions.Count <= i / 2) grid.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
                grid.Children.Add(cell);
            }
            readout.Children.Add(grid);
        }
        var constraints = snapshot.Array("negative_constraints").Select(c => c.ToString()).Where(c => c.Length > 0).ToList();
        if (constraints.Count > 0)
            readout.Children.Add(Kit.Caption("负面约束：" + string.Join("；", constraints)));
        content.Children.Add(readout);
    }

    private void RenderDraft(JsonElement draft)
    {
        var snapshot = package;
        var identity = snapshot.Element("identity_spec");
        var visual = snapshot.Element("visual_spec");
        age = new TextBox { Text = identity.Text("age_appearance") };
        gender = new TextBox { Text = identity.Text("gender") };
        personality = new TextBox { Text = identity.Text("personality") };
        identityNotes = new TextBox { Text = identity.Text("identity_notes"), AcceptsReturn = true, MinHeight = 44 };
        hair = new TextBox { Text = visual.Text("hair") };
        hairColor = new TextBox { Text = visual.Text("hair_color") };
        face = new TextBox { Text = visual.Text("face") };
        eyes = new TextBox { Text = visual.Text("eyes") };
        body = new TextBox { Text = visual.Text("body") };
        marks = new TextBox { Text = visual.Text("distinguishing_marks") };
        negative = new TextBox { Text = string.Join("\n", snapshot.Array("negative_constraints")), AcceptsReturn = true, MinHeight = 64 };

        var headerRow = new DockPanel { Margin = new Thickness(0, 8, 0, 8) };
        var actions = new StackPanel { Orientation = Orientation.Horizontal };
        var publish = Kit.Act($"发布当前版本 V{draft.Number("version_number")}", async (_, _) => await Publish(draft), "CompactInk");
        publish.ToolTip = "发布前至少绑定 1 张参考图（完整度不设门槛）";
        actions.Children.Add(publish);
        if (package.Array("versions").Count > 1)
        {
            var delete = Kit.Act("删除草稿", async (_, _) => await DeleteDraft(draft), "CompactDanger");
            delete.Margin = new Thickness(8, 0, 0, 0);
            actions.Children.Add(delete);
        }
        DockPanel.SetDock(actions, Dock.Right);
        headerRow.Children.Add(actions);
        headerRow.Children.Add(new TextBlock
        {
            Text = $"草稿 V{draft.Number("version_number")} · DRAFT 可编辑",
            FontWeight = FontWeights.Bold,
        });
        content.Children.Add(headerRow);

        var identityPanel = FieldGrid("身份锚点（每项 5 分）", [
            ("年龄段外观", age, "例如：17 岁高中生"), ("性别", gender, "例如：女"),
            ("核心性格", personality, "例如：冷静、话少、观察力强"), ("身份备注", identityNotes, "剧本中需要保持一致的身份设定"),
        ]);
        content.Children.Add(identityPanel);
        var visualPanel = FieldGrid("视觉规格", [
            ("发型", hair, "例如：黑色碎短发"), ("发色", hairColor, "例如：深黑"),
            ("面部", face, "例如：右耳银色耳钉"), ("瞳色", eyes, "例如：琥珀色"),
            ("体型", body, "例如：瘦高"), ("标识性特征", marks, "伤疤 / 眼镜 / 胎记…"),
        ]);
        content.Children.Add(visualPanel);
        content.Children.Add(new TextBlock { Text = "负面约束与防崩词（每行一条，≤20 条）", Style = (Style)Application.Current.FindResource("FieldLabel"), Margin = new Thickness(0, 8, 0, 6) });
        content.Children.Add(negative);
        var save = Kit.Act("保存草稿规格", async (_, _) => await SaveSpec(draft), "CompactInk");
        save.Margin = new Thickness(0, 10, 0, 0);
        save.HorizontalAlignment = HorizontalAlignment.Left;
        content.Children.Add(save);
        content.Children.Add(new TextBlock
        {
            Text = "规格属于包工作集，发布时冻结进版本快照。",
            Style = (Style)Application.Current.FindResource("Micro"), Margin = new Thickness(0, 6, 0, 0),
        });


        content.Children.Add(matrix);
        RenderMatrix(draft);
    }

    private static StackPanel FieldGrid(string title, (string Label, TextBox Box, string Hint)[] fields)
    {
        var panel = new StackPanel { Margin = new Thickness(0, 8, 0, 0) };
        panel.Children.Add(new TextBlock { Text = title, Style = (Style)Application.Current.FindResource("FieldLabel") });
        var grid = new Grid();
        for (var i = 0; i < 2; i++) grid.ColumnDefinitions.Add(new ColumnDefinition());
        for (var i = 0; i < fields.Length; i++)
        {
            var (label, box, hint) = fields[i];
            var cell = new StackPanel { Margin = new Thickness(0, 0, 12, 8) };
            cell.Children.Add(new TextBlock { Text = label, FontSize = 12, FontWeight = FontWeights.Bold });
            box.ToolTip = hint;
            cell.Children.Add(box);
            Grid.SetRow(cell, i / 2);
            Grid.SetColumn(cell, i % 2);
            if (grid.RowDefinitions.Count <= i / 2) grid.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
            grid.Children.Add(cell);
        }
        panel.Children.Add(grid);
        return panel;
    }

    private async Task SaveSpec(JsonElement draft)
    {
        if (busy || !Showing) return; busy = true; IsEnabled = false;
        try
        {
            await view.ApiSend(string.Format(Base, view.ProjectIdValue, character.Id), HttpMethod.Patch, new
            {
                identity_spec = new
                {
                    age_appearance = age.Text, gender = gender.Text,
                    personality = personality.Text, identity_notes = identityNotes.Text,
                },
                visual_spec = new
                {
                    hair = hair.Text, hair_color = hairColor.Text, face = face.Text,
                    eyes = eyes.Text, body = body.Text, distinguishing_marks = marks.Text,
                },
                negative_constraints = negative.Text.Split('\n', StringSplitOptions.RemoveEmptyEntries)
                    .Select(l => l.Trim()).Where(l => l.Length > 0).Take(20).ToArray(),
                version = package.Number("version"),
            });
            if (!Showing) return;
            view.Notify("草稿规格已保存。");
            await LoadAsync();
        }
        catch (Exception error) { if (Showing) view.Notify("保存失败：" + error.Message); }
        finally { busy = false; IsEnabled = true; }
    }

    private async Task Publish(JsonElement draft)
    {
        if (MessageBox.Show(view.WindowHost(),
                $"发布版本 V{draft.Number("version_number")}？发布后该版本将固化为不可变版本，用于后续分镜与生图；历史候选与已发布版本不受影响。确认发布？",
                "发布版本", MessageBoxButton.YesNo, MessageBoxImage.Question) != MessageBoxResult.Yes) return;
        try
        {
            await view.ApiSend($"{string.Format(Base, view.ProjectIdValue, character.Id)}/versions/{draft.Text("id")}/publish", HttpMethod.Post);
            if (!Showing) return;
            view.Notify("版本已发布。");
            await LoadAsync();
            await view.ReloadAssets();
        }
        catch (Exception error) { if (Showing) view.Notify("发布失败：" + error.Message); }
    }

    private async Task DeleteDraft(JsonElement draft)
    {
        if (MessageBox.Show(view.WindowHost(), $"删除草稿 V{draft.Number("version_number")} 及其矩阵绑定？该操作不可撤销。",
                "删除草稿", MessageBoxButton.YesNo, MessageBoxImage.Warning) != MessageBoxResult.Yes) return;
        try
        {
            await view.ApiSendOptional($"{string.Format(Base, view.ProjectIdValue, character.Id)}/versions/{draft.Text("id")}", HttpMethod.Delete);
            await LoadAsync();
        }
        catch (Exception error) { if (Showing) view.Notify(error.Message); }
    }

    private void RenderVersions()
    {
        versions.Children.Clear();
        var rows = package.Array("versions")
            .Where(v => v.Text("status") != "DRAFT")
            .OrderByDescending(v => v.Number("version_number"))
            .ToList();
        if (rows.Count == 0)
        {
            versions.Children.Add(Kit.Caption("已发布版本不可变；修改只能通过派生新版本。"));
            return;
        }
        versions.Children.Add(Kit.Caption("已发布版本不可变；修改只能通过派生新版本。"));
        foreach (var version in rows)
        {
            var row = new StackPanel { Orientation = Orientation.Horizontal, Margin = new Thickness(0, 6, 0, 6) };
            var isCurrent = version.Text("id") == package.Text("published_version_id");
            var label = $"V{version.Number("version_number")} · {Labels.Map(Labels.PackageVersionStatus, version.Text("status"))}" +
                        (isCurrent ? " · 当前发布版本" : "") +
                        $" · {version.Array("references").Count} 张参考图 · {version.Array("outfits").Count} 套服装";
            row.Children.Add(new TextBlock { Text = label, VerticalAlignment = VerticalAlignment.Center, TextWrapping = TextWrapping.Wrap });
            if (!isCurrent && version.Text("status") != "ARCHIVED")
            {
                var activate = Kit.Act("设为发布版本", async (_, _) =>
                {
                    try
                    {
                        await view.ApiSend($"{string.Format(Base, view.ProjectIdValue, character.Id)}/activate", HttpMethod.Post,
                            new { version_id = version.Text("id"), expected_published_version_id = package.TextOrNull("published_version_id") });
                        await LoadAsync();
                    }
                    catch (Exception error) { view.Notify(error.Message); }
                }, "Compact");
                activate.Margin = new Thickness(12, 0, 0, 0);
                row.Children.Add(activate);
            }
            versions.Children.Add(row);
        }
    }
}
