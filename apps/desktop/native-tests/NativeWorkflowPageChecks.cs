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
using System.Windows.Input;
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
            Require(Field<double>(view, "scale") == 1, "workflow opens at readable 100% zoom");
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
            Call(view, "ToggleLibrary", true); Layout(view, 650, 900);
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
            Require(Equals(name.Background, Application.Current.FindResource("Surface")), "inspector follows the application surface theme");
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
            VerifyPresentation(view, output);
            await view.ConfirmLeaveAsync();
            Require(fixture.Patches > 0 && JsonSerializer.Deserialize<JsonElement>(fixture.Saved).GetProperty("draft_graph")
                .GetProperty("nodes").EnumerateArray().Any(n => n.GetProperty("name").GetString() == "角色一致性与剧本解析"),
                "editor changes do not reach draft save");
            Call(view, "ClearSelection");
            Require(!Field<Button>(view, "runNodeButton").IsEnabled && !Field<Button>(view, "copyButton").IsEnabled, "cleared selection leaves contextual commands active");

            // ============ A01：保存失败必须阻断发布/校验/切换/离开 ============
            var status = Field<TextBlock>(view, "statusLine");
            var saveNow = typeof(WorkflowView).GetMethod("SaveNowAsync", BindingFlags.NonPublic | BindingFlags.Instance)!;
            var nodesNow = Field<System.Collections.IList>(view, "nodes");
            var nodeCount = nodesNow.Count;
            fixture.FailPatch = "error";
            Click(Desc(view).OfType<Button>().Single(b => Equals(b.Content, "发布")));
            await Until(() => status.Text.Contains("发布已取消"));
            Require(fixture.Publishes == 0, "publish never fires after a failed draft save (A01)");
            Click(Desc(view).OfType<Button>().Single(b => Equals(b.Content, "校验")));
            await Until(() => status.Text.Contains("校验已取消"));
            Require(fixture.Validates == 0, "validate never fires after a failed draft save (A01)");
            // 409：静默同步服务端版本（不回读图），草稿节点原样保留。
            fixture.FailPatch = "conflict"; fixture.WorkflowVersion = 9;
            Require(!await (Task<bool>)saveNow.Invoke(view, new object?[] { null })!, "conflicted save reports failure to the caller");
            await Until(() => Field<int>(view, "version") == 9);
            Require(Field<System.Collections.IList>(view, "nodes").Count == nodeCount, "draft nodes survive failed saves (A01)");
            // 恢复后发布照常放行。
            fixture.FailPatch = null;
            Click(Desc(view).OfType<Button>().Single(b => Equals(b.Content, "发布")));
            await Until(() => fixture.Publishes == 1);
            // 切换被阻断：留在原工作流，选择器回滚，目标工作流不被加载。
            fixture.FailPatch = "error";
            var selector = Field<ComboBox>(view, "workflowSelector");
            selector.SelectedItem = selector.Items.OfType<ComboBoxItem>().Single(i => (string?)i.Tag == "wf-other");
            await Until(() => status.Text.Contains("切换已取消"));
            Require(Field<string>(view, "workflowId") == "wf-layout" && Field<string>(view, "canvasWorkflowId") == "wf-layout",
                "blocked switch keeps the current workflow (A01)");
            Require((selector.SelectedItem as ComboBoxItem)!.Tag as string == "wf-layout", "selector reverts to the current workflow");
            Require(fixture.OtherReads == 0, "blocked switch never loads the target workflow");
            // 离开默认被阻断（草稿保留）；显式弃稿才放行。
            view.SaveFailLeaveOverride = _ => Task.FromResult(false);
            Require(!await view.ConfirmLeaveAsync(), "failed flush blocks leaving by default (A01)");
            view.SaveFailLeaveOverride = _ => Task.FromResult(true);
            Require(await view.ConfirmLeaveAsync(), "explicit discard allows leaving after a failed flush");
            view.SaveFailLeaveOverride = null;
            fixture.FailPatch = null;

            Console.WriteLine("PASS: workflow 1440/1100/940/650 layouts, themed fields, focus restoration, view menu, Shift-wheel, scaled scroll extent, contextual actions, zoom, copy/undo/redo, draft save, and failed-save gating (A01).");
            Console.WriteLine("Offscreen WPF + HTTP fixtures. Live API/Worker/provider, physical pointer drag, high DPI and frame timing NOT RUN.");
        }
        finally
        {
            // A01 后离开确认在「上一次保存失败」时也会询问弃稿——离屏检查无宿主
            // 窗口，MessageBox 会 ArgumentNullException；接测试缝显式弃稿离场。
            view.SaveFailLeaveOverride = _ => Task.FromResult(true);
            await view.ConfirmLeaveAsync();
            view.Deactivate();
        }
        NativeWorkflowShellChecks.Run(output);
    }
    private static void VerifyPresentation(WorkflowView view, string output)
    {
        Layout(view, 1440, 900);
        var viewport = Field<ScrollViewer>(view, "canvasScroll");
        viewport.ScrollToHorizontalOffset(180); viewport.ScrollToVerticalOffset(90); Layout(view, 1440, 900);
        var x = viewport.HorizontalOffset; var y = viewport.VerticalOffset;
        Require(view.HandleCanvasWheel(-120, ModifierKeys.Shift), "Shift-wheel must be handled");
        Layout(view, 1440, 900);
        Require(viewport.HorizontalOffset > x && viewport.VerticalOffset == y, "Shift-wheel pans only horizontally");
        Require(!view.HandleCanvasWheel(-120, ModifierKeys.None), "ordinary wheel remains vertical scrolling");
        var extent = viewport.ExtentWidth;
        Require(view.HandleCanvasWheel(120, ModifierKeys.Control), "Ctrl-wheel zoom is handled");
        Layout(view, 1440, 900);
        Require(Field<double>(view, "scale") > 1 && viewport.ExtentWidth > extent, "scroll extent follows zoom");
        Call(view, "ZoomCanvas", 1 / 1.1);
        Call(view, "Select", Field<System.Collections.IList>(view, "nodes")[2]!);
        Call(view, "ToggleLibrary", false); Call(view, "ToggleInspector", true);
        Layout(view, 1440, 900);
        var inspector = Field<StackPanel>(view, "inspector");
        var editor = Desc(inspector).OfType<TextBox>().First();
        var oldHeight = viewport.ActualHeight;
        Click(Field<Button>(view, "focusButton")); Layout(view, 1440, 900);
        Require(Field<FrameworkElement>(view, "libraryPane").Visibility == Visibility.Collapsed
            && Field<FrameworkElement>(view, "inspectorPane").Visibility == Visibility.Collapsed
            && Field<FrameworkElement>(view, "runnerPane").Visibility == Visibility.Collapsed, "focus removes auxiliary panels");
        Require(viewport.ActualHeight > oldHeight && viewport.ActualWidth > 1400, "focus returns room to canvas");
        var notice = Field<TextBlock>(view, "statusLine");
        var previousNotice = notice.Text;
        notice.Text = "保存失败，请重试"; Layout(view, 1440, 900);
        Require(notice.Visibility == Visibility.Visible && notice.ActualHeight > 0, "focus must not hide save failure notices");
        notice.Text = previousNotice;
        Render(view, 1440, 900, Path.Combine(output, "native-workflow-focus.png"));
        Layout(view, 650, 900); Layout(view, 1440, 900);
        Require(Field<FrameworkElement>(view, "libraryPane").Visibility == Visibility.Collapsed, "resizing cannot reopen focused panes");
        Require(view.HandlePresentationKey(Key.F11, ModifierKeys.None), "F11 enters fullscreen");
        Require(Equals(Field<Button>(view, "fullscreenButton").Content, "退出全屏"), "fullscreen has an explicit exit");
        var wireField = typeof(WorkflowView).GetField("cancelWire", BindingFlags.NonPublic | BindingFlags.Instance)!;
        var cancelled = false;
        wireField.SetValue(view, (Action)(() => { cancelled = true; wireField.SetValue(view, null); }));
        Require(view.HandlePresentationKey(Key.Escape, ModifierKeys.None) && cancelled && Field<bool>(view, "studioFullscreen"),
            "Escape cancels a pending wire before leaving fullscreen");
        Require(view.HandlePresentationKey(Key.Escape, ModifierKeys.None) && Field<bool>(view, "focusMode"), "Escape leaves fullscreen before focus");
        Require(view.HandlePresentationKey(Key.Escape, ModifierKeys.None), "Escape leaves focus");
        Layout(view, 1440, 900);
        Require(Field<FrameworkElement>(view, "libraryPane").Visibility == Visibility.Collapsed
            && Field<FrameworkElement>(view, "inspectorPane").Visibility == Visibility.Visible, "focus restores asymmetric panel choices");
        Require(ReferenceEquals(editor, Desc(inspector).OfType<TextBox>().First()), "focus preserves editing control and unsaved text");
        var menuButton = Desc(view).OfType<Button>().Single(b => Equals(b.Content, "视图 ▾"));
        Click(menuButton);
        var mapItem = menuButton.ContextMenu.Items.OfType<MenuItem>().Single(m => Equals(m.Header, "小地图"));
        mapItem.IsChecked = false; mapItem.RaiseEvent(new RoutedEventArgs(MenuItem.ClickEvent));
        Require(Field<FrameworkElement>(view, "minimapHost").Visibility == Visibility.Collapsed, "view menu hides minimap");
        menuButton.ContextMenu.IsOpen = false;
        Require(!Desc(view).OfType<Button>().Any(b => Equals(b.Content, "运行已发布流程")), "run has a single entry point");
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
        internal int Patches, Publishes, Validates, OtherReads;
        internal string? FailPatch;   // "error"=500 / "conflict"=409；持续到测试清除
        internal int WorkflowVersion = 3;
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
                if (FailPatch == "conflict")
                    return Response(new { detail = "草稿版本已落后，请刷新后重试" }, HttpStatusCode.Conflict);
                if (FailPatch == "error")
                    return Response(new { detail = "服务暂不可用" }, HttpStatusCode.ServiceUnavailable);
                var graph = JsonSerializer.Deserialize<JsonElement>(Saved).GetProperty("draft_graph");
                return Response(new { id = "wf-layout", name = "单页生产流程", version = Patches + 3, draft_version = Patches + 2, draft_graph = graph, published_version_id = "v1" });
            }
            if (request.Method == HttpMethod.Post)
            {
                if (path.EndsWith("/validate")) { Validates++; return Response(new { issues = Array.Empty<object>() }); }
                if (path.EndsWith("/publish")) { Publishes++; return Response(new { id = "wf-layout", name = "单页生产流程", version = WorkflowVersion, published_version_id = "v9" }); }
                throw new Exception("unexpected mutation: " + path);
            }
            Require(request.Method == HttpMethod.Get, "layout checks must not start paid work");
            if (path.EndsWith("/workflow-node-types")) return Response(Catalog);
            if (path.EndsWith("/projects/layout/workflows")) return Response(new[] {
                new { id = "wf-layout", name = "单页生产流程" },
                new { id = "wf-other", name = "备用工作流" } });
            if (path.EndsWith("/chapters")) return Response(new[] { new { id = "ch1", ordinal = 1, title = "第一章" } });
            if (path.EndsWith("/workflows/wf-layout")) return Response(new { id = "wf-layout", name = "单页生产流程", version = WorkflowVersion, draft_version = 2, draft_graph = Graph(), published_version_id = "v1" });
            if (path.EndsWith("/workflows/wf-other")) { OtherReads++; return Response(new { id = "wf-other", name = "备用工作流", version = 1, draft_version = 1, draft_graph = Graph(), published_version_id = (string?)null }); }
            return Response(Array.Empty<object>());
        }
        private static HttpResponseMessage Response(object data, HttpStatusCode status = HttpStatusCode.OK) => new(status) { Content = new StringContent(JsonSerializer.Serialize(data)) };
    }
}
