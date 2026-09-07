using System.Net.Http;
using System.Text.Json;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Controls.Primitives;
using System.Windows.Input;
using System.Windows.Media;
using MangaFlow.Native.Controls;
using MangaFlow.Native.Services;

namespace MangaFlow.Native.Views;

/// <summary>NUI-5/6: library — batch groups, filters, keyset paging, chapter export gate.</summary>
public sealed class LibraryView : WorkspaceView
{
    private readonly ScrollViewer scroller = new() { VerticalScrollBarVisibility = ScrollBarVisibility.Auto };
    private readonly StackPanel body = new();
    private readonly ComboBox chapterSelector = Selector("章节筛选", 200);
    private readonly ComboBox characterSelector = Selector("角色筛选", 160);
    private readonly ComboBox kindSelector = Selector("生成类型筛选", 150);
    private readonly ComboBox modelSelector = Selector("模型筛选", 170);
    private readonly ComboBox resolutionSelector = Selector("清晰度筛选", 90);
    private readonly ToggleButton favoriteOnly = new() { Content = "只看收藏", Style = (Style)Application.Current.FindResource("Chip") };
    private readonly TextBlock countLabel = new() { Style = (Style)Application.Current.FindResource("Caption") };
    private readonly StackPanel exportDesk = new();
    private readonly StackPanel exportList = new();
    private readonly TextBlock notice = new() { Style = (Style)Application.Current.FindResource("Caption"), TextWrapping = TextWrapping.Wrap, Margin = new Thickness(0, 0, 0, 12) };

    private List<ChapterItem> chapters = [];
    private List<CharacterItem> characters = [];
    private readonly List<string> pageStack = [];   // keyset cursors: previous pages
    private string? nextCursor;
    private bool hasLoaded;

    public LibraryView()
    {
        var panel = new StackPanel { Margin = new Thickness(4, 0, 24, 28) };
        panel.Children.Add(SectionHeader());
        var filters = new WrapPanel { Margin = new Thickness(0, 0, 0, 14) };
        chapterSelector.MinWidth = 170;
        filters.Children.Add(chapterSelector);
        favoriteOnly.Margin = new Thickness(8, 0, 8, 0);
        favoriteOnly.Click += (_, _) => { ResetPaging(); _ = LoadAsync(); };
        filters.Children.Add(favoriteOnly);
        characterSelector.MinWidth = 140;
        characterSelector.Margin = new Thickness(0, 0, 8, 0);
        filters.Children.Add(characterSelector);
        kindSelector.Margin = new Thickness(0, 0, 8, 0);
        foreach (var (key, label) in Labels.GenerationKind)
            kindSelector.Items.Add(new ComboBoxItem { Tag = key, Content = label });
        kindSelector.SelectionChanged += (_, _) => { ResetPaging(); _ = LoadAsync(); };
        filters.Children.Add(kindSelector);
        modelSelector.MinWidth = 150;
        modelSelector.Margin = new Thickness(0, 0, 8, 0);
        filters.Children.Add(modelSelector);
        resolutionSelector.Items.Add(new ComboBoxItem { Tag = "", Content = "清晰度" });
        foreach (var resolution in new[] { "1K", "2K", "4K" })
            resolutionSelector.Items.Add(new ComboBoxItem { Tag = resolution, Content = resolution });
        resolutionSelector.SelectedIndex = 0;
        resolutionSelector.SelectionChanged += (_, _) => { ResetPaging(); _ = LoadAsync(); };
        filters.Children.Add(resolutionSelector);
        chapterSelector.SelectionChanged += (_, _) => { ResetPaging(); _ = LoadAsync(); LoadExports(); };
        characterSelector.SelectionChanged += (_, _) => { ResetPaging(); _ = LoadAsync(); };
        modelSelector.SelectionChanged += (_, _) => { ResetPaging(); _ = LoadAsync(); };
        var reset = Kit.Act("重置", (_, _) =>
        {
            chapterSelector.SelectedIndex = -1;
            characterSelector.SelectedIndex = -1;
            kindSelector.SelectedIndex = -1;
            modelSelector.SelectedIndex = -1;
            resolutionSelector.SelectedIndex = 0;
            favoriteOnly.IsChecked = false;
            ResetPaging();
            _ = LoadAsync();
        }, "Compact");
        reset.Margin = new Thickness(8, 0, 0, 0);
        filters.Children.Add(reset);
        panel.Children.Add(filters);
        panel.Children.Add(notice);
        panel.Children.Add(body);
        panel.Children.Add(BuildPager());
        panel.Children.Add(new TextBlock { Text = "整章导出", Style = (Style)Application.Current.FindResource("SectionIndex"), Margin = new Thickness(0, 24, 0, 8) });
        panel.Children.Add(exportDesk);
        panel.Children.Add(exportList);
        scroller.Content = panel;
        Content = scroller;
    }

    private Border SectionHeader()
    {
        var border = new Border { Style = (Style)Application.Current.FindResource("CanvasHeader") };
        var grid = new Grid();
        grid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        grid.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        var heading = new StackPanel();
        heading.Children.Add(new TextBlock { Text = "LIBRARY", Style = (Style)Application.Current.FindResource("SectionIndex") });
        heading.Children.Add(new TextBlock
        {
            Text = "批次素材库 · 保存每一次值得比较的结果",
            FontFamily = (FontFamily)Application.Current.FindResource("Serif"),
            FontSize = 21, FontWeight = FontWeights.Bold, Margin = new Thickness(0, 5, 0, 0),
        });
        grid.Children.Add(heading);
        countLabel.VerticalAlignment = VerticalAlignment.Bottom;
        Grid.SetColumn(countLabel, 1);
        grid.Children.Add(countLabel);
        border.Child = grid;
        return border;
    }

    private FrameworkElement BuildPager()
    {
        var pager = new StackPanel { Orientation = Orientation.Horizontal, Margin = new Thickness(0, 14, 0, 0) };
        var previous = Kit.Act("上一页", (_, _) => { if (pageStack.Count > 0) { nextCursor = pageStack.Count > 1 ? pageStack[^2] : null; pageStack.RemoveAt(pageStack.Count - 1); _ = LoadAsync(); } }, "Compact");
        var next = Kit.Act("下一页", (_, _) => { if (nextCursor != null) { pageStack.Add(nextCursor); _ = LoadAsync(); } }, "Compact");
        pager.Children.Add(previous);
        var hint = new TextBlock { Text = "每页最多 30 个批次", Style = (Style)Application.Current.FindResource("Micro"), VerticalAlignment = VerticalAlignment.Center, Margin = new Thickness(12, 0, 12, 0) };
        pager.Children.Add(hint);
        pager.Children.Add(next);
        return pager;
    }

    private void ResetPaging()
    {
        pageStack.Clear();
        nextCursor = null;
    }

    public override async void Activate(WorkspaceContext context)
    {
        base.Activate(context);
        try
        {
            var chapterRows = await Api.SendAsync($"projects/{ProjectId}/chapters", cancellation: lifetime.Token);
            var characterRows = await Api.SendAsync($"projects/{ProjectId}/characters", cancellation: lifetime.Token);
            if (lifetime.Token.IsCancellationRequested) return;
            chapters = chapterRows.EnumerateArray().Select(ChapterItem.From).ToList();
            characters = characterRows.EnumerateArray().Select(CharacterItem.From).ToList();
            chapterSelector.Items.Clear();
            chapterSelector.Items.Add(new ComboBoxItem { Tag = "", Content = "全部章节" });
            foreach (var chapter in chapters)
                chapterSelector.Items.Add(new ComboBoxItem { Tag = chapter.Id, Content = $"第 {chapter.Ordinal} 章 · {chapter.Title}" });
            chapterSelector.SelectedIndex = 0;
            characterSelector.Items.Clear();
            characterSelector.Items.Add(new ComboBoxItem { Tag = "", Content = "全部角色" });
            foreach (var character in characters)
                characterSelector.Items.Add(new ComboBoxItem { Tag = character.Id, Content = character.PrimaryName });
            characterSelector.SelectedIndex = 0;
            await LoadAsync();
            LoadExports();
        }
        catch (Exception error) when (error is not OperationCanceledException)
        {
            body.Children.Clear();
            body.Children.Add(Kit.Caption($"素材库读取失败：{error.Message}"));
        }
    }

    private async Task LoadAsync()
    {
        if (!hasLoaded)
        {
            body.Children.Clear();
            var spinner = new StackPanel { Orientation = Orientation.Horizontal };
            spinner.Children.Add(new Spinner { Size = 16 });
            var hint = Kit.Caption("正在读取素材库…");
            hint.Margin = new Thickness(10, 0, 0, 0);
            spinner.Children.Add(hint);
            body.Children.Add(spinner);
        }
        try
        {
            var filters = new List<(string, object?)>
            {
                ("group_by", "batch"),
                ("limit", 30),
            };
            if (favoriteOnly.IsChecked == true) filters.Add(("favorite", "true"));
            if (chapterSelector.SelectedItem is ComboBoxItem { Tag: string chapterId } && chapterId.Length > 0) filters.Add(("chapter_id", chapterId));
            if (characterSelector.SelectedItem is ComboBoxItem { Tag: string characterId } && characterId.Length > 0) filters.Add(("character_id", characterId));
            if (kindSelector.SelectedItem is ComboBoxItem { Tag: string kind } && kind.Length > 0) filters.Add(("generation_kind", kind));
            if (modelSelector.SelectedItem is ComboBoxItem { Tag: string model } && model.Length > 0) filters.Add(("model_alias", model));
            if (resolutionSelector.SelectedItem is ComboBoxItem { Tag: string resolution } && resolution.Length > 0) filters.Add(("resolution", resolution));
            if (nextCursor != null) filters.Add(("cursor", nextCursor));
            var result = await Api.SendAsync(QueryBuilder.Build($"projects/{ProjectId}/library", [.. filters]), cancellation: lifetime.Token);
            if (lifetime.Token.IsCancellationRequested) return;
            hasLoaded = true;
            Render(result);
        }
        catch (Exception error) when (error is not OperationCanceledException)
        {
            body.Children.Clear();
            body.Children.Add(Kit.Caption($"素材库读取失败：{error.Message}"));
        }
    }

    private void Render(JsonElement result)
    {
        body.Children.Clear();
        var groups = result.Array("groups");
        var total = result.Number("total_candidates");
        countLabel.Text = $"{total} 个候选" + (favoriteOnly.IsChecked == true ? $" · {result.Number("favorite_count")} 收藏" : "");
        nextCursor = result.TextOrNull("next_cursor");
        if (groups.Count == 0)
        {
            var empty = new StackPanel();
            empty.Children.Add(new TextBlock { Text = "素材库还是空的", FontFamily = (FontFamily)Application.Current.FindResource("Serif"), FontSize = 18, FontWeight = FontWeights.Bold });
            empty.Children.Add(Kit.Caption("从单页抽卡开始，所有候选都会按批次保留。"));
            body.Children.Add(new Border { Style = (Style)Application.Current.FindResource("Card"), Padding = new Thickness(26), Child = empty });
            return;
        }
        var modelNames = new HashSet<string>();
        foreach (var group in groups)
            foreach (var candidate in group.Array("candidates"))
                modelNames.Add(candidate.Text("model_alias"));
        if (modelSelector.Items.Count <= 1)
        {
            modelSelector.Items.Clear();
            modelSelector.Items.Add(new ComboBoxItem { Tag = "", Content = "全部模型" });
            foreach (var model in modelNames.OrderBy(m => m))
                modelSelector.Items.Add(new ComboBoxItem { Tag = model, Content = model });
            modelSelector.SelectedIndex = 0;
        }
        foreach (var group in groups)
        {
            var batch = group.Element("batch");
            var candidates = group.Array("candidates");
            var section = new StackPanel { Margin = new Thickness(0, 0, 0, 18) };
            var header = new TextBlock
            {
                Text = $"BATCH {batch.Number("ordinal"):D3} · {Labels.Map(Labels.GenerationKind, batch.Text("generation_kind"))} · {batch.Text("created_at", "").Replace('T', ' ')[..Math.Min(16, batch.Text("created_at").Length)]} · {candidates.Count} 张",
                Style = (Style)Application.Current.FindResource("SectionIndex"),
            };
            section.Children.Add(header);
            var grid = new WrapPanel { Margin = new Thickness(0, 8, 0, 0) };
            foreach (var row in candidates)
            {
                var candidate = CandidateItem.From(row);
                grid.Children.Add(new LibraryCard(this, candidate));
            }
            section.Children.Add(grid);
            body.Children.Add(section);
        }
    }

    private async void LoadExports()
    {
        exportList.Children.Clear();
        exportDesk.Children.Clear();
        var chapter = chapters.FirstOrDefault(c => c.Id == (chapterSelector.SelectedItem as ComboBoxItem)?.Tag as string)
            ?? chapters.FirstOrDefault();
        if (chapter == null) return;
        try
        {
            var readiness = await Api.SendAsync($"chapters/{chapter.Id}/production-readiness", cancellation: lifetime.Token);
            var ready = readiness.Flag("ready");
            var readyPages = readiness.Number("ready_pages");
            var totalPages = readiness.Number("total_pages");
            var blockerPages = readiness.Array("pages").Where(p => !p.Flag("ready")).ToList();
            var status = new StackPanel();
            var statusLine = new TextBlock
            {
                Text = $"{readyPages}/{totalPages} 页生产通过",
                FontWeight = FontWeights.Bold,
                Foreground = ready ? (Brush)Application.Current.FindResource("Success") : (Brush)Application.Current.FindResource("Warning"),
            };
            status.Children.Add(statusLine);
            status.Children.Add(new TextBlock
            {
                Text = ready ? "全部页面已完成校对、版本确认和视觉检查" : blockerPages.FirstOrDefault().Array("blockers").FirstOrDefault().Text("message", "存在未通过的页面"),
                Style = (Style)Application.Current.FindResource("Micro"), Margin = new Thickness(0, 4, 0, 10),
            });
            var buttons = new StackPanel { Orientation = Orientation.Horizontal };
            foreach (var type in new[] { "PNG", "PDF", "JSON" })
            {
                var export = Kit.Act($"导出 {type}", async (_, _) => await ExportChapter(chapter.Id, type), "Compact");
                export.IsEnabled = ready;
                export.Margin = new Thickness(0, 0, 8, 0);
                buttons.Children.Add(export);
            }
            status.Children.Add(buttons);
            foreach (var page in blockerPages.Take(4))
            {
                var blocker = page.Array("blockers").FirstOrDefault().Text("message", "未通过");
                var row = new TextBlock
                {
                    Text = $"第 {page.Number("page_number")} 页 · {blocker}",
                    Style = (Style)Application.Current.FindResource("Micro"), Margin = new Thickness(0, 6, 0, 0), Cursor = Cursors.Hand,
                };
                row.MouseLeftButtonDown += async (_, _) =>
                {
                    KeyValueStore.Set("generate:page:" + ProjectId, page.Text("page_id"));
                    await Context!.NavigateSection("generate", "");
                };
                status.Children.Add(row);
            }
            exportDesk.Children.Add(new Border { Style = (Style)Application.Current.FindResource("Card"), Padding = new Thickness(18), Child = status });

            var exports = await Api.SendAsync($"projects/{ProjectId}/exports", cancellation: lifetime.Token);
            exportList.Children.Add(new TextBlock { Text = "导出文件", Style = (Style)Application.Current.FindResource("SectionIndex"), Margin = new Thickness(0, 12, 0, 6) });
            var rows = exports.EnumerateArray().Select(ExportItem.From).ToList();
            if (rows.Count == 0)
                exportList.Children.Add(Kit.Caption("还没有导出文件；通过上方门禁后即可导出整章 PNG / PDF / JSON。"));
            foreach (var item in rows)
            {
                var row = new StackPanel { Orientation = Orientation.Horizontal, Margin = new Thickness(0, 4, 0, 4) };
                row.Children.Add(new TextBlock { Text = $"{item.ExportType} · {item.PageCount} 页 · {item.SizeLabel}", VerticalAlignment = VerticalAlignment.Center });
                var download = Kit.Act("下载", (_, _) =>
                {
                    if (item.Url.Length > 0 && Context != null)
                        OpenImage(item.Url, item.FileName.Length > 0 ? item.FileName : item.Id);
                }, "Compact");
                download.Margin = new Thickness(12, 0, 0, 0);
                row.Children.Add(download);
                exportList.Children.Add(row);
            }
        }
        catch (Exception error) when (error is not OperationCanceledException)
        {
            exportDesk.Children.Add(Kit.Caption($"章节生产状态读取失败：{error.Message}"));
        }
    }

    private async Task ExportChapter(string chapterId, string type)
    {
        try
        {
            await Api.SendAsync($"chapters/{chapterId}/exports", HttpMethod.Post, new { export_type = type }, cancellation: lifetime.Token);
            notice.Text = $"{type} 导出任务已创建。";
            LoadExports();
        }
        catch (Exception error) when (error is not OperationCanceledException)
        {
            notice.Text = $"导出失败：{error.Message}";
        }
    }

    internal async Task Unselect(CandidateItem candidate, string pageId)
    {
        if (MessageBox.Show(Host, "撤回暂选后，候选图片和生成记录仍会保留，后续页面将标记为待复查。是否继续？",
                "撤回暂选", MessageBoxButton.YesNo, MessageBoxImage.Question) != MessageBoxResult.Yes) return;
        try
        {
            await Api.SendOptionalAsync($"pages/{pageId}/selected-candidate?candidate_id={candidate.Id}", HttpMethod.Delete, cancellation: lifetime.Token);
            Cache.Invalidate("library:" + ProjectId, "workbench:", "pages:", "dashboard");
            await LoadAsync();
        }
        catch (Exception error) when (error is not OperationCanceledException)
        {
            notice.Text = $"撤回失败：{error.Message}";
        }
    }

    public override Task RefreshAsync()
    {
        ResetPaging();
        _ = LoadAsync();
        return Task.CompletedTask;
    }

    // Bridges for the nested card control.
    internal WorkspaceContext? Ctx => Context;
    internal ApiClient LibApi => Api;
    internal ApiCache LibCache => Cache;
    internal string LibProjectId => base.ProjectId;
    internal void OpenExternalImage(string url, string label) => OpenImage(url, label);
}

internal sealed class LibraryCard : Border
{
    public LibraryCard(LibraryView view, CandidateItem candidate)
    {
        BorderBrush = (Brush)Application.Current.FindResource("Line");
        BorderThickness = new Thickness(1);
        Background = (Brush)Application.Current.FindResource("Surface");
        Padding = new Thickness(10);
        Margin = new Thickness(0, 0, 12, 12);
        Width = 176;
        var panel = new StackPanel();
        var artwork = new Border
        {
            Height = 160, Background = (Brush)Application.Current.FindResource("PaperDeep"),
            Cursor = Cursors.Hand, Margin = new Thickness(0, 0, 0, 8),
        };
        var url = candidate.ContentUrl.Length > 0
            ? candidate.ContentUrl
            : candidate.AssetId.Length > 0 ? $"assets/{candidate.AssetId}/content" : "";
        if (url.Length > 0)
        {
            artwork.Child = new ImageBox { SourceUrl = view.Ctx!.Api.PublicUrl(url) };
            artwork.MouseLeftButtonDown += (_, _) => view.OpenExternalImage(url, $"候选 {candidate.Ordinal}");
        }
        panel.Children.Add(artwork);
        panel.Children.Add(new TextBlock
        {
            Text = candidate.ModelAlias.Length > 0 ? candidate.ModelAlias : "—",
            FontWeight = FontWeights.Bold, FontSize = 12.5, TextTrimming = TextTrimming.CharacterEllipsis, TextWrapping = TextWrapping.NoWrap,
        });
        panel.Children.Add(new TextBlock
        {
            Text = $"{candidate.Resolution} · {candidate.StatusLabel} · {candidate.VersionLabel}",
            Style = (Style)Application.Current.FindResource("Micro"), Margin = new Thickness(0, 3, 0, 0),
        });
        var actions = new StackPanel { Orientation = Orientation.Horizontal, Margin = new Thickness(0, 8, 0, 0) };
        var favorite = new ToggleButton
        {
            Content = "♥", Style = (Style)Application.Current.FindResource("Pill"), MinWidth = 40, MinHeight = 30,
            IsChecked = candidate.Favorite,
        };
        favorite.Click += async (_, _) =>
        {
            try
            {
                await view.LibApi.SendAsync($"candidates/{candidate.Id}/favorite", HttpMethod.Patch,
                    new { is_favorite = favorite.IsChecked == true });
                view.LibCache.Invalidate("library:" + view.LibProjectId);
            }
            catch (Exception error) { MessageBox.Show(Window.GetWindow(view), error.Message, "收藏未保存"); }
        };
        actions.Children.Add(favorite);
        if (candidate.VersionState is "STALE_ACCEPTED" or "CURRENT" && candidate.Status is "READY" or "INSPECTED" && candidate.PageId.Length > 0)
        {
            var unselect = Kit.Act("撤回", async (_, _) => await view.Unselect(candidate, candidate.PageId), "CompactDanger");
            unselect.Margin = new Thickness(8, 0, 0, 0);
            unselect.ToolTip = "撤回本页暂选";
            actions.Children.Add(unselect);
        }
        panel.Children.Add(actions);
        Child = panel;
    }
}
