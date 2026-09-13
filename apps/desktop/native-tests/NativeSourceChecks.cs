using System.IO;
using System.Net;
using System.Net.Http;
using System.Reflection;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Controls.Primitives;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using MangaFlow.Native;
using MangaFlow.Native.Services;
using MangaFlow.Native.Views;

internal static class NativeSourceChecks
{
    public static async Task Run(string output)
    {
        var fake = new Fixture();
        using var api = new ApiClient("http://127.0.0.1:12345", fake);
        var view = new SourceView();
        var navigated = "";
        WorkspaceContext Context(string id) => new() { Api = api, State = new(), Window = null!, Project = new ProjectItem(id, "我最讨厌妹妹了", "1 章 · 11 页已规划", 0, 0), NavigateSection = (section, _) => { navigated = section; return Task.CompletedTask; }, OpenDashboard = () => Task.CompletedTask };
        view.Activate(Context("source-fixture"));
        try
        {
            await view.RefreshAsync(); Layout(view, 1062, 620);
            Require(!Field<Button>(view, "importButton").IsEnabled, "empty source must disable import");
            Require(Field<TextBox>(view, "bodyInput").ActualHeight == 80, "source textarea differs from web compact height");
            Require(Field<TextBox>(view, "titleInput").ActualHeight == 48, "source title differs from web input height");
            Require(Field<Button>(view, "planButton").IsEnabled && !Field<Button>(view, "parseButton").IsEnabled, "ready paginated chapter workflow gates");
            foreach (var width in new[] { 650, 1062 })
            {
                Layout(view, width, 620);
                var title = Field<TextBox>(view, "titleInput"); var body = Field<TextBox>(view, "bodyInput");
                Require(title.TranslatePoint(new Point(), view).Y + title.ActualHeight <= body.TranslatePoint(new Point(), view).Y, "source fields overlap");
                Render(view, width, 620, Path.Combine(output, $"native-source-restored-{width}.png"));
            }
            Require(Field<TextBlock>(view, "error").Visibility == Visibility.Collapsed && Field<TextBlock>(view, "notice").Visibility == Visibility.Collapsed, "empty notices must not leave blank form rows");
            Field<TextBlock>(view, "error").Text = "测试错误"; Layout(view, 1062, 620);
            Require(Field<TextBlock>(view, "error").Visibility == Visibility.Visible, "source errors must remain visible");
            Field<TextBlock>(view, "error").Text = "";
            var shell = new MainWindow("", Path.Combine(output, "unused-source-shell"));
            ((WorkspaceState)shell.DataContext).CurrentProject = Context("source-fixture").Project;
            typeof(MainWindow).GetField("page", BindingFlags.Instance | BindingFlags.NonPublic)!.SetValue(shell, "source");
            typeof(MainWindow).GetMethod("ApplySidebar", BindingFlags.Instance | BindingFlags.NonPublic)!.Invoke(shell, null);
            ((TextBlock)shell.FindName("TopTitle")).Text = "我最讨厌妹妹了";
            var host = (ContentControl)shell.FindName("ContentHost"); host.Content = view;
            var root = (FrameworkElement)shell.Content; Layout(root, 1280, 720);
            Render(root, 1280, 720, Path.Combine(output, "native-source-restored-shell.png")); host.Content = null;
            var second = NativeParityChecks.Descendants(view).OfType<ToggleButton>().Single(t => Equals(t.Tag, "c2"));
            second.RaiseEvent(new RoutedEventArgs(ButtonBase.ClickEvent));
            await Task.Delay(15);
            Require(Field<Button>(view, "parseButton").IsEnabled && Field<Button>(view, "planButton").IsEnabled, "second chapter selection did not update gates");
            var delayedPlan = fake.PendingPlan = new TaskCompletionSource<HttpResponseMessage>();
            Field<Button>(view, "planButton").RaiseEvent(new RoutedEventArgs(ButtonBase.ClickEvent));
            Field<Button>(view, "planButton").RaiseEvent(new RoutedEventArgs(ButtonBase.ClickEvent));
            Require(fake.PlanCalls == 1 && fake.PlanPath.EndsWith("/c2/plan"), "plan must target selected chapter exactly once");
            delayedPlan.SetResult(Json("{\"pages\":[{\"id\":\"page-c2\"}]}")); await Task.Delay(20);
            Require(navigated == "storyboard" && KeyValueStore.Get("workspace:chapter:source-fixture") == "c2" && KeyValueStore.Get("storyboard:page:source-fixture") == "page-c2", "plan lost downstream chapter/page selection");
            Field<Button>(view, "parseButton").RaiseEvent(new RoutedEventArgs(ButtonBase.ClickEvent)); await Task.Delay(15);
            Require(fake.ParsePath.EndsWith("/c2/parse") && navigated == "jobs", "parse targets the wrong chapter");
            Field<TextBox>(view, "bodyInput").Text = "测试完整原文";
            Field<Button>(view, "importButton").RaiseEvent(new RoutedEventArgs(ButtonBase.ClickEvent)); await Task.Delay(20);
            Require(fake.ImportBody.Contains("PASTE") && Field<TextBox>(view, "bodyInput").Text.Length == 0, "paste import did not save/reset");
            var file = Path.Combine(output, "source-upload-fixture.md");
            try
            {
                await File.WriteAllTextAsync(file, "# 原作\n完整章节内容");
                await (Task)typeof(SourceView).GetMethod("ImportFileAsync", BindingFlags.Instance | BindingFlags.NonPublic)!.Invoke(view, [file])!;
                Require(fake.UploadBody.Contains("完整章节内容") && fake.UploadBody.Contains("第一章") && fake.UploadBody.Contains("source-upload-fixture.md"), "file upload must use multipart title/file and import immediately");
            }
            finally { File.Delete(file); }
            var edit = NativeParityChecks.Descendants(view).OfType<Button>().First(b => System.Windows.Automation.AutomationProperties.GetName(b) == "修改 第一章 的原文");
            edit.RaiseEvent(new RoutedEventArgs(ButtonBase.ClickEvent)); await Task.Delay(15);
            Require(Field<TextBox>(view, "bodyInput").Text == "最新修订", "revision editor did not load latest revision");
            Field<Button>(view, "importButton").RaiseEvent(new RoutedEventArgs(ButtonBase.ClickEvent)); await Task.Delay(15);
            Require(fake.RevisionSaved && Field<TextBox>(view, "bodyInput").Text.Length == 0, "revision save did not refresh/reset");
            var delayedScript = fake.PendingScript = new TaskCompletionSource<HttpResponseMessage>();
            var lateRefresh = view.RefreshAsync();
            view.Deactivate(); view.Activate(Context("empty-project")); await view.RefreshAsync();
            delayedScript.SetResult(Json("{\"status\":\"READY\"}")); await lateRefresh;
            Require(Field<StackPanel>(view, "workflowActions").Visibility == Visibility.Collapsed, "late script response changed another project");
        }
        finally { view.Deactivate(); }
        Console.WriteLine("PASS: source layout, selected-chapter plan/parse, duplicate guard, multipart import, revision save and stale project isolation");
    }
    private static T Field<T>(object value, string name) => (T)value.GetType().GetField(name, BindingFlags.NonPublic | BindingFlags.Instance)!.GetValue(value)!;
    private static void Require(bool value, string message) { if (!value) throw new Exception(message); }
    private static void Layout(FrameworkElement view, int width, int height) { view.Measure(new Size(width, height)); view.Arrange(new Rect(0, 0, width, height)); view.UpdateLayout(); }
    private static void Render(FrameworkElement view, int width, int height, string path)
    {
        var bitmap = new RenderTargetBitmap(width, height, 96, 96, PixelFormats.Pbgra32);
        var paper = new DrawingVisual();
        using (var drawing = paper.RenderOpen()) drawing.DrawRectangle((Brush)Application.Current.FindResource("Paper"), null, new Rect(0, 0, width, height));
        bitmap.Render(paper); bitmap.Render(view);
        var encoder = new PngBitmapEncoder(); encoder.Frames.Add(BitmapFrame.Create(bitmap)); using var file = File.Create(path); encoder.Save(file);
    }
    private static HttpResponseMessage Json(string text) => new(HttpStatusCode.OK) { Content = new StringContent(text) };
    private sealed class Fixture : HttpMessageHandler
    {
        public int PlanCalls; public string PlanPath = "", ParsePath = "", ImportBody = "", UploadBody = ""; public bool RevisionSaved;
        public TaskCompletionSource<HttpResponseMessage>? PendingPlan, PendingScript;
        protected override async Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken token)
        {
            var path = request.RequestUri!.AbsolutePath;
            if (path.EndsWith("empty-project/chapters")) return Json("[]");
            if (path.EndsWith("/chapters")) return Json("""
                [{"id":"c1","title":"第一章","ordinal":1,"source_character_count":1342,"segment_count":9,"page_count":11,"status":"PAGES_PLANNED","coverage_ratio":1},
                 {"id":"c2","title":"第二章","ordinal":2,"source_character_count":500,"segment_count":4,"page_count":0,"status":"SCRIPT_READY","coverage_ratio":1}]
                """);
            if (path.EndsWith("/script")) { if (PendingScript is { } pending) { PendingScript = null; return await pending.Task; } return Json("{\"status\":\"READY\"}"); }
            if (path.EndsWith("/plan")) { PlanCalls++; PlanPath = path; return await PendingPlan!.Task; }
            if (path.EndsWith("/parse")) { ParsePath = path; return Json("{}"); }
            if (path.EndsWith("/sources/upload")) { UploadBody = await request.Content!.ReadAsStringAsync(token); return Json("{\"chapters\":[{\"id\":\"c1\"}]}"); }
            if (path.EndsWith("/sources/import")) { ImportBody = await request.Content!.ReadAsStringAsync(token); return Json("{\"chapters\":[{\"id\":\"c1\"}]}"); }
            if (path.EndsWith("/revisions")) { if (request.Method == HttpMethod.Post) { RevisionSaved = true; return Json("{}"); } return Json("[{\"revision\":2,\"original_text\":\"最新修订\"}]"); }
            throw new Exception("Unexpected source request: " + path);
        }
    }
}
