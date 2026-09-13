using System.IO;
using System.Net;
using System.Net.Http;
using System.Reflection;
using System.Text.Json;
using System.Web;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Controls.Primitives;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using System.Windows.Threading;
using MangaFlow.Native;
using MangaFlow.Native.Controls;
using MangaFlow.Native.Services;
using MangaFlow.Native.Views;

internal static class NativeWorkflowPageChecks
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
                app.Dispatcher.BeginInvoke(new Action(async () => { try { await Checks(output); await NativeWorkflowChecks.Run(); NativeWorkflowConnectionChecks.Run(); await NativeWorkflowRunChecks.Run(); NativeIssue427Checks.Run(); } catch (Exception e) { failure = e; } finally { app.Shutdown(); } })); app.Run();
            }); thread.SetApartmentState(ApartmentState.STA); thread.Start(); thread.Join();
            if (failure != null) throw new Exception("Workflow page checks failed", failure);
        }
        finally { KeyValueStore.UseLocation(previous); File.Delete(prefs); File.Delete(prefs + ".tmp"); }
    }
    private static async Task Checks(string output)
    {
        using var fixture = new Fixture(); using var api = new ApiClient("http://127.0.0.1:12345", fixture);
        var view = new WorkflowView();
        try
        {
            view.Activate(new WorkspaceContext { Api = api, State = new(), Window = null!,
                Project = new("layout", "我最讨厌妹妹了", "", 0, 0),
                NavigateSection = (_, _) => Task.CompletedTask, OpenDashboard = () => Task.CompletedTask });
            await Until(() => Field<string>(view, "canvasWorkflowId") == "wf-layout" && Field<int>(view, "runsLoading") == 0);
            foreach (int width in new[] { 1440, 1100, 940, 650 })
            {
                Layout(view, width, 900);
                var viewport = Field<ScrollViewer>(view, "canvasScroll");
                Require(viewport.ActualWidth > (width < 980 ? width - 40 : width - 570), "canvas squeezed by sidebars");
                Require(viewport.ActualHeight > 350, "toolbar or runner consumes the canvas");
                foreach (var bar in Desc(view).OfType<PageHeading>()) Fits(bar);
                Require(Field<FrameworkElement>(view, "libraryPane").Visibility == (width < 980 ? Visibility.Collapsed : Visibility.Visible), "compact sidebars start closed");
                Render(view, width, 900, Path.Combine(output, $"native-workflow-{width}.png"));
            }
            Click(Field<Button>(view, "libraryToggle")); Layout(view, 650, 900);
            Require(Field<FrameworkElement>(view, "libraryPane").Visibility == Visibility.Visible, "library drawer opens");
            Render(view, 650, 900, Path.Combine(output, "native-workflow-library-drawer.png"));
            Call(view, "ToggleInspector", true); Layout(view, 650, 900);
            Require(Field<FrameworkElement>(view, "libraryPane").Visibility == Visibility.Collapsed && Field<FrameworkElement>(view, "inspectorPane").Visibility == Visibility.Visible, "compact drawers do not overlap each other");
            Layout(view, 1440, 1000);
            var nodes = Field<System.Collections.IList>(view, "nodes"); var node = nodes[2]!;
            Call(view, "Select", node); Layout(view, 1440, 1000);
            Require(Field<Button>(view, "copyButton").IsEnabled && Field<Button>(view, "runNodeButton").IsEnabled, "node selection enables contextual commands");
            var inspector = Field<StackPanel>(view, "inspector");
            var name = Desc(inspector).OfType<TextBox>().Single(b => System.Windows.Automation.AutomationProperties.GetName(b) == "节点名称");
            Require(((SolidColorBrush)name.Background).Color == Color.FromRgb(0x19, 0x1d, 0x1a), "inspector still uses a light editor");
            name.Text = "角色一致性与剧本解析";
            Call(view, "ToggleInspector", false); Call(view, "ToggleInspector", true); Layout(view, 1440, 1000);
            Require(ReferenceEquals(name, Desc(inspector).OfType<TextBox>().Single(b => System.Windows.Automation.AutomationProperties.GetName(b) == "节点名称")), "drawer toggle rebuilt editing controls");
            Render(view, 1440, 1000, Path.Combine(output, "native-workflow-inspector.png"));
            var inspectorScroll = Desc(view).OfType<ScrollViewer>().Single(s => ReferenceEquals(s.Content, inspector));
            Require(inspectorScroll.ScrollableHeight > 0, "fixture must require inspector scrolling");
            inspectorScroll.ScrollToVerticalOffset(inspectorScroll.ScrollableHeight); Layout(view, 1440, 1000);
            Require(inspectorScroll.VerticalOffset > 0, "dark scroll template cannot reach lower inspector fields");
            inspectorScroll.ScrollToTop(); Layout(view, 1440, 1000);
            var nodeElement = (Border)node.GetType().GetProperty("Element")!.GetValue(node)!;
            nodeElement.RaiseEvent(new RoutedEventArgs(FrameworkElement.LoadedEvent));
            nodeElement.RaiseEvent(new RoutedEventArgs(FrameworkElement.LoadedEvent));
            Require(((StackPanel)nodeElement.Child).Children.OfType<Border>().Count(b => b.Height == 3) == 1, "repeated Loaded duplicated node tone bar");
            var originalCount = nodes.Count;
            Click(Field<Button>(view, "copyButton"));
            Require(nodes.Count == originalCount + 1 && Field<Button>(view, "undoButton").IsEnabled, "copy command or undo availability lost");
            Click(Field<Button>(view, "undoButton")); Require(nodes.Count == originalCount && Field<Button>(view, "redoButton").IsEnabled, "undo failed to restore graph");
            Click(Field<Button>(view, "redoButton")); Require(nodes.Count == originalCount + 1, "redo failed to restore copied node");
            Click(Desc(view).OfType<Button>().Single(b => Equals(b.Content, "100%")));
            Require(Math.Abs(Field<double>(view, "scale") - 1) < 0.001, "100% zoom control does not change canvas");
            await view.ConfirmLeaveAsync();
            Require(fixture.Patches > 0 && JsonSerializer.Deserialize<JsonElement>(fixture.Saved).GetProperty("draft_graph")
                .GetProperty("nodes").EnumerateArray().Any(n => n.GetProperty("name").GetString() == "角色一致性与剧本解析"),
                "editor changes do not reach draft save");
            Call(view, "ClearSelection");
            Require(!Field<Button>(view, "runNodeButton").IsEnabled && !Field<Button>(view, "copyButton").IsEnabled, "cleared selection leaves contextual commands active");
            Console.WriteLine("PASS: workflow 1440/1100/940/650 layouts, responsive drawers, preserved editor, dark fields, contextual actions, zoom, copy/undo/redo, draft save and repeated node loading.");
            Console.WriteLine("Offscreen WPF + HTTP fixtures. Live API/Worker/provider, physical pointer drag, high DPI and frame timing NOT RUN.");
        }
        finally { await view.ConfirmLeaveAsync(); view.Deactivate(); }
    }
    private static void Call(object target, string name, params object[] args) => target.GetType().GetMethod(name, BindingFlags.Instance | BindingFlags.NonPublic)!.Invoke(target, args);
    private static void Fits(Panel panel)
    {
        foreach (var child in panel.Children.Cast<FrameworkElement>().Where(c => c.Visibility != Visibility.Collapsed))
        {
            var box = child.TransformToAncestor(panel).TransformBounds(new Rect(child.RenderSize));
            Require(box.Left >= -1 && box.Right <= panel.ActualWidth + 1, "toolbar child outside available width");
        }
    }
    private static IEnumerable<DependencyObject> Desc(DependencyObject e) => NativeParityChecks.Descendants(e);
    private static string[] Texts(DependencyObject e) => Desc(e).OfType<TextBlock>().Select(t => t.Text).ToArray();
    private static void Click(ButtonBase b) => b.RaiseEvent(new RoutedEventArgs(ButtonBase.ClickEvent));
    private static T Field<T>(object e, string name) => (T)e.GetType().GetField(name, BindingFlags.Instance | BindingFlags.NonPublic)!.GetValue(e)!;
    private static void Require(bool ok, string message) { if (!ok) throw new Exception(message); }
    private static async Task Until(Func<bool> predicate) { using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(8)); while (!predicate()) await Task.Delay(10, timeout.Token); }
    private static void Layout(FrameworkElement e, int w, int h) { foreach (var child in Desc(e).OfType<UIElement>()) child.InvalidateMeasure(); e.InvalidateMeasure(); e.Measure(new Size(w, h)); e.Arrange(new Rect(0, 0, w, h)); e.UpdateLayout(); }
    private static void Render(FrameworkElement e, int w, int h, string path) { Layout(e, w, h); var bitmap = new RenderTargetBitmap(w, h, 96, 96, PixelFormats.Pbgra32); bitmap.Render(e); var encoder = new PngBitmapEncoder(); encoder.Frames.Add(BitmapFrame.Create(bitmap)); using var f = File.Create(path); encoder.Save(f); }
    private sealed class Fixture : HttpMessageHandler
    {
        internal int Patches;
        internal string Saved = "";
        private static object Port(string id, string label, string data_type) => new { id, label, data_type };
        private static object[] Catalog => [
            new { type = "source.chapter", display_name = "章节原文", description = "读取当前章节完整原文", category = "INPUT", inputs = Array.Empty<object>(), outputs = new[] { Port("text", "原文", "text") } },
            new { type = "source.assets", display_name = "项目参考资产", description = "读取绑定的人物与场景参考", category = "INPUT", inputs = Array.Empty<object>(), outputs = new[] { Port("assets", "参考", "json") } },
            new { type = "agent.parse", display_name = "剧本解析", description = "提取场景与情节拍，保持原文覆盖", category = "AGENT", inputs = new[] { Port("text", "原文", "text") }, outputs = new[] { Port("script", "剧本", "json") } },
            new { type = "generator.page", display_name = "单页生成", description = "使用选定图像模型生成当前页", category = "OUTPUT", inputs = new[] { Port("script", "剧本", "json"), Port("assets", "参考", "json") }, outputs = new[] { Port("image", "图像", "image") } }
        ];
        private static JsonElement Graph()
        {
            var types = JsonSerializer.SerializeToElement(Catalog).EnumerateArray().ToArray();
            var positions = new[] { (60, 70), (60, 290), (365, 70), (670, 130) };
            var nodes = types.Select((t, i) => new { id = $"n{i}", type = t.GetProperty("type").GetString(), name = t.GetProperty("display_name").GetString(),
                position = new { x = positions[i].Item1, y = positions[i].Item2 }, inputs = t.GetProperty("inputs"), outputs = t.GetProperty("outputs"),
                config = new { model_alias = i == 2 ? "auto" : null, timeout_seconds = 900, max_attempts = 3, concurrency = 1, temperature = 0.2 } }).ToArray();
            return JsonSerializer.SerializeToElement(new { schema_version = 2, nodes, edges = new[] {
                new { source_node = "n0", source_port = "text", target_node = "n2", target_port = "text" },
                new { source_node = "n2", source_port = "script", target_node = "n3", target_port = "script" },
                new { source_node = "n1", source_port = "assets", target_node = "n3", target_port = "assets" } } });
        }
        protected override async Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken token)
        {
            var path = request.RequestUri!.AbsolutePath;
            if (request.Method == HttpMethod.Patch)
            {
                Patches++; Saved = await request.Content!.ReadAsStringAsync(token);
                var graph = JsonSerializer.Deserialize<JsonElement>(Saved).GetProperty("draft_graph");
                return Response(new { id = "wf-layout", name = "单页生产流程", version = Patches + 3, draft_version = Patches + 2, draft_graph = graph, published_version_id = "v1" });
            }
            Require(request.Method == HttpMethod.Get, "layout checks must not start paid work");
            if (path.EndsWith("/workflow-node-types")) return Response(Catalog);
            if (path.EndsWith("/projects/layout/workflows")) return Response(new[] { new { id = "wf-layout", name = "单页生产流程" } });
            if (path.EndsWith("/chapters")) return Response(new[] { new { id = "ch1", ordinal = 1, title = "第一章" } });
            if (path.EndsWith("/workflows/wf-layout")) return Response(new { id = "wf-layout", name = "单页生产流程", version = 3, draft_version = 2, draft_graph = Graph(), published_version_id = "v1" });
            return Response(Array.Empty<object>());
        }
        private static HttpResponseMessage Response(object data) => new(HttpStatusCode.OK) { Content = new StringContent(JsonSerializer.Serialize(data)) };
    }
}
