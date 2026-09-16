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

internal static class NativeSystemSettingsPageChecks
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
                        foreach (var method in new[] { "SettingsRefreshDraftChecks" })
                            await (Task)typeof(NativeIssue469Checks).GetMethod(method, BindingFlags.NonPublic | BindingFlags.Static)!.Invoke(null, null)!;
                        await (Task)typeof(NativeConsistencyChecks).GetMethod("SettingsPublishChecks", BindingFlags.NonPublic | BindingFlags.Static)!.Invoke(null, null)!;
                    }
                    catch (Exception e) { failure = e; }
                    finally { app.Shutdown(); }
                }));
                app.Run();
            });
            thread.SetApartmentState(ApartmentState.STA); thread.Start(); thread.Join();
            if (failure != null) throw new Exception("System settings page checks failed", failure);
        }
        finally { KeyValueStore.UseLocation(previous); File.Delete(prefs); File.Delete(prefs + ".tmp"); }
    }

    private static async Task Checks(string output)
    {
        var fixture = new Fixture();
        using var api = new ApiClient("http://127.0.0.1:12345", fixture);
        var view = new SettingsView();
        var navigated = "";
        try
        {
            view.Activate(new WorkspaceContext { Api = api, State = new(), Window = null!, Project = null,
                NavigateSection = (section, _) => { navigated = section; return Task.CompletedTask; }, OpenDashboard = () => { navigated = "home"; return Task.CompletedTask; } });
            await Until(() => Field<Dictionary<string, FrameworkElement>>(view, "runtimeInputs").Count == 7 && fixture.ModelReads > 0);
            Require(Field<TextBlock>(view, "healthLabel").Text == "2 健康", "health label reflects provider state (A09)");
            foreach (int width in new[] { 1440, 1240, 760, 360 })
            {
                Layout(view, width, 1800);
                foreach (var tiles in Desc(view).OfType<SystemSettingsTiles>()) Fits(tiles);
                foreach (var heading in Desc(view).OfType<PageHeading>()) Fits(heading);
                var board = Desc(view).OfType<Grid>().Single(g => g.Name == "SettingsBoard");
                var side = Desc(view).OfType<FrameworkElement>().Single(g => g.Name == "SettingsDiagnostics");
                var main = (FrameworkElement)board.Children[0];
                Require(width >= 1280 ? side.TranslatePoint(new Point(), board).X >= main.ActualWidth
                    : side.TranslatePoint(new Point(), board).Y >= main.ActualHeight, "diagnostics web breakpoint");
                Render(view, width, width == 360 ? 1600 : 1200, Path.Combine(output, $"native-system-settings-{width}.png"));
            }
            var storage = Field<Dictionary<string, TextBlock>>(view, "storageValueBlocks");
            Require(storage["database_backend"].Text == "SQLITE", "storage database value uses runtime backend field");
            var search = Field<TextBox>(view, "searchInput");
            search.Text = "absent"; Layout(view, 1240, 1000);
            Require(Desc(view).OfType<TextBlock>().Any(t => t.Text.Contains("没有符合当前")), "search empty state");
            search.Text = "Codex"; Layout(view, 1240, 1000);
            Require(Field<StackPanel>(view, "providerList").Children.OfType<ProviderCard>().Count() == 1, "provider search filters cards");
            search.Text = "ImageGen"; Layout(view, 1240, 1000);
            Require(Field<StackPanel>(view, "providerList").Children.OfType<ProviderCard>().Count() == 1, "catalog model search filters providers by owned models");
            search.Text = "";
            Click(Desc(view).OfType<Button>().Single(b => Equals(b.Content, "＋ 添加供应商"))); Layout(view, 1240, 1000);
            Require(Desc(view).OfType<TextBox>().Any(box => System.Windows.Automation.AutomationProperties.GetName(box) == "供应商名称"),
                "add-provider opens the in-page form");
            Require(!Application.Current.Windows.OfType<Window>().Any(window => window.Title == "添加供应商"),
                "add-provider must not open a dialog");
            Click(Desc(view).OfType<Button>().Single(b => Equals(b.Content, "＋ 添加供应商")));
            Layout(view, 1240, 1000);
            Require(Desc(view).OfType<ModelRow>().Count() == 2, "populated model rows are rendered");
            var imageFilter = Desc(view).OfType<ToggleButton>().Single(b => Equals(b.Content, "图片"));
            imageFilter.IsChecked = true; Click(imageFilter); Layout(view, 1240, 1000);
            Require(Desc(view).OfType<ModelRow>().Count() == 1, "type filter affects connection model rows");
            imageFilter.IsChecked = false; Click(imageFilter); Layout(view, 1240, 1000);
            Click(Desc(view).OfType<Button>().First(b => Equals(b.Content, "＋ 手工添加模型")));
            foreach (int width in new[] { 760, 360 })
            {
                Layout(view, width, 1500);
                foreach (var tiles in Desc(view).OfType<SystemSettingsTiles>()) Fits(tiles);
                foreach (var heading in Desc(view).OfType<PageHeading>()) Fits(heading);
                var manual = Desc(view).OfType<TextBox>().Single(b => Equals(b.Tag, "上游模型 ID"));
                var formScroller = Field<ScrollViewer>(view, "scroller");
                formScroller.ScrollToVerticalOffset(formScroller.VerticalOffset + manual.TranslatePoint(new Point(), view).Y - 260);
                Layout(view, width, 1500);
                Render(view, width, 1500, Path.Combine(output, $"native-system-settings-model-form-{width}.png"));
            }
            var inputs = Field<Dictionary<string, FrameworkElement>>(view, "runtimeInputs");
            ((TextBox)inputs["default_concurrency"]).Text = "5";
            ((TextBox)inputs["job_timeout_seconds"]).Text = "900.5";
            ((TextBox)inputs["ui_poll_interval_seconds"]).Text = "4500";
            var queue = (ComboBox)inputs["queue_mode"]; queue.SelectedItem = queue.Items.OfType<ComboBoxItem>().Single(i => Equals(i.Tag, "LOCAL"));
            var save = Field<Button>(view, "runtimeSave");
            fixture.Gate = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
            Click(save); Click(save);
            await Until(() => fixture.Patches == 1); Require(!save.IsEnabled, "pending save disabled and duplicate guarded");
            fixture.Gate.SetResult(true); await Until(() => !Field<bool>(view, "runtimeSaving"));
            Require(fixture.Payload.Number("default_concurrency") == 5 && fixture.Payload.Number("job_timeout_seconds") == 901
                && fixture.Payload.Text("queue_mode") == "LOCAL" && fixture.Payload.Number("version") == 9
                && fixture.Payload.EnumerateObject().Count() == 8, "runtime editable-only PATCH and number rounding");
            Require(Field<Border>(view, "runtimeNotice").Visibility == Visibility.Visible, "successful save feedback");
            var scroll = Field<ScrollViewer>(view, "scroller"); Layout(view, 1440, 1000); scroll.ScrollToEnd(); Layout(view, 1440, 1000);
            Require(save.TranslatePoint(new Point(), view).Y < 150, "save remains at top after scrolling");
            Render(view, 1440, 1000, Path.Combine(output, "native-system-settings-saved.png"));
            fixture.Gate = null;
            // A05：保存期间的新运行参数不得被提交时的旧响应覆盖——提交 5 后改 7，
            // 响应落地仍显示 7、继续标脏；再次保存携带 7 与新版本。
            fixture.Gate = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
            Click(save); await Until(() => fixture.Patches == 2);
            ((TextBox)Field<Dictionary<string, FrameworkElement>>(view, "runtimeInputs")["default_concurrency"]).Text = "7";
            fixture.Gate.SetResult(true); await Until(() => !Field<bool>(view, "runtimeSaving"));
            var concurrencyAfterInFlight = (TextBox)Field<Dictionary<string, FrameworkElement>>(view, "runtimeInputs")["default_concurrency"];
            Require(concurrencyAfterInFlight.Text == "7", "runtime edits during save are preserved (A05)");
            var notice = Field<Border>(view, "runtimeNotice");
            Require(notice.Visibility == Visibility.Visible && (notice.Child as TextBlock)!.Text.Contains("之后又有新修改"),
                "in-flight edit notice distinguishes the newer edits");
            Require((bool)typeof(SettingsView).GetMethod("RuntimeDraft", BindingFlags.NonPublic | BindingFlags.Instance)!.Invoke(view, null)!,
                "form stays dirty until the newer edits are saved");
            Click(save); await Until(() => !Field<bool>(view, "runtimeSaving"));
            Require(fixture.Patches == 3 && fixture.Payload.Number("default_concurrency") == 7 && fixture.Payload.Number("version") == 11,
                "second save carries the newer value and refreshed version");

            fixture.Conflict = true;
            ((TextBox)Field<Dictionary<string, FrameworkElement>>(view, "runtimeInputs")["default_concurrency"]).Text = "6";
            Click(save); await Until(() => !Field<bool>(view, "runtimeSaving"));
            // A06：409 后本地草稿保留 + 静默同步版本（不重绘表单），下一次保存携带
            // 服务器当前版本而不是循环 409。
            Require(Field<TextBlock>(view, "runtimeError").Text.Contains("最新版本号"), "conflict recovery guidance visible");
            Require(Field<JsonElement>(view, "runtime").Number("version") == 13, "conflict synced the server version");
            Require(((TextBox)Field<Dictionary<string, FrameworkElement>>(view, "runtimeInputs")["default_concurrency"]).Text == "6",
                "conflict preserves local edits");
            Click(save); await Until(() => !Field<bool>(view, "runtimeSaving"));
            Require(fixture.Patches == 5 && fixture.Payload.Number("version") == 13 && fixture.Payload.Number("default_concurrency") == 6,
                "retry after conflict carries the synced version");

            // A09：诊断失败不得沿用上一次的执行器状态。
            fixture.FailDiagnostics = true;
            Click(Field<Button>(view, "recheckButton"));
            await Until(() => Field<StackPanel>(view, "diagnosticsList").Children.OfType<TextBlock>().Any(t => t.Text.Contains("诊断读取失败")));
            Require(Field<TextBlock>(view, "executorLabel").Text == "读取失败", "diagnostics failure clears the executor label (A09)");
            Render(view, 1440, 1000, Path.Combine(output, "native-system-settings-diagnostic-error.png"));
            fixture.FailDiagnostics = false; Click(Field<Button>(view, "recheckButton"));
            await Until(() => Field<StackPanel>(view, "diagnosticsList").Children.OfType<Border>().Any());
            Require(Field<TextBlock>(view, "executorLabel").Text == "LOCAL", "diagnostics retry restores the executor label");
            // A09：运行设置读取失败同样不得沿用旧值。
            fixture.FailRuntime = true;
            await (Task)typeof(SettingsView).GetMethod("LoadRuntimeAsync", BindingFlags.NonPublic | BindingFlags.Instance)!.Invoke(view, null)!;
            Require(Field<TextBlock>(view, "databaseLabel").Text == "读取失败", "runtime read failure clears the storage labels (A09)");
            fixture.FailRuntime = false;
            await (Task)typeof(SettingsView).GetMethod("LoadRuntimeAsync", BindingFlags.NonPublic | BindingFlags.Instance)!.Invoke(view, null)!;
            Require(Field<TextBlock>(view, "databaseLabel").Text == "SQLITE", "runtime retry restores storage labels");

            // A08：连接面板草稿在搜索/筛选/收起前必须确认；拒绝则原样保留。
            var password = Desc(view).OfType<System.Windows.Controls.PasswordBox>().FirstOrDefault();
            if (password != null)
            {
                password.Password = "half-typed-secret";
                view.DiscardDraftsConfirmOverride = _ => Task.FromResult(false);
                Field<TextBox>(view, "searchInput").Text = "Google";
                await Task.Delay(60);
                Require(Field<TextBox>(view, "searchInput").Text == "", "declined search reverts the filter (A08)");
                Require(Desc(view).OfType<System.Windows.Controls.PasswordBox>().Any(p => p.Password == "half-typed-secret"),
                    "declined search keeps the connection draft");
                var cardWithDraft = Desc(view).OfType<ProviderCard>()
                    .First(card => Desc(card).OfType<System.Windows.Controls.PasswordBox>().Any(p => p.Password.Length > 0));
                var header = Desc(cardWithDraft).OfType<Button>().First(b => b.Content is PageHeading);
                var panelsBefore = Desc(view).OfType<ConnectionPanel>().Count();
                Click(header);
                await Task.Delay(60);
                Require(Desc(view).OfType<ConnectionPanel>().Count() == panelsBefore, "declined collapse keeps the connection panels (A08)");
                view.DiscardDraftsConfirmOverride = _ => Task.FromResult(true);
                Field<TextBox>(view, "searchInput").Text = "Google";
                await Task.Delay(60);
                Require(Field<StackPanel>(view, "providerList").Children.OfType<ProviderCard>().Count() == 1, "accepted search filters cards");
                Field<TextBox>(view, "searchInput").Text = "";
                await Task.Delay(60);

                // A07：运行参数/连接草稿的离开保护——拒绝保留，同意弃稿。
                var freshPassword = Desc(view).OfType<System.Windows.Controls.PasswordBox>().First();
                freshPassword.Password = "leave-guard-secret";
                ((TextBox)Field<Dictionary<string, FrameworkElement>>(view, "runtimeInputs")["default_concurrency"]).Text = "4";
                view.LeaveConfirmOverride = _ => Task.FromResult(false);
                Require(!await view.ConfirmLeaveAsync(), "dirty drafts block leaving (A07)");
                Require(Desc(view).OfType<System.Windows.Controls.PasswordBox>().Any(p => p.Password == "leave-guard-secret")
                    && ((TextBox)Field<Dictionary<string, FrameworkElement>>(view, "runtimeInputs")["default_concurrency"]).Text == "4",
                    "declined leave keeps drafts intact");
                view.LeaveConfirmOverride = _ => Task.FromResult(true);
                Require(await view.ConfirmLeaveAsync(), "confirmed leave passes");
                Require(((TextBox)Field<Dictionary<string, FrameworkElement>>(view, "runtimeInputs")["default_concurrency"]).Text == "6"
                    && Desc(view).OfType<System.Windows.Controls.PasswordBox>().All(p => p.Password.Length == 0),
                    "confirmed leave discards drafts: runtime inputs revert and key fields clear (A07)");
            }

            Click(Desc(view).OfType<Button>().Single(b => Equals(b.Content, "用量与成本看板")));
            Require(navigated == "usage", "usage navigation is wired");
            Click(Desc(view).OfType<Button>().Single(b => Equals(b.Content, "返回项目")));
            Require(navigated == "home", "return navigation is wired");
            Console.WriteLine("PASS: system settings layouts, runtime payload/rounding, pending duplicate guard, in-flight edit protection (A05), conflict version sync + retry (A06), leave protection with draft discard (A07), connection-draft guards on search/collapse (A08), independent status labels on failures (A09), provider search, diagnostic retry and navigation.");
            Console.WriteLine("HTTP fixtures and offscreen WPF only. Live backend/provider mutations, native high-DPI windows and animation timing NOT RUN.");
        }
        finally { view.Deactivate(); }
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
        internal int ModelReads, Patches;
        internal bool Conflict, FailDiagnostics, FailRuntime;
        internal TaskCompletionSource<bool>? Gate;
        internal JsonElement Payload;
        private Dictionary<string, object?> runtime = new() {
            ["version"] = 9, ["queue_mode"] = "AUTO", ["job_timeout_seconds"] = 900, ["job_lease_seconds"] = 300,
            ["default_concurrency"] = 2, ["max_auto_repairs"] = 2, ["health_check_interval_seconds"] = 300,
            ["ui_poll_interval_seconds"] = 3000, ["database_backend"] = "SQLITE",
            ["storage_root"] = @"D:\MangaFlow\workspace\generated-content", ["upload_root"] = @"D:\MangaFlow\workspace\uploads" };
        protected override async Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken token)
        {
            string path = request.RequestUri!.AbsolutePath;
            if (request.Method == HttpMethod.Patch)
            {
                Require(path.EndsWith("/settings/runtime"), "unexpected mutation");
                using var doc = JsonDocument.Parse(await request.Content!.ReadAsStringAsync(token));
                Payload = doc.RootElement.Clone(); Patches++;
                if (Gate != null) await Gate.Task.WaitAsync(token);
                if (Conflict)
                {
                    // 一次性冲突 + 另一端已保存（服务端版本前进）：恢复路径的 GET 读到它。
                    Conflict = false;
                    runtime["version"] = Payload.Number("version") + 1;
                    return Response("{\"detail\":\"版本已更新，请刷新\"}", HttpStatusCode.Conflict);
                }
                foreach (var p in Payload.EnumerateObject()) runtime[p.Name] = p.Value.Clone();
                runtime["version"] = Payload.Number("version") + 1;
                return Response(JsonSerializer.Serialize(runtime));
            }
            Require(request.Method == HttpMethod.Get, "no paid calls in UI checks");
            if (path.EndsWith("/settings/runtime"))
            {
                if (FailRuntime) return Response("{\"detail\":\"服务暂时不可用\"}", HttpStatusCode.ServiceUnavailable);
                return Response(JsonSerializer.Serialize(runtime));
            }
            if (path.EndsWith("/settings/diagnostics"))
            {
                if (FailDiagnostics) return Response("{\"detail\":\"诊断服务暂时不可用\"}", HttpStatusCode.ServiceUnavailable);
                return Response(JsonSerializer.Serialize(new { queue = new { actual_executor = "LOCAL" }, checked_at = "2026-09-14T08:30:00+08:00",
                    checks = new[] {
                        new { label = "数据库连接", message = "本地数据库可读写", status = "OK", latency_ms = (int?)2 },
                        new { label = "本地执行器", message = "当前使用本地同步执行器；如需并行任务，请检查 Redis 与 Worker 的运行状态。", status = "WARNING", latency_ms = (int?)null },
                        new { label = "生成内容目录", message = @"D:\MangaFlow\workspace\generated-content", status = "OK", latency_ms = (int?)1 } } }));
            }
            if (path.EndsWith("/providers")) return Response(JsonSerializer.Serialize(new[] {
                Provider("p1", "OpenAI · Codex CLI", true), Provider("p2", "Google · Vertex AI", false),
                // A07/A08：Key 型连接（可录 API Key 的面板）。
                Provider("p3", "KeyVendor", true, "KEY") }));
            if (path.Contains("p3-connection")) return Response("[]");   // KeyVendor 连接：无模型行
            if (path.EndsWith("/models"))
            {
                ModelReads++;
                return Response(JsonSerializer.Serialize(new[] {
                    new { id = "text-1", catalog_id = "text-1", connection_id = "p1-connection", provider = "OpenAI · Codex CLI",
                        display_name = "Codex 文字与视觉理解模型", provider_model_id = "codex-text-model", model_id = "codex-text-model",
                        logical_alias = "codex-text", model_type = "TEXT",
                        enabled = true, display_enabled = true, confidence = "VERIFIED", source = "discovery", operations = new[] { "structured_text", "multimodal_analysis" } },
                    new { id = "image-1", catalog_id = "image-1", connection_id = "p1-connection", provider = "OpenAI · Codex CLI",
                        display_name = "Codex ImageGen", provider_model_id = "codex-imagegen", model_id = "codex-imagegen",
                        logical_alias = "codex-image", model_type = "IMAGE",
                        enabled = true, display_enabled = true, confidence = "MANUAL", source = "manual", operations = new[] { "image_generate", "image_edit" } }
                }));
            }
            throw new Exception("unexpected request: " + path);
        }
        private static object Provider(string id, string name, bool ready, string credentialSource = "CLI_SESSION") => new { id, name, enabled = true, category = "CUSTOM", risk_label = "LOW",
            preset_key = id, description = "用于漫画制作的文字与图像生成连接",
            connections = new[] { new { id = id + "-connection", name = "默认连接", protocol = "OPENAI", base_url = "https://api.example.invalid/v1",
                credential_source = credentialSource, configured = ready, enabled = true, health_state = ready ? "HEALTHY" : "UNCONFIGURED",
                credential_writable = credentialSource == "KEY",
                supports_model_discovery = true, supports_balance = false, model_count = ready ? 2 : 0, key_count = 0, keys = Array.Empty<object>() } } };
        private static HttpResponseMessage Response(string json, HttpStatusCode status = HttpStatusCode.OK) => new(status) { Content = new StringContent(json) };
    }
}
