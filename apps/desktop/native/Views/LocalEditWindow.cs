using System.ComponentModel;
using System.Net.Http;
using System.Text.Json;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;
using System.Windows.Media;
using System.Windows.Shapes;
using MangaFlow.Native.Controls;
using MangaFlow.Native.Services;

namespace MangaFlow.Native.Views;

/// <summary>Native mask editing over a locked adopted image; uses the same director preview/accept contract as web.</summary>
public sealed class LocalEditWindow : Window
{
    private readonly WorkspaceContext context;
    private readonly CandidateItem source;
    private readonly string pageId;
    private readonly Canvas canvas = new() { Background = Brushes.Transparent };
    private readonly Image artwork = new() { Stretch = Stretch.Fill };
    private readonly ScrollViewer viewport = new() { HorizontalScrollBarVisibility = ScrollBarVisibility.Auto, VerticalScrollBarVisibility = ScrollBarVisibility.Auto };
    private readonly ComboBox model = Kit.Selector("局部编辑模型", 280);
    private readonly ComboBox resolution = Kit.Selector("局部编辑输出清晰度", 150);
    private readonly TextBox instruction = new() { AcceptsReturn = true, MinHeight = 100, TextWrapping = TextWrapping.Wrap, MaxLength = 2000 };
    private readonly StackPanel editor = new(), preview = new();
    private readonly TextBlock notice = Kit.Caption("正在读取原图…");
    private readonly List<Point[]> regions = [];
    private readonly Stack<Point[][]> undo = new(), redo = new();
    private readonly List<Point> stroke = [];
    private readonly Polygon draft = new() { Fill = new SolidColorBrush(Color.FromArgb(80, 211, 74, 47)), Stroke = Brushes.OrangeRed, StrokeThickness = 2, IsHitTestVisible = false };
    private readonly List<Polygon> masks = [];
    private readonly CancellationTokenSource lifetime = new();
    // Poll period follows the runtime setting ui_poll_interval_seconds (default
    // 3000 ms), same as the MainWindow timers; windows opened after a settings
    // change pick up the new value, in-flight windows keep ticking on the old one.
    private readonly System.Windows.Threading.DispatcherTimer poll = new() { Interval = Services.PollInterval.Interval };
    private readonly List<JsonElement> capableModels;
    private string tool = "rect", commandId = "", groupId = "", acceptedId = "";
    private bool busy, loaded, closed, polling;
    private Point? start;
    private Point panOrigin;
    private double zoom = 1, brushSize = 40;
    private Size imageSize;

    public LocalEditWindow(WorkspaceContext context, PageItem page, CandidateItem source, IEnumerable<JsonElement> models)
    {
        this.context = context;
        this.source = source;
        pageId = page.Id;
        capableModels = models.Where(LocalEditRules.SupportsMask).ToList();
        Owner = context.Window;
        Title = $"局部修改 · 第 {page.PageNumber} 页 · 候选 {source.Ordinal}";
        Width = 1180; Height = 820; MinWidth = 900; MinHeight = 640;
        WindowStartupLocation = WindowStartupLocation.CenterOwner;
        Background = (Brush)FindResource("Paper");
        UseLayoutRounding = true;
        var root = new Grid { Margin = new Thickness(24) };
        root.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        root.RowDefinitions.Add(new RowDefinition());
        var header = new StackPanel();
        header.Children.Add(new TextBlock { Text = "LOCAL EDIT / 选区修改", Style = (Style)FindResource("SectionIndex") });
        header.Children.Add(new TextBlock { Text = "圈出要修改的地方，保留画面其余部分", Style = (Style)FindResource("Heading"), Margin = new Thickness(0, 8, 0, 18) });
        root.Children.Add(header);
        var split = new Grid();
        split.ColumnDefinitions.Add(new ColumnDefinition());
        split.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(320) });
        Grid.SetRow(split, 1); root.Children.Add(split);
        var canvasColumn = new DockPanel();
        var tools = new WrapPanel { Margin = new Thickness(0, 0, 0, 12) };
        foreach (var (key, label) in new[] { ("rect", "矩形"), ("brush", "画笔"), ("erase", "橡皮"), ("pan", "平移") })
        {
            var button = Kit.Act(label, (_, _) => { tool = key; notice.Text = $"当前工具：{label}"; }, "Compact");
            button.Margin = new Thickness(0, 0, 6, 6); tools.Children.Add(button);
        }
        tools.Children.Add(Kit.Act("撤销", (_, _) => History(undo, redo), "Compact"));
        tools.Children.Add(Kit.Act("重做", (_, _) => History(redo, undo), "Compact"));
        tools.Children.Add(Kit.Act("清空", (_, _) => { if (CanEdit()) { SaveUndo(); regions.Clear(); Paint(); } }, "Compact"));
        tools.Children.Add(Kit.Act("－", (_, _) => SetZoom(zoom / 1.25), "Compact"));
        tools.Children.Add(Kit.Act("＋", (_, _) => SetZoom(zoom * 1.25), "Compact"));
        tools.Children.Add(Kit.Act("适配", (_, _) => SetZoom(1), "Compact"));
        DockPanel.SetDock(tools, Dock.Top); canvasColumn.Children.Add(tools);
        canvas.Children.Add(artwork); canvas.Children.Add(draft);
        viewport.Content = canvas;
        viewport.Background = (Brush)FindResource("PaperDeep");
        canvasColumn.Children.Add(viewport); split.Children.Add(canvasColumn);
        var side = new StackPanel { Margin = new Thickness(20, 0, 0, 0) };
        Grid.SetColumn(side, 1); split.Children.Add(new ScrollViewer { Content = side, VerticalScrollBarVisibility = ScrollBarVisibility.Auto });
        Grid.SetColumn(split.Children[1], 1);
        side.Children.Add(new TextBlock { Text = $"源图：候选 {source.Ordinal} · {source.Resolution}", Style = (Style)FindResource("SubHeading") });
        side.Children.Add(notice); notice.Margin = new Thickness(0, 12, 0, 18);
        side.Children.Add(editor);
        editor.Children.Add(Kit.FieldLabel("支持选区编辑的模型")); editor.Children.Add(model);
        foreach (var item in capableModels) model.Items.Add(new ComboBoxItem { Content = item.Text("display_name"), Tag = item.Text("logical_alias") });
        model.SelectionChanged += (_, _) =>
        {
            var alias = (model.SelectedItem as ComboBoxItem)?.Tag as string;
            var options = capableModels.FirstOrDefault(m => m.Text("logical_alias") == alias).Strings("resolutions");
            if (options.Count == 0) options = new[] { source.Resolution, "1K", "2K", "4K" }.Distinct().ToList();
            resolution.Items.Clear();
            foreach (var value in options) resolution.Items.Add(value);
            resolution.SelectedItem = options.Contains(source.Resolution) ? source.Resolution : options.FirstOrDefault();
        };
        model.SelectedIndex = capableModels.Count > 0 ? 0 : -1;
        editor.Children.Add(Kit.FieldLabel("输出清晰度")); editor.Children.Add(resolution);
        editor.Children.Add(new TextBlock { Text = "画笔宽度", Style = (Style)FindResource("FieldLabel"), Margin = new Thickness(0, 16, 0, 6) });
        var brush = new Slider { Minimum = 4, Maximum = 160, Value = 40 };
        brush.ValueChanged += (_, _) => brushSize = brush.Value; editor.Children.Add(brush);
        editor.Children.Add(new TextBlock { Text = "修改指令", Style = (Style)FindResource("FieldLabel"), Margin = new Thickness(0, 16, 0, 6) });
        editor.Children.Add(instruction);
        var propose = Kit.Act("预览局部修改", async (_, _) => await Propose(), "InkButton");
        propose.Margin = new Thickness(0, 16, 0, 14); editor.Children.Add(propose);
        side.Children.Add(preview);
        Content = root;
        canvas.MouseLeftButtonDown += Start;
        canvas.MouseMove += Move;
        canvas.MouseLeftButtonUp += Finish;
        canvas.LostMouseCapture += (_, _) => { start = null; draft.Points.Clear(); };
        viewport.PreviewMouseWheel += (_, e) => { if (Keyboard.Modifiers == ModifierKeys.Control) { SetZoom(zoom * (e.Delta > 0 ? 1.1 : 1 / 1.1)); e.Handled = true; } };
        Loaded += async (_, _) => await LoadImage();
        Closing += OnClosing;
        Closed += (_, _) => { poll.Stop(); lifetime.Cancel(); lifetime.Dispose(); };
        PreviewKeyDown += (_, e) =>
        {
            if (e.OriginalSource is TextBox || Keyboard.Modifiers != ModifierKeys.Control) return;
            if (e.Key == Key.Z) { History(undo, redo); e.Handled = true; }
            if (e.Key == Key.Y) { History(redo, undo); e.Handled = true; }
        };
        poll.Tick += async (_, _) => await PollResult();
    }

    private bool CanEdit() => loaded && !busy && groupId.Length == 0 && acceptedId.Length == 0;
    private async Task LoadImage()
    {
        try
        {
            var url = source.ContentUrl.Length > 0 ? source.ContentUrl : $"assets/{source.AssetId}/content";
            var bitmap = await ImageStore.LoadAsync(context.Api.OriginUrl(url), lifetime.Token);
            if (bitmap == null) throw new InvalidOperationException("无法读取源图。");
            artwork.Source = bitmap; imageSize = new Size(bitmap.PixelWidth, bitmap.PixelHeight);
            canvas.Width = artwork.Width = imageSize.Width; canvas.Height = artwork.Height = imageSize.Height;
            SetZoom(1); loaded = true;
            notice.Text = capableModels.Count == 0 ? "当前没有支持显式选区 mask 的模型，请到系统设置配置。" : "矩形或画笔绘制选区，最多 8 个。源图必须是本页已暂选的候选。";
        }
        catch (OperationCanceledException) { }
        catch (Exception error) when (error is not OperationCanceledException) { notice.Text = error.Message; }
    }
    private void SetZoom(double value)
    {
        zoom = Math.Clamp(value, .5, 4);
        if (imageSize.Width > 0) canvas.LayoutTransform = new ScaleTransform(640 / imageSize.Width * zoom, 640 / imageSize.Width * zoom);
    }
    private void Start(object sender, MouseButtonEventArgs args)
    {
        if (!CanEdit()) return;
        start = args.GetPosition(canvas); stroke.Clear(); stroke.Add(start.Value);
        panOrigin = args.GetPosition(viewport); canvas.CaptureMouse(); args.Handled = true;
    }
    private void Move(object sender, MouseEventArgs args)
    {
        if (start == null || args.LeftButton != MouseButtonState.Pressed) return;
        if (tool == "pan")
        {
            var point = args.GetPosition(viewport);
            viewport.ScrollToHorizontalOffset(viewport.HorizontalOffset + panOrigin.X - point.X);
            viewport.ScrollToVerticalOffset(viewport.VerticalOffset + panOrigin.Y - point.Y); panOrigin = point; return;
        }
        var current = LocalEditRules.Clamp(args.GetPosition(canvas), imageSize);
        if ((current - stroke[^1]).Length > 2) stroke.Add(current);
        draft.Points = new PointCollection(tool == "rect" ? LocalEditRules.Rectangle(start.Value, current, imageSize)
            : LocalEditRules.Brush(stroke, brushSize, imageSize));
    }
    private void Finish(object sender, MouseButtonEventArgs args)
    {
        if (start == null) return;
        var end = LocalEditRules.Clamp(args.GetPosition(canvas), imageSize);
        if (tool == "erase")
        {
            SaveUndo();
            regions.RemoveAll(points => LocalEditRules.TouchedByStroke(points, stroke.Append(end).ToArray()));
        }
        else if (tool != "pan")
        {
            var points = tool == "rect" ? LocalEditRules.Rectangle(start.Value, end, imageSize) : LocalEditRules.Brush(stroke, brushSize, imageSize);
            if (regions.Count >= LocalEditRules.MaxRegions) notice.Text = "最多保留 8 个选区，请先擦除或清空。";
            else if (LocalEditRules.Area(points) >= 1) { SaveUndo(); regions.Add(points); }
        }
        start = null; canvas.ReleaseMouseCapture(); draft.Points.Clear(); Paint();
    }
    private void SaveUndo()
    {
        undo.Push(regions.Select(r => r.ToArray()).ToArray()); redo.Clear();
        if (undo.Count <= 50) return;
        var recent = undo.Take(50).Reverse().ToArray();
        undo.Clear(); foreach (var item in recent) undo.Push(item);
    }
    private void History(Stack<Point[][]> from, Stack<Point[][]> to)
    {
        if (!CanEdit() || from.Count == 0) return;
        to.Push(regions.Select(r => r.ToArray()).ToArray()); regions.Clear(); regions.AddRange(from.Pop()); Paint();
    }
    private void Paint()
    {
        foreach (var polygon in masks) canvas.Children.Remove(polygon);
        masks.Clear();
        foreach (var points in regions)
        {
            var polygon = new Polygon { Points = new PointCollection(points), Fill = draft.Fill, Stroke = draft.Stroke, StrokeThickness = 2, IsHitTestVisible = false };
            masks.Add(polygon); canvas.Children.Add(polygon);
        }
    }
    private async Task Propose()
    {
        if (!CanEdit() || model.SelectedItem is not ComboBoxItem { Tag: string alias }) return;
        if (resolution.SelectedItem is not string outputResolution) { notice.Text = "请选择模型支持的输出清晰度。"; return; }
        busy = true;
        try
        {
            var page = await context.Api.SendAsync($"pages/{pageId}", cancellation: lifetime.Token);
            if (page.Text("selected_candidate_id") != source.Id) throw new InvalidOperationException("请先将这张源图暂选为本页采用候选，再进行局部修改。");
            var proposedCommand = Guid.NewGuid().ToString(); var proposedGroup = Guid.NewGuid().ToString();
            var envelope = LocalEditRules.Envelope(context.ProjectId, page, instruction.Text, alias, outputResolution, regions, proposedCommand, proposedGroup);
            var group = await context.Api.SendAsync($"projects/{context.ProjectId}/director/command-groups", HttpMethod.Post,
                new { command_group_id = proposedGroup, commands = new[] { envelope } }, lifetime.Token);
            groupId = proposedGroup; commandId = proposedCommand; editor.IsEnabled = false;
            var command = group.Array("commands").FirstOrDefault();
            notice.Text = $"修改预览：{instruction.Text.Trim()}\n选区 {regions.Count} 个 · 模型 {alias}\n确认后调用图片模型并创建派生候选，可能计费。";
            preview.Children.Clear();
            if (command.Text("status") == "PREVIEWED")
                preview.Children.Add(Kit.Act("确认并生成派生候选", async (_, _) => await Accept(), "InkButton"));
            else notice.Text += "\n" + command.Element("error").Text("message", "命令未通过预览，请撤回后检查选区与模型。");
            preview.Children.Add(Kit.Act("撤回预览，继续编辑", async (_, _) => await Discard(), "Ghost"));
        }
        catch (OperationCanceledException) { }
        catch (Exception error) when (error is not OperationCanceledException) { notice.Text = error.Message; }
        finally { busy = false; }
    }
    private async Task Accept()
    {
        if (busy || commandId.Length == 0) return;
        busy = true;
        try
        {
            var page = await context.Api.SendAsync($"pages/{pageId}", cancellation: lifetime.Token);
            if (page.Text("selected_candidate_id") != source.Id) throw new InvalidOperationException("本页采用候选已变化，请撤回预览后重新选择源图。");
            var result = await context.Api.SendAsync($"projects/{context.ProjectId}/director/commands/{commandId}/accept", HttpMethod.Post, cancellation: lifetime.Token);
            var command = result.Array("commands").FirstOrDefault(c => c.Text("command_id") == commandId);
            if (command.Text("status") != "EXECUTED") throw new InvalidOperationException(command.Element("error").Text("message", "命令尚未执行，请刷新后检查状态。"));
            acceptedId = commandId; groupId = ""; preview.Children.Clear();
            notice.Text = "局部生成任务已提交，可以关闭窗口后在任务中心继续查看。";
            context.Cache.Invalidate("workbench:", "library:", "jobs:"); poll.Start();
        }
        catch (OperationCanceledException) { }
        catch (Exception error) when (error is not OperationCanceledException) { notice.Text = error.Message; }
        finally { busy = false; }
    }
    private async Task Discard()
    {
        if (busy || groupId.Length == 0) return;
        busy = true;
        try
        {
            await context.Api.SendAsync($"projects/{context.ProjectId}/director/command-groups/{groupId}/discard", HttpMethod.Post, cancellation: lifetime.Token);
            groupId = commandId = ""; preview.Children.Clear(); editor.IsEnabled = true;
        }
        catch (OperationCanceledException) { }
        catch (Exception error) when (error is not OperationCanceledException) { notice.Text = error.Message; }
        finally { busy = false; }
    }
    private async Task PollResult()
    {
        if (polling || acceptedId.Length == 0) return;
        polling = true;
        try
        {
            var batches = await context.Api.SendAsync($"pages/{pageId}/batches", cancellation: lifetime.Token);
            foreach (var batch in batches.EnumerateArray().OrderByDescending(b => b.Number("ordinal")))
            {
                var rows = await context.Api.SendAsync($"batches/{batch.Text("id")}/candidates", cancellation: lifetime.Token);
                var result = rows.EnumerateArray().FirstOrDefault(c => c.Element("prompt_snapshot").Element("lineage").Text("source_command_id") == acceptedId);
                if (result.ValueKind != JsonValueKind.Object) continue;
                var candidate = CandidateItem.From(result);
                notice.Text = $"派生候选 {candidate.Ordinal} · {candidate.StatusLabel}";
                if (candidate.HasImage)
                {
                    poll.Stop();
                    preview.Children.Add(new TextBlock { Text = "新候选（尚未采用）", Style = (Style)FindResource("SubHeading"), Margin = new Thickness(0, 12, 0, 8) });
                    var url = candidate.ContentUrl.Length > 0 ? candidate.ContentUrl : $"assets/{candidate.AssetId}/content";
                    var image = new ImageBox { Height = 260, SourceUrl = context.Api.PublicUrl(url) };
                    image.MouseLeftButtonDown += (_, _) => new Lightbox(this, context.Api.OriginUrl(url), "局部修改结果").ShowDialog();
                    preview.Children.Add(image);
                    preview.Children.Add(Kit.Act("人工校对并暂选此结果", async (_, _) => await SelectResult(candidate), "InkButton"));
                }
                else if (candidate.Status is "FAILED" or "CANCELED" or "CANCELLED") poll.Stop();
                return;
            }
        }
        catch (OperationCanceledException) { }
        catch (Exception error) when (error is not OperationCanceledException) { notice.Text = error.Message; }
        finally { polling = false; }
    }
    private async Task SelectResult(CandidateItem candidate)
    {
        if (busy || MessageBox.Show(this, "请确认修改区域与页面文字已人工校对。暂选此结果后仍需完成视觉检查。是否继续？",
            "采用局部修改结果", MessageBoxButton.YesNo, MessageBoxImage.Question) != MessageBoxResult.Yes) return;
        busy = true;
        try
        {
            await context.Api.SendAsync($"pages/{pageId}/select-candidate", HttpMethod.Post,
                new { candidate_id = candidate.Id, manual_text_confirmed = true, accept_stale = false }, lifetime.Token);
            context.Cache.Invalidate("workbench:", "library:", "pages:");
            notice.Text = "已暂选局部修改结果，请返回生成工作台完成视觉检查。";
            preview.IsEnabled = false;
        }
        catch (OperationCanceledException) { }
        catch (Exception error) { notice.Text = error.Message; }
        finally { busy = false; }
    }
    private async void OnClosing(object? sender, CancelEventArgs args)
    {
        if (closed) return;
        if (busy) { args.Cancel = true; return; }
        if (groupId.Length == 0) return;
        args.Cancel = true; await Discard();
        if (groupId.Length == 0) { closed = true; Close(); }
    }
}
