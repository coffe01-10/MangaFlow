using System.Net.Http;
using System.Text.Json;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;
using System.Windows.Shapes;
using MangaFlow.Native.Controls;
using MangaFlow.Native.Services;

namespace MangaFlow.Native.Views;

/// <summary>
/// PUB-01A/01C 章节手机阅读预览（只读）+ 条漫包导出入口。与 web
/// components/project-workspace/mobile-preview.tsx 同契约：按页码竖排已采用页，
/// 未达标页原位给占位卡与阻塞原因；切片边界按 目标宽度/页间距/最大片高 在图像
/// 像素空间计算（StripSlices，与导出端 compute_slices 同语义）再乘视口比例换算。
/// 「导出条漫包」按当前控件值提交 WEBTOON 导出，其余控件不写任何业务数据。
/// </summary>
public sealed class MobilePreviewWindow : Window
{
    private static readonly int[] ViewportWidths = [360, 390, 414];
    private static readonly int[] TargetWidths = [750, 1080, 1440];
    private static readonly int[] PageGaps = [0, 8, 16, 24, 32];
    private static readonly int[] SliceHeights = [2048, 4096, 8192];

    private readonly WorkspaceContext context;
    private readonly ChapterItem chapter;
    private readonly Func<Task>? exported;
    private readonly CancellationTokenSource lifetime = new();
    private readonly ComboBox viewportBox = Kit.Selector("手机视口宽度", 96);
    private readonly ComboBox targetBox = Kit.Selector("条漫目标宽度", 110);
    private readonly ComboBox gapBox = Kit.Selector("页间距", 90);
    private readonly ComboBox sliceBox = Kit.Selector("最大分片高度", 110);
    private readonly ComboBox formatBox = Kit.Selector("导出格式", 96);
    private readonly TextBlock summary = Kit.Caption("");
    private readonly TextBlock notice = Kit.Caption("");
    private readonly Button export;
    private readonly Canvas strip = new() { HorizontalAlignment = HorizontalAlignment.Center };
    private readonly StackPanel stage = new();
    private JsonElement preview;
    private bool loading, exporting, exportDone;

    public MobilePreviewWindow(WorkspaceContext context, ChapterItem chapter, Func<Task>? exported = null)
    {
        this.context = context;
        this.chapter = chapter;
        this.exported = exported;
        Owner = context.Window;
        Title = $"手机预览 · {chapter.Title}";
        Width = 560; Height = 800; MinWidth = 460; MinHeight = 480;
        WindowStartupLocation = WindowStartupLocation.CenterOwner;
        Background = (Brush)FindResource("Paper");
        UseLayoutRounding = true;

        var root = new Grid { Margin = new Thickness(24) };
        root.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        root.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        root.RowDefinitions.Add(new RowDefinition());
        var header = new StackPanel();
        header.Children.Add(new TextBlock { Text = "MOBILE PREVIEW / 手机阅读预览", Style = (Style)FindResource("SectionIndex") });
        header.Children.Add(new TextBlock
        {
            Text = $"第 {chapter.Ordinal} 章 · {chapter.Title} · 只读预览，不改变采用状态或原图",
            Style = (Style)FindResource("Micro"), Margin = new Thickness(0, 6, 0, 14), TextWrapping = TextWrapping.Wrap,
        });
        root.Children.Add(header);

        var toolbar = new WrapPanel { Margin = new Thickness(0, 0, 0, 12) };
        Fill(viewportBox, ViewportWidths, 390);
        Fill(targetBox, TargetWidths, 1080);
        Fill(gapBox, PageGaps, 16);
        Fill(sliceBox, SliceHeights, 4096);
        foreach (var (label, box) in new (string, ComboBox)[]
        {
            ("视口", viewportBox), ("目标宽度", targetBox), ("页间距", gapBox), ("最大片高", sliceBox),
        })
        {
            var field = new StackPanel { Margin = new Thickness(0, 0, 14, 8) };
            field.Children.Add(Kit.FieldLabel(label));
            field.Children.Add(box);
            toolbar.Children.Add(field);
        }
        var formatField = new StackPanel { Margin = new Thickness(0, 0, 14, 8) };
        formatField.Children.Add(Kit.FieldLabel("格式"));
        foreach (var option in new[] { "JPEG", "PNG" }) formatBox.Items.Add(new ComboBoxItem { Content = option, Tag = option });
        formatBox.SelectedIndex = 0;
        formatField.Children.Add(formatBox);
        toolbar.Children.Add(formatField);
        summary.VerticalAlignment = VerticalAlignment.Bottom;
        summary.Margin = new Thickness(0, 0, 14, 12);
        toolbar.Children.Add(summary);
        export = Kit.Act("导出条漫包", async (_, _) => await ExportAsync(), "InkButton");
        export.IsEnabled = false; export.VerticalAlignment = VerticalAlignment.Bottom; export.Margin = new Thickness(0, 0, 0, 8);
        export.ToolTip = "按当前参数生成条漫 ZIP；存在未达标页面时不可导出";
        toolbar.Children.Add(export);
        foreach (var box in new[] { viewportBox, targetBox, gapBox, sliceBox, formatBox })
            box.SelectionChanged += (_, _) => { if (!loading) RebuildStrip(); };
        Grid.SetRow(toolbar, 1);
        root.Children.Add(toolbar);

        var scroller = new ScrollViewer
        {
            Content = stage,
            VerticalScrollBarVisibility = ScrollBarVisibility.Auto,
            HorizontalScrollBarVisibility = ScrollBarVisibility.Disabled,
        };
        Grid.SetRow(scroller, 2);
        root.Children.Add(scroller);
        notice.TextWrapping = TextWrapping.Wrap;
        stage.Children.Add(notice);
        stage.Children.Add(strip);
        Content = root;
        Closed += (_, _) => lifetime.Cancel();
        Loaded += async (_, _) => await LoadAsync();
    }

    private static void Fill(ComboBox box, int[] values, int selected)
    {
        foreach (var value in values) box.Items.Add(new ComboBoxItem { Content = $"{value}px", Tag = value });
        box.SelectedItem = box.Items.OfType<ComboBoxItem>().FirstOrDefault(i => (int)i.Tag == selected) ?? box.Items[0];
    }

    private static int Choice(ComboBox box) => (box.SelectedItem as ComboBoxItem)?.Tag is int value ? value : 0;
    private static string TextChoice(ComboBox box) => (box.SelectedItem as ComboBoxItem)?.Tag as string ?? "JPEG";
    private static int? NullableInt(JsonElement element) =>
        element.ValueKind == JsonValueKind.Number && element.TryGetInt32(out var value) ? value : null;

    private async Task LoadAsync()
    {
        loading = true;
        var token = lifetime.Token;
        try
        {
            notice.Text = "正在读取章节预览…";
            preview = await context.Api.SendAsync($"chapters/{chapter.Id}/mobile-preview", cancellation: token);
            if (token.IsCancellationRequested) return;
            notice.Text = "";
            RebuildStrip();
        }
        catch (OperationCanceledException) { }
        catch (Exception error)
        {
            if (!token.IsCancellationRequested)
            {
                notice.Text = "预览加载失败，可关闭后重试：" + MediaErrors.Localize(error, "预览读取未能送达本地服务，请确认本地服务在线后重试。");
                strip.Children.Clear();
            }
        }
        finally { loading = false; }
    }

    private void RebuildStrip()
    {
        strip.Children.Clear();
        if (preview.ValueKind != JsonValueKind.Object) { summary.Text = ""; return; }
        var pages = preview.Array("pages");
        var targetWidth = Choice(targetBox);
        var gap = Choice(gapBox);
        var maxSlice = Choice(sliceBox);
        var viewport = Choice(viewportBox);
        // 与 web 一致：达标页按宽高比缩放，未达标/缺尺寸页占 4:3 竖版占位高。
        var stripModel = StripSlices.ComputeStrip(
            pages.Select(page => (page.Text("page_id"), (double)StripSlices.ScaledPageHeight(
                page.Flag("ready") ? NullableInt(page.Element("width")) : null,
                page.Flag("ready") ? NullableInt(page.Element("height")) : null,
                targetWidth))).ToList(),
            gap, maxSlice);
        var scale = targetWidth > 0 ? viewport / (double)targetWidth : 1;
        var blocked = pages.Count(page => !page.Flag("ready"));
        summary.Text = $"{pages.Count} 页 · {(blocked > 0 ? $"{blocked} 页未达标 · " : "")}{stripModel.Slices.Count} 片";
        strip.Width = viewport;
        strip.Height = Math.Max(1, stripModel.TotalHeight * scale);
        foreach (var page in pages)
        {
            var layout = stripModel.Items.FirstOrDefault(item => item.Id == page.Text("page_id"));
            if (layout == null) continue;
            FrameworkElement slot = page.Flag("ready") && page.Text("image_url").Length > 0
                ? new ImageBox { SourceUrl = context.Api.PublicUrl(page.Text("image_url")) }
                : BlockedCard(page);
            slot.Width = viewport;
            slot.Height = Math.Max(1, layout.Height * scale);
            Canvas.SetLeft(slot, 0);
            Canvas.SetTop(slot, layout.Top * scale);
            strip.Children.Add(slot);
        }
        var lineBrush = (Brush)FindResource("Muted");
        var accentBrush = (Brush)FindResource("Accent");
        foreach (var slice in stripModel.Slices.Skip(1))
        {
            var boundary = slice.Start * scale;
            strip.Children.Add(new Line
            {
                X1 = -8, X2 = viewport + 8, Y1 = boundary, Y2 = boundary,
                Stroke = lineBrush, StrokeThickness = 1, StrokeDashArray = new DoubleCollection { 4, 3 },
            });
            var tag = new Border
            {
                Background = (Brush)FindResource("Paper"), Padding = new Thickness(6, 1, 6, 1),
                BorderBrush = lineBrush, BorderThickness = new Thickness(1), CornerRadius = new CornerRadius(2),
                Child = new TextBlock { Text = $"片 {slice.Index}", FontSize = 10, Foreground = lineBrush },
            };
            Canvas.SetLeft(tag, 4); Canvas.SetTop(tag, boundary - 9);
            strip.Children.Add(tag);
        }
        foreach (var slice in stripModel.Slices.Where(s => s.Oversized))
        {
            var frame = new Border
            {
                Width = viewport, Height = Math.Max(1, (slice.End - slice.Start) * scale),
                BorderBrush = accentBrush, BorderThickness = new Thickness(2),
                IsHitTestVisible = false,
                Child = new TextBlock
                {
                    Text = $"片 {slice.Index} · 单页硬切", FontSize = 10, Foreground = accentBrush,
                    HorizontalAlignment = HorizontalAlignment.Right, VerticalAlignment = VerticalAlignment.Top,
                    Margin = new Thickness(0, 2, 4, 0),
                },
            };
            Canvas.SetLeft(frame, 0); Canvas.SetTop(frame, slice.Start * scale);
            strip.Children.Add(frame);
        }
        if (pages.Count == 0) notice.Text = "该章节还没有分页。";
        export.IsEnabled = !exporting && blocked == 0 && pages.Count > 0;
        export.Content = exportDone ? "已加入导出记录" : "导出条漫包";
    }

    private Border BlockedCard(JsonElement page)
    {
        var blockers = page.Array("blockers");
        var reason = blockers.FirstOrDefault().Text("message", "未达到生产通过条件");
        if (blockers.Count > 1) reason += $" 等 {blockers.Count} 项";
        return new Border
        {
            Background = (Brush)FindResource("PaperDeep"),
            BorderBrush = (Brush)FindResource("Warning"), BorderThickness = new Thickness(1),
            Child = new StackPanel
            {
                VerticalAlignment = VerticalAlignment.Center, Margin = new Thickness(14),
                Children =
                {
                    new TextBlock { Text = $"第 {page.Number("page_number")} 页", FontWeight = FontWeights.Bold, FontSize = 13 },
                    new TextBlock { Text = reason, Style = (Style)FindResource("Micro"), TextWrapping = TextWrapping.Wrap, Margin = new Thickness(0, 4, 0, 0) },
                },
            },
        };
    }

    private async Task ExportAsync()
    {
        if (exporting || preview.ValueKind != JsonValueKind.Object || lifetime.IsCancellationRequested) return;
        var pages = preview.Array("pages");
        if (pages.Count == 0 || pages.Any(page => !page.Flag("ready"))) return;
        exporting = true; export.IsEnabled = false; exportDone = false;
        var token = lifetime.Token;
        try
        {
            await context.Api.SendAsync($"chapters/{chapter.Id}/exports", HttpMethod.Post, new
            {
                export_type = "WEBTOON",
                width = Choice(targetBox),
                format = TextChoice(formatBox),
                gap_px = Choice(gapBox),
                max_slice_height = Choice(sliceBox),
            }, token);
            if (token.IsCancellationRequested) return;
            exportDone = true;
            export.Content = "已加入导出记录";
            notice.Text = "条漫 ZIP 已生成，可在素材库导出文件里下载。";
            if (exported != null) await exported();
        }
        catch (OperationCanceledException) { }
        catch (Exception error)
        {
            if (!token.IsCancellationRequested)
                notice.Text = "导出失败，请重试：" + MediaErrors.Localize(error, "导出请求未能送达本地服务，请确认本地服务在线后重试。");
        }
        finally
        {
            exporting = false;
            if (!token.IsCancellationRequested && preview.ValueKind == JsonValueKind.Object) RebuildStrip();
        }
    }
}
