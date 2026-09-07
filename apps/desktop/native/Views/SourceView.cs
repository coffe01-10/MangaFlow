using System.IO;
using System.Text;
using System.Net.Http;
using System.Text.Json;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;
using Microsoft.Win32;
using MangaFlow.Native.Controls;
using MangaFlow.Native.Services;

namespace MangaFlow.Native.Views;

/// <summary>NUI-2B: source import, revisions, chapter register, delete/restore, parse/plan.</summary>
public sealed class SourceView : WorkspaceView
{
    private readonly TextBox titleInput = new() { MaxLength = 200 };
    private readonly TextBox bodyInput = new()
    {
        AcceptsReturn = true, AcceptsTab = true, TextWrapping = TextWrapping.Wrap, MinHeight = 190,
        VerticalScrollBarVisibility = ScrollBarVisibility.Auto, VerticalContentAlignment = VerticalAlignment.Top,
    };
    private readonly TextBlock error = new() { Foreground = (Brush)Application.Current.FindResource("Danger"), TextWrapping = TextWrapping.Wrap };
    private readonly TextBlock notice = new() { Style = (Style)Application.Current.FindResource("Caption"), TextWrapping = TextWrapping.Wrap };
    private readonly Button importButton = new() { Content = "导入粘贴原文", Style = (Style)Application.Current.FindResource("InkButton") };
    private readonly Button fileButton = new() { Content = "选择 TXT / MD 文件", Style = (Style)Application.Current.FindResource("Outline") };
    private readonly Button cancelButton = new() { Content = "取消修改", Style = (Style)Application.Current.FindResource("Ghost") };
    private readonly Button parseButton = new() { Content = "生成漫画剧本", Style = (Style)Application.Current.FindResource("InkButton") };
    private readonly Button planButton = new() { Content = "从剧本计算分页", Style = (Style)Application.Current.FindResource("InkButton") };
    private readonly StackPanel chapterList = new();
    private readonly TextBlock chapterCount = new() { Style = (Style)Application.Current.FindResource("Caption") };
    private readonly Border undoBanner = Notice("章节已移入回收状态", "warn");
    private readonly TextBlock composeFooter = Caption("不会限制总页数 · 单页硬上限 180 个中文字符");
    private readonly ScrollViewer scroller = new() { VerticalScrollBarVisibility = ScrollBarVisibility.Auto };

    private List<ChapterItem> chapters = [];
    private string? editingChapterId;
    private bool importing, revisionLoading;
    private int revisionToken;
    private string? pendingRestoreChapterId;
    private string sourceType = "PASTE";

    public SourceView()
    {
        var panel = new StackPanel { Margin = new Thickness(4, 0, 24, 28) };
        var header = new Border { Style = (Style)Application.Current.FindResource("CanvasHeader") };
        var headerGrid = new Grid();
        headerGrid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        headerGrid.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        var heading = new StackPanel();
        heading.Children.Add(new TextBlock { Text = "SOURCE", Style = (Style)Application.Current.FindResource("SectionIndex") });
        heading.Children.Add(new TextBlock
        {
            Text = "原作 · 完整导入，不压缩故事",
            FontFamily = (FontFamily)Application.Current.FindResource("Serif"),
            FontSize = 21, FontWeight = FontWeights.Bold, Margin = new Thickness(0, 5, 0, 0),
        });
        headerGrid.Children.Add(heading);
        chapterCount.VerticalAlignment = VerticalAlignment.Bottom;
        Grid.SetColumn(chapterCount, 1);
        headerGrid.Children.Add(chapterCount);
        header.Child = headerGrid;
        panel.Children.Add(header);
        panel.Children.Add(BuildComposeCard());
        undoBanner.Visibility = Visibility.Collapsed;
        var restore = Act("撤回删除", RestoreChapter, "Outline");
        var bannerRow = new StackPanel { Orientation = Orientation.Horizontal };
        bannerRow.Children.Add(new TextBlock
        {
            Text = "章节已移入回收状态", VerticalAlignment = VerticalAlignment.Center,
            Style = (Style)Application.Current.FindResource("Caption"),
        });
        restore.Margin = new Thickness(12, 0, 0, 0);
        bannerRow.Children.Add(restore);
        undoBanner.Child = bannerRow;
        undoBanner.Visibility = Visibility.Collapsed;
        panel.Children.Add(undoBanner);
        panel.Children.Add(new TextBlock { Text = "章节登记 / CHAPTERS", Style = (Style)Application.Current.FindResource("SectionIndex"), Margin = new Thickness(0, 10, 0, 10) });
        panel.Children.Add(chapterList);
        scroller.Content = panel;
        Content = scroller;
    }

    private Border BuildComposeCard()
    {
        var form = new StackPanel();
        titleInput.Text = "第一章";
        System.Windows.Automation.AutomationProperties.SetName(titleInput, "章节标题");
        form.Children.Add(FieldLabel("章节标题"));
        form.Children.Add(titleInput);
        System.Windows.Automation.AutomationProperties.SetName(bodyInput, "章节原文");
        bodyInput.Margin = new Thickness(0, 14, 0, 0);
        form.Children.Add(bodyInput);
        error.Margin = new Thickness(0, 10, 0, 0);
        form.Children.Add(error);
        notice.Margin = new Thickness(0, 10, 0, 0);
        form.Children.Add(notice);
        var actions = new StackPanel { Orientation = Orientation.Horizontal, Margin = new Thickness(0, 14, 0, 0) };
        fileButton.Margin = new Thickness(0, 0, 10, 0);
        cancelButton.Margin = new Thickness(0, 0, 10, 0);
        cancelButton.Visibility = Visibility.Collapsed;
        actions.Children.Add(fileButton);
        actions.Children.Add(cancelButton);
        actions.Children.Add(importButton);
        form.Children.Add(actions);
        composeFooter.Margin = new Thickness(0, 10, 0, 0);
        form.Children.Add(composeFooter);
        importButton.Click += Submit;
        fileButton.Click += PickFile;
        cancelButton.Click += CancelEdit;
        return new Border { Style = (Style)Application.Current.FindResource("Card"), Padding = new Thickness(22), Child = form };
    }

    public override async void Activate(WorkspaceContext context)
    {
        base.Activate(context);
        parseButton.Click -= ParseChapter;
        planButton.Click -= PlanChapter;
        parseButton.Click += ParseChapter;
        planButton.Click += PlanChapter;
        await LoadChaptersAsync();
    }

    private async Task LoadChaptersAsync()
    {
        chapterList.Children.Clear();
        var spinner = new StackPanel { Orientation = Orientation.Horizontal };
        spinner.Children.Add(new Spinner { Size = 16 });
        var hint = Caption("正在读取章节…");
        hint.Margin = new Thickness(10, 0, 0, 0);
        spinner.Children.Add(hint);
        chapterList.Children.Add(spinner);
        try
        {
            var rows = await Api.SendAsync($"projects/{ProjectId}/chapters", cancellation: lifetime.Token);
            if (lifetime.Token.IsCancellationRequested) return;
            chapters = rows.EnumerateArray().Select(ChapterItem.From).ToList();
            RenderChapters();
        }
        catch (Exception error) when (error is not OperationCanceledException)
        {
            chapterList.Children.Clear();
            chapterList.Children.Add(Caption($"章节列表读取失败：{error.Message}"));
        }
    }

    private void RenderChapters()
    {
        chapterList.Children.Clear();
        chapterCount.Text = $"{chapters.Count} 个章节";
        if (chapters.Count == 0)
        {
            var empty = new StackPanel();
            empty.Children.Add(new TextBlock
            {
                Text = "尚未导入原作", FontFamily = (FontFamily)Application.Current.FindResource("Serif"),
                FontSize = 18, FontWeight = FontWeights.Bold,
            });
            empty.Children.Add(Caption("粘贴一个完整章节开始工作。"));
            var card = new Border { Style = (Style)Application.Current.FindResource("Card"), Padding = new Thickness(26), Child = empty };
            chapterList.Children.Add(card);
            return;
        }
        foreach (var chapter in chapters)
        {
            var row = new Border
            {
                Style = (Style)Application.Current.FindResource("Card"),
                Padding = new Thickness(18, 12, 14, 12),
                Margin = new Thickness(0, 0, 0, 8),
                Tag = chapter,
            };
            var grid = new Grid();
            grid.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
            grid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
            grid.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
            grid.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
            var index = new TextBlock
            {
                Text = chapter.Ordinal.ToString("D2"), Style = (Style)Application.Current.FindResource("SectionIndex"),
                VerticalAlignment = VerticalAlignment.Center, Margin = new Thickness(0, 0, 16, 0),
            };
            grid.Children.Add(index);
            var info = new StackPanel();
            var name = new Button
            {
                Content = chapter.Title, Style = (Style)Application.Current.FindResource("Nav"),
                FontWeight = FontWeights.Bold, FontSize = 14,
            };
            name.Click += (_, _) => OpenReader(chapter);
            info.Children.Add(name);
            var meta = Caption($"{chapter.Characters:N0} 字 · {chapter.Pages} 页 · {chapter.StatusLabel}");
            meta.Margin = new Thickness(0, 3, 0, 0);
            info.Children.Add(meta);
            grid.Children.Add(info);
            var coverage = new Border
            {
                Background = (Brush)Application.Current.FindResource("PaperDeep"),
                Padding = new Thickness(9, 3, 9, 3), CornerRadius = new CornerRadius(3),
                VerticalAlignment = VerticalAlignment.Center,
            };
            coverage.Child = new TextBlock { Text = $"{chapter.Coverage}% 覆盖", FontSize = 11 };
            Grid.SetColumn(coverage, 2);
            coverage.Margin = new Thickness(14, 0, 14, 0);
            grid.Children.Add(coverage);
            var actions = new StackPanel { Orientation = Orientation.Horizontal, VerticalAlignment = VerticalAlignment.Center };
            var edit = Act("修改原文", (_, _) => _ = EditChapter(chapter));
            var remove = Act("删除", (_, _) => _ = DeleteChapter(chapter), "DangerButton");
            remove.Margin = new Thickness(8, 0, 0, 0);
            actions.Children.Add(edit);
            actions.Children.Add(remove);
            Grid.SetColumn(actions, 3);
            grid.Children.Add(actions);
            row.Child = grid;
            chapterList.Children.Add(row);
        }
        RenderWorkflowActions();
    }

    private void RenderWorkflowActions()
    {
        var selected = chapters.FirstOrDefault();
        if (selected == null) return;
        var panel = new StackPanel { Orientation = Orientation.Horizontal, Margin = new Thickness(0, 16, 0, 0) };
        var scriptReady = selected.Status is "SCRIPT_READY" or "PAGES_PLANNED";
        parseButton.IsEnabled = selected.Pages == 0;
        if (selected.Pages > 0) parseButton.Content = "已有分页，请先删除剧本";
        else parseButton.Content = "生成漫画剧本";
        planButton.IsEnabled = scriptReady;
        panel.Children.Add(parseButton);
        planButton.Margin = new Thickness(10, 0, 0, 0);
        panel.Children.Add(planButton);
        var hint = Caption("针对第一章；完整流程也可以在剧本页操作。");
        hint.Margin = new Thickness(14, 0, 0, 0);
        hint.VerticalAlignment = VerticalAlignment.Center;
        panel.Children.Add(hint);
        chapterList.Children.Add(panel);
    }

    private void OpenReader(ChapterItem chapter) => _ = ReadChapterAsync(chapter);

    private async Task ReadChapterAsync(ChapterItem chapter)
    {
        State.ReaderTitle = chapter.Title;
        State.ReaderText = "正在读取原文…";
        try
        {
            var revisions = await Api.SendAsync($"chapters/{chapter.Id}/revisions", cancellation: lifetime.Token);
            if (lifetime.Token.IsCancellationRequested) return;
            var latest = revisions.EnumerateArray().OrderByDescending(r => r.Number("revision")).FirstOrDefault();
            State.ReaderText = latest.ValueKind == JsonValueKind.Undefined ? "这个章节尚无原文。" : latest.Text("original_text");
        }
        catch (Exception error) when (error is not OperationCanceledException)
        {
            State.ReaderText = $"原文修订加载失败：{error.Message}";
        }
    }

    private async Task EditChapter(ChapterItem chapter)
    {
        if (bodyInput.Text.Trim().Length > 0 &&
            MessageBox.Show(Host, $"当前输入框已有未导入的原文（{bodyInput.Text.Trim().Length} 字），载入章节修订会覆盖它。继续吗？",
                "修改原文", MessageBoxButton.YesNo, MessageBoxImage.Question) != MessageBoxResult.Yes) return;
        var token = ++revisionToken;
        revisionLoading = true;
        bodyInput.IsEnabled = false;
        error.Text = "";
        try
        {
            var revisions = await Api.SendAsync($"chapters/{chapter.Id}/revisions", cancellation: lifetime.Token);
            if (token != revisionToken || lifetime.Token.IsCancellationRequested) return;
            var latest = revisions.EnumerateArray().OrderByDescending(r => r.Number("revision")).FirstOrDefault();
            titleInput.Text = chapter.Title;
            bodyInput.Text = latest.Text("original_text");
            editingChapterId = chapter.Id;
            importButton.Content = "保存新修订";
            cancelButton.Visibility = Visibility.Visible;
            fileButton.Visibility = Visibility.Collapsed;
            composeFooter.Text = "保存后生成新修订，旧版本仍保留";
            scroller.ScrollToHome();
        }
        catch (Exception ex) when (ex is not OperationCanceledException)
        {
            error.Text = $"原文修订加载失败：{ex.Message}";
        }
        finally
        {
            if (token == revisionToken) revisionLoading = false;
            bodyInput.IsEnabled = true;
        }
    }

    private void CancelEdit(object sender, RoutedEventArgs e)
    {
        if (MessageBox.Show(Host, "取消修改会丢弃输入框中的全部文本。继续吗？", "取消修改",
            MessageBoxButton.YesNo, MessageBoxImage.Question) != MessageBoxResult.Yes) return;
        ResetCompose();
    }

    private void ResetCompose()
    {
        titleInput.Text = "第一章";
        bodyInput.Text = "";
        editingChapterId = null;
        importButton.Content = "导入粘贴原文";
        cancelButton.Visibility = Visibility.Collapsed;
        fileButton.Visibility = Visibility.Visible;
        composeFooter.Text = "不会限制总页数 · 单页硬上限 180 个中文字符";
        error.Text = "";
    }

    private async void PickFile(object sender, RoutedEventArgs e)
    {
        var picker = new OpenFileDialog { Filter = "文本原作|*.txt;*.md;*.markdown", Title = "选择原作文件" };
        if (picker.ShowDialog(Host) != true) return;
        try
        {
            if (new FileInfo(picker.FileName).Length > 8_000_000)
                throw new IOException("文件过大，请选择不超过 8 MB 的文本文件。");
            var content = await File.ReadAllTextAsync(picker.FileName, new UTF8Encoding(false, true));
            if (content.Length > 2_000_000) throw new IOException("正文超过 200 万字符，请分批导入。");
            bodyInput.Text = content;
            titleInput.Text = Path.GetFileNameWithoutExtension(picker.FileName);
            sourceType = Path.GetExtension(picker.FileName).Equals(".txt", StringComparison.OrdinalIgnoreCase) ? "TXT" : "MARKDOWN";
            error.Text = "";
        }
        catch (DecoderFallbackException) { error.Text = "文件不是有效的 UTF-8 编码。请先另存为 UTF-8 后导入。"; }
        catch (Exception reason) { error.Text = reason.Message; }
    }

    private async void Submit(object sender, RoutedEventArgs e)
    {
        if (importing || revisionLoading) return;
        var title = titleInput.Text.Trim();
        if (title.Length == 0) { error.Text = "请填写章节标题。"; return; }
        if (string.IsNullOrWhiteSpace(bodyInput.Text) || bodyInput.Text.Length > 2_000_000)
        { error.Text = "请输入 1–200 万字符的正文。"; return; }
        importing = true;
        importButton.IsEnabled = false;
        notice.Text = "";
        error.Text = "";
        try
        {
            if (editingChapterId is { } chapterId)
            {
                await Api.SendAsync($"chapters/{chapterId}/revisions", HttpMethod.Post,
                    new { title, text = bodyInput.Text, source_type = "PASTE" }, cancellation: lifetime.Token);
                notice.Text = "已保存为新修订。需要时可在下方章节上点击“修改原文”查看历史版本。";
                ResetCompose();
            }
            else
            {
                var result = await Api.SendAsync($"projects/{ProjectId}/sources/import", HttpMethod.Post,
                    new { title, text = bodyInput.Text, source_type = sourceType }, cancellation: lifetime.Token);
                var id = result.Array("chapters").FirstOrDefault().Text("id");
                notice.Text = $"已导入「{title}」。下一步：点击“生成漫画剧本”把这一章结构化成场景与情节拍。";
                ResetCompose();
                await LoadChaptersAsync();
                _ = id;
            }
            Cache.Invalidate("chapters:" + ProjectId, "dashboard");
        }
        catch (Exception reason) when (reason is not OperationCanceledException)
        {
            error.Text = reason is OperationCanceledException or TimeoutException
                ? "请求超时，服务可能已保存。请先刷新确认，避免重复提交。" : reason.Message;
        }
        finally
        {
            importing = false;
            importButton.IsEnabled = true;
        }
    }

    private async Task DeleteChapter(ChapterItem chapter)
    {
        if (MessageBox.Show(Host, "删除后会暂时隐藏该章节，可立即撤回。继续吗？", "删除章节",
            MessageBoxButton.YesNo, MessageBoxImage.Question) != MessageBoxResult.Yes) return;
        try
        {
            await Api.SendOptionalAsync($"chapters/{chapter.Id}", HttpMethod.Delete, cancellation: lifetime.Token);
            pendingRestoreChapterId = chapter.Id;
            undoBanner.Visibility = Visibility.Visible;
            Cache.Invalidate("chapters:" + ProjectId, "dashboard");
            await LoadChaptersAsync();
        }
        catch (Exception error) when (error is not OperationCanceledException)
        {
            MessageBox.Show(Host, "章节删除失败，请重试：" + error.Message, "删除未完成", MessageBoxButton.OK, MessageBoxImage.Warning);
        }
    }

    private async void RestoreChapter(object sender, RoutedEventArgs e)
    {
        if (pendingRestoreChapterId == null) return;
        ((Button)sender).IsEnabled = false;
        try
        {
            await Api.SendAsync($"chapters/{pendingRestoreChapterId}/restore", HttpMethod.Post, cancellation: lifetime.Token);
            undoBanner.Visibility = Visibility.Collapsed;
            pendingRestoreChapterId = null;
            Cache.Invalidate("chapters:" + ProjectId, "dashboard");
            await LoadChaptersAsync();
        }
        catch (Exception error) when (error is not OperationCanceledException)
        {
            MessageBox.Show(Host, "章节撤回失败，请重试：" + error.Message, "撤回未完成", MessageBoxButton.OK, MessageBoxImage.Warning);
        }
        finally { ((Button)sender).IsEnabled = true; }
    }

    private async void ParseChapter(object sender, RoutedEventArgs e)
    {
        var chapter = chapters.FirstOrDefault();
        if (chapter == null || chapter.Pages > 0) return;
        try
        {
            await Api.SendAsync($"chapters/{chapter.Id}/parse", HttpMethod.Post, cancellation: lifetime.Token);
            State.Status = "剧本解析任务已创建";
            await Context!.NavigateSection("jobs", "");
        }
        catch (Exception error) when (error is not OperationCanceledException)
        {
            MessageBox.Show(Host, error.Message, "生成剧本未完成", MessageBoxButton.OK, MessageBoxImage.Warning);
        }
    }

    private async void PlanChapter(object sender, RoutedEventArgs e)
    {
        var chapter = chapters.FirstOrDefault();
        if (chapter == null || chapter.Status is not ("SCRIPT_READY" or "PAGES_PLANNED")) return;
        try
        {
            await Api.SendAsync($"chapters/{chapter.Id}/plan", HttpMethod.Post,
                new { replace_existing = true }, cancellation: lifetime.Token);
            Cache.Invalidate("pages:" + chapter.Id, "chapters:" + ProjectId);
            State.Status = "分页计算完成";
            await Context!.NavigateSection("storyboard", "");
        }
        catch (Exception error) when (error is not OperationCanceledException)
        {
            MessageBox.Show(Host, error.Message, "计算分页未完成", MessageBoxButton.OK, MessageBoxImage.Warning);
        }
    }

    public override Task<bool> ConfirmLeaveAsync()
    {
        if (bodyInput.Text.Trim().Length == 0) return Task.FromResult(true);
        var message = editingChapterId != null
            ? "当前场景的修改尚未保存，离开会丢弃这些内容。确定离开吗？"
            : "输入框中还有未导入的原文，离开会丢失这些内容。确定离开吗？";
        var result = MessageBox.Show(Host, message, "离开确认", MessageBoxButton.YesNo, MessageBoxImage.Question);
        return Task.FromResult(result == MessageBoxResult.Yes);
    }

    public override Task RefreshAsync()
    {
        _ = LoadChaptersAsync();
        return Task.CompletedTask;
    }
}
