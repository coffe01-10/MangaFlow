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
using MangaFlow.Native.Services;
using MangaFlow.Native.Views;

internal static class NativeReferenceChecks
{
    internal static void Run(string output)
    {
        Exception? failure = null;
        var thread = new Thread(() =>
        {
            var app = new Application { ShutdownMode = ShutdownMode.OnExplicitShutdown };
            app.Resources.MergedDictionaries.Add(new ResourceDictionary { Source = new Uri("/MangaFlow.Native;component/Theme.xaml", UriKind.Relative) });
            SynchronizationContext.SetSynchronizationContext(new DispatcherSynchronizationContext());
            app.Dispatcher.BeginInvoke(new Action(async () => { try { Directory.CreateDirectory(output); await Checks(output); } catch (Exception e) { failure = e; } finally { app.Shutdown(); } })); app.Run();
        });
        thread.SetApartmentState(ApartmentState.STA); thread.Start(); thread.Join();
        if (failure != null) throw new Exception("Reference workspace checks failed", failure);
    }
    private static async Task Checks(string output)
    {
        var prefs = Path.Combine(output, "reference-test-prefs.json");
        var previous = (string)typeof(KeyValueStore).GetField("path", BindingFlags.Static | BindingFlags.NonPublic)!.GetValue(null)!;
        KeyValueStore.UseLocation(prefs);
        var fixture = new Fixture(); using var api = new ApiClient("http://127.0.0.1:12345", fixture); var view = new AssetsView();
        var upload = Path.Combine(output, "upload-fixture.png");
        try
        {
            view.Activate(new WorkspaceContext { Api = api, Cache = new(), State = new(), Window = null!, Project = new ProjectItem("p", "参考素材验收", "", 0, 0), NavigateSection = (_, _) => Task.CompletedTask, OpenDashboard = () => Task.CompletedTask });
            await view.RefreshAsync(); view.SelectedCharacter = view.characters[0]; await view.SwitchAsync(AssetsView.References);
            var pane = Field<Grid>(view, "host").Children.OfType<ReferencesPane>().Single(); await pane.ReloadStyleLinks();
            var cards = Field<Dictionary<string, ReferenceAssetCard>>(pane, "cards");
            var original = cards["a1"]; pane.Confirm = _ => true;
            Render(view, 1100, 1500, Path.Combine(output, "native-references-1100.png"));
            Require(cards["a1"].ActualWidth > 450 && cards["a1"].ActualWidth < 560, "wide viewport has two fluid columns");
            Render(view, 650, 1500, Path.Combine(output, "native-references-650.png"));
            Require(cards["a1"].ActualWidth > 580, "narrow viewport uses a single column");
            var rename = NativeParityChecks.Descendants(original).OfType<Button>().Single(b => Equals(b.ToolTip, "修改素材名称")); Click(rename);
            Field<TextBox>(original, "nameInput").Text = "修改后的角色原图.png";
            fixture.Reject = true; await original.Rename();
            Require(Field<TextBox>(original, "nameInput").Text == "修改后的角色原图.png" && Field<bool>(original, "editing"), "failed rename retains text and editor");
            fixture.Reject = false; await original.Rename();
            Require(original.Asset.Name == "修改后的角色原图.png" && ReferenceEquals(original, cards["a1"]), "successful rename adopts server data without replacing card");
            fixture.FailReads = true; await view.RefreshAsync();
            Require(ReferenceEquals(pane, Field<Grid>(view, "host").Children[0]) && Field<TextBlock>(pane, "error").Text.Contains("已保留"), "refresh failure preserves the visible pane with a retry");
            fixture.FailReads = false; await view.RefreshAsync();
            pane.Confirm = _ => false; int before = fixture.Writes;
            await original.Reclassify("SCENE_REFERENCE"); await original.Delete(); Require(fixture.Writes == before, "cancelled reclassification/deletion does not call API");
            pane.Confirm = _ => true;
            fixture.Hold = new TaskCompletionSource<bool>(); var first = original.Bind(); await Task.Delay(5); await original.Bind();
            Require(fixture.BindPosts == 1, "double click cannot duplicate binding");
            fixture.Hold.SetResult(true); await first; fixture.Hold = null;
            Require(view.SelectedCharacter!.References.Count == 1, "binding response is refreshed from server");
            await original.Bind(); Require(fixture.Unbinds == 1 && view.SelectedCharacter!.References.Count == 0, "unbind uses reference id and accepts 204");
            // Draft selections survive refresh and move to the actual editors without a POST.
            before = fixture.Writes; pane.ToggleDraft(view.assets.Single(a => a.Id == "outfit"));
            await view.RefreshAsync(); Require(ReferenceEquals(pane, Field<Grid>(view, "host").Children[0]) && view.PendingOutfitReferences.Contains("outfit"), "refresh preserves intake and selected draft references");
            await view.SwitchAsync(AssetsView.Outfits);
            var wardrobe = Field<Grid>(view, "host").Children.OfType<OutfitWorkspace>().Single();
            Require(Field<HashSet<string>>(wardrobe, "selected").Contains("outfit") && wardrobe.DraftDirty && fixture.Writes == before, "outfit reference reaches unsaved editor without a fake binding");
            view.OutfitDraftPrompt = _ => Task.FromResult("discard"); await view.SwitchAsync(AssetsView.References);
            pane = Field<Grid>(view, "host").Children.OfType<ReferencesPane>().Single(); pane.ToggleDraft(view.assets.Single(a => a.Id == "style")); await view.SwitchAsync(AssetsView.Style); await Task.Delay(20);
            var style = Field<Grid>(view, "host").Children.OfType<StyleWorkspace>().Single();
            Require(Field<HashSet<string>>(style, "selected").Contains("style"), "style reference reaches analysis draft");
            await view.SwitchAsync(AssetsView.References); pane = Field<Grid>(view, "host").Children.OfType<ReferencesPane>().Single(); pane.Confirm = _ => true; cards = Field<Dictionary<string, ReferenceAssetCard>>(pane, "cards");
            await cards["a1"].Reclassify("SCENE_REFERENCE"); Require(cards["a1"].Asset.Kind == "SCENE_REFERENCE", "classification moves card to server-reported purpose");
            await cards["a1"].Delete(); Require(!cards.ContainsKey("a1"), "204 deletion removes card after refresh");
            File.WriteAllBytes(upload, Convert.FromBase64String("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j5mQAAAAASUVORK5CYII="));
            view.ReferenceKind = "STYLE_REFERENCE"; await Upload(pane, upload);
            Require(fixture.UploadKind == "STYLE_REFERENCE" && view.PendingStyleReferences.Contains("uploaded"), "explicit upload purpose adds returned id to corresponding draft");
            int binds = fixture.BindPosts; fixture.WrongKind = true; view.ReferenceKind = "CHARACTER_REFERENCE"; await Upload(pane, upload);
            Require(fixture.BindPosts == binds && Field<TextBlock>(pane, "error").Text.Contains("其他参考用途"), "wrong-purpose duplicate upload never creates a character binding");
            fixture.WrongKind = false; fixture.RejectBinding = true; await Upload(pane, upload);
            Require(view.assets.Any(a => a.Id == "uploaded") && Field<TextBlock>(pane, "error").Text.Contains("图片已上传，但角色绑定失败"), "partial upload success remains visible with an actionable binding error");
            fixture.RejectBinding = false; fixture.HoldUpload = new TaskCompletionSource<bool>(); var pendingUpload = Upload(pane, upload); await Task.Delay(5); binds = fixture.BindPosts;
            await view.SwitchAsync(AssetsView.Scenes); fixture.HoldUpload.SetResult(true); await pendingUpload;
            Require(fixture.BindPosts == binds, "detached upload cannot run a follow-up mutation against another pane");
            Console.WriteLine("PASS: reference responsive layout, rename failure retention, confirmation, binding/unbinding, duplicate guard, refresh identity, real editor draft handoff, classification, deletion, upload purpose, partial success, detached response.");
            Console.WriteLine("WPF offscreen + HTTP fixtures; actual backend checked separately. Real-window/DPI interaction NOT RUN.");
        }
        finally { view.Deactivate(); KeyValueStore.UseLocation(previous); File.Delete(prefs); File.Delete(prefs + ".tmp"); File.Delete(upload); }
    }
    private static Task Upload(ReferencesPane pane, string path) => (Task)pane.GetType().GetMethod("Upload", BindingFlags.NonPublic | BindingFlags.Instance)!.Invoke(pane, [path])!;
    private static T Field<T>(object value, string name) => (T)value.GetType().GetField(name, BindingFlags.NonPublic | BindingFlags.Instance)!.GetValue(value)!;
    private static void Click(ButtonBase b) => b.RaiseEvent(new RoutedEventArgs(ButtonBase.ClickEvent));
    private static void Require(bool ok, string message) { if (!ok) throw new Exception(message); }
    private static void Render(FrameworkElement e, int w, int h, string path) { e.Measure(new Size(w, h)); e.Arrange(new Rect(0, 0, w, h)); e.UpdateLayout(); var bitmap = new RenderTargetBitmap(w, h, 96, 96, PixelFormats.Pbgra32); bitmap.Render(e); var encoder = new PngBitmapEncoder(); encoder.Frames.Add(BitmapFrame.Create(bitmap)); using var f = File.Create(path); encoder.Save(f); }
    private static HttpResponseMessage Json(object value) => new(HttpStatusCode.OK) { Content = new StringContent(JsonSerializer.Serialize(value)) };
    private sealed class Fixture : HttpMessageHandler
    {
        internal int Writes, BindPosts, Unbinds;
        internal bool Reject, WrongKind, RejectBinding, FailReads;
        internal string UploadKind = "";
        internal TaskCompletionSource<bool>? Hold, HoldUpload;
        private string? bound;
        private readonly Dictionary<string, (string Kind, string Name)> assets = new() { ["a1"] = ("CHARACTER_REFERENCE", "秋（日常）.png"), ["a2"] = ("CHARACTER_REFERENCE", "妈妈（第一次出场）.png"), ["outfit"] = ("OUTFIT_REFERENCE", "雨夜外套.png"), ["style"] = ("STYLE_REFERENCE", "京都暖灰色板.png"), ["scene"] = ("SCENE_REFERENCE", "京都老宅.png") };
        private object Asset(string id) => new { id, kind = assets[id].Kind, display_name = assets[id].Name, original_name = assets[id].Name, byte_size = 1300000, status = "UPLOADED" };
        protected override async Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken token)
        {
            var path = request.RequestUri!.AbsolutePath;
            if (request.Method == HttpMethod.Get)
            {
                if (FailReads) return new(HttpStatusCode.ServiceUnavailable) { Content = new StringContent("{\"detail\":\"连接暂不可用\"}") };
                if (path.EndsWith("/assets")) return Json(assets.Keys.Select(Asset).ToArray());
                if (path.EndsWith("/characters")) return Json(new[] { new { id = "c1", primary_name = "我", aliases = new[] { "秋" }, references = bound == null ? Array.Empty<object>() : new object[] { new { id = "binding", asset_id = bound } } } });
                if (path.EndsWith("/styles")) return Json(new[] { new { id = "style1", name = "京都暖灰", profile = new { reference_asset_ids = new[] { "style" } } } });
                if (path.EndsWith("/projects/p")) return Json(new { id = "p" });
                return Json(Array.Empty<object>());
            }
            Writes++;
            if (path.EndsWith("/assets/upload"))
            {
                var parts = (MultipartFormDataContent)request.Content!; UploadKind = await parts.Single(p => p.Headers.ContentDisposition?.Name?.Trim('"') == "kind").ReadAsStringAsync(token);
                if (HoldUpload != null) await HoldUpload.Task;
                assets["uploaded"] = (WrongKind ? "SCENE_REFERENCE" : UploadKind, "uploaded.png"); return Json(Asset("uploaded"));
            }
            if (Reject) return new HttpResponseMessage(HttpStatusCode.Conflict) { Content = new StringContent("{\"detail\":\"名称保存失败\"}") };
            if (path.EndsWith("/references"))
            {
                BindPosts++; if (Hold != null) await Hold.Task;
                if (RejectBinding) return new HttpResponseMessage(HttpStatusCode.Conflict) { Content = new StringContent("{\"detail\":\"绑定失败\"}") };
                var body = JsonSerializer.Deserialize<JsonElement>(await request.Content!.ReadAsStringAsync(token)); bound = body.Text("asset_id"); return Json(new { id = "binding" });
            }
            if (path.EndsWith("/character-references/binding")) { Unbinds++; bound = null; return new(HttpStatusCode.NoContent); }
            var id = path.Split('/').Last();
            if (request.Method == HttpMethod.Delete) { assets.Remove(id); return new(HttpStatusCode.NoContent); }
            var patch = JsonSerializer.Deserialize<JsonElement>(await request.Content!.ReadAsStringAsync(token));
            assets[id] = (patch.Text("kind", assets[id].Kind), patch.Text("display_name", assets[id].Name)); return Json(Asset(id));
        }
    }
}
