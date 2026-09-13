using System.IO;
using System.Net;
using System.Net.Http;
using System.Reflection;
using System.Text.Json;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using System.Windows.Threading;
using MangaFlow.Native;
using MangaFlow.Native.Services;
using MangaFlow.Native.Views;

internal static class NativeSceneChecks
{
    internal static void Run(string output)
    {
        Exception? failure = null;
        var thread = new Thread(() =>
        {
            var app = new Application { ShutdownMode = ShutdownMode.OnExplicitShutdown };
            app.Resources.MergedDictionaries.Add(new ResourceDictionary { Source = new Uri("/MangaFlow.Native;component/Theme.xaml", UriKind.Relative) });
            SynchronizationContext.SetSynchronizationContext(new DispatcherSynchronizationContext());
            app.Dispatcher.BeginInvoke(new Action(async () =>
            {
                try { Directory.CreateDirectory(output); await CheckAsync(output); }
                catch (Exception ex) { failure = ex; }
                finally { app.Shutdown(); }
            })); app.Run();
        }); thread.SetApartmentState(ApartmentState.STA); thread.Start(); thread.Join();
        if (failure != null) throw new Exception("Scene workspace checks failed", failure);
    }
    private static async Task CheckAsync(string output)
    {
        var fixture = new Fixture(); using var api = new ApiClient("http://127.0.0.1:12345", fixture);
        var view = new AssetsView();
        view.Activate(new WorkspaceContext { Api = api, State = new(), Window = null!, Project = new ProjectItem("scene-project", "场景页测试", "", 0, 0), NavigateSection = (_, _) => Task.CompletedTask, OpenDashboard = () => Task.CompletedTask });
        await view.RefreshAsync(); await view.SwitchAsync(AssetsView.Scenes); await Task.Delay(25);
        view.Measure(new Size(1100, 1100)); view.Arrange(new Rect(0, 0, 1100, 1100)); view.UpdateLayout();
        var pane = NativeParityChecks.Descendants(view).OfType<SceneWorkspace>().Single(); await pane.InitialLoad;
        Require(Field<ListBox>(pane, "list").Items.Count == 2, "scene list comes from HTTP results");
        foreach (var width in new[] { 1100, 650 })
        {
            Render(view, width, 1100, 96, Path.Combine(output, $"native-scenes-{width}.png"));
            Require(Field<StackPanel>(pane, "detail").ActualWidth > 580, "responsive details must retain usable width");
        }
        Render(view, 1100, 1100, 120, Path.Combine(output, "native-scenes-125pct.png"));
        Require(NativeParityChecks.Descendants(pane).OfType<TextBlock>().Any(t => t.Text.Contains("室内")), "interior reads structured.interior");
        var search = Field<TextBox>(pane, "search"); search.Text = "河岸";
        Require(Field<ListBox>(pane, "list").Items.Count == 1, "search includes structured place"); search.Clear();
        var archived = Field<CheckBox>(pane, "archived"); archived.IsChecked = true;
        await pane.ReloadAsync(); Require(fixture.Queries.Last().Contains("include_deleted=true"), "archive filter reaches backend");
        var status = Field<ComboBox>(pane, "status"); status.SelectedIndex = 5; await pane.ReloadAsync(); Require(fixture.Queries.Last().Contains("status=CANONICAL"), "status filter reaches backend"); status.SelectedIndex = 0;
        await pane.ChangeAsync("projects/scene-project/scene-assets/s1", HttpMethod.Patch, new { version = 7, status = "NEEDS_CONFIRMATION" });
        Require(fixture.Writes.Last().Body.Number("version") == 7, "status mutation uses server version");
        Require(await pane.CountBindingsAsync("s1") == 1, "archive counts persisted script references");
        fixture.FailScript = true; Require(await pane.CountBindingsAsync("s1") == null, "failed binding lookup never reports zero"); fixture.FailScript = false;
        var scene = JsonSerializer.Deserialize<JsonElement>(Fixture.Row);
        object? captured = null;
        var editor = new SceneEditor(scene, false, payload => { captured = payload; return Task.CompletedTask; });
        var fields = Field<Dictionary<string, TextBox>>(editor, "inputs"); fields["name"].Text = "京都老宅 · 修订"; fields["fixed_props"].Text = "佛龛、木柱，灯笼";
        fields["spatial_relations"].Text = "入口 > 正对 > 佛龛";
        Require(await editor.SaveAsync(), "scene editor saves structured fields");
        var payload = JsonSerializer.SerializeToElement(captured);
        Require(payload.Number("version") == 7 && payload.Element("structured").Array("fixed_props").Count == 3 && payload.Element("structured").Array("spatial_relations")[0].Text("to") == "佛龛", "version and structured arrays match API");
        Require(!payload.TryGetProperty("location_hint", out _), "editing preserves read-only source location");
        Render((FrameworkElement)editor.Content, 676, 760, 96, Path.Combine(output, "native-scene-editor.png")); editor.Close();
        var failed = new SceneEditor(scene, false, _ => throw new InvalidOperationException("版本已变化，请刷新后重试"));
        Field<Dictionary<string, TextBox>>(failed, "inputs")["name"].Text = "冲突后保留的输入";
        Require(!await failed.SaveAsync() && Field<TextBlock>(failed, "error").Text.Contains("请刷新") && Field<Dictionary<string, TextBox>>(failed, "inputs")["name"].Text == "冲突后保留的输入", "failure remains visible inside modal and preserves input"); failed.Close();
        var variant = new SceneEditor(null, true, _ => Task.CompletedTask); var variantFields = Field<Dictionary<string, TextBox>>(variant, "inputs"); variantFields["name"].Text = "雨夜";
        var variantPayload = JsonSerializer.SerializeToElement(variant.Payload());
        Require(!variantPayload.Element("structured_overrides").TryGetProperty("place", out _) && !variantPayload.Element("structured_overrides").TryGetProperty("fixed_props", out _), "variant cannot override permanent spatial identity");
        Render((FrameworkElement)variant.Content, 526, 600, 96, Path.Combine(output, "native-scene-variant-editor.png")); variant.Close();
        var pendingSave = new TaskCompletionSource(); int submissions = 0;
        var dedup = new SceneEditor(scene, false, async _ => { submissions++; await pendingSave.Task; });
        var first = dedup.SaveAsync(); Require(!await dedup.SaveAsync() && submissions == 1, "double submit cannot duplicate saves"); pendingSave.SetResult(); Require(await first, "first save finishes"); dedup.Close();
        var pending = new TaskCompletionSource<HttpResponseMessage>(); fixture.Pending = pending;
        var delayed = pane.ReloadAsync(); await view.SwitchAsync(AssetsView.References);
        pending.SetResult(Json("[]")); await delayed;
        Require(Field<ListBox>(pane, "list").Items.Count > 0, "detached scene pane ignores late response");
        view.Deactivate();
        using var iconStream = Application.GetResourceStream(new Uri("/MangaFlow.Native;component/Assets/App.ico", UriKind.Relative))!.Stream;
        var icon = new IconBitmapDecoder(iconStream, BitmapCreateOptions.PreservePixelFormat, BitmapCacheOption.OnLoad);
        Require(icon.Frames.Count == 9 && icon.Frames.Any(f => f.PixelWidth == 256), "application icon contains all nine DPI sizes");
        Console.WriteLine("PASS: scene responsive layouts, structured/interior display, filters, versioned mutations, modal errors/dedup, variant allowlist, detached response and 9-frame application icon");
        Console.WriteLine("NOT RUN: real WPF mouse interaction and production backend; HTTP fixture used for these UI checks");
    }
    private static T Field<T>(object value, string name) => (T)value.GetType().GetField(name, BindingFlags.Instance | BindingFlags.NonPublic)!.GetValue(value)!;
    private static void Require(bool value, string message) { if (!value) throw new Exception(message); }
    private static void Render(FrameworkElement element, int width, int height, int dpi, string path)
    {
        element.Measure(new Size(width, height)); element.Arrange(new Rect(0, 0, width, height)); element.UpdateLayout();
        var bitmap = new RenderTargetBitmap(width * dpi / 96, height * dpi / 96, dpi, dpi, PixelFormats.Pbgra32);
        var paper = new DrawingVisual(); using (var drawing = paper.RenderOpen()) drawing.DrawRectangle((Brush)Application.Current.FindResource("Paper"), null, new Rect(0, 0, width, height)); bitmap.Render(paper); bitmap.Render(element);
        var encoder = new PngBitmapEncoder(); encoder.Frames.Add(BitmapFrame.Create(bitmap)); using var file = File.Create(path); encoder.Save(file);
    }
    private static HttpResponseMessage Json(string text) => new(HttpStatusCode.OK) { Content = new StringContent(text) };
    private sealed class Fixture : HttpMessageHandler
    {
        internal const string Row = """{"id":"s1","name":"京都老宅佛堂","description":"老木结构，佛龛位于房间尽头","location_hint":"京都，爸爸的灵牌前","status":"NEEDS_CONFIRMATION","version":7,"structured":{"place":"京都老宅","interior":true,"weather":"小雨","fixed_props":["佛龛","木柱"],"palette":{"dominant":["#e3d7c2"],"mood":"安静、克制"}},"references":[{"asset_id":"a1","role":"main","is_canonical":true}],"variants":[{"id":"v1","name":"雨夜","version":2,"is_canonical":true,"structured_overrides":{"time_of_day":"night","weather":"小雨","lighting":"室内暖光"},"references":[]}]}""";
        internal readonly List<string> Queries = [];
        internal readonly List<(string Path, JsonElement Body)> Writes = [];
        internal TaskCompletionSource<HttpResponseMessage>? Pending;
        internal bool FailScript;
        protected override async Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken token)
        {
            var path = request.RequestUri!.AbsolutePath;
            if (path.EndsWith("/chapters")) return Json("[{\"id\":\"chapter1\"}]");
            if (path.EndsWith("/script")) return FailScript ? new HttpResponseMessage(HttpStatusCode.ServiceUnavailable) : Json("{\"scenes\":[{\"scene_asset_id\":\"s1\"},{\"scene_asset_id\":\"other\"}]}");
            if (request.Method != HttpMethod.Get) { Writes.Add((path, JsonSerializer.Deserialize<JsonElement>(request.Content == null ? "{}" : await request.Content.ReadAsStringAsync(token)))); return Json(Row); }
            if (path.EndsWith("/scene-assets"))
            {
                Queries.Add(request.RequestUri.Query); if (Pending != null) { var p = Pending; Pending = null; return await p.Task; }
                return Json("[" + Row + """,{"id":"s2","name":"鸭川步道","status":"UPLOADED","version":1,"structured":{"place":"河岸","interior":false},"references":[],"variants":[]}]""");
            }
            return Json("[]");
        }
    }
}
