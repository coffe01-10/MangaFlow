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
using MangaFlow.Native;
using MangaFlow.Native.Controls;
using MangaFlow.Native.Services;
using MangaFlow.Native.Views;

internal static class NativeCharacterPageChecks
{
    public static async Task Run(string output)
    {
        var fake = new Fixture(); using var api = new ApiClient("http://127.0.0.1:12345", fake);
        var view = new AssetsView();
        WorkspaceContext Context(string id) => new() { Api = api, State = new(), Window = null!, Project = new ProjectItem(id, "人物资产测试", "", 0, 0), NavigateSection = (_, _) => Task.CompletedTask, OpenDashboard = () => Task.CompletedTask };
        view.Activate(Context("characters-fixture"));
        try
        {
            await view.RefreshAsync(); Layout(view, 1100, 900);
            Require(view.SelectedCharacter == null && view.SelectedImageModel == "", "opening characters must not silently select a character or model");
            Require(view.characters[0].ReferenceCount == 1 && view.characters[0].LockedReferences == 2 && view.characters[0].LockedFeatures == "黑发，泪痣", "character wire arrays/counts lost");
            Require(view.assets[0].FileSize == 1300000, "asset byte_size was ignored");
            var pane = Descendants(view).OfType<CharactersPane>().Single();
            var name = Field<TextBox>(pane, "nameInput"); var alias = Field<TextBox>(pane, "aliasInput");
            foreach (var width in new[] { 650, 1100 })
            {
                Layout(view, width, 900);
                var a = name.TranslatePoint(new Point(), view); var b = alias.TranslatePoint(new Point(), view);
                Require(name.ActualWidth > (width == 650 ? 220 : 420) && a.X + name.ActualWidth <= b.X, "character form is fixed width or overlapping");
                Render(view, width, 900, 96, Path.Combine(output, $"native-characters-restored-{width}.png"));
            }
            Render(view, 1100, 900, 120, Path.Combine(output, "native-characters-text-125pct.png"));
            Require(TextOptions.GetTextFormattingMode(view) == TextFormattingMode.Display && TextOptions.GetTextRenderingMode(view) == TextRenderingMode.ClearType && view.UseLayoutRounding && view.SnapsToDevicePixels, "page did not inherit sharp native text settings");
            var executable = Path.Combine(Path.GetDirectoryName(typeof(AssetsView).Assembly.Location)!, "MangaFlow.Native.exe");
            Require(System.Text.Encoding.UTF8.GetString(File.ReadAllBytes(executable)).Contains("PerMonitorV2"), "compiled native executable omitted DPI awareness manifest");
            var preview = new AssetsView(); preview.Activate(Context("character-preview")); await preview.RefreshAsync();
            var shell = new MainWindow("", Path.Combine(output, "unused-character-shell"));
            ((WorkspaceState)shell.DataContext).CurrentProject = Context("characters-fixture").Project;
            typeof(MainWindow).GetField("page", BindingFlags.Instance | BindingFlags.NonPublic)!.SetValue(shell, "assets");
            typeof(MainWindow).GetMethod("ApplySidebar", BindingFlags.Instance | BindingFlags.NonPublic)!.Invoke(shell, null);
            ((TextBlock)shell.FindName("TopTitle")).Text = "人物资产测试";
            ((ListBox)shell.FindName("ProjectSections")).SelectedItem = ProjectPages.Get(ProjectPageId.Assets);
            var host = (ContentControl)shell.FindName("ContentHost"); host.Content = preview;
            Render((FrameworkElement)shell.Content, 1320, 900, 96, Path.Combine(output, "native-characters-restored-shell.png")); host.Content = null; preview.Deactivate();
            Layout(view, 1100, 900);
            var character = Descendants(pane).OfType<ToggleButton>().Single(t => Equals(t.Tag, "c1")); Click(character); Layout(view, 1100, 900);
            Require(view.SelectedCharacter?.Id == "c1", "character selection not retained");
            var locked = Descendants(pane).OfType<TextBox>().Single(t => t.Text == "黑发，泪痣"); locked.Text = "黑发，泪痣，瘦高";
            Click(Button(pane, "保存角色规范")); await Task.Delay(15);
            using (var body = JsonDocument.Parse(fake.LastCharacterPatch)) Require(body.RootElement.GetProperty("locked_features").ValueKind == JsonValueKind.Array && body.RootElement.GetProperty("locked_features").GetArrayLength() == 3, "character save must send arrays, not JSON-looking strings");
            pane = Descendants(view).OfType<CharactersPane>().Single(); name = Field<TextBox>(pane, "nameInput");
            var create = Field<Button>(pane, "addButton"); name.Text = "新角色";
            var pendingCreate = fake.PendingCreate = new TaskCompletionSource<HttpResponseMessage>(); Click(create); Click(create);
            Require(fake.CreateCount == 1 && !name.IsEnabled, "create character duplicated or editable while pending");
            pendingCreate.SetResult(Json("{\"id\":\"new-character\"}")); await Task.Delay(15); Layout(view, 1100, 900);
            Descendants(view).OfType<CharacterPackagesWorkspace>().Single().BringIntoView(); Layout(view, 1100, 900);
            var waitStart = DateTime.UtcNow;
            while (!Descendants(view).OfType<CharacterPackagePane>().Any() && DateTime.UtcNow - waitStart < TimeSpan.FromSeconds(3)) { await Task.Delay(10); Layout(view, 1100, 900); }
            Require(Descendants(view).OfType<CharacterPackagePane>().Any(), "Package missing: " + string.Join(" | ", Descendants(view).OfType<TextBlock>().Select(t => t.Text)));
            var package = Descendants(view).OfType<CharacterPackagePane>().Single();
            Require(Field<TextBox>(package, "age").Text == "17 岁", "draft editor read stale frozen snapshot instead of package working spec");
            Require(Descendants(package).OfType<ProgressBar>().Single().Value == 15, "completeness must use server score");
            var packagePreview = new CharacterPackagePane(view, view.characters[0]);
            await Task.Delay(10); Render(packagePreview, 800, 1600, 96, Path.Combine(output, "native-character-package-restored.png"));
            using (var diff = JsonDocument.Parse("{\"changed\":[{\"field\":\"age_appearance\",\"base_value\":\"17 岁\",\"target_value\":\"18 岁\"}]}")) Require(package.DescribeChanges("identity_spec", diff.RootElement) == "修改 · 年龄段外观：17 岁 → 18 岁", "history comparison must show readable field changes");
            var picker = Descendants(package).OfType<ComboBox>().Single(c => System.Windows.Automation.AutomationProperties.GetName(c) == "绑定已有素材 封面图");
            picker.SelectedIndex = 1; await Task.Delay(15);
            Require(fake.Mutations.Any(m => m.Method == "PUT" && m.Path.EndsWith("/versions/v1/cover")), "cover must use the PUT cover endpoint");
            picker = Descendants(package).OfType<ComboBox>().Single(c => System.Windows.Automation.AutomationProperties.GetName(c) == "绑定已有素材 正面（主视）");
            picker.SelectedIndex = 1; await Task.Delay(15);
            Require(fake.Mutations.Any(m => m.Path.EndsWith("/versions/v1/references") && m.Body.Contains("front") && m.Body.Contains("version")), "matrix bind omitted role/version");
            Click(Button(package, "保存草稿规格")); await Task.Delay(15);
            Require(fake.Mutations.Any(m => m.Method == "PATCH" && m.Path.EndsWith("/c1/package") && m.Body.Contains("identity_spec")), "package spec save contract changed");
            var references = Descendants(view).OfType<CharacterReferencesPane>().Single();
            Render(new CharacterReferencesPane(view), 1100, 700, 96, Path.Combine(output, "native-character-references-restored.png"));
            Click(Button(references, "解除与 樱 的绑定")); await Task.Delay(15);
            Require(fake.Mutations.Any(m => m.Method == "DELETE" && m.Path.EndsWith("/character-references/ref1")), "reference unbind targets wrong endpoint");
            var held = fake.PendingCharacters = new TaskCompletionSource<HttpResponseMessage>(); var loading = view.RefreshAsync();
            view.Deactivate(); view.Activate(Context("empty-project")); await view.RefreshAsync();
            held.SetResult(Json(Fixture.Characters)); await loading;
            Require(view.characters.Count == 0 && view.SelectedCharacter == null, "old character response leaked into another project");
        }
        finally { view.Deactivate(); }
        Console.WriteLine("PASS: character page layout/text, explicit selection, real wire arrays, dedup creation, package cover/matrix/spec, reference unbind and project isolation");
    }
    private static IEnumerable<DependencyObject> Descendants(DependencyObject root) => NativeParityChecks.Descendants(root);
    private static T Field<T>(object value, string name) => (T)value.GetType().GetField(name, BindingFlags.Instance | BindingFlags.NonPublic)!.GetValue(value)!;
    private static Button Button(DependencyObject root, string content) => Descendants(root).OfType<Button>().First(b => Equals(b.Content, content));
    private static void Click(ButtonBase button) => button.RaiseEvent(new RoutedEventArgs(ButtonBase.ClickEvent));
    private static void Require(bool value, string message) { if (!value) throw new Exception(message); }
    private static void Layout(FrameworkElement view, int width, int height) { view.Measure(new Size(width, height)); view.Arrange(new Rect(0, 0, width, height)); view.UpdateLayout(); }
    private static void Render(FrameworkElement view, int width, int height, int dpi, string path)
    {
        Layout(view, width, height);
        var bitmap = new RenderTargetBitmap(width * dpi / 96, height * dpi / 96, dpi, dpi, PixelFormats.Pbgra32);
        var paper = new DrawingVisual(); using (var drawing = paper.RenderOpen()) drawing.DrawRectangle(AssetPageUi.Brush("Paper"), null, new Rect(0, 0, width, height)); bitmap.Render(paper); bitmap.Render(view);
        var encoder = new PngBitmapEncoder(); encoder.Frames.Add(BitmapFrame.Create(bitmap)); using var file = File.Create(path); encoder.Save(file);
    }
    private static HttpResponseMessage Json(string value) => new(HttpStatusCode.OK) { Content = new StringContent(value) };
    private sealed class Fixture : HttpMessageHandler
    {
        public const string Characters = """
          [{"id":"c1","primary_name":"樱","aliases":["妹妹"],"version":3,"locked_features":["黑发","泪痣"],"forbidden_changes":["瞳色"],"references":[{"id":"ref1","asset_id":"a1"}]},
           {"id":"c2","primary_name":"我","aliases":["哥哥"],"locked_features":[],"forbidden_changes":[],"references":[]}]
          """;
        private const string Package = """
          {"id":"pkg1","character_id":"c1","status":"ACTIVE","version":5,"identity_spec":{"age_appearance":"17 岁"},"visual_spec":{},"negative_constraints":[],
           "versions":[{"id":"v1","status":"DRAFT","version_number":1,"version":2,"references":[],"outfits":[],"spec_snapshot":{},"completeness":{"score":15,"missing":[{"message":"缺少正面参考","suggestion":"上传或绑定正面图片"}]}}]}
          """;
        public readonly List<(string Method, string Path, string Body)> Mutations = [];
        public int CreateCount; public string LastCharacterPatch = "";
        public TaskCompletionSource<HttpResponseMessage>? PendingCreate, PendingCharacters;
        protected override async Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken token)
        {
            var path = request.RequestUri!.AbsolutePath;
            if (request.Method != HttpMethod.Get)
            {
                var body = request.Content == null ? "" : await request.Content.ReadAsStringAsync(token); Mutations.Add((request.Method.Method, path, body));
                if (path.EndsWith("/characters")) { CreateCount++; return await PendingCreate!.Task; }
                if (path.EndsWith("/characters/c1")) LastCharacterPatch = body;
                return Json("{}");
            }
            if (path.Contains("empty-project")) return Json("[]");
            if (path.EndsWith("/characters")) { if (PendingCharacters is { } pending) { PendingCharacters = null; return await pending.Task; } return Json(Characters); }
            if (path.EndsWith("/character-packages")) return Json("[{\"id\":\"pkg1\",\"character_id\":\"c1\",\"status\":\"ACTIVE\"}]");
            if (path.EndsWith("/package")) return Json(Package);
            if (path.EndsWith("/assets")) return Json("[{\"id\":\"a1\",\"kind\":\"CHARACTER_REFERENCE\",\"original_name\":\"樱（冬装）.png\",\"byte_size\":1300000,\"status\":\"GENERATED\"}]");
            if (path.EndsWith("/models")) return Json("""
              [{"model_type":"IMAGE","enabled":true,"operations":["image_edit"],"logical_alias":"model-a","display_name":"Codex CLI ImageGen","provider":"Codex CLI","model_id":"codex-imagegen"},
               {"model_type":"IMAGE","enabled":true,"operations":["image_edit"],"logical_alias":"model-b","display_name":"Google: Nano Banana 2","provider":"OpenRouter","model_id":"google/gemini-3.1-flash-image"}]
              """);
            return Json("[]");
        }
    }
}
