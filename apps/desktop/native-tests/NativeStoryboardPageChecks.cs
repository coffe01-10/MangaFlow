using System.IO;
using System.Net;
using System.Net.Http;
using System.Reflection;
using System.Text.Json;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Controls.Primitives;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using System.Windows.Threading;
using MangaFlow.Native;
using MangaFlow.Native.Services;
using MangaFlow.Native.Views;

internal static class NativeStoryboardPageChecks
{
    internal static void Run(string output)
    {
        Directory.CreateDirectory(output);
        var previous = (string)typeof(KeyValueStore).GetField("path", BindingFlags.Static | BindingFlags.NonPublic)!.GetValue(null)!;
        var prefs = Path.Combine(output, "test-prefs.json"); File.WriteAllText(prefs, "{}"); KeyValueStore.UseLocation(prefs);
        try
        {
            Exception? failure = null;
            var thread = new Thread(() =>
            {
                var app = new Application { ShutdownMode = ShutdownMode.OnExplicitShutdown };
                app.Resources.MergedDictionaries.Add(new ResourceDictionary { Source = new Uri("/MangaFlow.Native;component/Theme.xaml", UriKind.Relative) });
                SynchronizationContext.SetSynchronizationContext(new DispatcherSynchronizationContext());
                app.Dispatcher.BeginInvoke(new Action(async () => { try { await Checks(output); } catch (Exception e) { failure = e; } finally { app.Shutdown(); } })); app.Run();
            }); thread.SetApartmentState(ApartmentState.STA); thread.Start(); thread.Join();
            if (failure != null) throw new Exception("Storyboard page checks failed", failure);
        }
        finally { KeyValueStore.UseLocation(previous); File.Delete(prefs); File.Delete(prefs + ".tmp"); }
    }

    private static async Task Checks(string output)
    {
        var fixture = new Fixture(); using var api = new ApiClient("http://127.0.0.1:12345", fixture); var view = new StoryboardView();
        try
        {
            view.Activate(new WorkspaceContext { Api = api, State = new(), Window = null!, Project = new ProjectItem("layout", "我最讨厌妹妹了", "", 0, 0), NavigateSection = (_, _) => Task.CompletedTask, OpenDashboard = () => Task.CompletedTask });
            await Until(() => view.PanelCountForTest == 3);
            view.SelectPanelForTest(0); Render(view, 1320, 900, Path.Combine(output, "native-storyboard-1320.png"));
            var strip = Field<StackPanel>(view, "pageBar"); var viewport = Field<ScrollViewer>(view, "viewport");
            Require(strip.Orientation == Orientation.Vertical && strip.Children.Count == 11, "wide page strip lists all pages vertically");
            Require(!Field<Button>(view, "saveButton").IsEnabled && !Field<Button>(view, "undoButton").IsEnabled && !Field<Button>(view, "redoButton").IsEnabled, "clean page disables save and unavailable history actions");
            Require(Field<TextBlock>(view, "pageLoadText").Text == "第 1 页 · 130/180 字 · 3/8 气泡", "page load uses API estimates");
            Require(Field<TextBlock>(view, "pageSourceText").Text.Contains("1 个场景 · 3 个情节拍"), "source provenance reads scene/beat ids");
            Require(Field<Border>(view, "pageHost").ActualHeight < viewport.ActualHeight, "first layout fits the full page vertically");
            var canvas = Field<Canvas>(view, "page");
            var badges = canvas.Children.OfType<FrameworkElement>().Where(e => Equals(e.Tag, "order-badge")).ToArray();
            Require(badges.Length == 3 && badges.Select(Canvas.GetTop).Distinct().Count() == 2 && badges.All(b => b.ActualWidth > 100), "reading-order labels track each panel after first-fit zoom");
            var texts = NativeParityChecks.Descendants(view).OfType<TextBlock>().Select(t => t.Text).ToArray();
            Require(texts.Contains("爸爸的灵牌") && texts.Contains("京都，爸爸的灵牌前") && texts.Contains("我 · 实际出镜"), "director readout uses props/background/cast data");
            var reads = fixture.Reads; var active = strip.Children.OfType<ToggleButton>().First(); active.IsChecked = false; Click(active);
            Require(active.IsChecked == true && fixture.Reads == reads, "active page click preserves selection without reloading");
            Field<ToggleButton>(view, "focusButton").IsChecked = true; Click(Field<ToggleButton>(view, "focusButton")); Layout(view, 1320, 900);
            Require(strip.Visibility == Visibility.Collapsed && Field<Border>(view, "pageSummary").Visibility == Visibility.Collapsed, "focus removes page navigation and summary");
            Field<ToggleButton>(view, "focusButton").IsChecked = false; Click(Field<ToggleButton>(view, "focusButton")); Layout(view, 1320, 900);
            Click(Field<Button>(view, "directorToggle")); Layout(view, 1320, 900);
            Require(Field<ScrollViewer>(view, "directorScroll").Visibility == Visibility.Collapsed && viewport.ActualWidth > 1100, "closing director releases canvas space");
            Click(Field<Button>(view, "directorToggle"));
            Render(view, 940, 800, Path.Combine(output, "native-storyboard-940.png"));
            Require(viewport.ActualWidth > 300 && viewport.ActualHeight > 250, "medium width leaves a usable canvas");
            Render(view, 650, 1200, Path.Combine(output, "native-storyboard-650.png"));
            var selector = Field<ComboBox>(view, "compactPageSelector"); var director = Field<ScrollViewer>(view, "directorScroll");
            Require(selector.Visibility == Visibility.Visible && Grid.GetRow(director) == 1 && director.ActualWidth <= 650, "narrow layout uses a page selector and stacked director");
            Require(Field<Border>(view, "pageHost").ActualHeight < viewport.ActualHeight, "fit mode adapts to narrow viewport");
            var input = NativeParityChecks.Descendants(view).OfType<TextBox>().First(t => System.Windows.Automation.AutomationProperties.GetName(t) == "气泡 1 文字");
            input.Text = "未保存的对白"; view.LeaveConfirmOverride = () => Task.FromResult(false);
            selector.SelectedIndex = 1; await Task.Delay(30);
            Require(selector.SelectedIndex == 0 && fixture.Reads == reads && input.Text == "未保存的对白", "canceling page navigation retains dialogue draft and selection");
            view.LeaveConfirmOverride = () => Task.FromResult(true); selector.SelectedIndex = 1;
            await Until(() => fixture.Reads > reads && Field<TextBlock>(view, "pageLoadText").Text.StartsWith("第 2 页"));
            Require(selector.SelectedIndex == 1, "confirmed page selection loads the selected API page");
            Console.WriteLine("PASS: storyboard page strip, API summary/readout, first fit, focus, director collapse, 1320/940/650 layouts, selected-page stability and dirty-page navigation.");
            Console.WriteLine("Offscreen WPF + HTTP fixtures; real-window DPI, frame timing and live backend/provider acceptance NOT RUN.");
        }
        finally { view.Deactivate(); }
    }
    private static void Click(ButtonBase b) => b.RaiseEvent(new RoutedEventArgs(ButtonBase.ClickEvent));
    private static T Field<T>(object o, string name) => (T)o.GetType().GetField(name, BindingFlags.Instance | BindingFlags.NonPublic)!.GetValue(o)!;
    private static void Require(bool ok, string message) { if (!ok) throw new Exception(message); }
    private static async Task Until(Func<bool> predicate) { using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(8)); while (!predicate()) await Task.Delay(10, timeout.Token); }
    private static void Layout(FrameworkElement e, int w, int h) { e.Measure(new Size(w, h)); e.Arrange(new Rect(0, 0, w, h)); e.UpdateLayout(); }
    private static void Render(FrameworkElement e, int w, int h, string path) { Layout(e, w, h); var bitmap = new RenderTargetBitmap(w, h, 96, 96, PixelFormats.Pbgra32); bitmap.Render(e); var encoder = new PngBitmapEncoder(); encoder.Frames.Add(BitmapFrame.Create(bitmap)); using var f = File.Create(path); encoder.Save(f); }
    private sealed class Fixture : HttpMessageHandler
    {
        internal int Reads;
        private static object Page(int n) => new { id = $"pg-{n}", chapter_id = "ch", page_number = n, panel_count = 3, storyboard_version = 7, estimated_text_chars = 130, estimated_bubbles = 3, scene_ids = new[] { "s1" }, beat_ids = new[] { "b1", "b2", "b3" }, continuity_status = "NEEDS_REVIEW", canvas = new { width_mm = 182, height_mm = 257, bleed_mm = 3, safe_mm = 5 } };
        private static object Panel(int n) => new
        {
            id = $"panel-{n}", reading_order = n, version = 2,
            bounds = new { x = n == 2 ? .52 : .012, y = n == 1 ? .012 : .47, width = n == 1 ? .976 : .468, height = n == 1 ? .436 : .518 },
            shot_type = "establishing", camera_angle = "eye_level", actions = new { script_action = "“我”跪在爸爸的灵牌前，肩膀颤抖，失声痛哭。窗外下着淅淅沥沥的小雨。" },
            background = "京都，爸爸的灵牌前", props = new[] { "爸爸的灵牌" }, characters = new[] { "c1" }, character_presence = new Dictionary<string, string> { ["c1"] = "VISIBLE" },
            dialogues = new[] { new { id = $"dlg-{n}", panel_id = $"panel-{n}", reading_order = 1, target_text = "四月初，京都下起了小雨。", text_direction = "vertical", rewrite_forbidden = true } }
        };
        protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken token)
        {
            if (request.Method != HttpMethod.Get) throw new Exception("Layout checks must not issue writes");
            var path = request.RequestUri!.AbsolutePath; object data = Array.Empty<object>();
            if (path.EndsWith("/chapters")) data = new[] { new { id = "ch", ordinal = 1, title = "第一章", page_count = 11 } };
            else if (path.EndsWith("/characters")) data = new[] { new { id = "c1", primary_name = "我" } };
            else if (path.EndsWith("/pages")) data = Enumerable.Range(1, 11).Select(Page).ToArray();
            else if (path.EndsWith("/storyboard")) { Reads++; int n = int.Parse(path.Split('/')[^2].Split('-')[1]); data = new { page = Page(n), panels = Enumerable.Range(1, 3).Select(Panel).ToArray() }; }
            return Task.FromResult(new HttpResponseMessage(HttpStatusCode.OK) { Content = new StringContent(JsonSerializer.Serialize(data)) });
        }
    }
}
