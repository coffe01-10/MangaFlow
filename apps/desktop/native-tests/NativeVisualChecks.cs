using System.IO;
using System.Text.Json;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using System.Windows.Threading;
using MangaFlow.Native;

internal static class NativeVisualChecks
{
    public static void Run(string output)
    {
        Exception? failure = null;
        var thread = new Thread(() =>
        {
            try
            {
                var app = new Application();
                app.Resources.MergedDictionaries.Add(new ResourceDictionary { Source = new Uri("/MangaFlow.Native;component/Theme.xaml", UriKind.Relative) });
                var state = new WorkspaceState { Connected = true, ConnectionLabel = "本地服务已连接" };
                state.Projects.Add(new("1", "雨夜来信", "3 章 · 12 页 · 4 已采用", 0, 0) { PageCount = 12, SelectedPages = 4, Resolution = "2K", ModeLabel = "半自动" });
                state.Projects.Add(new("2", "末班列车", "2 章 · 8 页 · 2 已采用", 0, 0) { PageCount = 8, SelectedPages = 2, Resolution = "2K", ModeLabel = "半自动" });
                state.Projects.Add(new("3", "山海之间", "1 章 · 4 页 · 0 已采用", 0, 0) { PageCount = 4, Resolution = "2K", ModeLabel = "手动" });
                using var data = JsonDocument.Parse("{\"totals\":{\"page_count\":24,\"selected_page_count\":6,\"review_page_count\":2},\"ai_overview\":{\"enabled_model_count\":4,\"configured_connection_count\":2,\"healthy_connection_count\":1}}");
                state.UpdateDashboard(data.RootElement);
                if (state.PageMetric != "24" || state.ReviewMetric != "02" || state.AiConnectionTitle != "AI 连接已就绪") throw new Exception("Dashboard metrics diverged from API");
                Directory.CreateDirectory(output);
                foreach (var width in new[] { 1320, 940 })
                {
                    var view = new DashboardView { DataContext = state, Width = width, Height = 1000 };
                    view.Measure(new Size(width, 1000));
                    view.Arrange(new Rect(0, 0, width, 1000));
                    view.UpdateLayout();
                    Dispatcher.CurrentDispatcher.Invoke(() => { }, DispatcherPriority.ApplicationIdle);
                    view.UpdateLayout();
                    var rail = (Border)view.FindName("DashboardRail");
                    if (Grid.GetRow(rail) != (width < 1180 ? 1 : 0)) throw new Exception("Rail breakpoint failed");
                    if (rail.ActualWidth < 250) throw new Exception("Connection panel is clipped");
                    var bitmap = new RenderTargetBitmap(width, 1000, 96, 96, PixelFormats.Pbgra32);
                    bitmap.Render(view);
                    var encoder = new PngBitmapEncoder();
                    encoder.Frames.Add(BitmapFrame.Create(bitmap));
                    using var file = File.Create(Path.Combine(output, $"native-dashboard-{width}.png"));
                    encoder.Save(file);
                }
                // No Show(): construct the real shell and render only its content tree.
                // The startup Loaded handler is not attached to a presentation source.
                var window = new MainWindow("", Path.Combine(Path.GetTempPath(), "mangaflow-unused-visual-fixture"))
                {
                    DataContext = state,
                };
                var content = (FrameworkElement)window.Content;
                content.Measure(new Size(1320, 1000));
                content.Arrange(new Rect(0, 0, 1320, 1000));
                content.UpdateLayout();
                Dispatcher.CurrentDispatcher.Invoke(() => { }, DispatcherPriority.ApplicationIdle);
                var background = new DrawingVisual();
                using (var drawing = background.RenderOpen()) drawing.DrawRectangle((Brush)app.FindResource("Paper"), null, new Rect(0, 0, 1320, 1000));
                var shell = new RenderTargetBitmap(1320, 1000, 96, 96, PixelFormats.Pbgra32);
                shell.Render(background);
                shell.Render(content);
                var shellEncoder = new PngBitmapEncoder();
                shellEncoder.Frames.Add(BitmapFrame.Create(shell));
                using (var file = File.Create(Path.Combine(output, "native-home-1320.png"))) shellEncoder.Save(file);
                app.Shutdown();
            }
            catch (Exception e) { failure = e; }
        });
        thread.SetApartmentState(ApartmentState.STA);
        thread.Start();
        thread.Join();
        if (failure != null) throw new Exception("Offscreen native UI validation failed", failure);
        Console.WriteLine("PASS: native XAML loads and lays out at 1320 / 940 px; review fixtures rendered");
    }
}
