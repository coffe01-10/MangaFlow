using System.Net.Http;
using System.Text.Json;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Controls.Primitives;
using System.Windows.Media;
using MangaFlow.Native.Controls;
using MangaFlow.Native.Services;

namespace MangaFlow.Native.Views;

internal sealed class StyleProductionCard : Border
{
    private readonly StyleWorkspace owner;
    private JsonElement data;
    internal string Id => data.Text("id");
    internal StyleItem Item => StyleItem.From(data);
    private JsonElement Profile => data.Element("profile");
    private readonly TextBlock title = new() { FontSize = 18, FontWeight = FontWeights.Bold };
    private readonly TextBlock status = StyleWorkspace.Caption("");
    private readonly TextBlock badge = StyleWorkspace.Caption("");
    private readonly TextBox atmosphere = new() { Text = "葬礼后的克制情绪、潮湿京都、低饱和但保留人物识别色", AcceptsReturn = true, MinHeight = 80, TextWrapping = TextWrapping.Wrap };
    private readonly StackPanel paletteList = new();
    private readonly StackPanel candidates = new();
    private readonly TextBlock draftState = StyleWorkspace.Caption("");
    private readonly TextBlock paletteState = StyleWorkspace.Caption("");
    private readonly TextBlock testState = StyleWorkspace.Caption("");
    private readonly TextBlock activationHint = StyleWorkspace.Caption("");
    private readonly TextBlock error = new() { Foreground = AssetPageUi.Brush("Danger"), TextWrapping = TextWrapping.Wrap };
    private readonly WrapPanel modes;
    private readonly Button analyze, draft, approvePalette, generate, activate;
    private readonly TilePanel stages = new() { Gap = 0, MinimumTileWidth = 240, MaximumColumns = 4 };
    private readonly List<(TextBox Name, TextBox Value)> paletteRows = [];
    private List<JsonElement> candidateRows = [];
    private string paletteFingerprint = "", pendingBatch = "", batchId = "";
    private int paletteVersion, readRequest;
    private bool busy, reading, paletteDirty, active;
    private bool Attached => owner.Active && Parent != null;
    private bool Color => data.Text("color_mode") == "color";
    internal bool NeedsPoll => data.Text("status") == "ANALYZING" || candidateRows.Any(c => c.Text("status") is "PENDING" or "QUEUED" or "WAITING" or "GENERATING" or "RUNNING") || pendingBatch.Length > 0;

    internal StyleProductionCard(StyleWorkspace owner, JsonElement data)
    {
        this.owner = owner; this.data = data;
        BorderBrush = AssetPageUi.Brush("Line"); BorderThickness = new Thickness(1); Background = AssetPageUi.Brush("Surface"); Padding = new Thickness(16); Margin = new Thickness(0, 0, 0, 14);
        var body = new StackPanel(); var heading = new StackPanel(); heading.Children.Add(badge); heading.Children.Add(title); heading.Children.Add(status);
        modes = StyleWorkspace.Modes(data.Text("color_mode"), ChangeModeAsync); body.Children.Add(new PageHeading(heading, modes));
        analyze = Button("重新分析画面语言", () => RunAsync(async () => { var job = await owner.View.ApiSend($"styles/{Id}/analyze", HttpMethod.Post); owner.Notify(StyleWorkspace.JobLabel(job)); }));
        var tools = new WrapPanel(); tools.Children.Add(analyze);
        tools.Children.Add(Button("重新载入档案", async () =>
        {
            if (paletteDirty && MessageBox.Show(owner.View.WindowHost(), "放弃当前未保存的色板修改，重新读取档案？", "重新载入", MessageBoxButton.YesNo) != MessageBoxResult.Yes) return;
            paletteDirty = false; paletteFingerprint = ""; await owner.ReloadAsync();
        })); body.Children.Add(tools);
        var stage1 = Stage("01 / AI 色板草稿", draftState);
        stage1.Children.Add(StyleWorkspace.Field("章节氛围", atmosphere));
        draft = Button("由默认文字模型提议色板", () => RunAsync(async () => { var job = await owner.View.ApiSend($"styles/{Id}/palette-draft", HttpMethod.Post, new { atmosphere = atmosphere.Text.Trim() }); owner.Notify(StyleWorkspace.JobLabel(job)); })); stage1.Children.Add(draft);
        var stage2 = Stage("02 / 编辑并确认色板", paletteState); stage2.Children.Add(paletteList);
        stage2.Children.Add(Button("＋ 增加色板项", () => { AddPaletteRow("", "", true); return Task.CompletedTask; }));
        approvePalette = Button("确认彩色色板", SavePaletteAsync); stage2.Children.Add(approvePalette);
        var stage3 = Stage("03 / 风格测试图", testState); generate = Button("生成 1K 风格测试图", GenerateAsync); stage3.Children.Add(generate); stage3.Children.Add(candidates);
        var stage4 = Stage("04 / 激活正式风格", StyleWorkspace.Caption("")); stage4.Children.Add(activationHint); activate = Button("激活彩色风格", ActivateAsync, "CompactInk"); stage4.Children.Add(activate);
        body.Children.Add(stages); error.Margin = new Thickness(0, 10, 0, 0); body.Children.Add(error); Child = body;
        SizeChanged += (_, _) => { var columns = ActualWidth < 630 ? 1 : ActualWidth < 1150 ? 2 : 4; if (stages.MaximumColumns != columns) { stages.MaximumColumns = columns; stages.InvalidateMeasure(); } };
        Adopt(data, false); RenderCandidates();
    }
    private StackPanel Stage(string name, TextBlock state)
    {
        var panel = new StackPanel(); panel.Children.Add(new TextBlock { Text = name, FontWeight = FontWeights.Bold, FontSize = 12, Margin = new Thickness(0, 0, 0, 8) });
        state.Margin = new Thickness(0, 0, 0, 12); panel.Children.Add(state); stages.Children.Add(StyleWorkspace.Surface(panel, padding: 14)); return panel;
    }
    /// <summary>?style= deep link: web's deep-link-focus ring (accent border + scroll).</summary>
    internal void MarkDeepLinkFocus()
    {
        BorderBrush = (Brush)FindResource("Accent");
        BorderThickness = new Thickness(2);
        ToolTip = "深链定位的风格档案";
        BringIntoView();
    }
    private Button Button(string name, Func<Task> action, string style = "Compact")
    {
        var button = Kit.Act(name, async (_, _) => { if (!busy && Attached) await action(); }, style);
        button.Margin = new Thickness(0, 10, 0, 8); button.HorizontalAlignment = HorizontalAlignment.Left; return button;
    }
    internal void Adopt(JsonElement next, bool isActive)
    {
        var previousApproval = Profile.Text("test_candidate_id") + Profile.Flag("test_image_approved");
        data = next.Clone(); active = isActive;
        var source = Profile.Flag("palette_confirmed") ? Profile.Element("palette") : Profile.Element("palette_draft");
        if (source.ValueKind != JsonValueKind.Object) source = Profile.Element(Profile.Flag("palette_confirmed") ? "palette_draft" : "palette");
        var fingerprint = source.ValueKind == JsonValueKind.Object ? source.GetRawText() : "{}";
        if (!paletteDirty && fingerprint != paletteFingerprint)
        {
            paletteRows.Clear(); paletteList.Children.Clear();
            if (source.ValueKind == JsonValueKind.Object) foreach (var value in source.EnumerateObject()) AddPaletteRow(value.Name, value.Value.ValueKind == JsonValueKind.String ? value.Value.GetString()! : value.Value.GetRawText(), false);
            paletteFingerprint = fingerprint;
        }
        if (!paletteDirty) paletteVersion = data.Number("version"); UpdateState();
        if (previousApproval != Profile.Text("test_candidate_id") + Profile.Flag("test_image_approved")) RenderCandidates();
    }
    internal void UpdateState()
    {
        title.Text = data.Text("name"); badge.Text = active ? "CURRENT STYLE" : "STYLE PROFILE";
        status.Text = $"{Labels.Map(Labels.StyleStatus, data.Text("status"))} · {Profile.Array("reference_asset_ids").Count} 张参考 · {data.Array("locked_fields").Count} 项锁定";
        BorderBrush = AssetPageUi.Brush(active ? "Success" : "Line");
        foreach (var mode in modes.Children.OfType<ToggleButton>()) mode.IsChecked = Equals(mode.Tag, data.Text("color_mode"));
        draftState.Text = data.Text("status") == "ANALYZING" ? "正在分析" : paletteRows.Count > 0 ? $"{paletteRows.Count} 项" : "尚未生成";
        paletteState.Text = paletteDirty ? "有未保存修改" : Profile.Flag("palette_confirmed") ? "已确认" : "待确认";
        testState.Text = Profile.Flag("test_image_approved") ? "人工已通过" : "尚未通过";
        analyze.IsEnabled = !busy && Profile.Array("reference_asset_ids").Count > 0 && data.Text("status") != "ANALYZING";
        draft.IsEnabled = !busy && Color && data.Text("status") != "ANALYZING" && !paletteDirty; draft.Content = paletteRows.Count > 0 ? "重新提议色板" : "由默认文字模型提议色板";
        approvePalette.IsEnabled = !busy && Color && Palette().Count > 0; approvePalette.Content = Profile.Flag("palette_confirmed") ? "保存色板修改" : "确认彩色色板";
        generate.IsEnabled = !busy && Color && Profile.Flag("palette_confirmed") && !paletteDirty && owner.Model.Length > 0 && !candidateRows.Any(c => c.Text("status") is "PENDING" or "QUEUED" or "WAITING" or "GENERATING" or "RUNNING");
        activate.IsEnabled = !busy && !active;
        var label = !Color ? "先切换为彩色漫画" : paletteDirty ? "先保存色板修改" : paletteRows.Count == 0 ? "先生成色板" : !Profile.Flag("palette_confirmed") ? "先确认色板" : !Profile.Flag("test_image_approved") ? "先通过测试图" : "激活彩色风格";
        activate.Content = active ? "已用于正式页面" : label;
        activationHint.Text = active ? "该档案已经进入正式页面提示词。" : label == "激活彩色风格" ? "色板和测试图均已确认，可以激活为正式风格。" : "还缺：" + label + "。";
    }
    private void MarkPaletteDirty() { if (!paletteDirty) paletteVersion = data.Number("version"); paletteDirty = true; UpdateState(); }
    private void AddPaletteRow(string name, string value, bool dirty)
    {
        var key = new TextBox { Text = name, MinHeight = 38 }; var content = new TextBox { Text = value, MinHeight = 38 };
        System.Windows.Automation.AutomationProperties.SetName(key, "色板项名称"); System.Windows.Automation.AutomationProperties.SetName(content, "色板项颜色或规则");
        var row = new Grid { Margin = new Thickness(0, 0, 0, 7) }; row.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(.8, GridUnitType.Star) }); row.ColumnDefinitions.Add(new ColumnDefinition()); row.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        row.Children.Add(key); content.Margin = new Thickness(5, 0, 5, 0); Grid.SetColumn(content, 1); row.Children.Add(content);
        var remove = Kit.Act("×", (_, _) => { paletteRows.Remove((key, content)); paletteList.Children.Remove(row); MarkPaletteDirty(); }, "Compact"); remove.ToolTip = "删除色板项"; Grid.SetColumn(remove, 2); row.Children.Add(remove);
        paletteRows.Add((key, content)); paletteList.Children.Add(row);
        key.TextChanged += (_, _) => MarkPaletteDirty(); content.TextChanged += (_, _) => MarkPaletteDirty(); if (dirty) MarkPaletteDirty();
    }
    internal Dictionary<string, string> Palette()
    {
        var result = new Dictionary<string, string>(); foreach (var row in paletteRows) if (row.Name.Text.Trim().Length > 0 && row.Value.Text.Trim().Length > 0) result[row.Name.Text.Trim()] = row.Value.Text.Trim(); return result;
    }
    internal Task SavePaletteAsync() => RunAsync(async () =>
    {
        if (Palette().Count == 0) throw new InvalidOperationException("请填写至少一项色板。");
        var result = await owner.View.ApiSend($"styles/{Id}/palette-approve", HttpMethod.Post, new { version = paletteVersion, palette = Palette() });
        paletteDirty = false; Adopt(result, false);
    });
    private Task ChangeModeAsync(string mode)
    {
        if (mode == data.Text("color_mode")) return Task.CompletedTask;
        if (paletteDirty) { error.Text = "请先保存色板修改，再切换档案模式。"; UpdateState(); return Task.CompletedTask; }
        return RunAsync(async () => { var result = await owner.View.ApiSend($"styles/{Id}", HttpMethod.Patch, new { version = data.Number("version"), color_mode = mode }); Adopt(result, false); });
    }
    internal Task GenerateAsync() => RunAsync(async () =>
    {
        if (owner.Model.Length == 0) throw new InvalidOperationException("请先在上方选择本次风格测试模型。");
        if (!Profile.Flag("palette_confirmed") || paletteDirty) throw new InvalidOperationException("请先确认并保存彩色色板。");
        if (pendingBatch.Length == 0) { var batch = await owner.View.ApiSend("asset-generation-batches", HttpMethod.Post, new { target_type = "STYLE", target_id = Id, generation_kind = "STYLE_TEST" }); pendingBatch = batch.Text("id"); }
        await owner.View.ApiSend($"asset-generation-batches/{pendingBatch}/candidates", HttpMethod.Post, new { model_alias = owner.Model, resolution = "1K", variant = "STYLE_TEST", instruction = "" });
        batchId = pendingBatch; pendingBatch = ""; owner.Notify("风格测试任务已提交，候选会在完成后自动更新。");
    });
    private Task ActivateAsync()
    {
        if (!Color) { modes.BringIntoView(); return Task.CompletedTask; }
        if (paletteDirty || !Profile.Flag("palette_confirmed")) { paletteList.BringIntoView(); return Task.CompletedTask; }
        if (paletteRows.Count == 0) { atmosphere.BringIntoView(); return Task.CompletedTask; }
        if (!Profile.Flag("test_image_approved")) { candidates.BringIntoView(); return Task.CompletedTask; }
        return RunAsync(async () => { await owner.View.ApiSend($"projects/{owner.ProjectId}/styles/{Id}/activate", HttpMethod.Post); });
    }
    private async Task RunAsync(Func<Task> action)
    {
        if (busy || !Attached) return; busy = true; IsEnabled = false; error.Text = "";
        try { await action(); if (Attached) { owner.View.InvalidateStyleDependents(); await owner.ReloadAsync(); } }
        catch (Exception ex) { if (Attached) { error.Text = ex.Message; if (pendingBatch.Length > 0) error.Text += "\n批次已创建，点击生成可在该批次重试。"; } }
        finally { busy = false; IsEnabled = true; UpdateState(); RenderCandidates(); }
    }
    internal async Task LoadCandidatesAsync()
    {
        if (!Attached || reading) return; reading = true; var ticket = ++readRequest;
        try
        {
            var batches = await owner.View.ApiSend(QueryBuilder.Build("asset-generation-batches", ("target_type", "STYLE"), ("target_id", Id), ("limit", 10)));
            if (!Attached || ticket != readRequest) return;
            var batch = batches.EnumerateArray().FirstOrDefault(b => b.Text("generation_kind") == "STYLE_TEST"); batchId = batch.Text("id");
            var result = batchId.Length > 0 ? await owner.View.ApiSend($"batches/{batchId}/candidates") : default;
            if (!Attached || ticket != readRequest) return;
            var next = result.ValueKind == JsonValueKind.Array ? result.EnumerateArray().Where(c => c.Text("variant") == "STYLE_TEST").Select(c => c.Clone()).ToList() : [];
            if (string.Join("|", next.Select(c => c.GetRawText())) != string.Join("|", candidateRows.Select(c => c.GetRawText()))) { candidateRows = next; RenderCandidates(); }
            UpdateState();
        }
        catch (OperationCanceledException) { }
        catch (Exception ex) { if (Attached && ticket == readRequest) error.Text = "测试图读取失败：" + ex.Message; }
        finally { reading = false; }
    }
    private void RenderCandidates()
    {
        candidates.Children.Clear();
        if (candidateRows.Count == 0) candidates.Children.Add(StyleWorkspace.Caption("尚无测试图。确认色板并选择模型后生成。"));
        foreach (var row in candidateRows)
        {
            var content = new StackPanel(); var url = row.Text("content_url");
            if (url.Length == 0 && row.Text("asset_id").Length > 0) url = $"assets/{row.Text("asset_id")}/content";
            if (url.Length > 0)
            {
                var artwork = new Button { Content = new ImageBox { SourceUrl = owner.View.OriginFor(url.Replace("/content", "/thumbnail/640")), Height = 125 }, Padding = new Thickness(0), ToolTip = "查看风格测试原图" };
                artwork.Click += (_, _) => { try { owner.View.ShowImage(url, data.Text("name") + " 风格测试"); } catch (Exception ex) { error.Text = ex.Message; } }; content.Children.Add(artwork);
            }
            var approved = Profile.Text("test_candidate_id") == row.Text("id") && Profile.Flag("test_image_approved");
            content.Children.Add(new TextBlock { Text = approved ? "已通过" : $"测试图 {row.Number("ordinal")}", FontWeight = FontWeights.Bold }); content.Children.Add(StyleWorkspace.Caption(Labels.Map(Labels.CandidateStatus, row.Text("status"))));
            var approve = Button("人工通过", () => RunAsync(async () => { await owner.View.ApiSend($"styles/{Id}/style-test-approve", HttpMethod.Post, new { version = data.Number("version"), candidate_id = row.Text("id"), approved = true }); }));
            approve.IsEnabled = !busy && !approved && row.Text("status") == "READY" && row.Text("asset_id").Length > 0; content.Children.Add(approve); candidates.Children.Add(StyleWorkspace.Surface(content, approved));
        }
    }
}
