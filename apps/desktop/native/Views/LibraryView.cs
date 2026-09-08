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

/// <summary>Native batch library with filter-scoped paging and chapter export readiness.</summary>
public sealed class LibraryView : WorkspaceView
{
    private readonly StackPanel body = new(), exportDesk = new(), exportList = new();
    private readonly ComboBox chapterSelector = Selector("章节筛选", 200), characterSelector = Selector("角色筛选", 160),
        kindSelector = Selector("生成类型筛选", 150), modelSelector = Selector("模型筛选", 190), resolutionSelector = Selector("清晰度筛选", 110);
    private readonly DatePicker dateFrom = new() { Width = 140 }, dateTo = new() { Width = 140 };
    private readonly ToggleButton favoriteOnly = new() { Content = "只看收藏", Style = (Style)Application.Current.FindResource("Chip") };
    private readonly TextBlock countLabel = Kit.Caption(""), pagerLabel = Kit.Caption("每页最多 30 个批次");
    private readonly TextBlock notice = Kit.Caption("");
    private readonly WrapPanel pager = new() { Margin = new Thickness(0, 14, 0, 0) };
    private readonly Button previous = Kit.Act("← 上一页", (_, _) => { }, "Compact"), next = Kit.Act("下一页 →", (_, _) => { }, "Compact");
    private readonly LibraryFeed feed = new();
    private readonly Dictionary<string, string> modelNames = new();
    private readonly HashSet<string> pending = [];
    private readonly Dictionary<string, WrapPanel> candidateActions = new();
    private string renderedData = "";
    private List<ChapterItem> chapters = [];
    private bool populating, exporting;
    private int activation, exportsRead;
    private string lastProject = "";
    private string? readyChapter;

    public LibraryView()
    {
        var panel = new StackPanel { Margin = new Thickness(4, 0, 24, 28) };
        var heading = new StackPanel();
        heading.Children.Add(Kicker("LIBRARY / 批次素材库"));
        heading.Children.Add(new TextBlock { Text = "保存每一次值得比较的结果", FontFamily = (FontFamily)FindResource("Serif"), FontSize = 21, FontWeight = FontWeights.Bold, Margin = new Thickness(0, 5, 0, 0) });
        panel.Children.Add(new Border { Style = (Style)FindResource("CanvasHeader"), Child = new PageHeading(heading, countLabel) });
        var filters = new WrapPanel { Margin = new Thickness(0, 0, 0, 14) };
        foreach (var control in new FrameworkElement[] { chapterSelector, favoriteOnly, characterSelector, kindSelector, modelSelector, resolutionSelector })
        {
            control.Margin = new Thickness(0, 0, 8, 8);
            filters.Children.Add(control);
        }
        AddChoices(kindSelector, "全部类型", Labels.GenerationKind.Select(kv => (kv.Key, kv.Value)));
        AddChoices(resolutionSelector, "全部清晰度", new[] { "1K", "2K", "4K" }.Select(s => (s, s)));
        foreach (var (label, picker) in new[] { ("从", dateFrom), ("至", dateTo) })
        {
            var field = new StackPanel { Orientation = Orientation.Horizontal, Margin = new Thickness(0, 0, 8, 8) };
            field.Children.Add(new TextBlock { Text = label, VerticalAlignment = VerticalAlignment.Center, Margin = new Thickness(0, 0, 6, 0) });
            field.Children.Add(picker); filters.Children.Add(field);
        }
        System.Windows.Automation.AutomationProperties.SetName(dateFrom, "素材开始日期");
        System.Windows.Automation.AutomationProperties.SetName(dateTo, "素材结束日期");
        foreach (var selector in new[] { chapterSelector, characterSelector, kindSelector, modelSelector, resolutionSelector })
            selector.SelectionChanged += (_, _) => { if (!populating) { _ = LoadAsync(); if (selector == chapterSelector) _ = LoadExportsAsync(); } };
        favoriteOnly.Click += (_, _) => { if (!populating) _ = LoadAsync(); };
        dateFrom.SelectedDateChanged += (_, _) => { if (!populating) _ = LoadAsync(); };
        dateTo.SelectedDateChanged += (_, _) => { if (!populating) _ = LoadAsync(); };
        filters.Children.Add(Kit.Act("重置", (_, _) =>
        {
            populating = true;
            try { ResetControls(); }
            finally { populating = false; }
            _ = LoadAsync(); _ = LoadExportsAsync();
        }, "Compact"));
        panel.Children.Add(filters);
        notice.Margin = new Thickness(0, 0, 0, 12);
        notice.TextWrapping = TextWrapping.Wrap;
        panel.Children.Add(notice); panel.Children.Add(body);
        previous.Click += async (_, _) => await MovePage(false);
        next.Click += async (_, _) => await MovePage(true);
        pagerLabel.Margin = new Thickness(12, 0, 12, 0);
        pagerLabel.VerticalAlignment = VerticalAlignment.Center;
        pager.Children.Add(previous); pager.Children.Add(pagerLabel); pager.Children.Add(next);
        panel.Children.Add(pager);
        exportDesk.Margin = new Thickness(0, 28, 0, 0);
        panel.Children.Add(exportDesk); panel.Children.Add(exportList);
        Content = new ScrollViewer { Content = panel, VerticalScrollBarVisibility = ScrollBarVisibility.Auto };
        UpdatePager();
    }

    private static string? Selected(ComboBox box) => (box.SelectedItem as ComboBoxItem)?.Tag as string;
    private static void AddChoices(ComboBox box, string all, IEnumerable<(string Id, string Label)> rows, string? selected = null)
    {
        box.Items.Clear(); box.Items.Add(new ComboBoxItem { Content = all, Tag = "" });
        foreach (var (id, label) in rows) box.Items.Add(new ComboBoxItem { Content = label, Tag = id });
        box.SelectedItem = box.Items.OfType<ComboBoxItem>().FirstOrDefault(i => (string?)i.Tag == selected) ?? box.Items[0];
    }
    private void ResetControls()
    {
        foreach (var box in new[] { chapterSelector, characterSelector, kindSelector, modelSelector, resolutionSelector }) box.SelectedIndex = box.Items.Count > 0 ? 0 : -1;
        favoriteOnly.IsChecked = false; dateFrom.SelectedDate = dateTo.SelectedDate = null;
    }

    public override async void Activate(WorkspaceContext context)
    {
        base.Activate(context);
        var epoch = ++activation; var token = lifetime.Token;
        var changedProject = lastProject != ProjectId;
        lastProject = ProjectId;
        feed.Reset(); pending.Clear(); exporting = false; readyChapter = null;
        renderedData = ""; candidateActions.Clear();
        exportDesk.IsEnabled = true;
        body.Children.Clear(); exportDesk.Children.Clear(); exportList.Children.Clear();
        if (changedProject)
        {
            populating = true; ResetControls(); populating = false;
            chapters.Clear(); modelNames.Clear();
        }
        try
        {
            var chapterTask = Api.SendAsync($"projects/{ProjectId}/chapters", cancellation: token);
            var characterTask = Api.SendAsync($"projects/{ProjectId}/characters", cancellation: token);
            var modelTask = Api.SendAsync("models", cancellation: token);
            await Task.WhenAll(chapterTask, characterTask, modelTask);
            if (token.IsCancellationRequested || epoch != activation) return;
            chapters = (await chapterTask).EnumerateArray().Select(ChapterItem.From).ToList();
            populating = true;
            try
            {
                AddChoices(chapterSelector, "全部章节", chapters.Select(c => (c.Id, $"第 {c.Ordinal} 章 · {c.Title}")), Selected(chapterSelector));
                AddChoices(characterSelector, "全部角色", (await characterTask).EnumerateArray().Select(c => (c.Text("id"), c.Text("primary_name"))), Selected(characterSelector));
                modelNames.Clear();
                foreach (var model in (await modelTask).EnumerateArray().Where(m => m.Text("model_type") == "IMAGE"))
                    modelNames[model.Text("logical_alias")] = model.Text("display_name", model.Text("model_id"));
                AddChoices(modelSelector, "全部模型", modelNames.Select(kv => (kv.Key, kv.Value)), Selected(modelSelector));
            }
            finally { populating = false; }
            await Task.WhenAll(LoadAsync(), LoadExportsAsync());
        }
        catch (OperationCanceledException) { }
        catch (Exception error) { if (epoch == activation && !token.IsCancellationRequested) notice.Text = "素材库配置读取失败，可刷新重试：" + error.Message; }
    }

    public override void Deactivate()
    {
        activation++; exportsRead++; feed.Reset(); readyChapter = null;
        base.Deactivate();
    }
    private LibraryFilter Filter() => new(ProjectId, Selected(chapterSelector), Selected(characterSelector),
        Selected(kindSelector), Selected(modelSelector), Selected(resolutionSelector), favoriteOnly.IsChecked == true, dateFrom.SelectedDate, dateTo.SelectedDate);

    private async Task LoadAsync()
    {
        if (Context == null || lifetime.IsCancellationRequested || populating) return;
        await ReadLibrary(() => feed.LoadAsync(Api, Filter(), lifetime.Token));
    }
    private Task MovePage(bool forward) => ReadLibrary(() => forward ? feed.NextAsync(Api, lifetime.Token) : feed.PreviousAsync(Api, lifetime.Token));
    private async Task ReadLibrary(Func<Task<bool>> read)
    {
        var epoch = activation; var token = lifetime.Token;
        try
        {
            var task = read();
            UpdatePager();
            if (feed.Data.ValueKind != JsonValueKind.Object)
            {
                body.Children.Clear(); body.Children.Add(Kit.Caption("正在读取素材库…"));
            }
            if (await task && epoch == activation && !token.IsCancellationRequested)
            {
                notice.Text = ""; Render();
            }
        }
        catch (OperationCanceledException) { }
        catch (Exception error)
        {
            if (epoch == activation && !token.IsCancellationRequested)
            {
                notice.Text = "素材库读取失败，可刷新重试：" + error.Message;
                if (feed.Data.ValueKind != JsonValueKind.Object) body.Children.Clear();
            }
        }
        finally { if (epoch == activation) UpdatePager(); }
    }
    private void UpdatePager()
    {
        previous.IsEnabled = feed.CanPrevious; next.IsEnabled = feed.CanNext;
        pager.Visibility = feed.PageNumber > 1 || feed.NextCursor != null ? Visibility.Visible : Visibility.Collapsed;
        pagerLabel.Text = $"第 {feed.PageNumber} 页 · 每页最多 30 个批次" + (feed.Loading ? " · 读取中…" : "");
    }

    private void Render()
    {
        var snapshot = feed.Data.ValueKind == JsonValueKind.Object ? feed.Data.GetRawText() : "";
        if (snapshot == renderedData && body.Children.Count > 0) { UpdateCandidateActions(); return; }
        renderedData = snapshot; candidateActions.Clear();
        body.Children.Clear();
        var groups = feed.Data.Array("groups");
        countLabel.Text = $"{feed.Data.Number("total_candidates")} 个候选";
        favoriteOnly.Content = $"只看收藏（{feed.Data.Number("favorite_count")}）";
        if (groups.Count == 0)
        {
            body.Children.Add(new Border { Style = (Style)FindResource("Card"), Padding = new Thickness(26),
                Child = new StackPanel { Children = { new TextBlock { Text = "素材库还是空的", FontSize = 18, FontFamily = (FontFamily)FindResource("Serif") },
                    Kit.Caption("从单页抽卡开始，所有候选都会按批次保留。") } } });
            return;
        }
        var batches = new BatchPanel();
        body.Children.Add(batches);
        foreach (var group in groups)
        {
            var batch = group.Element("batch");
            var candidates = group.Array("candidates");
            var section = new StackPanel();
            var date = DateTimeOffset.TryParse(batch.Text("created_at"), out var created) ? created.ToLocalTime().ToString("yyyy-MM-dd HH:mm") : "";
            var title = new StackPanel();
            title.Children.Add(Kit.Caption($"BATCH {batch.Number("ordinal"):D3}"));
            title.Children.Add(new TextBlock { Text = Labels.Map(Labels.GenerationKind, batch.Text("generation_kind")), FontFamily = (FontFamily)FindResource("Serif"), FontWeight = FontWeights.Bold, FontSize = 12 });
            section.Children.Add(new Border { Padding = new Thickness(13, 11, 13, 11), BorderBrush = (Brush)FindResource("Line"), BorderThickness = new Thickness(0, 0, 0, 1),
                Child = new PageHeading(title, Kit.Caption($"{date} · {candidates.Count} 张")) });
            var tiles = new TilePanel { MinimumTileWidth = 160, MaximumColumns = Math.Clamp(candidates.Count, 1, 3), Gap = 14, Margin = new Thickness(14) };
            foreach (var row in candidates) tiles.Children.Add(CandidateCard(CandidateItem.From(row)));
            section.Children.Add(tiles);
            var frame = new Border { Child = section, BorderBrush = (Brush)FindResource("LineDark"), BorderThickness = new Thickness(1), Background = (Brush)FindResource("Surface") };
            Grid.SetColumnSpan(frame, Math.Clamp(candidates.Count, 1, 3));
            batches.Children.Add(frame);
        }
    }
    private Border CandidateCard(CandidateItem candidate)
    {
        var panel = new StackPanel();
        var url = candidate.ContentUrl.Length > 0 ? candidate.ContentUrl : candidate.AssetId.Length > 0 ? $"assets/{candidate.AssetId}/content" : "";
        var artwork = new Border { Background = (Brush)FindResource("PaperDeep") };
        if (url.Length > 0)
        {
            artwork.Child = new ImageBox { SourceUrl = Api.PublicUrl(url) };
        }
        else artwork.Child = new TextBlock { Text = candidate.StatusLabel, HorizontalAlignment = HorizontalAlignment.Center, VerticalAlignment = VerticalAlignment.Center };
        var preview = Kit.Act("", (_, _) => { if (url.Length > 0) OpenImage(url, $"批次候选 {candidate.Ordinal}"); }, "Ghost");
        preview.Content = artwork; preview.Padding = new Thickness(0); preview.BorderThickness = new Thickness(0);
        preview.HorizontalContentAlignment = HorizontalAlignment.Stretch; preview.VerticalContentAlignment = VerticalAlignment.Stretch;
        System.Windows.Automation.AutomationProperties.SetName(preview, $"放大查看批次候选 {candidate.Ordinal}");
        panel.Children.Add(new ArtworkFrame { Child = preview });
        panel.Children.Add(new TextBlock { Text = modelNames.GetValueOrDefault(candidate.ModelAlias, candidate.ModelAlias), FontWeight = FontWeights.Bold, TextTrimming = TextTrimming.CharacterEllipsis, Margin = new Thickness(0, 8, 0, 3) });
        panel.Children.Add(Kit.Caption($"{candidate.Resolution} · {candidate.StatusLabel} · {candidate.VersionLabel}"));
        var actions = new WrapPanel { Margin = new Thickness(0, 8, 0, 0), IsEnabled = !pending.Contains(candidate.Id) };
        candidateActions[candidate.Id] = actions;
        actions.Children.Add(Kit.Act(candidate.Favorite ? "♥ 已收藏" : "♡ 收藏", async (_, _) => await CandidateAction(candidate, "favorite"), "Compact"));
        if (candidate.IsSelected)
        {
            actions.Children.Add(new TextBlock { Text = "✓ 已暂选", Foreground = (Brush)FindResource("Success"), VerticalAlignment = VerticalAlignment.Center, Margin = new Thickness(8, 0, 8, 0) });
            var retract = Kit.Act("撤回", async (_, _) =>
            {
                if (MessageBox.Show(Host, "撤回暂选后，候选图片和生成记录仍会保留，后续页面将标记为待复查。是否继续？", "撤回暂选", MessageBoxButton.YesNo, MessageBoxImage.Question) == MessageBoxResult.Yes)
                    await CandidateAction(candidate, "retract");
            }, "CompactDanger");
            retract.IsEnabled = candidate.PageId.Length > 0;
            actions.Children.Add(retract);
        }
        else actions.Children.Add(Kit.Act("删除", async (_, _) =>
        {
            if (MessageBox.Show(Host, "从素材库隐藏这个候选？生成文件和任务记录会保留。", "隐藏候选", MessageBoxButton.YesNo, MessageBoxImage.Question) == MessageBoxResult.Yes)
                await CandidateAction(candidate, "delete");
        }, "CompactDanger"));
        foreach (var button in actions.Children.OfType<Button>()) button.Margin = new Thickness(0, 0, 6, 4);
        panel.Children.Add(actions);
        return new Border { Child = panel, BorderBrush = (Brush)FindResource(candidate.IsSelected ? "Success" : "Line"),
            BorderThickness = new Thickness(1, 1, 1, candidate.IsSelected ? 3 : 1), Background = (Brush)FindResource("Surface"), Padding = new Thickness(10), Tag = candidate.Id };
    }

    private void UpdateCandidateActions()
    {
        foreach (var (id, actions) in candidateActions) actions.IsEnabled = !pending.Contains(id);
    }

    private async Task CandidateAction(CandidateItem candidate, string action)
    {
        if (action is not ("favorite" or "delete" or "retract") || lifetime.IsCancellationRequested || (action == "delete" && candidate.IsSelected) || (action == "retract" && (!candidate.IsSelected || candidate.PageId.Length == 0)) || !pending.Add(candidate.Id)) return;
        var epoch = activation; var token = lifetime.Token; var project = ProjectId;
        Render();
        try
        {
            if (action == "favorite") await Api.SendAsync($"candidates/{candidate.Id}/favorite", HttpMethod.Patch, new { is_favorite = !candidate.Favorite }, token);
            else if (action == "delete") await Api.SendOptionalAsync($"candidates/{candidate.Id}", HttpMethod.Delete, cancellation: token);
            else if (action == "retract") await Api.SendOptionalAsync($"pages/{candidate.PageId}/selected-candidate?candidate_id={candidate.Id}", HttpMethod.Delete, cancellation: token);
            Cache.Invalidate("library:" + project, "workbench:", "pages:", "dashboard");
            if (epoch != activation || token.IsCancellationRequested) return;
            await ReadLibrary(() => feed.RefreshAsync(Api, token));
            if (action == "retract") await LoadExportsAsync();
        }
        catch (OperationCanceledException) { }
        catch (Exception error) { if (epoch == activation && !token.IsCancellationRequested) notice.Text = "操作未保存，请刷新核对后重试：" + error.Message; }
        finally { if (epoch == activation) { pending.Remove(candidate.Id); if (feed.Data.ValueKind == JsonValueKind.Object) Render(); } }
    }

    private ChapterItem? ExportChapterSelection => chapters.FirstOrDefault(c => c.Id == Selected(chapterSelector)) ?? chapters.FirstOrDefault();
    private async Task LoadExportsAsync()
    {
        if (Context == null || lifetime.IsCancellationRequested || populating) return;
        var request = ++exportsRead; var epoch = activation; var token = lifetime.Token;
        var chapter = ExportChapterSelection; var project = ProjectId;
        readyChapter = null; exportDesk.Children.Clear(); exportList.Children.Clear();
        bool Current() => request == exportsRead && epoch == activation && !token.IsCancellationRequested;
        async Task ReadReadiness()
        {
            if (chapter == null) { exportDesk.Children.Add(Kit.Caption("章节没有可导出的页面。")); return; }
            try
            {
                var data = await Api.SendAsync($"chapters/{chapter.Id}/production-readiness", cancellation: token);
                if (!Current()) return;
                var ready = data.Flag("ready");
                readyChapter = ready ? chapter.Id : null;
                var status = new StackPanel();
                var light = new SolidColorBrush(Color.FromRgb(216, 212, 204));
                status.Children.Add(new TextBlock { Text = "EXPORT / 整章导出门禁", Foreground = light, FontSize = 10, FontWeight = FontWeights.Bold });
                status.Children.Add(new TextBlock { Text = $"第 {chapter.Ordinal} 章 · {chapter.Title} · {data.Number("ready_pages")}/{data.Number("total_pages")} 页生产通过", Foreground = Brushes.White, FontFamily = (FontFamily)FindResource("Serif"), FontWeight = FontWeights.Bold, FontSize = 14, Margin = new Thickness(0, 5, 0, 4) });
                status.Children.Add(new TextBlock { Text = ready ? "全部页面已完成校对、版本确认和视觉检查" : "请完成下列页面的生产检查后再导出。", Foreground = light, FontSize = 11 });
                var actions = new WrapPanel { VerticalAlignment = VerticalAlignment.Center, IsEnabled = ready && !exporting };
                foreach (var type in new[] { "PNG", "PDF", "JSON" })
                {
                    var button = Kit.Act("↓ " + type, async (_, _) => await ExportChapter(chapter.Id, type), "Compact");
                    button.Foreground = Brushes.White; button.Background = Brushes.Transparent;
                    button.BorderBrush = light; button.Margin = new Thickness(6, 2, 0, 2);
                    actions.Children.Add(button);
                }
                exportDesk.Children.Add(new Border { Padding = new Thickness(16), Background = ready ? new SolidColorBrush(Color.FromRgb(32, 58, 40)) : (Brush)FindResource("Ink"),
                    Child = new PageHeading(status, actions) });
                foreach (var page in data.Array("pages").Where(p => !p.Flag("ready")).Take(4))
                {
                    var number = page.Number("page_number");
                    var go = Kit.Act((number > 0 ? $"第 {number} 页" : "查看页面") + " · " + page.Array("blockers").FirstOrDefault().Text("message", "尚未通过"), async (_, _) =>
                    {
                        KeyValueStore.Set("generate:chapter:" + project, chapter.Id);
                        KeyValueStore.Set("generate:page:" + project, page.Text("page_id"));
                        await Context!.NavigateSection("generate", "");
                    }, "Ghost");
                    go.HorizontalAlignment = HorizontalAlignment.Left; exportDesk.Children.Add(go);
                }
            }
            catch (OperationCanceledException) { }
            catch (Exception error)
            {
                if (!Current()) return;
                exportDesk.Children.Add(Kit.Caption("章节生产状态读取失败：" + error.Message));
                exportDesk.Children.Add(Kit.Act("重试读取", async (_, _) => await LoadExportsAsync(), "Compact"));
            }
        }
        async Task ReadFiles()
        {
            try
            {
                var rows = await Api.SendAsync($"projects/{project}/exports", cancellation: token);
                if (!Current()) return;
                exportList.Children.Add(new TextBlock { Text = "导出文件", Style = (Style)FindResource("SectionIndex"), Margin = new Thickness(0, 14, 0, 6) });
                if (rows.GetArrayLength() == 0) exportList.Children.Add(Kit.Caption("还没有导出文件；通过门禁后即可导出整章 PNG / PDF / JSON。"));
                foreach (var row in rows.EnumerateArray())
                {
                    var item = ExportItem.From(row);
                    var download = Kit.Act($"{item.ExportType} · {item.PageCount} 页 · {item.SizeLabel}    ↓ 下载", async (sender, _) => await Download(item, (Button)sender), "Ghost");
                    download.HorizontalAlignment = HorizontalAlignment.Left; exportList.Children.Add(download);
                }
            }
            catch (OperationCanceledException) { }
            catch (Exception error)
            {
                if (!Current()) return;
                exportList.Children.Add(Kit.Caption("导出记录读取失败：" + error.Message));
                exportList.Children.Add(Kit.Act("重试读取导出记录", async (_, _) => await LoadExportsAsync(), "Compact"));
            }
        }
        await Task.WhenAll(ReadReadiness(), ReadFiles());
    }
    private async Task ExportChapter(string chapterId, string type)
    {
        if (exporting || readyChapter != chapterId || lifetime.IsCancellationRequested) return;
        exporting = true; exportDesk.IsEnabled = false;
        var epoch = activation; var token = lifetime.Token; var project = ProjectId;
        try
        {
            await Api.SendAsync($"chapters/{chapterId}/exports", HttpMethod.Post, new { export_type = type }, token);
            Cache.Invalidate("exports:" + project);
            if (epoch != activation || token.IsCancellationRequested) return;
            notice.Text = $"{type} 导出文件已生成，可在下方下载。";
            await LoadExportsAsync();
        }
        catch (OperationCanceledException) { }
        catch (Exception error) { if (epoch == activation && !token.IsCancellationRequested) notice.Text = "导出失败，请刷新核对：" + error.Message; }
        finally
        {
            if (epoch == activation) { exporting = false; exportDesk.IsEnabled = true; foreach (var item in Descendants(exportDesk).OfType<WrapPanel>()) item.IsEnabled = readyChapter != null; }
        }
    }
    private async Task Download(ExportItem item, Button button)
    {
        var extension = item.ExportType switch { "PNG" => ".zip", "PDF" => ".pdf", _ => ".json" };
        var dialog = new SaveFileDialog { Title = "保存导出文件", FileName = "mangaflow-" + item.Id + extension, Filter = $"导出文件|*{extension}", DefaultExt = extension, AddExtension = true };
        if (dialog.ShowDialog(Host) != true) return;
        button.IsEnabled = false;
        var epoch = activation; var token = lifetime.Token;
        try
        {
            await Api.SaveDownloadAsync(item.Url, dialog.FileName, token);
            if (epoch == activation && !token.IsCancellationRequested) notice.Text = "导出文件已保存。";
        }
        catch (OperationCanceledException) when (token.IsCancellationRequested) { }
        catch (OperationCanceledException) { if (epoch == activation) notice.Text = "下载超时，请重试；原有文件未被替换。"; }
        catch (Exception error) { if (epoch == activation && !token.IsCancellationRequested) notice.Text = "下载失败，可重试：" + error.Message; }
        finally { button.IsEnabled = true; }
    }
    private static IEnumerable<DependencyObject> Descendants(DependencyObject root)
    {
        yield return root;
        foreach (var child in LogicalTreeHelper.GetChildren(root).OfType<DependencyObject>())
            foreach (var item in Descendants(child)) yield return item;
    }
    public override async Task RefreshAsync()
    {
        if (Context == null || lifetime.IsCancellationRequested) return;
        if (modelSelector.Items.Count == 0) { Activate(Context); return; }
        await Task.WhenAll(LoadAsync(), LoadExportsAsync());
    }
}
