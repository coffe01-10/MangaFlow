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
using MangaFlow.Native.Controls;
using MangaFlow.Native.Services;
using MangaFlow.Native.Views;

internal static class NativeProjectSettingsPageChecks
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
                app.Dispatcher.BeginInvoke(new Action(async () =>
                {
                    try
                    {
                        await Checks(output);
                        foreach (var method in new[] { "ProjectSettingsLeaveAndRefreshChecks", "ProjectSettingsCancelledSaveChecks" })
                            await (Task)typeof(NativeIssue469Checks).GetMethod(method, BindingFlags.NonPublic | BindingFlags.Static)!.Invoke(null, null)!;
                    }
                    catch (Exception e) { failure = e; }
                    finally { app.Shutdown(); }
                }));
                app.Run();
            });
            thread.SetApartmentState(ApartmentState.STA); thread.Start(); thread.Join();
            if (failure != null) throw new Exception("Project settings page checks failed", failure);
        }
        finally { KeyValueStore.UseLocation(previous); File.Delete(prefs); File.Delete(prefs + ".tmp"); }
    }

    private static async Task Checks(string output)
    {
        var fixture = new Fixture();
        using var api = new ApiClient("http://127.0.0.1:12345", fixture);
        var view = new ProjectSettingsView();
        var save = Field<Button>(view, "saveButton");
        try
        {
            view.Activate(new WorkspaceContext { Api = api, State = new(), Window = null!,
                Project = new("layout", "我最讨厌妹妹了", "", 0, 0),
                NavigateSection = (_, _) => Task.CompletedTask, OpenDashboard = () => Task.CompletedTask });
            await Until(() => save.IsEnabled);
            foreach (int width in new[] { 1240, 940, 650, 360 })
            {
                Layout(view, width, 1600);
                var grid = Desc(view).OfType<ProjectSettingsGrid>().Single();
                Fits(grid);
                var first = ((FrameworkElement)grid.Children[0]).TransformToAncestor(grid).Transform(new Point());
                var second = ((FrameworkElement)grid.Children[1]).TransformToAncestor(grid).Transform(new Point());
                Require(grid.ActualWidth >= 760 ? Math.Abs(first.Y - second.Y) < 1 : second.Y > first.Y, "responsive settings columns");
                foreach (var heading in Desc(view).OfType<PageHeading>()) Fits(heading);
                var danger = Field<Border>(view, "dangerZone");
                Require(danger.TranslatePoint(new Point(), view).Y >= grid.TranslatePoint(new Point(0, grid.ActualHeight), view).Y, "danger zone follows all settings");
                Render(view, width, width < 680 ? 1500 : 1100, Path.Combine(output, $"native-project-settings-{width}.png"));
            }
            var input = Field<TextBox>(view, "concurrencyInput");
            var error = Field<TextBlock>(view, "saveError");
            var success = Field<Border>(view, "saveSuccess");
            foreach (string invalid in new[] { "9", "abc", "0", "1.5" })
            {
                input.Text = invalid; Click(save);
                Require(fixture.Patches == 0 && error.Visibility == Visibility.Visible, "invalid concurrency rejected before HTTP");
            }
            input.Text = "8";
            Field<StackPanel>(view, "modeGroup").Children.OfType<RadioButton>().Single(b => Equals(b.Tag, "DIRECTOR")).IsChecked = true;
            var draft = Field<StackPanel>(view, "draftGroup").Children.OfType<ToggleButton>().Single(b => Equals(b.Content, "2K"));
            draft.IsChecked = true; draft.IsChecked = false; Click(draft);
            Require(draft.IsChecked == true, "selected resolution cannot be deselected");
            Field<StackPanel>(view, "finalGroup").Children.OfType<ToggleButton>().Single(b => Equals(b.Content, "4K")).IsChecked = true;
            Field<CheckBox>(view, "consistencySwitch").IsChecked = false;
            fixture.Gate = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
            Click(save); Click(save); await Until(() => fixture.Patches == 1);
            Require(!save.IsEnabled, "save is disabled while pending");
            fixture.Gate.SetResult(true); await Until(() => save.IsEnabled);
            Require(success.Visibility == Visibility.Visible && error.Visibility == Visibility.Collapsed, "successful save feedback");
            var sent = fixture.LastPayload;
            Require(sent.Text("workflow_mode") == "DIRECTOR" && sent.Text("draft_resolution") == "2K"
                && sent.Text("default_resolution") == "4K" && sent.Number("default_concurrency") == 8
                && !sent.Flag("consistency_check_enabled") && sent.Number("version") == 3, "exact settings PATCH fields and optimistic version");
            Require(sent.GetProperty("default_text_model_id").ValueKind == JsonValueKind.Null
                && sent.GetProperty("text_model_alias").ValueKind == JsonValueKind.Null, "auto route clears both model fields");
            Require(Field<int>(view, "version") == 4, "returned version retained");
            Layout(view, 1240, 900);
            var scroll = Desc(view).OfType<ScrollViewer>().First(s => ReferenceEquals(s.Content, Field<StackPanel>(view, "body")));
            scroll.ScrollToEnd(); Layout(view, 1240, 900);
            Require(save.TranslatePoint(new Point(), view).Y < 100 && success.TranslatePoint(new Point(), view).Y < 180, "save and feedback stay above scrolled form");
            Render(view, 1240, 900, Path.Combine(output, "native-project-settings-saved.png"));
            fixture.Gate = null; fixture.Conflict = true;
            input.Text = "7"; Click(save); await Until(() => save.IsEnabled);
            Require(fixture.Patches == 2 && error.Visibility == Visibility.Visible && error.Text.Contains("刷新")
                && success.Visibility == Visibility.Collapsed && input.Text == "7", "conflict preserves edits and exposes actionable error");
            Render(view, 1240, 900, Path.Combine(output, "native-project-settings-conflict.png"));
            var delete = Field<Button>(view, "deleteButton");
            var name = Field<TextBox>(view, "nameForDelete");
            Require(!delete.IsEnabled, "delete disabled without name");
            name.Text = "错误名称"; Require(!delete.IsEnabled, "delete disabled for wrong name");
            name.Text = "我最讨厌妹妹了"; Require(delete.IsEnabled, "exact project name enables confirmation");
            name.Text += " "; Require(!delete.IsEnabled, "name confirmation requires exact match");
            typeof(ProjectSettingsView).GetMethod("Render", BindingFlags.NonPublic | BindingFlags.Instance | BindingFlags.DeclaredOnly)!.Invoke(view, null);
            typeof(ProjectSettingsView).GetMethod("Render", BindingFlags.NonPublic | BindingFlags.Instance | BindingFlags.DeclaredOnly)!.Invoke(view, null);
            fixture.FailRead = true;
            await (Task)typeof(ProjectSettingsView).GetMethod("LoadAsync", BindingFlags.NonPublic | BindingFlags.Instance)!.Invoke(view, null)!;
            Layout(view, 1240, 900);
            Require(Desc(view).OfType<TextBlock>().Any(t => t.Text == "项目设置读取失败") && !save.IsEnabled, "read failure is rendered without parent reuse and disables save");
            Render(view, 1240, 900, Path.Combine(output, "native-project-settings-read-error.png"));
            fixture.FailRead = false;
            Click(Desc(view).OfType<Button>().Single(b => Equals(b.Content, "重试"))); await Until(() => save.IsEnabled);
            Require(Field<TextBox>(view, "concurrencyInput").Text == "8", "retry reloads server state");
            Console.WriteLine("PASS: project settings 1240/940/650/360 layouts, pinned save/feedback, exclusive resolutions, input validation, PATCH payload/version, pending deduplication, conflict, exact delete-name gate, rerender and read retry.");
            Console.WriteLine("Offscreen WPF and HTTP fixtures only; live backend, deletion, providers, high-DPI windows and frame timing NOT RUN.");
        }
        finally { Field<System.Timers.Timer?>(view, "successTimer")?.Dispose(); view.Deactivate(); }
    }
    private static void Fits(Panel panel)
    {
        var boxes = panel.Children.Cast<FrameworkElement>().Where(c => c.Visibility != Visibility.Collapsed)
            .Select(c => c.TransformToAncestor(panel).TransformBounds(new Rect(c.RenderSize))).ToArray();
        Require(boxes.All(b => b.Left >= -1 && b.Right <= panel.ActualWidth + 1), $"child escaped {panel.GetType().Name} width {panel.ActualWidth}");
        for (int i = 0; i < boxes.Length; i++) for (int j = i + 1; j < boxes.Length; j++)
        {
            var overlap = Rect.Intersect(boxes[i], boxes[j]);
            Require(overlap.IsEmpty || overlap.Width <= 1 || overlap.Height <= 1, "layout children overlap");
        }
    }
    private static IEnumerable<DependencyObject> Desc(DependencyObject e) => NativeParityChecks.Descendants(e);
    private static void Click(ButtonBase b) => b.RaiseEvent(new RoutedEventArgs(ButtonBase.ClickEvent));
    private static T Field<T>(object e, string name) => (T)e.GetType().GetField(name, BindingFlags.Instance | BindingFlags.NonPublic)!.GetValue(e)!;
    private static void Require(bool ok, string message) { if (!ok) throw new Exception(message); }
    private static async Task Until(Func<bool> predicate) { using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(8)); while (!predicate()) await Task.Delay(10, timeout.Token); }
    private static void Layout(FrameworkElement e, int w, int h) { foreach (var child in Desc(e).OfType<UIElement>()) child.InvalidateMeasure(); e.InvalidateMeasure(); e.Measure(new Size(w, h)); e.Arrange(new Rect(0, 0, w, h)); e.UpdateLayout(); }
    private static void Render(FrameworkElement e, int w, int h, string path) { Layout(e, w, h); var bitmap = new RenderTargetBitmap(w, h, 96, 96, PixelFormats.Pbgra32); bitmap.Render(e); var encoder = new PngBitmapEncoder(); encoder.Frames.Add(BitmapFrame.Create(bitmap)); using var f = File.Create(path); encoder.Save(f); }
    private sealed class Fixture : HttpMessageHandler
    {
        internal bool Conflict, FailRead;
        internal int Patches;
        internal JsonElement LastPayload;
        internal TaskCompletionSource<bool>? Gate;
        private Dictionary<string, object?> project = new() {
            ["id"] = "layout", ["name"] = "我最讨厌妹妹了", ["workflow_mode"] = "SEMI_AUTO",
            ["draft_resolution"] = "1K", ["default_resolution"] = "2K", ["default_concurrency"] = 2,
            ["consistency_check_enabled"] = true, ["version"] = 3 };
        protected override async Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken token)
        {
            if (request.Method == HttpMethod.Patch)
            {
                Require(request.RequestUri!.AbsolutePath.EndsWith("/projects/layout"), "unexpected mutation path");
                LastPayload = JsonDocument.Parse(await request.Content!.ReadAsStringAsync(token)).RootElement.Clone(); Patches++;
                if (Gate != null) await Gate.Task.WaitAsync(token);
                if (Conflict) return Response("{\"detail\":\"项目版本已更新，请刷新后再试\"}", HttpStatusCode.Conflict);
                foreach (var property in LastPayload.EnumerateObject()) project[property.Name] = property.Value.Clone();
                project["version"] = LastPayload.Number("version") + 1;
                return Response(JsonSerializer.Serialize(project));
            }
            Require(request.Method == HttpMethod.Get, "unexpected mutation method");
            if (FailRead) return Response("{\"detail\":\"服务暂时不可用\"}", HttpStatusCode.ServiceUnavailable);
            if (request.RequestUri!.AbsolutePath.EndsWith("/models"))
                return Response("[{\"model_type\":\"TEXT\",\"logical_alias\":\"text-fixture\",\"display_name\":\"已验证文字模型\",\"provider\":\"Fixture\",\"operations\":[\"structured_text\",\"multimodal_analysis\"]}]");
            return Response(JsonSerializer.Serialize(project));
        }
        private static HttpResponseMessage Response(string json, HttpStatusCode status = HttpStatusCode.OK) => new(status) { Content = new StringContent(json) };
    }
}
