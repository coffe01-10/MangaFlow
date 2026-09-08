using System.Reflection;
using System.Text.Json;
using System.Windows;
using System.Windows.Controls;
using MangaFlow.Native;
using MangaFlow.Native.Views;

/// <summary>
/// Regression smoke for the "shared control field + rebuilt container" crash family
/// (review round 2, B-2..B-5): rendering the same pane twice must not try to
/// re-parent a control that still belongs to the previous, detached tree.
/// Must run on the visual-checks STA thread after the theme resources are loaded.
/// </summary>
internal static class NativeRerenderChecks
{
    public static void Run()
    {
        if (Application.Current == null) throw new Exception("rerender checks need the visual-checks Application");

        CharactersPaneTwice();
        ConnectionPanelTwice();
        PackagePaneDraftTwice();
        ProjectSettingsTwice();

        Console.WriteLine("PASS: re-rendering panes does not re-parent shared controls (B-2..B-5)");
    }

    private static void CharactersPaneTwice()
    {
        var view = new AssetsView();
        using var doc = JsonDocument.Parse(
            """{"id":"c1","primary_name":"小满","aliases":["小满子"],"locked_features":"黑短发","forbidden_changes":"不许换发型","version":2}""");
        view.characters.Add(CharacterItem.From(doc.RootElement));
        view.SelectedCharacter = view.characters[0];
        var pane = new CharactersPane(view);
        // Clicking another character chip re-runs RenderStrip + RenderEditor on the
        // same pane instance; both must survive a second pass.
        Invoke(pane, "RenderStrip");
        Invoke(pane, "RenderEditor");
        Invoke(pane, "RenderEditor");
    }

    private static void ConnectionPanelTwice()
    {
        var owner = new SettingsView();
        using var connection = JsonDocument.Parse("""
            {"id":"conn-1","name":"测试连接","protocol":"OPENAI","base_url":"http://127.0.0.1:9","health_state":"UNKNOWN",
             "credential_source":"API_KEY","credential_writable":true,"enabled":true,"key_count":0,"model_count":0,
             "keys":[],"supported_model_types":["TEXT"]}
            """);
        var panel = new ConnectionPanel(owner, connection.RootElement.Clone(), connection.RootElement.Clone(), []);
        // 停用/启用连接或切回手工表单都会重跑 Render；再模拟一次开-关手工表单。
        Invoke(panel, "Render");
        Invoke(panel, "Render");
        var toggle = panel.GetType().GetMethod("ToggleManualForm", BindingFlags.Instance | BindingFlags.NonPublic)!;
        toggle.Invoke(panel, [new Button(), new RoutedEventArgs()]);
        toggle.Invoke(panel, [new Button(), new RoutedEventArgs()]);
    }

    private static void PackagePaneDraftTwice()
    {
        var view = new AssetsView();
        using var doc = JsonDocument.Parse(
            """{"id":"c1","primary_name":"小满","aliases":[],"locked_features":"","forbidden_changes":"","version":2}""");
        var character = CharacterItem.From(doc.RootElement);
        var pane = new CharacterPackagePane(view, character);
        using var package = JsonDocument.Parse("""
            {"status":"ACTIVE","version":3,"published_version_id":"v1",
             "versions":[{"id":"v1","version_number":1,"status":"PUBLISHED"},
               {"id":"v2","version_number":2,"status":"DRAFT","version":5,"references":[],
                "spec_snapshot":{"identity_spec":{"age_appearance":"17","gender":"女","personality":"冷静","identity_notes":"转学生"},
                  "visual_spec":{"hair":"黑短发","hair_color":"黑","face":"右耳银色耳钉","eyes":"琥珀色","body":"瘦高","distinguishing_marks":"无"},
                  "negative_constraints":["崩坏","多指"]}}]}
            """);
        SetField(pane, "package", package.RootElement.Clone());
        // 第一次“保存草稿规格”成功后 LoadAsync→Render 会再次执行同一实例上的渲染。
        Invoke(pane, "Render");
        Invoke(pane, "Render");
    }

    private static void ProjectSettingsTwice()
    {
        var view = new ProjectSettingsView();
        using var project = JsonDocument.Parse(
            """{"name":"雨夜来信","version":4,"workflow_mode":"SEMI_AUTO","draft_resolution":"1K","default_resolution":"2K","default_concurrency":2,"consistency_check_enabled":true}""");
        SetField(view, "project", project.RootElement.Clone());
        SetField(view, "version", 4);
        SetField(view, "textModels", new List<ModelOption>());
        // 离开再进项目设置页 / F5 会对同一实例再次 LoadAsync→Render。
        Invoke(view, "Render");
        Invoke(view, "Render");
    }

    private static void Invoke(object target, string method) =>
        // DeclaredOnly first: inherited same-name members (e.g. base Render) make the
        // plain GetMethod ambiguous for some panes.
        (target.GetType().GetMethods(BindingFlags.Instance | BindingFlags.NonPublic | BindingFlags.Public | BindingFlags.DeclaredOnly)
            .FirstOrDefault(m => m.Name == method && m.GetParameters().Length == 0)
            ?? target.GetType().GetMethod(method, BindingFlags.Instance | BindingFlags.NonPublic | BindingFlags.Public))!
        .Invoke(target, null);

    private static void SetField(object target, string field, object value) =>
        target.GetType().GetField(field, BindingFlags.Instance | BindingFlags.NonPublic)!.SetValue(target, value);
}
