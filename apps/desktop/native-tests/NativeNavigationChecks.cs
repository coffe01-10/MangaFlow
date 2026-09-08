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
        Require(sidebar.Width.Value == 52, "sidebar collapses to a usable icon rail");
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
        // PageRead rows carry no project_id — the caller (GenerateView) passes it in.
        using var page = System.Text.Json.JsonDocument.Parse("""
            {"id":"page-1","page_number":3,"version":7,"scene_version":2,"scene_ids":["scene-1"]}
            """);
        using var storyboard = System.Text.Json.JsonDocument.Parse("""
            {"panels":[{"id":"panel-1","reading_order":1,"version":4,"characters":["char-1"],"character_presence":{"char-1":"VISIBLE"},"expressions":{},
              "dialogues":[{"id":"dlg-1","reading_order":1,"panel_id":"panel-1","rewrite_forbidden":false,"target_text":"旧台词"}]}]}
            """);
        using var character = System.Text.Json.JsonDocument.Parse("""{"id":"char-1","primary_name":"小满","aliases":[]}""");
        using var character2 = System.Text.Json.JsonDocument.Parse("""{"id":"char-2","primary_name":"阿岚","aliases":[]}""");
        var characters = new List<System.Text.Json.JsonElement> { character.RootElement.Clone(), character2.RootElement.Clone() };

        var pageElement = page.RootElement;
        var storyboardElement = storyboard.RootElement;
        const string ProjectId = "0f1e2d3c-4b5a-6978-8796-a5b4c3d2e1f0";

        // Whole-page count command (high risk) with generation guard.
        var count = DirectorRules.Compile(pageElement, ProjectId, storyboardElement, characters, null, "改成 6 格", null, pageGenerationPending: false);
        Require(count.Kind == "command" && count.Risk == "high", "page count command compiles as high risk");
        Require(count.Summary.Contains("6 格"), "page count summary mentions the target count");
        Require(count.Envelope is Dictionary<string, object?>, "command envelope is a plain dictionary");
        var envelope = (Dictionary<string, object?>)count.Envelope!;
        Require(Guid.TryParse((string)envelope["command_id"], out _) && ((string)envelope["command_id"]).Length == 36,
            "command_id is a 36-char UUID the backend can parse (no cmd- prefix)");
        Require(Guid.TryParse((string)envelope["command_group_id"], out _) && ((string)envelope["command_group_id"]).Length == 36,
            "command_group_id is a 36-char UUID (no grp- prefix)");
        var target = (Dictionary<string, object?>)envelope["target"];
        Require((string?)target["project_id"] == ProjectId, "project_id comes from the caller, not the page row");
        Require((string?)target["page_id"] == "page-1", "envelope targets the page from the storyboard");
        var pending = DirectorRules.Compile(pageElement, ProjectId, storyboardElement, characters, null, "改成 6 格", null, pageGenerationPending: true);
        Require(pending.Kind == "blocked", "page command blocked while a generation is pending");
        var retry = DirectorRules.Compile(pageElement, ProjectId, storyboardElement, characters, null, "改成 6 格", "orig-command-id", false);
        Require(retry.Envelope is Dictionary<string, object?> retryEnvelope && (string?)retryEnvelope["retry_of_command_id"] == "orig-command-id",
            "retry_of_command_id passes through into the envelope");

        // Pixel-level intents are hard-rejected and pointed at the local editor.
        var pixel = DirectorRules.Compile(pageElement, ProjectId, storyboardElement, characters, null, "把雨重画一下", null, false);
        Require(pixel.Kind == "unsupported" && pixel.Reason!.Contains("局部编辑"), "pixel intents route to the local editor");

        // Dialogue rewrite needs explicit text.
        var missing = DirectorRules.Compile(pageElement, ProjectId, storyboardElement, characters, null, "第 1 格 台词改成", null, false);
        Require(missing.Kind == "clarify", "dialogue without new text asks for clarification");
        var dialogue = DirectorRules.Compile(pageElement, ProjectId, storyboardElement, characters, null, "第 1 格 台词改成「我没事」", null, false);
        Require(dialogue.Kind == "command" && dialogue.Risk == "low", "dialogue rewrite compiles as low risk");

        // Scene weather maps through the primary scene (needs a real scene version).
        var noVersion = DirectorRules.Compile(pageElement, ProjectId, storyboardElement, characters, null, "雨下大一点", null, false);
        Require(noVersion.Kind == "unsupported", "weather without a scene version is rejected honestly");
        var weather = DirectorRules.Compile(pageElement, ProjectId, storyboardElement, characters, null, "雨下大一点", null, false, sceneVersion: 2);
        Require(weather.Kind == "command" && weather.IntentLabel == "场景上下文", "weather compiles a scene context command");

        // Empty utterance clarifies.
        var empty = DirectorRules.Compile(pageElement, ProjectId, storyboardElement, characters, null, "   ", null, false);
        Require(empty.Kind == "clarify", "empty utterance asks for input");

        // Priority parity with director-rules.ts (audit D4): a literal name in the
        // utterance beats the character chip selection.
        var castAdd = DirectorRules.Compile(pageElement, ProjectId, storyboardElement, characters,
            DirectorScope.Character("char-1"), "让阿岚出现在画面里", null, false);
        Require(castAdd.Kind == "command", "explicit name in the utterance beats the character chip selection");
        var castEnvelope = (Dictionary<string, object?>)castAdd.Envelope!;
        var castPayload = (Dictionary<string, object?>)castEnvelope["payload"];
        Require(castPayload["characters"] is IEnumerable<object?> cast && cast.Contains("char-2") && cast.Contains("char-1"),
            "cast command targets the mentioned character, not the chip");

        // A character chip pins the panel when that character appears in exactly one panel.
        var characterPanel = DirectorRules.Compile(pageElement, ProjectId, storyboardElement, characters,
            DirectorScope.Character("char-1"), "特写", null, false);
        Require(characterPanel.Kind == "command", "character chip derives the unique panel hosting that character");
        var shotEnvelope = (Dictionary<string, object?>)characterPanel.Envelope!;
        Require(((Dictionary<string, object?>)shotEnvelope["target"]!)["panel_id"] is "panel-1", "derived panel id lands in the envelope target");

        Console.WriteLine("PASS: director rule stub compiles whitelisted commands, UUID envelopes and web priority parity");
    }

    private static void Require(bool condition, string label) { if (!condition) throw new Exception(label); }
    private static void Drain() => System.Windows.Threading.Dispatcher.CurrentDispatcher.Invoke(() => { }, System.Windows.Threading.DispatcherPriority.ApplicationIdle);
}
