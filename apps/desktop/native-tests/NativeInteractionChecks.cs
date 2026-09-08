using System.Net;
using System.Net.Http;
using System.Reflection;
using System.Text.Json;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Controls.Primitives;
using System.Windows.Threading;
using MangaFlow.Native;
using MangaFlow.Native.Services;
using MangaFlow.Native.Views;

internal static class NativeInteractionChecks
{
    public static void Run()
    {
        Exception? failure = null;
        var frame = new DispatcherFrame();
        var timeout = new DispatcherTimer { Interval = TimeSpan.FromSeconds(20) };
        timeout.Tick += (_, _) => { failure = new TimeoutException("Native interaction checks timed out"); frame.Continue = false; };
        timeout.Start();
        Dispatcher.CurrentDispatcher.BeginInvoke(new Action(async () =>
        {
            try { await Creation(); await Generation(); await LocalEdit(); }
            catch (Exception error) { failure = error; }
            finally { frame.Continue = false; }
        }));
        Dispatcher.PushFrame(frame);
        timeout.Stop();
        if (failure != null) throw new Exception("Native interaction regression", failure);
        Console.WriteLine("PASS: native creation preserves card choices; generation posts reference overrides once; local-edit preview/accept/discard preserve source and command contracts");
    }

    private static WorkspaceContext Context(ApiClient api) => new()
    {
        Api = api, Cache = new ApiCache(), State = new WorkspaceState(), Window = null!,
        Project = new ProjectItem("project", "交互测试", "", 0, 0),
        NavigateSection = (_, _) => Task.CompletedTask, OpenDashboard = () => Task.CompletedTask,
    };

    private static async Task Creation()
    {
        JsonElement submitted = default;
        using var api = new ApiClient("http://127.0.0.1:12345", new Handler(async request =>
        {
            Require(request.Method == HttpMethod.Post && request.RequestUri!.AbsolutePath.EndsWith("/projects"), "unexpected creation request");
            submitted = JsonSerializer.Deserialize<JsonElement>(await request.Content!.ReadAsStringAsync());
            return Response("{}");
        }));
        var view = new HomeView();
        typeof(WorkspaceView).GetProperty("Context", BindingFlags.Instance | BindingFlags.NonPublic)!.SetValue(view, Context(api));
        Field<TextBox>(view, "nameInput").Text = "测试新项目";
        Field<StackPanel>(view, "modeGroup").Children.OfType<RadioButton>().Single(b => (string?)b.Tag == "DIRECTOR").IsChecked = true;
        var fourK = Field<UniformGrid>(view, "resolutionGroup").Children.OfType<ToggleButton>().Single(b => (string?)b.Tag == "4K");
        fourK.RaiseEvent(new RoutedEventArgs(ButtonBase.ClickEvent));
        fourK.IsChecked = false;
        fourK.RaiseEvent(new RoutedEventArgs(ButtonBase.ClickEvent));
        Require(fourK.IsChecked == true, "reselecting resolution left no selected card");
        var finished = new TaskCompletionSource();
        view.CreateRequested += () => finished.SetResult();
        Field<Button>(view, "createButton").RaiseEvent(new RoutedEventArgs(ButtonBase.ClickEvent));
        await finished.Task.WaitAsync(TimeSpan.FromSeconds(3));
        Require(submitted.Text("workflow_mode") == "DIRECTOR" && submitted.Text("default_resolution") == "4K"
            && submitted.Text("name") == "测试新项目", "creation cards submitted different values than selected");
        view.Deactivate();
    }

    private static async Task Generation()
    {
        var body = JsonSerializer.SerializeToElement(new { });
        var dispatched = 0;
        var release = new TaskCompletionSource<HttpResponseMessage>();
        using var api = new ApiClient("http://127.0.0.1:12345", new Handler(async request =>
        {
            if (request.Method == HttpMethod.Post && request.RequestUri!.AbsolutePath.EndsWith("/batches/batch/candidates"))
            {
                dispatched++;
                body = JsonSerializer.Deserialize<JsonElement>(await request.Content!.ReadAsStringAsync());
                return await release.Task;
            }
            if (request.RequestUri!.AbsolutePath.EndsWith("/generation-workbench"))
                return Response("""{"page":{"id":"page","page_number":1,"storyboard_version":4},"storyboard":{"panels":[{"characters":["alice"],"outfits":{}}]},"readiness":{"ready":true},"current_batch":{"id":"batch"},"candidates":[]}""");
            if (request.RequestUri.AbsolutePath.EndsWith("/batches")) return Response("[]");
            throw new Exception("Unexpected generation request: " + request.RequestUri);
        }));
        var view = new GenerateView();
        typeof(WorkspaceView).GetProperty("Context", BindingFlags.Instance | BindingFlags.NonPublic)!.SetValue(view, Context(api));
        Set(view, "currentPage", new PageItem("page", 1) { StoryboardVersion = 4 });
        Set(view, "workbench", JsonSerializer.Deserialize<JsonElement>("""{"page":{"id":"page"},"storyboard":{"panels":[{"characters":["alice"],"outfits":{}}]},"readiness":{"ready":true},"current_batch":{"id":"batch"},"candidates":[]}"""));
        Set(view, "selectedModel", "image-model");
        Set(view, "referencesLoaded", true);
        Set(view, "referenceCharacters", JsonSerializer.Deserialize<List<JsonElement>>("""[{"id":"alice","primary_name":"小满","references":[{"asset_id":"default-ref"}]}]""")!);
        Field<Dictionary<string, ReferenceChoice>>(view, "referenceSelections")["alice"] = new("chosen-ref", null, null);
        var first = Call(view, "GenerateAsync");
        await Call(view, "GenerateAsync");
        Require(dispatched == 1, "double generate dispatched more than once");
        Require(body.Element("reference_selections").Element("alice").Text("character_asset_id") == "chosen-ref"
            && body.Number("storyboard_version") == 4 && body.Text("model_alias") == "image-model", "generate payload lost reference/model/version selection");
        release.SetResult(Response("{}"));
        await first;
        view.Deactivate();
    }

    private static async Task LocalEdit()
    {
        string selected = "source", commandId = "", proposedGroup = "";
        var proposed = 0; var accepted = 0; var discarded = 0;
        using var api = new ApiClient("http://127.0.0.1:12345", new Handler(async request =>
        {
            var path = request.RequestUri!.AbsolutePath;
            if (request.Method == HttpMethod.Get && path.EndsWith("/pages/page"))
                return Response(JsonSerializer.Serialize(new { id = "page", version = 7, selected_candidate_id = selected }));
            if (request.Method == HttpMethod.Post && path.EndsWith("/director/command-groups"))
            {
                proposed++;
                var payload = JsonSerializer.Deserialize<JsonElement>(await request.Content!.ReadAsStringAsync());
                var command = payload.Array("commands").Single();
                commandId = command.Text("command_id"); proposedGroup = payload.Text("command_group_id");
                Require(command.Text("operation") == "regenerate_region" && command.Element("expected_version").Number("value") == 7
                    && command.Element("payload").Array("mask").Count == 1, "local edit changed director contract");
                return Response(JsonSerializer.Serialize(new { commands = new[] { new { command_id = commandId, status = "PREVIEWED" } } }));
            }
            if (path.EndsWith("/accept"))
            {
                accepted++;
                Require(path.EndsWith($"/commands/{commandId}/accept"), "accept did not target the previewed command");
                return Response(JsonSerializer.Serialize(new { commands = new[] { new { command_id = commandId, status = "EXECUTED" } } }));
            }
            if (path.EndsWith("/discard"))
            {
                discarded++;
                Require(path.EndsWith($"/command-groups/{proposedGroup}/discard"), "discard targeted another preview");
                return Response("{}");
            }
            throw new Exception("Unexpected local-edit request: " + path);
        }));
        var models = JsonSerializer.Deserialize<List<JsonElement>>("""[{"logical_alias":"mask-model","display_name":"选区模型","model_type":"IMAGE","enabled":true,"operations":["image_edit"],"accepts_explicit_mask":true,"resolutions":["1K"]}]""")!;
        LocalEditWindow MakeWindow()
        {
            var window = new LocalEditWindow(Context(api), new PageItem("page", 1), new CandidateItem("source", 1, "COMPLETED") { Resolution = "1K", AssetId = "source-image" }, models);
            Set(window, "loaded", true);
            Field<TextBox>(window, "instruction").Text = "修正雨伞";
            Field<List<Point[]>>(window, "regions").Add(LocalEditRules.Rectangle(new(10, 10), new(30, 30), new(100, 100)));
            return window;
        }
        var editor = MakeWindow();
        selected = "another-source";
        await Call(editor, "Propose");
        Require(proposed == 0, "unselected source created a local-edit preview");
        selected = "source";
        await Call(editor, "Propose");
        Require(proposed == 1 && accepted == 0, "preview unexpectedly executed generation");
        selected = "changed-after-preview";
        await Call(editor, "Accept");
        Require(accepted == 0, "changed adopted source was not checked before accept");
        await Call(editor, "Discard");
        Require(discarded == 1 && Field<string>(editor, "groupId") == "", "discard did not release edit draft");
        selected = "source";
        await Call(editor, "Propose");
        await Call(editor, "Accept");
        Require(accepted == 1 && Field<string>(editor, "acceptedId") == commandId, "accepted local edit did not track its command lineage");
        Field<DispatcherTimer>(editor, "poll").Stop();
        editor.Close();
    }

    private static HttpResponseMessage Response(string body) => new(HttpStatusCode.OK) { Content = new StringContent(body) };
    private static T Field<T>(object target, string name) => (T)target.GetType().GetField(name, BindingFlags.Instance | BindingFlags.NonPublic)!.GetValue(target)!;
    private static void Set(object target, string name, object value) => target.GetType().GetField(name, BindingFlags.Instance | BindingFlags.NonPublic)!.SetValue(target, value);
    private static Task Call(object target, string name) => (Task)target.GetType().GetMethod(name, BindingFlags.Instance | BindingFlags.NonPublic | BindingFlags.DeclaredOnly)!.Invoke(target, null)!;
    private static void Require(bool condition, string message) { if (!condition) throw new Exception(message); }
    private sealed class Handler(Func<HttpRequestMessage, Task<HttpResponseMessage>> respond) : HttpMessageHandler
    {
        protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken cancellationToken) => respond(request);
    }
}
