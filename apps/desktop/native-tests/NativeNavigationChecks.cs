using System.IO;
using System.Net;
using System.Net.Http;
using System.Reflection;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using System.Windows.Threading;
using MangaFlow.Native;
using MangaFlow.Native.Services;

internal static class NativeNavigationChecks
{
    public static void Run(string output)
    {
        const BindingFlags flags = BindingFlags.Instance | BindingFlags.NonPublic;
        var window = new MainWindow("", Path.Combine(Path.GetTempPath(), "mangaflow-unused-navigation-fixture"));
        var state = (WorkspaceState)window.DataContext;
        var sections = (ListBox)window.FindName("ProjectSections");
        var current = typeof(MainWindow).GetMethod("LoadCurrentAsync", flags)!;
        var paths = new List<string>();
        var delayed = new TaskCompletionSource<HttpResponseMessage>();
        var delayNext = false;
        CancellationToken readToken = default;
        using var api = new ApiClient("http://127.0.0.1:12345", new Handler((request, token) =>
        {
            paths.Add(request.RequestUri!.AbsolutePath);
            if (delayNext) { delayNext = false; readToken = token; return delayed.Task; }
            return Task.FromResult(Response("[]"));
        }));
        typeof(MainWindow).GetField("api", flags)!.SetValue(window, api);
        state.CurrentProject = new ProjectItem("fixture", "雨夜来信", "3 章 · 12 页", 0, 0);
        state.Projects.Add(state.CurrentProject);
        ((ComboBox)window.FindName("ProjectList")).SelectedItem = state.CurrentProject;
        state.Connected = true;
        typeof(MainWindow).GetMethod("Navigate", flags)!.Invoke(window, ["project"]);
        var frame = (WorkspacePageFrame)window.FindName("ProjectPage");
        foreach (var definition in ProjectPages.All)
        {
            sections.SelectedItem = definition;
            Drain();
            Require(state.Navigation.Current == definition && frame.Page == definition, "selection and page header agree");
            Require(state.Breadcrumb == $"雨夜来信 / {definition.Title}", "breadcrumb follows selected page");
            Require(((FrameworkElement)window.FindName("PendingPage")).Visibility ==
                (definition.IsConnected ? Visibility.Collapsed : Visibility.Visible), "availability is honest");
            var before = paths.Count;
            Complete((Task)current.Invoke(window, [false])!);
            Require(paths.Count == before + (definition.IsConnected ? 1 : 0), "preview never sends a fallback request");
            if (definition.IsConnected)
                Require(paths.Last() == $"/api/v1/projects/fixture/{definition.ReadResource}", "page uses its explicit resource");
        }
        // A slow source read is still in flight when the user leaves for a preview.
        // The handler deliberately ignores cancellation and returns a late row.
        sections.SelectedItem = ProjectPages.Get(ProjectPageId.Source);
        Drain();
        delayNext = true;
        var read = (Task)current.Invoke(window, [false])!;
        sections.SelectedItem = ProjectPages.Get(ProjectPageId.Storyboard);
        Require(readToken.IsCancellationRequested, "leaving source cancels its request even for an offline preview");
        delayed.SetResult(Response("[{\"id\":\"late\",\"title\":\"迟到章节\"}]"));
        Complete(read);
        Require(state.Chapters.Count == 0 && state.Navigation.Current.Id == ProjectPageId.Storyboard,
            "late source response cannot populate the new page");
        Require(!state.Busy, "cancelled reads release busy state");
        state.Connected = false;
        sections.SelectedItem = ProjectPages.Get(ProjectPageId.Jobs);
        Require(((FrameworkElement)window.FindName("JobsPane")).Visibility == Visibility.Visible,
            "offline navigation still switches content immediately");
        Require(ReferenceEquals(frame, window.FindName("ProjectPage")), "navigation retains the shared page container");

        foreach (var width in new[] { 1320, 940 })
        {
            window.Width = width;
            var content = (FrameworkElement)window.Content;
            sections.SelectedItem = ProjectPages.Get(ProjectPageId.Storyboard);
            content.Measure(new Size(width, 860));
            content.Arrange(new Rect(0, 0, width, 860));
            content.UpdateLayout();
            Drain();
            Require(frame.ActualWidth >= 600, "workspace remains usable at minimum desktop width");
            Require(frame.Page.Title == "分页与分镜", "frame binding survives layout");
            var background = new DrawingVisual();
            using (var drawing = background.RenderOpen())
                drawing.DrawRectangle((Brush)window.FindResource("Paper"), null, new Rect(0, 0, width, 860));
            var bitmap = new RenderTargetBitmap(width, 860, 96, 96, PixelFormats.Pbgra32);
            bitmap.Render(background);
            bitmap.Render(content);
            var encoder = new PngBitmapEncoder();
            encoder.Frames.Add(BitmapFrame.Create(bitmap));
            using var file = File.Create(Path.Combine(output, $"native-workspace-{width}.png"));
            encoder.Save(file);
        }
        Console.WriteLine("PASS: nine native routes, real selection events, request routing, stale response cancellation, offline navigation and workspace layouts");
    }

    private static HttpResponseMessage Response(string json) => new(HttpStatusCode.OK) { Content = new StringContent(json) };
    private static void Require(bool condition, string label) { if (!condition) throw new Exception(label); }
    private static void Drain() => Dispatcher.CurrentDispatcher.Invoke(() => { }, DispatcherPriority.ApplicationIdle);
    private static void Complete(Task task)
    {
        var deadline = DateTime.UtcNow.AddSeconds(5);
        while (!task.IsCompleted && DateTime.UtcNow < deadline) Drain();
        Require(task.IsCompleted, "UI request completes within five seconds");
        task.GetAwaiter().GetResult();
    }
    private sealed class Handler(Func<HttpRequestMessage, CancellationToken, Task<HttpResponseMessage>> send) : HttpMessageHandler
    {
        protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken cancellationToken) => send(request, cancellationToken);
    }
}
