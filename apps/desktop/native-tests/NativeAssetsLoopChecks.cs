using System.IO;
using System.Net;
using System.Net.Http;
using System.Reflection;
using System.Text.Json;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Controls.Primitives;
using System.Windows.Input;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using MangaFlow.Native;
using MangaFlow.Native.Controls;
using MangaFlow.Native.Services;
using MangaFlow.Native.Views;

// Regression checks for the 2026-09-09 connectivity-audit fixes on the assets
// workspace: media preview single-conversion, concept generation/adoption loop,
// frozen package spec readout, and unsaved-draft protection.
internal static class NativeAssetsLoopChecks
{
    public static async Task Run(string output)
    {
        var previous = (string)typeof(KeyValueStore).GetField("path", BindingFlags.Static | BindingFlags.NonPublic)!.GetValue(null)!;
        var prefs = Path.Combine(output, "assets-loop-test-prefs.json");
        File.WriteAllText(prefs, "{}"); KeyValueStore.UseLocation(prefs);
        try { await RunIsolated(output); }
        finally { KeyValueStore.UseLocation(previous); File.Delete(prefs); File.Delete(prefs + ".tmp"); }
    }

    private static async Task RunIsolated(string output)
    {
        var fake = new Fixture();
        using var api = new ApiClient("http://127.0.0.1:12345", fake);
        var view = new AssetsView();
        WorkspaceContext Context(string id) => new() { Api = api, Cache = new(), State = new(), Window = null!, Project = new ProjectItem(id, "资产闭环测试", "", 0, 0), NavigateSection = (_, _) => Task.CompletedTask, OpenDashboard = () => Task.CompletedTask };
        view.Activate(Context("loop-fixture"));
        view.RecordPreviewInsteadOfDialog = true;
        try
        {
            await view.RefreshAsync(); Layout(view, 1200, 1000);
            await MediaPreviewChecks(view, fake);
            await ConceptLoopChecks(view, fake);
            await FrozenSpecChecks(view);
            await DraftGuardChecks(view, fake);
        }
        finally { view.Deactivate(); }
        Console.WriteLine("PASS: media preview converts once, concept loop polls/adopts/dedupes, frozen spec readout uses version snapshots, unsaved outfit drafts are guarded");
    }

    // ── Task 1: preview click chains must resolve the media URL exactly once ──
    private static async Task MediaPreviewChecks(AssetsView view, Fixture fake)
    {
        fake.CandidateStatus = "READY";
        // Character reference thumbnail (server-form path carrying the /api/v1 prefix).
        var references = Descendants(view).OfType<CharacterReferencesPane>().Single();
        var referenceThumb = Descendants(references).OfType<Button>().First(b => b.Content is ImageBox);
        Click(referenceThumb);
        Require(view.LastPreviewUrl == "http://127.0.0.1:12345/api/v1/assets/a1/content", "reference preview did not resolve the server media path once");
        // Pin the original defect: a pre-converted absolute URL is NOT a valid input —
        // the boundary must keep rejecting it so double conversion can never return.
        try { view.OriginFor(view.LastPreviewUrl!); throw new Exception("OriginUrl accepted an already-absolute URL"); }
        catch (ArgumentException) { }

        // Concept candidate artwork (selected character required for the panel).
        Click(Descendants(view).OfType<ToggleButton>().Single(t => Equals(t.Tag, "c1"))); Layout(view, 1200, 1000);
        var artwork = Descendants(view).OfType<ConceptPanel>().Single();
        var conceptThumb = Descendants(artwork).OfType<Border>().First(b => b.Cursor == Cursors.Hand);
        ClickVisual(conceptThumb);
        Require(view.LastPreviewUrl == "http://127.0.0.1:12345/api/v1/assets/sheet1/content", "concept preview URL mismatch");

        // Scene reference thumbnail. 961bcec refactored the tile from a
        // Hand-cursor Border into an accessible Button{Content=ImageBox} whose
        // Click opens the preview — the click semantics and the resolved media
        // path are unchanged, so the check targets the Button and raises its
        // Click event (down-only visual clicks never fire a WPF Button). The
        // detail pane fills from a dispatcher-deferred load: bounded-wait like
        // FrozenSpecChecks instead of a single Settle.
        await view.SwitchAsync(AssetsView.Scenes);
        Button? sceneThumb = null;
        var sceneWait = DateTime.UtcNow;
        while (sceneThumb == null && DateTime.UtcNow - sceneWait < TimeSpan.FromSeconds(3))
        {
            await Settle(); Layout(view, 1200, 1000);
            sceneThumb = Descendants(view).OfType<Button>().FirstOrDefault(b => b.Content is ImageBox);
        }
        if (sceneThumb == null) throw new Exception("scene reference thumbnail (Button+ImageBox) never appeared");
        Click(sceneThumb);
        Require(view.LastPreviewUrl == "http://127.0.0.1:12345/api/v1/assets/a1/content", "scene preview did not resolve the scene reference path");

        // Raw reference library card. The reference-intake rework replaced the
        // 168px AssetCard (Hand-cursor Border around an ImageBox) with
        // ReferenceAssetCard's accessible Button{Content=ImageBox} whose Click
        // opens the preview — the same migration the scene tile went through
        // above, so target the Button, bounded-wait the dispatcher-deferred
        // pane build, and raise Click (visual clicks never fire a WPF Button).
        await view.SwitchAsync(AssetsView.References); Layout(view, 1200, 1000);
        Button? cardThumb = null;
        var cardWait = DateTime.UtcNow;
        while (cardThumb == null && DateTime.UtcNow - cardWait < TimeSpan.FromSeconds(3))
        {
            await Settle(); Layout(view, 1200, 1000);
            cardThumb = Descendants(view).OfType<Button>().FirstOrDefault(b => b.Content is ImageBox);
        }
        if (cardThumb == null) throw new Exception("reference library thumbnail (Button+ImageBox) never appeared");
        Click(cardThumb);
        Require(view.LastPreviewUrl == "http://127.0.0.1:12345/api/v1/assets/a1/content", "reference library preview mismatch");

        // Outfit wardrobe reference card keeps its raw-path contract (no regression).
        await view.SwitchAsync(AssetsView.Outfits); Layout(view, 1200, 1000);
        var wardrobeThumb = Descendants(view).OfType<Button>().First(b => b.Content is ImageBox);
        Click(wardrobeThumb);
        Require(view.LastPreviewUrl == "http://127.0.0.1:12345/api/v1/assets/a2/content", "wardrobe preview regressed");

        // Empty and invalid inputs never open a dialog and never crash the click chain.
        view.ShowImage("", "空");
        Require(view.LastPreviewUrl == null && view.LastPreviewError == null, "empty path produced a preview");
        view.ShowImage("https://evil.example/steal.png", "非法");
        Require(view.LastPreviewUrl == null && view.LastPreviewError != null, "invalid path must surface a visible error, not open a preview");
    }

    // ── Task 2: concept generation → polling → adoption loop ──
    private static async Task ConceptLoopChecks(AssetsView view, Fixture fake)
    {
        await view.SwitchAsync(AssetsView.Characters); Layout(view, 1200, 1000); await Settle(); Layout(view, 1200, 1000);
        // The concept generator requires an explicit project image model first.
        Click(Descendants(view).OfType<ToggleButton>().Single(t => Equals(t.Tag, "model-a")));
        var panel = Descendants(view).OfType<ConceptPanel>().Single();
        // Reset the fixture to an in-flight concept and reload the panel.
        fake.CandidateStatus = "GENERATING"; fake.CandidateApproved = false;
        await CallAsync(panel, "LoadCandidatesAsync"); Layout(view, 1200, 1000);
        var name = Field<TextBox>(panel, "outfitName"); var description = Field<TextBox>(panel, "outfitDescription");
        name.Text = "葬礼正装"; description.Text = "深色、克制、庄重";
        Call(panel, "SaveDraft");

        // Non-SHEET variants of the same character must not enter the concept list.
        var captions = Descendants(panel).OfType<TextBlock>().Select(t => t.Text).ToList();
        Require(!captions.Any(c => c.Contains("front1")), "non-SHEET candidate leaked into the concept panel");
        Require(captions.Any(c => c.Contains("排队/生成中…")), "pending concept candidate missing its queued status");
        Require(Descendants(panel).OfType<Button>().All(b => !Equals(b.Content, "确认为规范参考")), "pending candidate offered adoption");

        // Submitting deduplicates and blocks while the request is in flight.
        var generate = Descendants(panel).OfType<Button>().Single(b => Equals(b.Content, "生成概念设定草稿"));
        var sheetHold = fake.PendingSheet = Held();
        Click(generate); Click(generate);
        Require(fake.SheetPosts == 1 && !generate.IsEnabled, "concept submission duplicated or stayed enabled while pending");
        var sheetBody = fake.SheetBody;
        sheetHold.SetResult(QueuedSheet());
        await Settle(); Layout(view, 1200, 1000);
        Require(sheetBody.Text("model_alias") == "model-a" && sheetBody.Text("generation_mode") == "CONCEPT" && sheetBody.Text("outfit_name") == "葬礼正装",
            "complete-sheet payload lost model/mode/outfit fields");

        // Generation finishing AFTER the first candidate read: the poll flips the card.
        fake.CandidateStatus = "READY";
        view.PollTick(); await Settle(); Layout(view, 1200, 1000);
        panel = Descendants(view).OfType<ConceptPanel>().Single();
        var approve = Descendants(panel).OfType<Button>().FirstOrDefault(b => Equals(b.Content, "确认为规范参考"));
        Require(approve != null, "polling did not surface the finished concept candidate");

        // Adoption is single-shot and updates character/outfit data without dropping input.
        var approveHold = fake.PendingApprove = Held();
        Click(approve); Click(approve);
        Require(fake.ApprovePosts == 1 && !approve.IsEnabled, "adoption duplicated or stayed enabled while pending");
        var approveBody = fake.ApproveBody;
        Require(approveBody.Text("character_id") == "c1" && approveBody.Flag("bind_character_reference") && approveBody.Flag("set_canonical")
            && approveBody.Text("outfit_name") == "葬礼正装", "approve payload lost character/outfit binding fields");
        fake.CandidateApproved = true; fake.OufitAdopted = true;
        approveHold.SetResult(Json("""{"candidate_id":"sheet1","asset_id":"a1","character_id":"c1","outfit_id":"o3","approved":true}"""));
        await Settle(); Layout(view, 1200, 1000);
        var samePanel = Descendants(view).OfType<ConceptPanel>().Single();
        Require(ReferenceEquals(panel, samePanel) && name.Text == "葬礼正装", "adoption reload rebuilt the concept panel and lost input");
        Require(view.outfits.Any(o => o.Id == "o3"), "adoption did not refresh outfit data");
        Require(view.characters.First(c => c.Id == "c1").ReferenceCount == 1, "adoption did not refresh character references");
        Require(Descendants(samePanel).OfType<TextBlock>().Any(t => t.Text == "已确认为规范参考"), "approved candidate missing its confirmed state");
        Require(Descendants(samePanel).OfType<Button>().All(b => !Equals(b.Content, "确认为规范参考")), "approved candidate still offers adoption");

        // Failure state: visible failure, no adoption.
        fake.CandidateApproved = false; fake.CandidateStatus = "FAILED";
        await CallAsync(samePanel, "LoadCandidatesAsync"); Layout(view, 1200, 1000);
        Require(Descendants(samePanel).OfType<TextBlock>().Any(t => t.Text.Contains("生成失败")), "failed candidate lost its failure placeholder");

        // Draft persistence across character switches (web localStorage parity).
        fake.CandidateStatus = "GENERATING";
        await CallAsync(samePanel, "LoadCandidatesAsync");
        Click(Descendants(view).OfType<ToggleButton>().Single(t => Equals(t.Tag, "c2"))); Layout(view, 1200, 1000); await Settle(); Layout(view, 1200, 1000);
        var other = Descendants(view).OfType<ConceptPanel>().Single();
        Require(Field<TextBox>(other, "outfitName").Text == "", "other character hydrated a foreign draft");
        Click(Descendants(view).OfType<ToggleButton>().Single(t => Equals(t.Tag, "c1"))); Layout(view, 1200, 1000); await Settle(); Layout(view, 1200, 1000);
        var back = Descendants(view).OfType<ConceptPanel>().Single();
        Require(Field<TextBox>(back, "outfitName").Text == "葬礼正装", "concept draft did not survive reselecting the character");

        // A late candidate response cannot paint a replaced panel.
        fake.CandidateStatus = "READY";
        var hold = fake.PendingCandidates = Held();
        var late = CallAsync(back, "LoadCandidatesAsync");   // parked on the held response
        fake.CandidateStatus = "GENERATING";
        Click(Descendants(view).OfType<ToggleButton>().Single(t => Equals(t.Tag, "c2"))); Layout(view, 1200, 1000); await Settle(); Layout(view, 1200, 1000);
        var target = Descendants(view).OfType<ConceptPanel>().Single();
        hold.SetResult(Json("""[{"id":"sheet1","status":"READY","variant":"SHEET","resolution":"1K","content_url":"/api/v1/assets/sheet1/content"}]"""));
        await late; await Settle(); Layout(view, 1200, 1000);
        Require(ReferenceEquals(Descendants(view).OfType<ConceptPanel>().Single(), target)
            && !Descendants(target).OfType<Button>().Any(b => Equals(b.Content, "确认为规范参考")),
            "stale concept response painted the replacement panel");
        Click(Descendants(view).OfType<ToggleButton>().Single(t => Equals(t.Tag, "c1"))); Layout(view, 1200, 1000); await Settle();
    }

    // ── Task 3: frozen package versions read THEIR OWN spec snapshot ──
    private static async Task FrozenSpecChecks(AssetsView view)
    {
        Layout(view, 1200, 1000); await Settle(); Layout(view, 1200, 1000);
        var packageList = Descendants(view).OfType<ListBox>().Single(l => System.Windows.Automation.AutomationProperties.GetName(l) == "角色模型包列表");
        var wait = DateTime.UtcNow;
        while (Descendants(view).OfType<CharacterPackagePane>().Count() == 0 && DateTime.UtcNow - wait < TimeSpan.FromSeconds(3))
        { await Settle(); Layout(view, 1200, 1000); }
        // c1 has a draft: the workspace shows the editable draft (working spec B), not a frozen readout.
        var pane = Descendants(view).OfType<CharacterPackagePane>().Single();
        Require(Descendants(pane).OfType<TextBox>().Any(t => t.Text == "18 岁"), "c1 draft editor must show the working spec (B), not the frozen snapshot");
        Require(!PaneTexts(view).Any(t => t.Contains("已冻结版本")), "draft-mode package must not render a frozen readout");

        // c2: published-only. The frozen readout must show V1's snapshot A even though
        // the package working spec has already drifted to B.
        packageList.SelectedItem = packageList.Items.OfType<ListBoxItem>().Single(i => Equals(i.Tag, "c2"));
        await Settle(); Layout(view, 1200, 1000); await Settle(); Layout(view, 1200, 1000);
        var texts = PaneTexts(view);
        Require(texts.Any(t => t.StartsWith("已冻结版本 V1")), "published-only package missing the frozen readout header");
        Require(texts.Any(t => t == "17 岁") && texts.Any(t => t == "黑发"), "frozen readout must show the V1 snapshot values");
        Require(!texts.Any(t => t == "18 岁"), "frozen readout leaked the working spec (B) into the published V1 view");
        Require(texts.Any(t => t.StartsWith("负面约束：")), "frozen readout missing negative constraints");
        pane = Descendants(view).OfType<CharacterPackagePane>().Single();
        Require(!Descendants(pane).OfType<TextBox>().Any(), "frozen readout must be read-only (no text inputs)");
        Require(Descendants(pane).OfType<ProgressBar>().Any(), "frozen view must keep the completeness gauge");

        // c3: published pointer absent → falls back to the newest locked version; a
        // version without spec text shows the empty state instead of throwing.
        packageList.SelectedItem = packageList.Items.OfType<ListBoxItem>().Single(i => Equals(i.Tag, "c3"));
        await Settle(); Layout(view, 1200, 1000); await Settle(); Layout(view, 1200, 1000);
        texts = PaneTexts(view);
        Require(texts.Any(t => t.StartsWith("已冻结版本 V1")), "locked-version fallback missing");
        Require(texts.Any(t => t.Contains("该版本未填写规格文字。")), "empty snapshot must render the empty state");
    }

    // ── Task 4: unsaved outfit drafts and late-response isolation ──
    private static async Task DraftGuardChecks(AssetsView view, Fixture fake)
    {
        // A delayed character save lands while the wardrobe edit is in progress.
        Click(Descendants(view).OfType<ToggleButton>().Single(t => Equals(t.Tag, "c1"))); Layout(view, 1200, 1000);
        var locked = Descendants(view).OfType<TextBox>().Single(t => t.Text == "黑发，泪痣");
        locked.Text = "黑发，泪痣，瘦高";
        var saveCharacter = Descendants(view).OfType<Button>().Single(b => Equals(b.Content, "保存角色规范"));
        var characterHold = fake.PendingCharacterPatch = Held();
        Click(saveCharacter);
        await view.SwitchAsync(AssetsView.Outfits); Layout(view, 1200, 1000);
        var wardrobe = Descendants(view).OfType<OutfitWorkspace>().Single();
        var outfitName = Field<TextBox>(wardrobe, "outfitName");
        outfitName.Text = "夜行衣";
        fake.CharacterPatched = true;
        characterHold.SetResult(Json("{}")); await Settle(); Layout(view, 1200, 1000);
        Require(ReferenceEquals(Descendants(view).OfType<OutfitWorkspace>().Single(), wardrobe) && outfitName.Text == "夜行衣",
            "late character response rebuilt the wardrobe and destroyed unsaved input");

        // Tab switch with a dirty draft: cancel keeps everything.
        view.OutfitDraftPrompt = _ => Task.FromResult("cancel");
        await view.SwitchAsync(AssetsView.Characters); Layout(view, 1200, 1000);
        Require(ReferenceEquals(Descendants(view).OfType<OutfitWorkspace>().SingleOrDefault(), wardrobe), "cancelled guard still switched tabs");
        Require(outfitName.Text == "夜行衣", "cancelled guard lost the draft");

        // Leave confirmation with a dirty draft.
        view.OutfitDraftPrompt = _ => Task.FromResult("cancel");
        Require(!await view.ConfirmLeaveAsync(), "dirty draft must block leaving");
        view.OutfitDraftPrompt = _ => Task.FromResult("discard");
        Require(await view.ConfirmLeaveAsync(), "discarded draft must allow leaving");
        Require(outfitName.Text == "", "discard did not reset the draft");

        // Edit an existing record, then guard a save-and-continue over a conflict.
        var selector = Field<ComboBox>(wardrobe, "characterSelector");
        selector.SelectedIndex = 1;
        Click(Buttons(wardrobe, "管理参考图").First()); await Settle(); Layout(view, 1200, 1000);
        Require(outfitName.Text == "校服" && Field<TextBox>(wardrobe, "lockedFields").Text == "蓝色，领结", "edit did not load the versioned record");
        outfitName.Text = "更新校服";
        var records = Field<StackPanel>(wardrobe, "records");
        fake.FailPatch = true;
        view.OutfitDraftPrompt = _ => Task.FromResult("save");
        await view.SwitchAsync(AssetsView.Characters); await Settle(); Layout(view, 1200, 1000);
        var stayed = Descendants(view).OfType<OutfitWorkspace>().SingleOrDefault();
        Require(stayed != null && ReferenceEquals(stayed, wardrobe) && Field<TextBox>(stayed, "outfitName").Text == "更新校服",
            "a rejected save must keep the draft and stay on the wardrobe tab");
        fake.FailPatch = false;

        // Editing a different record with unsaved changes: cancel keeps the edits.
        view.OutfitDraftPrompt = _ => Task.FromResult("cancel");
        Click(Buttons(records, "管理参考图").Skip(1).First()); await Settle(); Layout(view, 1200, 1000);
        Require(Field<TextBox>(wardrobe, "outfitName").Text == "更新校服", "record switch destroyed unsaved edits despite cancel");

        // Character selector change with a dirty draft: cancel restores the owner.
        var ownerIndex = selector.SelectedIndex;
        selector.SelectedIndex = ownerIndex + 1; await Settle(); Layout(view, 1200, 1000);
        Require(selector.SelectedIndex == ownerIndex && Field<TextBox>(wardrobe, "outfitName").Text == "更新校服",
            "cancelled character switch must restore the draft owner selection");

        // Clean draft → navigation proceeds without prompting.
        view.OutfitDraftPrompt = _ => throw new Exception("prompt must not fire for a clean draft");
        Click(Field<Button>(wardrobe, "cancel"));
        await view.SwitchAsync(AssetsView.Characters); Layout(view, 1200, 1000);
        Require(Descendants(view).OfType<CharactersPane>().Any(), "clean wardrobe must switch without a prompt");
    }

    private static TaskCompletionSource<HttpResponseMessage> Held() =>
        new(TaskCreationOptions.RunContinuationsAsynchronously);
    private static HttpResponseMessage QueuedSheet() =>
        Json("""{"job_id":"j1","job_status":"QUEUED","candidate":{"id":"sheet2","status":"QUEUED","variant":"SHEET"}}""");
    private static List<string> PaneTexts(AssetsView view) =>
        Descendants(Descendants(view).OfType<CharacterPackagePane>().Single()).OfType<TextBlock>().Select(t => t.Text).ToList();
    private static async Task Settle() => await Task.Delay(30);

    private static IEnumerable<DependencyObject> Descendants(DependencyObject root)
    {
        var count = VisualTreeHelper.GetChildrenCount(root);
        for (var i = 0; i < count; i++)
        {
            var child = VisualTreeHelper.GetChild(root, i);
            yield return child;
            foreach (var nested in Descendants(child)) yield return nested;
        }
    }

    private static IEnumerable<Button> Buttons(DependencyObject root, string content) =>
        Descendants(root).OfType<Button>().Where(b => Equals(b.Content, content));

    private static T Field<T>(object value, string name) => (T)value.GetType().GetField(name, BindingFlags.Instance | BindingFlags.NonPublic)!.GetValue(value)!;

    private static void Click(ButtonBase button) => button.RaiseEvent(new RoutedEventArgs(ButtonBase.ClickEvent));

    private static void ClickVisual(UIElement element) => element.RaiseEvent(new MouseButtonEventArgs(Mouse.PrimaryDevice, 0, MouseButton.Left)
    { RoutedEvent = UIElement.MouseLeftButtonDownEvent });

    private static void Call(object target, string name) => target.GetType().GetMethod(name, BindingFlags.Instance | BindingFlags.NonPublic | BindingFlags.Public)!.Invoke(target, null);

    private static async Task CallAsync(object target, string name) =>
        await (Task)target.GetType().GetMethod(name, BindingFlags.Instance | BindingFlags.NonPublic | BindingFlags.Public)!.Invoke(target, null)!;

    private static void Require(bool value, string message) { if (!value) throw new Exception(message); }

    private static void Layout(FrameworkElement view, int width, int height)
    {
        view.Measure(new Size(width, height));
        view.Arrange(new Rect(0, 0, width, height));
        view.UpdateLayout();
    }

    private static HttpResponseMessage Json(string text) => new(HttpStatusCode.OK) { Content = new StringContent(text) };

    private sealed class Fixture : HttpMessageHandler
    {
        private const string Characters = """
          [{"id":"c1","primary_name":"樱","aliases":["妹妹"],"version":3,"locked_features":["黑发","泪痣"],"forbidden_changes":["瞳色"],"references":[]},
           {"id":"c2","primary_name":"我","aliases":["哥哥"],"locked_features":[],"forbidden_changes":[],"references":[]},
           {"id":"c3","primary_name":"空包","aliases":[],"locked_features":[],"forbidden_changes":[],"references":[]}]
          """;
        private const string CharactersAdopted = """
          [{"id":"c1","primary_name":"樱","aliases":["妹妹"],"version":4,"locked_features":["黑发","泪痣"],"forbidden_changes":["瞳色"],"references":[{"id":"ref1","asset_id":"a1"}]},
           {"id":"c2","primary_name":"我","aliases":["哥哥"],"locked_features":[],"forbidden_changes":[],"references":[]},
           {"id":"c3","primary_name":"空包","aliases":[],"locked_features":[],"forbidden_changes":[],"references":[]}]
          """;
        private const string Outfits = """
          [{"id":"o1","character_id":"c1","name":"校服","version":7,"locked_fields":["蓝色","领结"],"reference_asset_ids":["a2"]},
           {"id":"o2","character_id":"c2","name":"冬季便装","version":2,"locked_fields":[],"reference_asset_ids":["a2"]}]
          """;
        private const string OutfitsAdopted = """
          [{"id":"o1","character_id":"c1","name":"校服","version":7,"locked_fields":["蓝色","领结"],"reference_asset_ids":["a2"]},
           {"id":"o2","character_id":"c2","name":"冬季便装","version":2,"locked_fields":[],"reference_asset_ids":["a2"]},
           {"id":"o3","character_id":"c1","name":"葬礼正装","version":1,"locked_fields":[],"reference_asset_ids":[]}]
          """;
        private const string Assets = """
          [{"id":"a1","kind":"CHARACTER_REFERENCE","original_name":"樱（冬装）.png","byte_size":1300000,"status":"GENERATED","content_url":"/api/v1/assets/a1/content"},
           {"id":"a2","kind":"OUTFIT_REFERENCE","original_name":"校服参考.png","byte_size":1400000,"status":"UPLOADED","content_url":"/api/v1/assets/a2/content"}]
          """;
        private const string Models = """
          [{"model_type":"IMAGE","enabled":true,"operations":["image_edit"],"logical_alias":"model-a","display_name":"Codex CLI ImageGen","provider":"Codex CLI","model_id":"codex-imagegen"},
           {"model_type":"IMAGE","enabled":true,"operations":["image_edit"],"logical_alias":"model-b","display_name":"Google: Nano Banana 2","provider":"OpenRouter","model_id":"google/gemini-3.1-flash-image"}]
          """;
        private const string Packages = """
          [{"id":"pkg1","character_id":"c1","status":"ACTIVE","published_version_number":1,"published_completeness":{"score":40},"character":{"primary_name":"樱"}},
           {"id":"pkg2","character_id":"c2","status":"ACTIVE","published_version_number":1,"published_completeness":{"score":60},"character":{"primary_name":"我"}},
           {"id":"pkg3","character_id":"c3","status":"ACTIVE","published_version_number":1,"published_completeness":{"score":10},"character":{"primary_name":"空包"}}]
          """;
        // P1: draft v2 exists; the working spec has drifted to B (18 岁) while the
        // published V1 snapshot still freezes A (17 岁 / 黑发 / 负面约束 瞳色).
        private const string PackageWithDraft = """
          {"id":"pkg1","character_id":"c1","status":"ACTIVE","version":5,"published_version_id":"v1",
           "identity_spec":{"age_appearance":"18 岁"},"visual_spec":{},"negative_constraints":[],
           "versions":[{"id":"v1","status":"READY","version_number":1,"version":2,"references":[],"outfits":[],
                        "spec_snapshot":{"identity_spec":{"age_appearance":"17 岁"},"visual_spec":{"hair":"黑发"},"negative_constraints":["瞳色"]},
                        "completeness":{"score":15,"missing":[{"message":"缺少正面参考","suggestion":"上传或绑定正面图片"}]}},
                       {"id":"v2","status":"DRAFT","version_number":2,"version":1,"references":[],"outfits":[],"spec_snapshot":{},
                        "completeness":{"score":10,"missing":[]}}]}
          """;
        private const string PackagePublishedOnly = """
          {"id":"pkg2","character_id":"c2","status":"ACTIVE","version":6,"published_version_id":"v1",
           "identity_spec":{"age_appearance":"18 岁"},"visual_spec":{},"negative_constraints":["发色"],
           "versions":[{"id":"v1","status":"READY","version_number":1,"version":3,"references":[],"outfits":[],
                        "spec_snapshot":{"identity_spec":{"age_appearance":"17 岁"},"visual_spec":{"hair":"黑发"},"negative_constraints":["瞳色"]},
                        "completeness":{"score":60,"missing":[]}}]}
          """;
        private const string PackageNoPointerNoSpec = """
          {"id":"pkg3","character_id":"c3","status":"ACTIVE","version":1,"published_version_id":null,
           "identity_spec":{},"visual_spec":{},"negative_constraints":[],
           "versions":[{"id":"v1","status":"READY","version_number":1,"version":1,"references":[],"outfits":[],"spec_snapshot":{},
                        "completeness":{"score":10,"missing":[]}}]}
          """;

        public readonly List<(string Method, string Path, JsonElement Body)> Writes = [];
        public TaskCompletionSource<HttpResponseMessage>? PendingSheet, PendingApprove, PendingCharacterPatch, PendingCandidates;
        public JsonElement SheetBody, ApproveBody;
        public int SheetPosts, ApprovePosts;
        public string CandidateStatus = "GENERATING";
        public bool CandidateApproved, OufitAdopted, CharacterPatched, FailPatch;

        protected override async Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken token)
        {
            var path = request.RequestUri!.AbsolutePath;
            var query = request.RequestUri.Query;
            if (request.Method != HttpMethod.Get)
            {
                var body = request.Content == null ? "{}" : await request.Content.ReadAsStringAsync(token);
                var parsed = JsonSerializer.Deserialize<JsonElement>(body);
                Writes.Add((request.Method.Method, path, parsed));
                if (path.EndsWith("/complete-sheet"))
                { SheetPosts++; SheetBody = parsed; if (PendingSheet is { } sheet) { PendingSheet = null; return await sheet.Task; } return QueuedSheet(); }
                if (path.EndsWith("/approve-reference"))
                { ApprovePosts++; ApproveBody = parsed; if (PendingApprove is { } approve) { PendingApprove = null; return await approve.Task; } return Json("""{"approved":true}"""); }
                if (path.EndsWith("/characters/c1") && request.Method == HttpMethod.Patch)
                { if (PendingCharacterPatch is { } patch) { PendingCharacterPatch = null; return await patch.Task; } return Json("{}"); }
                if (request.Method == HttpMethod.Patch && FailPatch)
                    return new HttpResponseMessage(HttpStatusCode.Conflict) { Content = new StringContent("{\"detail\":\"版本已更新，请刷新后重试\"}") };
                return Json("{}");
            }
            if (path.EndsWith("/models")) return Json(Models);
            if (path.EndsWith("/assets")) return Json(Assets);
            if (path.EndsWith("/characters")) return Json(OufitAdopted || CharacterPatched ? CharactersAdopted : Characters);
            if (path.EndsWith("/outfits")) return Json(OufitAdopted ? OutfitsAdopted : Outfits);
            if (path.EndsWith("/character-packages")) return Json(Packages);
            if (path.EndsWith("/c1/package")) return Json(PackageWithDraft);
            if (path.EndsWith("/c2/package")) return Json(PackagePublishedOnly);
            if (path.EndsWith("/c3/package")) return Json(PackageNoPointerNoSpec);
            if (path.EndsWith("/scene-assets")) return Json("""[{"id":"s1","name":"京都老宅佛堂","description":"昏暗佛堂","location_hint":"","interior":true,"version":1,"references":[{"asset_id":"a1","is_canonical":true}],"variants":[]}]""");
            if (path.EndsWith("/asset-generation-batches") && query.Contains("CHARACTER"))
                return Json("""[{"id":"b1","target_type":"CHARACTER","target_id":"c1","generation_kind":"CHARACTER","ordinal":1,"status":"OPEN"}]""");
            if (path.EndsWith("/b1/candidates"))
            {
                if (PendingCandidates is { } hold) { PendingCandidates = null; return await hold.Task; }
                var approval = CandidateApproved ? ",\"prompt_snapshot\":{\"reference_approval\":{\"approved\":true}}" : "";
                var sheet = CandidateStatus == "READY"
                    ? $"{{\"id\":\"sheet1\",\"ordinal\":1,\"status\":\"READY\",\"variant\":\"SHEET\",\"resolution\":\"1K\",\"asset_id\":\"sheet1\",\"content_url\":\"/api/v1/assets/sheet1/content\"{approval}}}"
                    : $"{{\"id\":\"sheet1\",\"ordinal\":1,\"status\":\"{CandidateStatus}\",\"variant\":\"SHEET\",\"resolution\":\"1K\"{approval}}}";
                var front = "{\"id\":\"front1\",\"ordinal\":2,\"status\":\"READY\",\"variant\":\"FRONT\",\"resolution\":\"1K\",\"content_url\":\"/api/v1/assets/front1/content\"}";
                return Json("[" + sheet + "," + front + "]");
            }
            if (path.EndsWith("/styles")) return Json("[]");
            return Json("[]");
        }
    }
}
