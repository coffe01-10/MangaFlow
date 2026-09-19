using System.IO;
using System.Net.Http;
using System.Reflection;
using System.Text.Json;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using MangaFlow.Native;
using MangaFlow.Native.Controls;
using MangaFlow.Native.Views;

/// <summary>NUI-8 P1-2 的离屏回归面：web↔原生视觉差异 D1（hero 朱红下划线）、
/// D3（侧栏副标口径）、D7（非图片路径的错误文案本地化）。</summary>
internal static class NativeNui8ParityChecks
{
    public static void Run(string output)
    {
        var state = new WorkspaceState { Connected = true };
        state.Projects.Add(new ProjectItem("1", "我最讨厌妹妹了", "3 章 · 11 页 · 1 已采用", 0, 0) { PageCount = 11, ChapterCount = 3, SelectedPages = 1 });
        var hero = new HomeView { DataContext = state };
        Layout(hero, 1320, 1000);
        var accent = (Brush)Application.Current.FindResource("Accent");

        // D1：web 的第二行有一条约 1° 倾斜的 2px 朱红线，只覆盖第二行文字本身。
        var text = NativeParityChecks.Descendants(hero).OfType<TextBlock>().Single(t => t.Text == "把故事画出来。");
        var rule = NativeParityChecks.Descendants(hero).OfType<Border>()
            .SingleOrDefault(b => System.Windows.Automation.AutomationProperties.GetName(b) == "hero-accent-rule");
        Require(rule != null, "hero accent rule missing");
        Require(rule!.Height == 2 && ReferenceEquals(rule.Background, accent), "hero accent rule is not a 2px vermillion rule");
        Require(rule.RenderTransform is RotateTransform { Angle: -1 }, "hero accent rule must keep the web -1deg tilt");
        Require(Math.Abs(rule.ActualWidth - text.ActualWidth) <= 1, $"hero rule must span the second line only, got {rule.ActualWidth} vs text {text.ActualWidth}");
        var ruleTop = rule.TranslatePoint(new Point(), hero).Y;
        var textTop = text.TranslatePoint(new Point(), hero).Y;
        Require(ruleTop >= textTop && ruleTop <= textTop + text.ActualHeight + 6, "hero rule must sit under the second line");
        Require(Math.Abs(rule.TranslatePoint(new Point(), hero).X - text.TranslatePoint(new Point(), hero).X) <= 0.5, "hero rule must start at the text's left edge");
        Directory.CreateDirectory(output);
        foreach (var width in new[] { 1320, 940 })
        {
            Layout(hero, width, 1000);
            Render(hero, width, 1000, Path.Combine(output, $"native-dashboard-hero-{width}.png"));
        }

        // D2：仪表盘「最近创作」右侧的项目计数必须真的渲染出来（实机 UIA 树里没有
        // 这条文本，离屏必须能断到，否则两侧证据不一致本身就是缺陷）。
        Layout(hero, 1320, 1000);
        var count = NativeParityChecks.Descendants(hero).OfType<TextBlock>().FirstOrDefault(t => t.Text.EndsWith("个项目"));
        Require(count != null && count.Text == "1 个项目", $"NUI-8 D2: dashboard project count missing, found {count?.Text ?? "<none>"}");
        Require(count!.ActualWidth > 20, $"NUI-8 D2: project count laid out empty, width {count.ActualWidth}");
        Require(count.TranslatePoint(new Point(count.ActualWidth, 0), hero).X > 1320 * 0.6,
            "NUI-8 D2: project count must sit on the right edge of the section header");

        // D3：侧栏副标与项目卡副标是两条口径，卡片仍按 web 的「N 页 · N 已采用」。
        Require(state.Projects[0].SidebarSummary == "3 章 · 11 页已规划", "sidebar summary must match web '章 / 页已规划'");
        Require(state.Projects[0].Summary == "3 章 · 11 页 · 1 已采用", "dashboard card subtitle keeps the adopted-page count");
        using var empty = JsonDocument.Parse("""
            {"project":{"id":"p9","name":"新项目","default_resolution":"2K","workflow_mode":"SEMI_AUTO"},
             "chapter_count":0,"page_count":0,"selected_page_count":0,"pending_job_count":0,"failed_job_count":0,
             "next_action":{"section":"source","label":"导入第一章"}}
            """);
        Require(ProjectItem.From(empty.RootElement).SidebarSummary == "漫画生产工作区", "chapter-less project must fall back to the web sidebar label");
        var shell = ReadSource("native", "MainWindow.xaml") ?? ReadSource("apps", "desktop", "native", "MainWindow.xaml");
        Require(shell != null && shell.Contains("CurrentProject.SidebarSummary") && !shell.Contains("Binding CurrentProject.Summary"),
            "sidebar subtitle must bind the sidebar summary, not the card subtitle (#D3)");

        // D7：传输层失败不再把英文原文抛给用户，服务端 detail 仍然原样透出。
        Require(MediaErrors.Localize(new HttpRequestException("An error occurred while sending the request."), "导出文件下载未能送达本地服务") == "导出文件下载未能送达本地服务",
            "transport failures must fall back to localized copy");
        Require(MediaErrors.Localize(new InvalidOperationException("导出资源不存在（404）"), "回退") == "导出资源不存在（404）",
            "server-mapped failures keep their detail");
        var library = ReadSource("native", "Views", "LibraryView.cs") ?? ReadSource("apps", "desktop", "native", "Views", "LibraryView.cs");
        Require(library != null
            && library.Split("MediaErrors.Localize(").Length == 3
            && !library.Contains("请刷新核对：\" + error.Message") && !library.Contains("可重试：\" + error.Message"),
            "library export/download must localize raw transport errors (#D7)");
        Console.WriteLine("PASS: hero accent rule geometry, sidebar vs card summary split, localized export/download failures");
    }

    private static void Require(bool condition, string message) { if (!condition) throw new Exception(message); }
    private static void Layout(FrameworkElement view, double width, double height)
    {
        view.Width = width; view.Height = height;
        view.Measure(new Size(width, height)); view.Arrange(new Rect(0, 0, width, height)); view.UpdateLayout();
    }
    private static void Render(FrameworkElement view, int width, int height, string path)
    {
        var bitmap = new RenderTargetBitmap(width, height, 96, 96, PixelFormats.Pbgra32);
        var paper = new DrawingVisual();
        using (var drawing = paper.RenderOpen()) drawing.DrawRectangle((Brush)Application.Current.FindResource("Paper"), null, new Rect(0, 0, width, height));
        bitmap.Render(paper); bitmap.Render(view);
        var encoder = new PngBitmapEncoder(); encoder.Frames.Add(BitmapFrame.Create(bitmap));
        using var file = File.Create(path); encoder.Save(file);
    }
    private static string? ReadSource(params string[] relative)
    {
        var dir = new DirectoryInfo(AppContext.BaseDirectory);
        while (dir != null)
        {
            var candidate = Path.Combine(new[] { dir.FullName }.Concat(relative).ToArray());
            if (File.Exists(candidate)) return File.ReadAllText(candidate);
            dir = dir.Parent;
        }
        return null;
    }
}
