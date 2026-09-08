using System.IO;
using System.Net.Http;
using System.Text.Json;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Controls.Primitives;
using System.Windows.Data;
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
        AcceptsReturn = true, AcceptsTab = true, TextWrapping = TextWrapping.Wrap, Height = 80, Padding = new Thickness(16), FontSize = 13,
        VerticalScrollBarVisibility = ScrollBarVisibility.Auto, VerticalContentAlignment = VerticalAlignment.Top,
    };
    private readonly TextBlock error = new() { Foreground = (Brush)Application.Current.FindResource("Danger"), TextWrapping = TextWrapping.Wrap };
    private readonly TextBlock notice = new() { Style = (Style)Application.Current.FindResource("Caption"), TextWrapping = TextWrapping.Wrap };
    private readonly Button importButton = new() { Content = "导入粘贴原文", Style = (Style)Application.Current.FindResource("InkButton") };
    private readonly Button fileButton = new() { Content = "选择 TXT / MD", Style = (Style)Application.Current.FindResource("Outline") };
    private readonly Button cancelButton = new() { Content = "取消修改", Style = (Style)Application.Current.FindResource("Ghost") };
    private readonly Button parseButton = new() { Content = "生成漫画剧本", Style = (Style)Application.Current.FindResource("InkButton") };
    private readonly Button planButton = new() { Content = "从剧本计算分页", Style = (Style)Application.Current.FindResource("InkButton") };
    private readonly StackPanel chapterList = new();
    private readonly TextBlock chapterCount = new() { Text = "0 个章节", Style = (Style)Application.Current.FindResource("Caption") };
    private readonly Border undoBanner = Notice("章节已移入回收状态", "warn");
    private readonly TextBlock undoBannerText = new()
    { Text = "章节已移入回收状态", Style = (Style)Application.Current.FindResource("Caption") };
    private readonly TextBlock composeFooter = Caption("不会限制总页数 · 单页硬上限 180 个中文字符");
    private readonly ScrollViewer scroller = new() { VerticalScrollBarVisibility = ScrollBarVisibility.Auto };

    private List<ChapterItem> chapters = [];
    private string? editingChapterId, activeChapterId;
    private bool workflowBusy;
    private int activation;
    private readonly StackPanel workflowActions = new() { Orientation = Orientation.Horizontal, HorizontalAlignment = HorizontalAlignment.Right, Margin = new Thickness(0, 18, 0, 0) };
    private bool importing, revisionLoading;
    private int revisionToken;
    // 连续删除多章时逐个入列；单槽会丢掉除最后一次删除外的撤回能力。
    private readonly List<string> pendingRestoreChapterIds = [];
    private string sourceType = "PASTE";

    public SourceView()
    {
        var panel = new StackPanel { Margin = new Thickness(0, 0, 0, 70) };
        var header = new Border { BorderBrush = (Brush)Application.Current.FindResource("Ink"), BorderThickness = new Thickness(0, 0, 0, 1), Padding = new Thickness(0, 6, 0, 17) };
        var heading = new StackPanel();
        heading.Children.Add(new TextBlock { Text = "S O U R C E  /  原作", FontSize = 9, FontWeight = FontWeights.Bold, Foreground = (Brush)Application.Current.FindResource("Muted") });
        heading.Children.Add(new TextBlock
        {
            Text = "完整导入，不压缩故事",
            FontFamily = (FontFamily)Application.Current.FindResource("Serif"),
            FontSize = 23, FontWeight = FontWeights.Normal, Margin = new Thickness(0, 10, 0, 0),
        });
        chapterCount.VerticalAlignment = VerticalAlignment.Bottom;
        header.Child = new PageHeading(heading, chapterCount);
        panel.Children.Add(header);
        panel.Children.Add(BuildComposeCard());
        undoBanner.Visibility = Visibility.Collapsed;
        var restore = Act("撤回删除", RestoreChapter, "Outline");
        var bannerRow = new StackPanel { Orientation = Orientation.Horizontal };
        undoBannerText.VerticalAlignment = VerticalAlignment.Center;
        bannerRow.Children.Add(undoBannerText);
        restore.Margin = new Thickness(12, 0, 0, 0);
        bannerRow.Children.Add(restore);
        undoBanner.Child = bannerRow;
        undoBanner.Visibility = Visibility.Collapsed;
        panel.Children.Add(new Border { BorderBrush = (Brush)Application.Current.FindResource("LineDark"), BorderThickness = new Thickness(1), Background = new SolidColorBrush(Color.FromArgb(102, 255, 255, 255)), Margin = new Thickness(0, 14, 0, 0), Padding = new Thickness(0, 18, 0, 14), Child = chapterList });
        panel.Children.Add(undoBanner);
        workflowActions.Children.Add(parseButton);
        workflowActions.Children.Add(planButton);
        parseButton.Style = (Style)Application.Current.FindResource("Outline");
        parseButton.MinHeight = planButton.MinHeight = 44;
        planButton.Margin = new Thickness(9, 0, 0, 0);
        panel.Children.Add(workflowActions);
        workflowActions.Visibility = Visibility.Collapsed;
        scroller.Content = panel;
        Content = scroller;
    }

    private Border BuildComposeCard()
    {
        var form = new StackPanel();
        titleInput.Text = "第一章";
        titleInput.Height = 48; titleInput.FontSize = 13;
        System.Windows.Automation.AutomationProperties.SetName(titleInput, "章节标题");
        form.Children.Add(titleInput);
        System.Windows.Automation.AutomationProperties.SetName(bodyInput, "章节原文");
        var body = new Grid { Margin = new Thickness(0, 10, 0, 0) };
        bodyInput.FontFamily = (FontFamily)Application.Current.FindResource("Serif");
        body.Children.Add(bodyInput);
        var placeholder = Caption("粘贴完整章节。系统先无损分段，再根据文字和剧本长度动态计算页数。");
        placeholder.Margin = new Thickness(17, 17, 17, 0); placeholder.FontSize = 12;
        placeholder.Opacity = .55; placeholder.TextWrapping = TextWrapping.Wrap; placeholder.IsHitTestVisible = false;
        body.Children.Add(placeholder); form.Children.Add(body);
        error.Margin = notice.Margin = new Thickness(0, 10, 0, 0);
        error.SetBinding(VisibilityProperty, new System.Windows.Data.Binding("Text") { Source = error, ConverterParameter = "collapse", Converter = (IValueConverter)Application.Current.FindResource("EmptyToCollapsed") });
        notice.SetBinding(VisibilityProperty, new System.Windows.Data.Binding("Text") { Source = notice, ConverterParameter = "collapse", Converter = (IValueConverter)Application.Current.FindResource("EmptyToCollapsed") });
        form.Children.Add(error); form.Children.Add(notice);
        var footer = new Grid { Margin = new Thickness(0, 16, 0, 0) };
        footer.ColumnDefinitions.Add(new ColumnDefinition());
        footer.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        composeFooter.FontSize = 9; composeFooter.VerticalAlignment = VerticalAlignment.Center;
        composeFooter.TextWrapping = TextWrapping.Wrap; composeFooter.Margin = new Thickness(0, 0, 16, 0);
        footer.Children.Add(composeFooter);
        var actions = new StackPanel { Orientation = Orientation.Horizontal };
        fileButton.Margin = cancelButton.Margin = new Thickness(0, 0, 8, 0);
        fileButton.MinHeight = cancelButton.MinHeight = importButton.MinHeight = 44;
        fileButton.Content = SourceIcon.Label("file", "选择 TXT / MD");
        importButton.Content = SourceIcon.Label("upload", "导入粘贴原文");
        cancelButton.Visibility = Visibility.Collapsed;
        actions.Children.Add(fileButton); actions.Children.Add(cancelButton); actions.Children.Add(importButton);
        Grid.SetColumn(actions, 1); footer.Children.Add(actions); form.Children.Add(footer);
        importButton.Click += Submit; fileButton.Click += PickFile; cancelButton.Click += CancelEdit;
        bodyInput.TextChanged += (_, _) => { placeholder.Visibility = bodyInput.Text.Length == 0 ? Visibility.Visible : Visibility.Collapsed; importButton.IsEnabled = !importing && !revisionLoading && !string.IsNullOrWhiteSpace(bodyInput.Text); };
        importButton.IsEnabled = false;
        return new Border { BorderBrush = (Brush)Application.Current.FindResource("LineDark"), BorderThickness = new Thickness(1), Background = new SolidColorBrush(Color.FromArgb(102, 255, 255, 255)), Margin = new Thickness(0, 18, 0, 0), Padding = new Thickness(18), Child = form };
    }

    public override async void Activate(WorkspaceContext context)
    {
        var changed = ProjectId != context.ProjectId;
        base.Activate(context);
        activation++;
        workflowBusy = importing = revisionLoading = false;
        SetComposeEnabled(true);
        if (changed) { ResetCompose(); titleInput.Text = "第一章"; notice.Text = ""; pendingRestoreChapterIds.Clear(); undoBanner.Visibility = Visibility.Collapsed; activeChapterId = null; }
        activeChapterId ??= KeyValueStore.Get("workspace:chapter:" + ProjectId);
        parseButton.Click -= ParseChapter;
        planButton.Click -= PlanChapter;
        parseButton.Click += ParseChapter;
        planButton.Click += PlanChapter;
        // Deactivation cancels in-flight reads; async void has no caller to observe the
        // cancellation, so swallow it here instead of crashing the dispatcher.
        try { await LoadChaptersAsync(); }
        catch (OperationCanceledException) { }
    }

    public override void Deactivate() { activation++; base.Deactivate(); }

    private async Task LoadChaptersAsync()
    {
        var epoch = activation; var cancellation = lifetime.Token;
        workflowActions.Visibility = Visibility.Collapsed;
        chapterList.Children.Clear();
        var spinner = new StackPanel { Orientation = Orientation.Horizontal };
        spinner.Children.Add(new Spinner { Size = 16 });
        var hint = Caption("正在读取章节…");
        hint.Margin = new Thickness(10, 0, 0, 0);
        spinner.Children.Add(hint);
        chapterList.Children.Add(spinner);
        try
        {
            var rows = await Api.SendAsync($"projects/{ProjectId}/chapters", cancellation: cancellation);
            if (epoch != activation || cancellation.IsCancellationRequested) return;
            chapters = rows.EnumerateArray().Select(ChapterItem.From).ToList();
            activeChapterId = chapters.FirstOrDefault(c => c.Id == activeChapterId)?.Id ?? chapters.FirstOrDefault()?.Id;
            RenderChapters();
        }
         catch (OperationCanceledException) { }
        catch (Exception error) when (error is not OperationCanceledException)
        {
            if (epoch != activation || cancellation.IsCancellationRequested) return;
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
            workflowActions.Visibility = Visibility.Collapsed;
            return;
        }
        foreach (var chapter in chapters)
        {
            var selected = chapter.Id == activeChapterId;
            var row = new Border { BorderThickness = new Thickness(3, 0, 0, 0), BorderBrush = selected ? (Brush)Application.Current.FindResource("Accent") : Brushes.Transparent, Background = selected ? new SolidColorBrush(Color.FromArgb(158, 255, 255, 255)) : Brushes.Transparent, Tag = chapter };
            var grid = new Grid { MinHeight = 68, Margin = new Thickness(15, 0, 18, 0) };
            foreach (var width in new[] { new GridLength(42), new GridLength(1, GridUnitType.Star), GridLength.Auto, GridLength.Auto }) grid.ColumnDefinitions.Add(new ColumnDefinition { Width = width });
            grid.Children.Add(new TextBlock { Text = chapter.Ordinal.ToString("D2"), Foreground = (Brush)Application.Current.FindResource("Muted"), VerticalAlignment = VerticalAlignment.Center });
            var info = new StackPanel();
            info.Children.Add(new TextBlock { Text = chapter.Title, FontSize = 13, FontFamily = (FontFamily)Application.Current.FindResource("Serif"), TextTrimming = TextTrimming.CharacterEllipsis });
            var meta = Caption($"{chapter.Characters} 字 · {chapter.Segments} 段 · {chapter.Pages} 页 · {chapter.StatusLabel}");
            meta.FontSize = 12; meta.Margin = new Thickness(0, 6, 0, 0); info.Children.Add(meta);
            var choose = new ToggleButton { Content = info, Tag = chapter.Id, IsChecked = selected, Style = (Style)Application.Current.FindResource("SourceChapterChoice") };
            System.Windows.Automation.AutomationProperties.SetName(choose, "选择章节 " + chapter.Title);
            choose.Click += (_, _) => { activeChapterId = chapter.Id; KeyValueStore.Set("workspace:chapter:" + ProjectId, chapter.Id); RenderChapters(); };
            Grid.SetColumn(choose, 1); grid.Children.Add(choose);
            var coverage = new TextBlock { Text = $"{chapter.Coverage}% 覆盖", FontSize = 12, FontWeight = FontWeights.Bold, Foreground = (Brush)Application.Current.FindResource("Success"), VerticalAlignment = VerticalAlignment.Center, Margin = new Thickness(10, 0, 10, 0) };
            Grid.SetColumn(coverage, 2); grid.Children.Add(coverage);
            var edit = SourceIcon.Action("edit", $"修改 {chapter.Title} 的原文", (_, _) => _ = EditChapter(chapter));
            var remove = SourceIcon.Action("trash", $"删除章节 {chapter.Title}", (_, _) => _ = DeleteChapter(chapter));
            remove.Margin = new Thickness(4, 0, 0, 0);
            var actions = Row(edit, remove); actions.VerticalAlignment = VerticalAlignment.Center;
            Grid.SetColumn(actions, 3); grid.Children.Add(actions);
            row.Child = grid; chapterList.Children.Add(row);
        }
        RenderWorkflowActions();
    }

    private int scriptRequest;
    private bool scriptReady;
    private async void RenderWorkflowActions()
    {
        var selected = chapters.FirstOrDefault(c => c.Id == activeChapterId);
        workflowActions.Visibility = selected == null ? Visibility.Collapsed : Visibility.Visible;
        if (selected == null) return;
        parseButton.Content = SourceIcon.Label("sparkles", selected.Pages > 0 ? "已有分页，请先删除剧本" : "生成漫画剧本");
        planButton.Content = SourceIcon.Label("panel", "从剧本计算分页");
        parseButton.IsEnabled = selected.Pages == 0 && !workflowBusy;
        scriptReady = false; planButton.IsEnabled = false;
        var epoch = activation; var token = lifetime.Token; var request = ++scriptRequest;
        try
        {
            var script = await Api.SendAsync($"chapters/{selected.Id}/script", cancellation: token);
            if (epoch != activation || token.IsCancellationRequested || request != scriptRequest || activeChapterId != selected.Id) return;
            scriptReady = script.Text("status") == "READY";
            planButton.IsEnabled = scriptReady && !workflowBusy;
        }
        catch (OperationCanceledException) { }
        catch (Exception ex) { if (epoch == activation && !token.IsCancellationRequested && request == scriptRequest) error.Text = "剧本状态读取失败，请刷新重试：" + ex.Message; }
    }

    private async Task EditChapter(ChapterItem chapter)
    {
        if (importing || revisionLoading || workflowBusy) return;
        if (bodyInput.Text.Trim().Length > 0 &&
            MessageBox.Show(Host, $"当前输入框已有未导入的原文（{bodyInput.Text.Trim().Length} 字），载入章节修订会覆盖它。继续吗？",
                "修改原文", MessageBoxButton.YesNo, MessageBoxImage.Question) != MessageBoxResult.Yes) return;
        var epoch = activation; var cancellation = lifetime.Token;
        var token = ++revisionToken;
        activeChapterId = chapter.Id; RenderChapters();
        revisionLoading = true;
        bodyInput.IsEnabled = false;
        error.Text = "";
        try
        {
            var revisions = await Api.SendAsync($"chapters/{chapter.Id}/revisions", cancellation: cancellation);
            if (epoch != activation || token != revisionToken || cancellation.IsCancellationRequested) return;
            var latest = revisions.EnumerateArray().OrderByDescending(r => r.Number("revision")).FirstOrDefault();
            titleInput.Text = chapter.Title;
            bodyInput.Text = latest.Text("original_text");
            editingChapterId = chapter.Id;
            importButton.Content = SourceIcon.Label("save", "保存新修订");
            cancelButton.Visibility = Visibility.Visible;
            fileButton.Visibility = Visibility.Collapsed;
            composeFooter.Text = "保存后生成新修订，旧版本仍保留";
            scroller.ScrollToHome();
        }
         catch (OperationCanceledException) { }
        catch (Exception ex) when (ex is not OperationCanceledException)
        {
            if (epoch == activation && !cancellation.IsCancellationRequested) error.Text = $"原文修订加载失败：{ex.Message}";
        }
        finally
        {
            if (epoch == activation && token == revisionToken) { revisionLoading = false; bodyInput.IsEnabled = true; importButton.IsEnabled = !string.IsNullOrWhiteSpace(bodyInput.Text); }
        }
    }

    private void CancelEdit(object sender, RoutedEventArgs e)
    {
        if (bodyInput.Text.Trim().Length > 0 && MessageBox.Show(Host, "取消修改会丢弃输入框中的全部文本。继续吗？", "取消修改",
            MessageBoxButton.YesNo, MessageBoxImage.Question) != MessageBoxResult.Yes) return;
        ResetCompose();
    }

    private void ResetCompose()
    {
        bodyInput.Text = "";
        sourceType = "PASTE";
        editingChapterId = null;
        importButton.Content = SourceIcon.Label("upload", "导入粘贴原文");
        cancelButton.Visibility = Visibility.Collapsed;
        fileButton.Visibility = Visibility.Visible;
        composeFooter.Text = "不会限制总页数 · 单页硬上限 180 个中文字符";
        error.Text = "";
    }

    private async void PickFile(object sender, RoutedEventArgs e)
    {
        if (importing || revisionLoading) return;
        var picker = new OpenFileDialog { Filter = "文本原作|*.txt;*.md;*.markdown", Title = "选择原作文件" };
        if (picker.ShowDialog(Host) == true) await ImportFileAsync(picker.FileName);
    }

    private async Task ImportFileAsync(string fileName)
    {
        if (importing || revisionLoading) return;
        var epoch = activation; var token = lifetime.Token; var projectId = ProjectId;
        var title = string.IsNullOrWhiteSpace(titleInput.Text) ? Path.GetFileNameWithoutExtension(fileName) : titleInput.Text.Trim();
        importing = true; SetComposeEnabled(false); error.Text = notice.Text = "";
        try
        {
            var bytes = await File.ReadAllBytesAsync(fileName, token);
            if (epoch != activation || token.IsCancellationRequested) return;
            var result = await Api.UploadAsync($"projects/{projectId}/sources/upload", new Dictionary<string, string> { ["title"] = title },
                ("file", Path.GetFileName(fileName), Path.GetExtension(fileName).Equals(".txt", StringComparison.OrdinalIgnoreCase) ? "text/plain" : "text/markdown", bytes), token);
            if (epoch != activation || token.IsCancellationRequested) return;
            activeChapterId = result.Array("chapters").FirstOrDefault().Text("id");
            ResetCompose(); notice.Text = $"已导入「{title}」。";
            Cache.Invalidate("chapters:" + projectId, "dashboard"); await LoadChaptersAsync();
        }
        catch (OperationCanceledException) { }
        catch (Exception ex) { if (epoch == activation) error.Text = ex.Message; }
        finally { if (epoch == activation) { importing = false; SetComposeEnabled(true); } }
    }

    private void SetComposeEnabled(bool enabled)
    {
        titleInput.IsEnabled = bodyInput.IsEnabled = fileButton.IsEnabled = cancelButton.IsEnabled = enabled;
        importButton.IsEnabled = enabled && !string.IsNullOrWhiteSpace(bodyInput.Text);
    }

    private async void Submit(object sender, RoutedEventArgs e)
    {
        if (importing || revisionLoading) return;
        var title = titleInput.Text.Trim();
        if (title.Length == 0) { error.Text = "请填写章节标题。"; return; }
        if (string.IsNullOrWhiteSpace(bodyInput.Text) || bodyInput.Text.Length > 2_000_000)
        { error.Text = "请输入 1–200 万字符的正文。"; return; }
        var epoch = activation; var cancellation = lifetime.Token;
        importing = true;
        SetComposeEnabled(false);
        notice.Text = "";
        error.Text = "";
        try
        {
            if (editingChapterId is { } chapterId)
            {
                await Api.SendAsync($"chapters/{chapterId}/revisions", HttpMethod.Post,
                    new { title, text = bodyInput.Text, source_type = "PASTE" }, cancellation: cancellation);
                if (epoch != activation || cancellation.IsCancellationRequested) return;
                notice.Text = "已保存为新修订。需要时可在下方章节上点击“修改原文”查看历史版本。";
                ResetCompose();
                await LoadChaptersAsync();
            }
            else
            {
                var result = await Api.SendAsync($"projects/{ProjectId}/sources/import", HttpMethod.Post,
                    new { title, text = bodyInput.Text, source_type = sourceType }, cancellation: cancellation);
                if (epoch != activation || cancellation.IsCancellationRequested) return;
                activeChapterId = result.Array("chapters").FirstOrDefault().Text("id");
                notice.Text = $"已导入「{title}」。下一步：点击“生成漫画剧本”把这一章结构化成场景与情节拍。";
                ResetCompose();
                await LoadChaptersAsync();
            }
            if (epoch == activation) Cache.Invalidate("chapters:" + ProjectId, "script:", "pages:", "dashboard");
        }
         catch (OperationCanceledException) { }
        catch (Exception reason) when (reason is not OperationCanceledException)
        {
            if (epoch != activation || cancellation.IsCancellationRequested) return;
            error.Text = reason is OperationCanceledException or TimeoutException
                ? "请求超时，服务可能已保存。请先刷新确认，避免重复提交。" : reason.Message;
        }
        finally
        {
            if (epoch == activation) { importing = false; SetComposeEnabled(true); }
        }
    }

    private async Task DeleteChapter(ChapterItem chapter)
    {
        if (MessageBox.Show(Host, "删除后会暂时隐藏该章节，可立即撤回。继续吗？", "删除章节",
            MessageBoxButton.YesNo, MessageBoxImage.Question) != MessageBoxResult.Yes) return;
        try
        {
            await Api.SendOptionalAsync($"chapters/{chapter.Id}", HttpMethod.Delete, cancellation: lifetime.Token);
            pendingRestoreChapterIds.Add(chapter.Id);
            undoBannerText.Text = pendingRestoreChapterIds.Count == 1
                ? "章节已移入回收状态"
                : $"已删除 {pendingRestoreChapterIds.Count} 章，可一次全部撤回";
            undoBanner.Visibility = Visibility.Visible;
            Cache.Invalidate("chapters:" + ProjectId, "dashboard");
            await LoadChaptersAsync();
        }
         catch (OperationCanceledException) { }
        catch (Exception error) when (error is not OperationCanceledException)
        {
            MessageBox.Show(Host, "章节删除失败，请重试：" + error.Message, "删除未完成", MessageBoxButton.OK, MessageBoxImage.Warning);
        }
    }

    private async void RestoreChapter(object sender, RoutedEventArgs e)
    {
        if (pendingRestoreChapterIds.Count == 0) return;
        ((Button)sender).IsEnabled = false;
        try
        {
            // 每章恢复成功即出列：部分失败时重试不会重复 restore 已恢复章节。
            while (pendingRestoreChapterIds.Count > 0)
            {
                var chapterId = pendingRestoreChapterIds[0];
                await Api.SendAsync($"chapters/{chapterId}/restore", HttpMethod.Post, cancellation: lifetime.Token);
                pendingRestoreChapterIds.RemoveAt(0);
            }
            undoBanner.Visibility = Visibility.Collapsed;
            Cache.Invalidate("chapters:" + ProjectId, "dashboard");
            await LoadChaptersAsync();
        }
         catch (OperationCanceledException) { }
        catch (Exception error) when (error is not OperationCanceledException)
        {
            if (pendingRestoreChapterIds.Count > 0)
                undoBannerText.Text = pendingRestoreChapterIds.Count == 1
                    ? "章节已移入回收状态"
                    : $"已删除 {pendingRestoreChapterIds.Count} 章，可一次全部撤回";
            MessageBox.Show(Host, "章节撤回失败，请重试：" + error.Message, "撤回未完成", MessageBoxButton.OK, MessageBoxImage.Warning);
        }
        finally { ((Button)sender).IsEnabled = true; }
    }

    private async void ParseChapter(object sender, RoutedEventArgs e) => await RunWorkflowAsync(false);
    private async void PlanChapter(object sender, RoutedEventArgs e) => await RunWorkflowAsync(true);
    private async Task RunWorkflowAsync(bool plan)
    {
        var chapter = chapters.FirstOrDefault(c => c.Id == activeChapterId);
        if (chapter == null || workflowBusy || (plan ? !scriptReady : chapter.Pages > 0)) return;
        var epoch = activation; var token = lifetime.Token; var context = Context!;
        workflowBusy = true; parseButton.IsEnabled = planButton.IsEnabled = false;
        try
        {
            var result = await Api.SendAsync($"chapters/{chapter.Id}/{(plan ? "plan" : "parse")}", HttpMethod.Post,
                plan ? new { replace_existing = true } : null, cancellation: token);
            if (epoch != activation || token.IsCancellationRequested) return;
            KeyValueStore.Set("workspace:chapter:" + ProjectId, chapter.Id);
            if (plan)
            {
                var pageId = result.Array("pages").FirstOrDefault().Text("id");
                if (pageId.Length > 0) KeyValueStore.Set("storyboard:page:" + ProjectId, pageId);
                Cache.Invalidate("pages:" + chapter.Id, "chapters:" + ProjectId);
            }
            State.Status = plan ? "分页计算完成" : "剧本解析任务已创建";
            await context.NavigateSection(plan ? "storyboard" : "jobs", "");
        }
        catch (OperationCanceledException) { }
        catch (Exception ex) { if (epoch == activation && !token.IsCancellationRequested) error.Text = ex.Message; }
        finally { if (epoch == activation) { workflowBusy = false; RenderWorkflowActions(); } }
    }

    public override Task<bool> ConfirmLeaveAsync()
    {
        if (bodyInput.Text.Trim().Length == 0) return Task.FromResult(true);
        var message = editingChapterId != null
            ? "当前章节的修改尚未保存，离开会丢弃这些内容。确定离开吗？"
            : "输入框中还有未导入的原文，离开会丢失这些内容。确定离开吗？";
        var result = MessageBox.Show(Host, message, "离开确认", MessageBoxButton.YesNo, MessageBoxImage.Question);
        return Task.FromResult(result == MessageBoxResult.Yes);
    }

    public override Task RefreshAsync() => LoadChaptersAsync();
}
