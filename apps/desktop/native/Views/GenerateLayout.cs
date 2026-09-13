using System.Net.Http;
using System.Text.Json;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;
using MangaFlow.Native.Controls;

namespace MangaFlow.Native.Views;

public sealed partial class GenerateView
{
    private readonly TextBlock generationTitle = new(), generationIndex = new();
    private readonly WrapPanel batchActions = new();
    private readonly StackPanel generationWarnings = new();
    private readonly Border generationNavigation = new();
    private bool diagnosticsOpen;
    private string LatestBatchId => workbench.Element("current_batch").Text("id",
        pageBatches.OrderByDescending(b => b.Number("ordinal")).FirstOrDefault().Text("id"));
    private bool ViewingHistory => viewedBatchId != null && viewedBatchId != LatestBatchId;

    private void BuildGenerationLayout()
    {
        var content = new StackPanel { Margin = new Thickness(0, 0, 14, 24), Background = AssetPageUi.Brush("Paper"), UseLayoutRounding = true, SnapsToDevicePixels = true };
        TextOptions.SetTextFormattingMode(content, TextFormattingMode.Display); RenderOptions.SetClearTypeHint(content, ClearTypeHint.Enabled);
        var noticeStyle = new Style(typeof(TextBlock), notice.Style);
        var emptyNotice = new DataTrigger { Binding = new System.Windows.Data.Binding("Text") { RelativeSource = new System.Windows.Data.RelativeSource(System.Windows.Data.RelativeSourceMode.Self) }, Value = "" };
        emptyNotice.Setters.Add(new Setter(VisibilityProperty, Visibility.Collapsed)); noticeStyle.Triggers.Add(emptyNotice); notice.Style = noticeStyle;
        generationIndex.FontSize = 10; generationIndex.FontWeight = FontWeights.Bold; generationIndex.Foreground = AssetPageUi.Brush("Muted");
        generationTitle.FontFamily = (FontFamily)FindResource("Serif"); generationTitle.FontSize = 24; generationTitle.TextWrapping = TextWrapping.Wrap; generationTitle.Margin = new Thickness(0, 10, 0, 0);
        var title = new StackPanel(); title.Children.Add(generationIndex); title.Children.Add(generationTitle);
        var modes = new StackPanel { Orientation = Orientation.Horizontal, VerticalAlignment = VerticalAlignment.Center };
        drawMode.MinHeight = directorMode.MinHeight = 44; drawMode.MinWidth = directorMode.MinWidth = 58;
        drawMode.Click += (_, _) => SwitchMode(false); directorMode.Click += (_, _) => SwitchMode(true);
        modes.Children.Add(drawMode); modes.Children.Add(directorMode);
        var right = new StackPanel { HorizontalAlignment = HorizontalAlignment.Right };
        right.Children.Add(new TextBlock { Text = "每次只生成 1 页", Foreground = AssetPageUi.Brush("Muted"), HorizontalAlignment = HorizontalAlignment.Right });
        chapterSelector.MinHeight = 38; chapterSelector.Margin = new Thickness(0, 8, 0, 0); right.Children.Add(chapterSelector);
        var header = new Grid(); header.ColumnDefinitions.Add(new ColumnDefinition()); header.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto }); header.ColumnDefinitions.Add(new ColumnDefinition());
        header.Children.Add(title); Grid.SetColumn(modes, 1); header.Children.Add(modes); Grid.SetColumn(right, 2); header.Children.Add(right);
        header.SizeChanged += (_, _) =>
        {
            bool narrow = header.ActualWidth < 720;
            header.RowDefinitions.Clear(); header.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
            if (narrow) header.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
            Grid.SetColumnSpan(title, narrow ? 3 : 1); Grid.SetRow(modes, narrow ? 1 : 0); Grid.SetColumn(modes, narrow ? 0 : 1);
            Grid.SetRow(right, narrow ? 1 : 0); modes.Margin = new Thickness(0, narrow ? 12 : 0, 0, 0);
        };
        content.Children.Add(new Border { Child = header, BorderBrush = AssetPageUi.Brush("Ink"), BorderThickness = new Thickness(0, 0, 0, 1), Padding = new Thickness(0, 0, 0, 18), Margin = new Thickness(0, 0, 0, 16) });
        content.Children.Add(notice); content.Children.Add(generationWarnings);
        generationNavigation.Child = new PageHeading(pageBar, batchActions); generationNavigation.Padding = new Thickness(0, 0, 0, 14); generationNavigation.Margin = new Thickness(0, 0, 0, 16); generationNavigation.BorderBrush = AssetPageUi.Brush("Line"); generationNavigation.BorderThickness = new Thickness(0, 0, 0, 1);
        content.Children.Add(generationNavigation); content.Children.Add(body);
        Content = new ScrollViewer { Content = content, VerticalScrollBarVisibility = ScrollBarVisibility.Auto, HorizontalScrollBarVisibility = ScrollBarVisibility.Disabled };
        RenderGenerationNavigation();
    }

    private void RenderGenerationNavigation()
    {
        generationIndex.Text = director ? "DIRECTOR / 导演台" : "DRAW / 单页抽卡";
        generationTitle.Text = currentPage == null ? "选择一页开始" : $"第 {currentPage.PageNumber} 页候选";
        chapterSelector.Visibility = chapters.Count > 1 ? Visibility.Visible : Visibility.Collapsed;
        generationNavigation.Visibility = director || currentPage == null ? Visibility.Collapsed : Visibility.Visible;
        generationWarnings.Children.Clear(); batchActions.Children.Clear();
        if (currentPage == null) return;
        if (!director && currentPage.ContinuityStatus == "NEEDS_REVIEW")
        {
            var words = new StackPanel(); words.Children.Add(new TextBlock { Text = "剧本或分镜已修改", FontWeight = FontWeights.Bold, Foreground = AssetPageUi.Brush("AccentInk") });
            words.Children.Add(new TextBlock { Text = "历史候选仍然保留，但可能不再对应当前脚本。建议重新抽卡并执行连续性检查。", TextWrapping = TextWrapping.Wrap, Margin = new Thickness(0, 6, 0, 0), Foreground = AssetPageUi.Brush("Muted") });
            var pageId = currentPage.Id;
            generationWarnings.Children.Add(new Border { Child = new PageHeading(words, Kit.Act("检查分镜", async (_, _) => await Context!.NavigateSection("storyboard", "page=" + Uri.EscapeDataString(pageId)), "Compact")), Padding = new Thickness(16), Background = AssetPageUi.Brush("WarningBg"), BorderBrush = AssetPageUi.Brush("Accent"), BorderThickness = new Thickness(1), Margin = new Thickness(0, 0, 0, 16) });
        }
        if (workbench.Element("page").Text("id") != currentPage.Id) return;
        var ordered = pageBatches.OrderBy(b => b.Number("ordinal")).ToList();
        var id = viewedBatchId ?? LatestBatchId;
        var index = ordered.FindIndex(b => b.Text("id") == id);
        void Add(FrameworkElement control) { control.Margin = new Thickness(5, 0, 0, 5); if (control is Control c) c.MinHeight = 42; batchActions.Children.Add(control); }
        if (ordered.Count > 0)
        {
            var previous = Kit.Act("← 上一批", async (_, _) => { if (index > 0) await ViewBatchAsync(ordered[index - 1].Text("id")); }, "Compact"); previous.IsEnabled = index > 0; Add(previous);
            var picker = Selector("浏览生成批次", 166);
            foreach (var batch in ordered.AsEnumerable().Reverse()) picker.Items.Add(new ComboBoxItem { Tag = batch.Text("id"), Content = $"批次 {batch.Number("ordinal")}" + (batch.Text("id") == LatestBatchId ? " · 最新" : "") });
            picker.SelectedItem = picker.Items.OfType<ComboBoxItem>().FirstOrDefault(i => Equals(i.Tag, id));
            picker.SelectionChanged += async (_, _) => { if (picker.SelectedItem is ComboBoxItem { Tag: string target } && target != id) await ViewBatchAsync(target); }; Add(picker);
            var next = Kit.Act("下一批 →", async (_, _) => { if (index >= 0 && index + 1 < ordered.Count) await ViewBatchAsync(ordered[index + 1].Text("id")); }, "Compact"); next.IsEnabled = index >= 0 && index + 1 < ordered.Count; Add(next);
        }
        var create = Kit.Act(pendingRows.Contains("batch") ? "正在创建…" : "＋ 新批次", async (_, _) => await StartBatchAsync(), "Compact");
        create.IsEnabled = workbench.Element("page").Text("id") == currentPage.Id && workbench.Element("readiness").Flag("ready") && !pendingRows.Contains("batch") && !pendingRows.Contains("generate"); Add(create);
    }

    private async Task StartBatchAsync()
    {
        if (currentPage == null || workbench.Element("page").Text("id") != currentPage.Id || !workbench.Element("readiness").Flag("ready") || pendingRows.Contains("generate") || !pendingRows.Add("batch")) return;
        var pageId = currentPage.Id; var token = lifetime.Token; RenderGenerationNavigation();
        try
        {
            await Api.SendAsync($"pages/{pageId}/batches", HttpMethod.Post, cancellation: token);
            if (token.IsCancellationRequested || currentPage?.Id != pageId) return;
            viewedBatchId = null; historicalCandidates = null; reviewCandidateId = null; panelError = null;
            await LoadWorkbenchAsync();
        }
        catch (OperationCanceledException) { }
        catch (Exception error) { if (!token.IsCancellationRequested && currentPage?.Id == pageId) notice.Text = "创建批次失败：" + error.Message; }
        finally
        {
            pendingRows.Remove("batch");
            if (!token.IsCancellationRequested)
            {
                // A batch request may finish after switching pages or opening a director draft.
                // Release the controls without replacing a live director editor.
                if (!director) Render(); else RenderGenerationNavigation();
            }
        }
    }

    private Border BuildPageContext()
    {
        var info = workbench.Element("page"); var caption = new StackPanel { MinWidth = 145 };
        caption.Children.Add(new TextBlock { Text = "PAGE LOAD", FontSize = 10, Foreground = AssetPageUi.Brush("Muted") });
        caption.Children.Add(new TextBlock { Text = $"{info.Number("estimated_text_chars")} 字", FontFamily = (FontFamily)FindResource("Serif"), FontSize = 24, Margin = new Thickness(0, 6, 0, 6) });
        caption.Children.Add(Kit.Caption($"{info.Number("panel_count")} 格 / {info.Number("estimated_bubbles")} 气泡"));
        var source = string.Concat(info.Element("source_coverage").Array("ranges").Select(r => r.Text("text")));
        if (source.Length > 180) source = source[..180];
        var text = new TextBlock { Text = source.Length > 0 ? source : "本页未提供原文摘要。", FontFamily = (FontFamily)FindResource("Serif"), FontSize = 16, TextWrapping = TextWrapping.Wrap, Foreground = AssetPageUi.Brush("Muted") };
        var row = new Grid(); row.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(165) }); row.ColumnDefinitions.Add(new ColumnDefinition()); row.Children.Add(caption); Grid.SetColumn(text, 1); row.Children.Add(text);
        return new Border { Child = row, Padding = new Thickness(16), BorderThickness = new Thickness(3, 0, 0, 0), BorderBrush = AssetPageUi.Brush("Accent"), Background = AssetPageUi.Brush("Surface"), Margin = new Thickness(0, 0, 0, 16) };
    }

    private static StackPanel ModelTileContent(JsonElement model)
    {
        var content = new StackPanel();
        content.Children.Add(new TextBlock { Text = model.Text("display_name"), FontWeight = FontWeights.Bold, FontSize = 12, TextWrapping = TextWrapping.Wrap });
        content.Children.Add(new TextBlock { Text = model.Text("provider_name", model.Text("provider")) + " · " + model.Text("model_id"), FontSize = 11, FontFamily = (FontFamily)Application.Current.FindResource("Mono"), TextWrapping = TextWrapping.Wrap, Margin = new Thickness(0, 6, 0, 0) });
        return content;
    }

    private Border BuildReadiness(JsonElement readiness)
    {
        var body = new StackPanel(); var heading = new StackPanel();
        heading.Children.Add(new TextBlock { Text = "PRODUCTION CHECK / 页面生产准备", FontSize = 10, FontWeight = FontWeights.Bold, Foreground = AssetPageUi.Brush("AccentInk") });
        heading.Children.Add(new TextBlock { Text = "服务器统一判断这页能不能正式生成", FontWeight = FontWeights.Bold, Margin = new Thickness(0, 6, 0, 0) });
        bool ready = readiness.Flag("ready");
        body.Children.Add(new Border { Child = new PageHeading(heading, Kit.Caption(ready ? "准备完成" : "存在阻塞项")), Padding = new Thickness(14) });
        if (ready)
            body.Children.Add(new Border { Child = new TextBlock { Text = "✓ 页面生产条件已全部满足，可以确认参考图后生成 1 个 1K 彩色候选。", Foreground = AssetPageUi.Brush("Success"), TextWrapping = TextWrapping.Wrap }, Background = AssetPageUi.Brush("SuccessBg"), Padding = new Thickness(14) });
        else
        {
            var blockers = readiness.Array("blockers");
            var warnings = new StackPanel();
            warnings.Children.Add(new TextBlock { Text = blockers.Count > 0 ? $"{blockers.Count} 项准备工作未完成" : "生产准备尚未就绪，请刷新后重试。", Foreground = AssetPageUi.Brush("Warning"), FontWeight = FontWeights.Bold });
            foreach (var blocker in blockers)
            {
                var route = RouteForBlocker(blocker, currentPage!.Id);
                warnings.Children.Add(new PageHeading(new TextBlock { Text = blocker.Text("message"), TextWrapping = TextWrapping.Wrap, Margin = new Thickness(0, 8, 0, 0) }, Kit.Act("去处理", async (_, _) => await Context!.NavigateSection(route.Section, route.Query), "Compact")));
            }
            body.Children.Add(new Border { Child = warnings, Padding = new Thickness(14), Background = AssetPageUi.Brush("WarningBg") });
        }
        var details = new StackPanel();
        void Line(string text) => details.Children.Add(new TextBlock { Text = text, TextWrapping = TextWrapping.Wrap, Margin = new Thickness(0, 6, 0, 6), FontSize = 12 });
        Line($"内容追溯 · 原文{(readiness.Flag("source_complete") ? "完整" : "未覆盖")} · 剧本{(readiness.Flag("script_complete") ? "完整" : "未完成")}");
        var visible = string.Join("、", readiness.Array("visible_characters").Select(c => c.Text("primary_name")));
        Line("实际出镜 · " + (visible.Length > 0 ? visible : "本页无实际出镜人物"));
        var style = readiness.Element("style"); Line("正式彩色风格 · " + style.Text("name", "尚未选择风格"));
        var provider = readiness.Element("provider"); var worker = readiness.Element("worker");
        Line($"可用图片模型 {provider.Number("usable_image_model_count")} 个 · 已验证 {provider.Number("auto_image_model_count")} 个");
        Line($"执行器 · {worker.Text("executor", "未提供")} · {worker.Text("queue_mode")} · {(worker.Flag("can_execute") ? "可执行" : "未就绪")}");
        var dialogues = workbench.Element("storyboard").Array("panels").SelectMany(p => p.Array("dialogues")).Select(d => d.Text("target_text")).Where(t => t.Length > 0).ToList();
        Line("目标中文 · " + (dialogues.Count > 0 ? string.Join("　", dialogues.Select((t, i) => $"{i + 1}. {t}")) : "本页没有对白或旁白。"));
        var expander = new Expander { Header = "查看原文覆盖、供应商目录与执行器诊断", IsExpanded = diagnosticsOpen, Content = details, Margin = new Thickness(14) };
        expander.Expanded += (_, _) => diagnosticsOpen = true; expander.Collapsed += (_, _) => diagnosticsOpen = false; body.Children.Add(expander);
        return new Border { Child = body, Background = AssetPageUi.Brush("Surface"), BorderBrush = AssetPageUi.Brush("Accent"), BorderThickness = new Thickness(1, 3, 1, 1), Margin = new Thickness(0, 0, 0, 16) };
    }
}
