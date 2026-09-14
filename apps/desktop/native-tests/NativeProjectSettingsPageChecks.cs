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
            // A02：保存期间的新编辑不得被提交时的旧响应静默清脏——提交 2 后改 8，
            // 响应落地仍显示 8、继续标脏，成功提示说明“之后又有新修改”；第二次
            // 保存携带 8 与新版本（web submittedDraftRef/sameProjectDraft 同款）。
            input.Text = "2";
            fixture.Gate = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
            Click(save); await Until(() => fixture.Patches == 2);
            input.Text = "8";
            fixture.Gate.SetResult(true); await Until(() => save.IsEnabled);
            Require(input.Text == "8" && Field<bool>(view, "dirty"), "edits during save stay visible and dirty");
            Require(success.Visibility == Visibility.Visible
                && (success.Child as TextBlock)!.Text.Contains("之后又有新修改"), "in-flight edit notice distinguishes the newer edits");
            Click(save); await Until(() => save.IsEnabled);
            Require(fixture.Patches == 3 && fixture.LastPayload.Number("default_concurrency") == 8
                && fixture.LastPayload.Number("version") == 5, "second save carries the newer value and refreshed version");
            Require(!Field<bool>(view, "dirty"), "clean form after the second save");

            // A04：目录模型/旧 alias 的写入字段区分。
            var selector = Field<ComboBox>(view, "modelSelector");
            var catalogOption = selector.Items.OfType<ComboBoxItem>().Single(i => (string?)i.Tag == "cat-new");
            selector.SelectedItem = catalogOption;
            Click(save); await Until(() => fixture.Patches == 4);
            Require(fixture.LastPayload.Text("default_text_model_id") == "cat-new"
                && fixture.LastPayload.GetProperty("text_model_alias").ValueKind == JsonValueKind.Null,
                "new catalog model writes default_text_model_id, not the alias");
            fixture.State["text_model_alias"] = "legacy-alias";
            fixture.State["default_text_model_id"] = null;
            await (Task)typeof(ProjectSettingsView).GetMethod("LoadAsync", BindingFlags.NonPublic | BindingFlags.Instance)!.Invoke(view, null)!;
            await Until(() => save.IsEnabled);
            selector = Field<ComboBox>(view, "modelSelector");
            Require((selector.SelectedItem as ComboBoxItem)!.Tag as string == "legacy-alias"
                && selector.Items.OfType<ComboBoxItem>().Any(i => i.Content is string label && label.EndsWith("（已隐藏）")),
                "legacy alias round-trips as the selected route and hidden models stay labelled");
            Click(save); await Until(() => fixture.Patches == 5);
            Require(fixture.LastPayload.GetProperty("default_text_model_id").ValueKind == JsonValueKind.Null
                && fixture.LastPayload.Text("text_model_alias") == "legacy-alias",
                "explicit legacy alias selection keeps text_model_alias");
            fixture.State["text_model_alias"] = null;
            fixture.State["default_text_model_id"] = "gone-model";
            await (Task)typeof(ProjectSettingsView).GetMethod("LoadAsync", BindingFlags.NonPublic | BindingFlags.Instance)!.Invoke(view, null)!;
            await Until(() => save.IsEnabled);
            selector = Field<ComboBox>(view, "modelSelector");
            var kept = (selector.SelectedItem as ComboBoxItem)!;
            Require((string?)kept.Tag == "gone-model" && (kept.Content as string)!.StartsWith("当前配置 · "),
                "model missing from the catalog stays visible as the current configuration");

            fixture.Gate = null; fixture.Conflict = true;
            var readsBeforeConflict = fixture.Reads;
            // A04 的重载换过控件实例：并发输入框必须实时反射获取。
            Field<TextBox>(view, "concurrencyInput").Text = "7"; Click(save);
            await Until(() => save.IsEnabled && fixture.Reads > readsBeforeConflict);
            // A03：409 后本地编辑保留 + 静默重读服务端版本（不重绘表单），下一次
            // 保存携带服务器当前版本而不是停在旧值上循环 409。
            Require(fixture.Patches == 6 && error.Visibility == Visibility.Visible && error.Text.Contains("本地修改已保留")
                && success.Visibility == Visibility.Collapsed && Field<TextBox>(view, "concurrencyInput").Text == "7", "conflict preserves edits and exposes actionable error");
            Require(Field<int>(view, "version") == 9, "conflict recovery synced the server version");
            Click(save); await Until(() => save.IsEnabled);
            Require(fixture.Patches == 7, $"retry patch count (got {fixture.Patches})");
            Require(fixture.LastPayload.Number("version") == 9, $"retry carries synced version (got {fixture.LastPayload.Number("version")})");
            Require(fixture.LastPayload.Number("default_concurrency") == 7, $"retry carries preserved concurrency (got {fixture.LastPayload.Number("default_concurrency")})");
            Require(success.Visibility == Visibility.Visible, $"retry success feedback (error text: {Field<TextBlock>(view, "saveError").Text})");
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
            Require(Field<TextBox>(view, "concurrencyInput").Text == "7", "retry reloads server state");

            // 无障碍：每个可交互控件必须有可用的 UIA 名——AutomationProperties
            // 显式名或字符串 Content 至少其一。基线数量防止「树没走到」的空转绿灯。
            var interactives = Desc(view).OfType<Control>().Where(c => c is Button or ToggleButton or RadioButton or CheckBox or ComboBox or TextBox).ToArray();
            Require(interactives.Length >= 12, $"expected the full settings form's controls, found {interactives.Length}");
            foreach (var control in interactives)
            {
                var named = System.Windows.Automation.AutomationProperties.GetName(control);
                var contentName = (control as ContentControl)?.Content as string;
                Require(!string.IsNullOrWhiteSpace(named) || !string.IsNullOrWhiteSpace(contentName),
                    $"{control.GetType().Name} exposes no UIA name (AutomationProperties or string Content)");
            }
            Require(interactives.OfType<RadioButton>().All(r => !string.IsNullOrWhiteSpace(System.Windows.Automation.AutomationProperties.GetName(r))),
                "workflow-mode cards need explicit names (panel Content degrades to a type name)");

            // 返回工作区：干净表单直接导航；query 必须非空（MainWindow 深链解析
            // 解引用它，null 即 NRE 崩溃）。脏表单先过离开确认：拒绝则不导航。
            var navigations = new List<(string Section, string Query)>();
            view.Activate(new WorkspaceContext { Api = api, State = new(), Window = null!,
                Project = new("layout", "我最讨厌妹妹了", "", 0, 0),
                NavigateSection = (section, query) => { navigations.Add((section, query)); return Task.CompletedTask; },
                OpenDashboard = () => Task.CompletedTask });
            await Until(() => save.IsEnabled);
            var back = Desc(view).OfType<Button>().Single(b => Equals(b.Content, "返回工作区"));
            Click(back);
            await Until(() => navigations.Count == 1);
            Require(navigations[0].Section == "source" && navigations[0].Query != null,
                "back navigates to source with a non-null query");
            Field<TextBox>(view, "concurrencyInput").Text = "5";
            view.LeaveConfirmOverride = () => Task.FromResult(false);
            Click(back); await Task.Delay(100);
            Require(navigations.Count == 1, "blocked leave confirmation suppresses navigation");
            view.LeaveConfirmOverride = () => Task.FromResult(true);
            Click(back);
            await Until(() => navigations.Count == 2);
            Require(navigations[1].Section == "source" && navigations[1].Query != null,
                "confirmed leave navigates with a non-null query");

            Console.WriteLine("PASS: project settings layouts, pinned save/feedback, exclusive resolutions, input validation, PATCH payload/version, pending deduplication, in-flight edit protection (A02), conflict version sync + retry (A03), catalog/alias routing (A04), exact delete-name gate, rerender, read retry, UIA names, back navigation.");
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
        internal int Patches, Reads;
        internal JsonElement LastPayload;
        internal TaskCompletionSource<bool>? Gate;
        internal Dictionary<string, object?> State => project;
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
                if (Conflict)
                {
                    // 一次性冲突 + 另一端已保存（服务端版本前进）：恢复路径的 GET 读到它。
                    Conflict = false;
                    project["version"] = LastPayload.Number("version") + 1;
                    return Response("{\"detail\":\"项目版本已更新，请刷新后再试\"}", HttpStatusCode.Conflict);
                }
                foreach (var property in LastPayload.EnumerateObject()) project[property.Name] = property.Value.Clone();
                project["version"] = LastPayload.Number("version") + 1;
                return Response(JsonSerializer.Serialize(project));
            }
            Require(request.Method == HttpMethod.Get, "unexpected mutation method");
            if (FailRead) return Response("{\"detail\":\"服务暂时不可用\"}", HttpStatusCode.ServiceUnavailable);
            if (request.RequestUri!.AbsolutePath.EndsWith("/models"))
                // A04：目录模型（catalog_id 与 logical_alias 相同）、旧别名模型
                // （两者不同）与已隐藏模型三种形态。
                return Response(JsonSerializer.Serialize(new object[] {
                    new { model_type = "TEXT", catalog_id = "cat-new", logical_alias = "cat-new", display_name = "目录新模型", provider = "Fixture", display_enabled = true, operations = new[] { "structured_text", "multimodal_analysis" } },
                    new { model_type = "TEXT", catalog_id = "cat-legacy", logical_alias = "legacy-alias", display_name = "旧别名模型", provider = "Fixture", display_enabled = true, operations = new[] { "structured_text", "multimodal_analysis" } },
                    new { model_type = "TEXT", catalog_id = "cat-hidden", logical_alias = "cat-hidden", display_name = "已隐藏模型", provider = "Fixture", display_enabled = false, operations = new[] { "structured_text", "multimodal_analysis" } },
                }));
            Reads++;
            return Response(JsonSerializer.Serialize(project));
        }
        private static HttpResponseMessage Response(string json, HttpStatusCode status = HttpStatusCode.OK) => new(status) { Content = new StringContent(json) };
    }
}
