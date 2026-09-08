using System.IO;
using System.Reflection;
using System.Text.Json;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Controls.Primitives;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using System.Windows.Threading;
using MangaFlow.Native;
using MangaFlow.Native.Controls;
using MangaFlow.Native.Views;

internal static class NativeParityChecks
{
    public static void Run(string output, WorkspaceState fixture)
    {
        var shell = new MainWindow("", Path.Combine(Path.GetTempPath(), "mangaflow-unused-parity-fixture"));
        var shellState = (WorkspaceState)shell.DataContext;
        var host = (TransitioningContentControl)shell.FindName("ContentHost");
        Require(host.Content is HomeView, "startup paints home before the API connects");
        Require(((ColumnDefinition)shell.FindName("SidebarColumn")).Width.Value == 0 && !shellState.DockShown,
            "dashboard has no workspace sidebar or queue");

        foreach (var width in new[] { 940, 1320, 1600 })
        {
            var home = new HomeView { DataContext = fixture };
            Layout(home, width, 1000);
            var main = Descendants(home).OfType<FrameworkElement>().Single(x => x.Name == "DashboardMain");
            var rail = Descendants(home).OfType<FrameworkElement>().Single(x => x.Name == "DashboardRail");
            var mainBounds = Bounds(main, home);
            var railBounds = Bounds(rail, home);
            Require(width < 1180 ? railBounds.Top >= mainBounds.Bottom : railBounds.Left >= mainBounds.Right,
                $"{width}: home main and connection rail never overlap");
            var tiles = Descendants(home).OfType<TilePanel>().Single();
            Require(tiles.Children.Count == fixture.DashboardProjects.Count + 1, "new project shares the card grid");
            var rectangles = tiles.Children.Cast<FrameworkElement>().Select(x => Bounds(x, tiles)).ToList();
            foreach (var rect in rectangles) Require(rect.Width >= 180 && rect.Right <= tiles.ActualWidth + 1,
                $"{width}: cards remain within the grid");
            for (var i = 0; i < rectangles.Count; i++)
                for (var j = i + 1; j < rectangles.Count; j++)
                    Require(!rectangles[i].IntersectsWith(rectangles[j]), "project cards have independent hit areas");
            ProjectItem? clicked = null;
            home.ProjectRequested += project => clicked = project;
            var card = Descendants(home).OfType<Button>().First(x => x.Tag is ProjectItem);
            card.RaiseEvent(new RoutedEventArgs(Button.ClickEvent));
            Require(clicked?.Id == fixture.DashboardProjects[0].Id, "project card dispatches its bound project identity");
            home.OpenCreationDrawer();
            Layout(home, width, 1000);
            var drawer = Descendants(home).OfType<DrawerOverlay>().Single();
            Require(drawer.ActualWidth >= width - 1, "creation backdrop covers the workspace");
            Render(home, Path.Combine(output, $"native-create-{width}.png"), width, 1000);
            drawer.Open = false;

            var settings = new SettingsView();
            Layout(settings, width, 1000);
            var board = Descendants(settings).OfType<Grid>().Single(x => x.Name == "SettingsBoard");
            var side = Descendants(settings).OfType<FrameworkElement>().Single(x => x.Name == "SettingsDiagnostics");
            var primary = (FrameworkElement)board.Children[0];
            Require(width < 1280 ? Bounds(side, board).Top >= Bounds(primary, board).Bottom
                : Bounds(side, board).Left >= Bounds(primary, board).Right, "settings diagnostics follow the web breakpoint");
            Render(settings, Path.Combine(output, $"native-system-settings-{width}.png"), width, 1000);
            var help = new HelpView();
            Layout(help, width, 1200);
            var helpStages = Descendants(help).OfType<UniformGrid>().Single(x => x.Name == "HelpStages");
            Require(helpStages.Columns == 4 && helpStages.Children.Count == 4, "desktop help keeps the web four-stage grid");
            var troubleshooting = Descendants(help).OfType<Grid>().Single(x => x.Name == "HelpTroubleshooting");
            for (var i = 0; i < troubleshooting.Children.Count; i++)
                for (var j = i + 1; j < troubleshooting.Children.Count; j++)
                    Require(!Bounds((FrameworkElement)troubleshooting.Children[i], troubleshooting)
                        .IntersectsWith(Bounds((FrameworkElement)troubleshooting.Children[j], troubleshooting)), "help troubleshooting controls do not overlap");
            Render(help, Path.Combine(output, $"native-help-{width}.png"), width, 1200);
        }

        shellState.CurrentProject = fixture.Projects[0];
        shellState.Projects.Add(fixture.Projects[0]);
        var sections = (ListBox)shell.FindName("ProjectSections");
        foreach (var definition in ProjectPages.All)
        {
            sections.SelectedItem = definition;
            Drain();
            // Selecting the initially highlighted source entry raises no event; enter it from another page.
            if (host.Content is HomeView)
            {
                sections.SelectedItem = ProjectPages.Get(ProjectPageId.Jobs);
                sections.SelectedItem = definition;
            }
            foreach (var width in new[] { 940, 1320 })
            {
                var root = (FrameworkElement)shell.Content;
                Layout(root, width, 900);
                Require(host.ActualWidth >= 650, "workspace preserves usable canvas width");
                foreach (var heading in Descendants(host).OfType<PageHeading>())
                {
                    Require(!Bounds((FrameworkElement)heading.Children[0], heading)
                        .IntersectsWith(Bounds((FrameworkElement)heading.Children[1], heading)),
                        $"{definition.WebSection}/{width}: title and controls do not overlap");
                }
                Render(root, Path.Combine(output, $"native-{definition.WebSection}-{width}.png"), width, 900);
            }
        }
        var generate = new GenerateView();
        Set(generate, "currentPage", new PageItem("fixture-page", 1));
        using var workbench = JsonDocument.Parse("""{"readiness":{"ready":true},"storyboard":{"panels":[]},"candidates":[],"batches":[],"production":{"ready":false}}""");
        using var model = JsonDocument.Parse("""{"model_type":"IMAGE","enabled":true,"operations":["image_edit"],"logical_alias":"fixture-image","display_name":"参考图模型","model_id":"fixture-model"}""");
        Set(generate, "workbench", workbench.RootElement.Clone());
        Set(generate, "models", new List<JsonElement> { model.RootElement.Clone() });
        typeof(GenerateView).GetMethod("Render", BindingFlags.Instance | BindingFlags.NonPublic | BindingFlags.DeclaredOnly)!.Invoke(generate, null);
        Layout(generate, 1060, 1000);
        Require(Descendants(generate).OfType<ToggleButton>().Any(x => x.Content?.ToString()?.Contains("fixture-model") == true),
            "eligible generation models are actually present in the visual tree");
        Render(generate, Path.Combine(output, "native-generate-models.png"), 1060, 1000);
        var ease = new EaseOutQuint();
        Require(ease.Ease(.25) > .6 && ease.Ease(.5) > .9 && ease.Ease(.9) > .99,
            "motion follows the web ease-out curve instead of accelerating late");
        Console.WriteLine("PASS: visual parity regressions — startup, grid bounds, project hits, settings breakpoints, nine pages, model picker, drawers and easing");
    }

    internal static IEnumerable<DependencyObject> Descendants(DependencyObject root)
    {
        yield return root;
        for (var i = 0; i < VisualTreeHelper.GetChildrenCount(root); i++)
            foreach (var child in Descendants(VisualTreeHelper.GetChild(root, i))) yield return child;
    }
    private static Rect Bounds(FrameworkElement element, Visual relative) =>
        element.TransformToAncestor(relative).TransformBounds(new Rect(element.RenderSize));
    private static void Layout(FrameworkElement view, int width, int height)
    {
        view.Measure(new Size(width, height));
        view.Arrange(new Rect(0, 0, width, height));
        view.UpdateLayout();
        Drain();
        view.UpdateLayout();
    }
    private static void Drain() => Dispatcher.CurrentDispatcher.Invoke(() => { }, DispatcherPriority.ApplicationIdle);
    private static void Set(object instance, string field, object value) =>
        instance.GetType().GetField(field, BindingFlags.Instance | BindingFlags.NonPublic)!.SetValue(instance, value);
    private static void Require(bool condition, string message)
    {
        if (!condition) throw new Exception(message);
    }
    private static void Render(FrameworkElement element, string path, int width, int height)
    {
        var background = new DrawingVisual();
        using (var drawing = background.RenderOpen())
            drawing.DrawRectangle((Brush)Application.Current.FindResource("Paper"), null, new Rect(0, 0, width, height));
        var bitmap = new RenderTargetBitmap(width, height, 96, 96, PixelFormats.Pbgra32);
        bitmap.Render(background);
        bitmap.Render(element);
        var encoder = new PngBitmapEncoder();
        encoder.Frames.Add(BitmapFrame.Create(bitmap));
        using var file = File.Create(path);
        encoder.Save(file);
    }
}
