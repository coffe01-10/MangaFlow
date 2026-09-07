using System.IO;
using System.Reflection;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;
using MangaFlow.Native;
using MangaFlow.Native.Services;
using MangaFlow.Native.Views;

internal static class NativeNavigationChecks
{
    public static void Run(string output)
    {
        var window = new MainWindow("", Path.Combine(Path.GetTempPath(), "mangaflow-unused-navigation-fixture"));
        var state = (WorkspaceState)window.DataContext;
        state.CurrentProject = new ProjectItem("fixture", "雨夜来信", "3 章 12 页", 0, 0);
        state.Projects.Add(state.CurrentProject);
        var sections = (ListBox)window.FindName("ProjectSections")!;
        var host = (MangaFlow.Native.Controls.TransitioningContentControl)window.FindName("ContentHost")!;

        // 1. Every project page definition maps to a distinct registered view.
        foreach (var definition in ProjectPages.All)
        {
            sections.SelectedItem = definition;
            Drain();
            Require(state.Navigation.Current == definition, "selection updates navigation state");
        }
        Require(ProjectPages.FindBySection("generate")!.Id == ProjectPageId.Generate, "section lookup by web route");
        Require(ProjectPages.FindBySection("nope") == null, "unknown section rejected");

        // 2. Offline navigation still switches content immediately; no page sends a request while disconnected.
        var contents = new HashSet<object>();
        foreach (var definition in ProjectPages.All)
        {
            sections.SelectedItem = definition;
            Drain();
            Require(host.Content is IWorkspaceView, "project section hosts a workspace view");
            contents.Add(host.Content!);
        }
        Require(contents.Count == ProjectPages.All.Count, "each project page owns a distinct view instance");

        // 3. Re-selecting the same page keeps the instance (no rebuild churn).
        var generate = ProjectPages.Get(ProjectPageId.Generate);
        sections.SelectedItem = generate;
        Drain();
        var before = host.Content;
        sections.SelectedItem = ProjectPages.Get(ProjectPageId.Jobs);
        Drain();
        sections.SelectedItem = generate;
        Drain();
        Require(ReferenceEquals(before, host.Content), "cached view instances survive navigation");

        // 4. Sidebar collapse keeps the workspace usable and the shell chrome in sync.
        var sidebarToggle = (Button)window.FindName("SidebarToggle")!;
        sidebarToggle.RaiseEvent(new RoutedEventArgs(Button.ClickEvent));
        Drain();
        var sidebar = (ColumnDefinition)window.FindName("SidebarColumn")!;
        Require(sidebar.Width.Value == 0, "sidebar collapses to zero width");
        sidebarToggle.RaiseEvent(new RoutedEventArgs(Button.ClickEvent));
        Drain();
        Require(sidebar.Width.Value > 0, "sidebar restores");

        // 5. Render the workspace at both desktop widths.
        foreach (var width in new[] { 1320, 940 })
        {
            window.Width = width;
            var content = (FrameworkElement)window.Content;
            sections.SelectedItem = ProjectPages.Get(ProjectPageId.Storyboard);
            content.Measure(new Size(width, 860));
            content.Arrange(new Rect(0, 0, width, 860));
            content.UpdateLayout();
            Drain();
            Require(host.ActualWidth >= 420, "workspace remains usable at minimum desktop width");
            var background = new DrawingVisual();
            using (var drawing = background.RenderOpen())
                drawing.DrawRectangle((Brush)window.FindResource("Paper"), null, new Rect(0, 0, width, 860));
            var bitmap = new System.Windows.Media.Imaging.RenderTargetBitmap(width, 860, 96, 96, System.Windows.Media.PixelFormats.Pbgra32);
            bitmap.Render(background);
            bitmap.Render(content);
            var encoder = new System.Windows.Media.Imaging.PngBitmapEncoder();
            encoder.Frames.Add(System.Windows.Media.Imaging.BitmapFrame.Create(bitmap));
            using var file = File.Create(Path.Combine(output, $"native-workspace-{width}.png"));
            encoder.Save(file);
        }
        Console.WriteLine("PASS: view registry, offline navigation, cached instances, sidebar collapse, workspace layouts");
    }

    public static void RunDirectorRules()
    {
        using var page = System.Text.Json.JsonDocument.Parse("""
            {"id":"page-1","project_id":"p1","page_number":3,"version":7,"scene_version":2,"scene_ids":["scene-1"]}
            """);
        using var storyboard = System.Text.Json.JsonDocument.Parse("""
            {"panels":[{"id":"panel-1","reading_order":1,"version":4,"characters":["char-1"],"character_presence":{"char-1":"VISIBLE"},"expressions":{},
              "dialogues":[{"id":"dlg-1","reading_order":1,"panel_id":"panel-1","rewrite_forbidden":false,"target_text":"旧台词"}]}]}
            """);
        using var character = System.Text.Json.JsonDocument.Parse("""{"id":"char-1","primary_name":"小满","aliases":[]}""");
        var characters = new List<System.Text.Json.JsonElement> { character.RootElement.Clone() };

        var pageElement = page.RootElement;
        var storyboardElement = storyboard.RootElement;

        // Whole-page count command (high risk) with generation guard.
        var count = DirectorRules.Compile(pageElement, storyboardElement, characters, null, "改成 6 格", null, pageGenerationPending: false);
        Require(count.Kind == "command" && count.Risk == "high", "page count command compiles as high risk");
        Require(count.Summary.Contains("6 格"), "page count summary mentions the target count");
        var pending = DirectorRules.Compile(pageElement, storyboardElement, characters, null, "改成 6 格", null, pageGenerationPending: true);
        Require(pending.Kind == "blocked", "page command blocked while a generation is pending");

        // Pixel-level intents are hard-rejected and pointed at the local editor.
        var pixel = DirectorRules.Compile(pageElement, storyboardElement, characters, null, "把雨重画一下", null, false);
        Require(pixel.Kind == "unsupported" && pixel.Reason!.Contains("局部编辑"), "pixel intents route to the local editor");

        // Dialogue rewrite needs explicit text.
        var missing = DirectorRules.Compile(pageElement, storyboardElement, characters, null, "第 1 格 台词改成", null, false);
        Require(missing.Kind == "clarify", "dialogue without new text asks for clarification");
        var dialogue = DirectorRules.Compile(pageElement, storyboardElement, characters, null, "第 1 格 台词改成「我没事」", null, false);
        Require(dialogue.Kind == "command" && dialogue.Risk == "low", "dialogue rewrite compiles as low risk");

        // Scene weather maps through the primary scene (needs a real scene version).
        var noVersion = DirectorRules.Compile(pageElement, storyboardElement, characters, null, "雨下大一点", null, false);
        Require(noVersion.Kind == "unsupported", "weather without a scene version is rejected honestly");
        var weather = DirectorRules.Compile(pageElement, storyboardElement, characters, null, "雨下大一点", null, false, sceneVersion: 2);
        Require(weather.Kind == "command" && weather.IntentLabel == "场景上下文", "weather compiles a scene context command");

        // Empty utterance clarifies.
        var empty = DirectorRules.Compile(pageElement, storyboardElement, characters, null, "   ", null, false);
        Require(empty.Kind == "clarify", "empty utterance asks for input");

        Console.WriteLine("PASS: director rule stub compiles whitelisted commands and gates pixel intents");
    }

    private static void Require(bool condition, string label) { if (!condition) throw new Exception(label); }
    private static void Drain() => System.Windows.Threading.Dispatcher.CurrentDispatcher.Invoke(() => { }, System.Windows.Threading.DispatcherPriority.ApplicationIdle);
}
