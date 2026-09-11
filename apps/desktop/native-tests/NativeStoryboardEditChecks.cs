using System.IO;
using System.Net;
using System.Net.Http;
using System.Reflection;
using System.Text.Json;
using System.Threading;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Controls.Primitives;
using System.Windows.Input;
using System.Windows.Media;
using System.Windows.Threading;
using MangaFlow.Native;
using MangaFlow.Native.Services;
using MangaFlow.Native.Views;

// 分镜视图六项编辑能力的回归检查（面板缩放手柄 / 新增气泡 / 对白编辑 409 /
// 出血安全区叠加 / 专注模式 / 从本页重新计算）。
//
// 【需 lead 注册】本文件按任务约束未接入运行入口：建议在
// NativeInteractionChecks.RunIsolated 的 await 链（或专用入口）追加
// `await NativeStoryboardEditChecks.Run(output);`——Run 内部自带 STA 调度，
// 也可独立调用（无 Application 时会自建 STA 线程 + Application/Theme）。
// 注册调用点必须在 STA/Dispatcher 线程上下文中（WPF 视觉树访问要求）。
internal static class NativeStoryboardEditChecks
{
    internal static void Run(string output)
    {
        var previous = (string)typeof(KeyValueStore).GetField("path", BindingFlags.Static | BindingFlags.NonPublic)!.GetValue(null)!;
        Directory.CreateDirectory(output);
        var prefs = Path.Combine(output, "storyboard-edit-test-prefs.json");
        File.WriteAllText(prefs, "{}");
        KeyValueStore.UseLocation(prefs);
        try
        {
            if (Application.Current is null) RunOnDedicatedStaThread(output);
            else RunFrame(output);
        }
        finally
        {
            KeyValueStore.UseLocation(previous);
            File.Delete(prefs);
            File.Delete(prefs + ".tmp");
        }
    }

    // 独立运行（进程里还没有 Application）：自建 STA 线程 + Application/Theme
    //（NativeSceneChecks 的模式）。Application 是 AppDomain 级单例，本方法每
    // 进程至多走一次；已有 Application 时走 RunFrame 的调用方线程。
    private static void RunOnDedicatedStaThread(string output)
    {
        Exception? failure = null;
        var thread = new Thread(() =>
        {
            try
            {
                var app = new Application { ShutdownMode = ShutdownMode.OnExplicitShutdown };
                app.Resources.MergedDictionaries.Add(new ResourceDictionary { Source = new Uri("/MangaFlow.Native;component/Theme.xaml", UriKind.Relative) });
                RunFrame(output);
            }
            catch (Exception error) { failure = error; }
        });
        thread.SetApartmentState(ApartmentState.STA);
        thread.Start();
        thread.Join();
        if (failure != null) throw new Exception("Storyboard edit checks failed", failure);
    }

    // STA + DispatcherFrame 泵（NativeInteractionChecks.RunIsolated 的模式）：
    // async-void 的按钮处理经由 DispatcherSynchronizationContext 回到泵上执行。
    private static void RunFrame(string output)
    {
        Exception? failure = null;
        var frame = new DispatcherFrame();
        Dispatcher.CurrentDispatcher.UnhandledException += (_, e) =>
        {
            e.Handled = true;
            failure ??= new Exception("Dispatcher unhandled: " + e.Exception.Message, e.Exception);
            frame.Continue = false;
        };
        var timeout = new DispatcherTimer { Interval = TimeSpan.FromSeconds(60) };
        timeout.Tick += (_, _) => { failure = new TimeoutException("Storyboard edit checks timed out"); frame.Continue = false; };
        timeout.Start();
        SynchronizationContext.SetSynchronizationContext(new DispatcherSynchronizationContext());
        Dispatcher.CurrentDispatcher.BeginInvoke(new Action(async () =>
        {
            try { await CheckAsync(output); }
            catch (Exception error) { failure = error; }
            finally { frame.Continue = false; }
        }));
        Dispatcher.PushFrame(frame);
        timeout.Stop();
        if (failure != null) throw new Exception("Storyboard edit checks failed", failure);
    }

    private static async Task CheckAsync(string output)
    {
        var fixture = new Fixture();
        using var api = new ApiClient("http://127.0.0.1:12345", fixture);
        var view = new StoryboardView();
        view.Activate(new WorkspaceContext
        {
            Api = api, Cache = new ApiCache(), State = new WorkspaceState(), Window = null!,
            Project = new ProjectItem("sb-edit", "分镜编辑测试", "", 0, 0),
            NavigateSection = (_, _) => Task.CompletedTask, OpenDashboard = () => Task.CompletedTask,
        });
        try
        {
            try { await Until(() => view.PanelCountForTest == 1); }
            catch (OperationCanceledException)
            {
                // 超时必须可诊断：逻辑树提示文本（视图未布局时视觉树为空）+ 夹具请求计数 + 加载状态字段。
                var inspector = Field<System.Windows.Controls.StackPanel>(view, "inspector");
                var inspectorTexts = inspector == null ? "" : string.Join(" | ", inspector.Children.OfType<System.Windows.Controls.TextBlock>().Select(t => t.Text).Take(6));
                var board = Field<JsonElement>(view, "storyboard");
                var guard = Field<int>(view, "pageLoadVersion");
                var status = Field<System.Windows.Controls.TextBlock>(view, "statusLine").Text;
                throw new Exception($"初始加载超时：PanelCount={view.PanelCountForTest} StoryboardGets={fixture.StoryboardGets} pageLoadVersion={guard} storyboard.ValueKind={board.ValueKind} statusLine={status} inspector：{inspectorTexts}");
            }
            await ResizeHandleChecks(view, fixture);
            await NewBubbleChecks(view, fixture);
            await DialogueEditChecks(view, fixture);
            await OverlayChecks(view, fixture);
            FocusModeChecks(view);
            await ReplanChecks(view, fixture);
            await InspectorKeyIsolationChecks(view, fixture);
        }
        finally { view.Deactivate(); }
        Console.WriteLine("PASS: storyboard resize handles/bubble create/dialogue 409 recovery/bleed-safe overlay/focus mode/replan/inspector key isolation all match the web contract");
    }

    // ── 1. 缩放手柄：bounds 变更、撤销栈、整包 PUT 载荷、最小尺寸 ──
    private static async Task ResizeHandleChecks(StoryboardView view, Fixture fixture)
    {
        var page = Field<Canvas>(view, "page");
        view.SelectPanelForTest(0);
        Layout(view, 1400, 1000);
        // 单选矩形格 → 恰好 8 向手柄（nw/n/ne/e/se/s/sw/w），Tag 可寻址
        Require(view.HandleCountForTest == 8, $"单选格应显示 8 向缩放手柄（实际 {view.HandleCountForTest}）");
        var names = page.Children.OfType<FrameworkElement>()
            .Select(element => element.Tag as string)
            .Where(tag => tag is not null && tag.StartsWith("resize-handle:", StringComparison.Ordinal))
            .Select(tag => tag!["resize-handle:".Length..]).ToHashSet();
        Require(names.SetEquals(["nw", "n", "ne", "e", "se", "s", "sw", "w"]), $"手柄集合应与网页一致（实际 {string.Join(",", names)}）");
        // 取消选中 → 手柄隐藏（网页多选/无选中时不挂面板手柄的桌面等价面）
        view.SelectPanelForTest(-1);
        Require(view.HandleCountForTest == 0, "无选中时不得残留缩放手柄");
        view.SelectPanelForTest(0);

        var before = view.PanelRectForTest(0);
        view.ResizeViaHandleForTest(0, "se", new Point(0.85, 0.75));
        var grown = view.PanelRectForTest(0);
        Require(Math.Abs(grown.X - before.X) < 1e-9 && Math.Abs(grown.Y - before.Y) < 1e-9, "se 缩放不得移动左上角");
        Require(Math.Abs(grown.Width - 0.75) < 1e-9 && Math.Abs(grown.Height - 0.65) < 1e-9,
            $"se 缩放应把右下角拖到指针（实际 {grown.Width:F3}×{grown.Height:F3}，期望 0.750×0.650）");
        Require(view.CanUndoForTest, "缩放必须进入撤销栈");
        Layout(view, 1400, 1000);
        Click(Buttons(view, "撤销").Single());
        Require(Math.Abs(view.PanelRectForTest(0).Width - before.Width) < 1e-9, "撤销应还原缩放");
        Click(Buttons(view, "重做").Single());
        Require(Math.Abs(view.PanelRectForTest(0).Width - 0.75) < 1e-9, "重做应恢复缩放");

        // 指针拖过对边 → 宽高兜底到网页一致的 0.03 最小尺寸
        var beforeNw = view.PanelRectForTest(0);
        view.ResizeViaHandleForTest(0, "nw", new Point(0.95, 0.8));
        var tiny = view.PanelRectForTest(0);
        Require(tiny.Width >= 0.03 - 1e-9 && tiny.Height >= 0.03 - 1e-9,
            $"缩放后不得小于最小尺寸 0.03（实际 {tiny.Width:F3}×{tiny.Height:F3}）");
        Require(Math.Abs(tiny.X - 0.82) < 1e-9 && Math.Abs(tiny.Y - 0.72) < 1e-9,
            $"最小尺寸分支应贴合对面边（实际 X={tiny.X:F4} Y={tiny.Y:F4}，期望 0.82/0.72；origin={beforeNw} mods={System.Windows.Input.Keyboard.Modifiers}）");

        // 整包几何 PUT：缩放后的 bounds 与 request_id 一起提交
        // 基线在点击前一并捕获：假夹具响应极快，保存后的重载 GET 会在首个
        // Until 的泵送期间就完成，晚取基线会永远等不到「再一次」读取。
        var saves = fixture.GeometryPuts;
        var reads = fixture.StoryboardGets;
        Click(Buttons(view, "保存本页").Single());
        try { await Until(() => fixture.GeometryPuts == saves + 1); }
        catch (OperationCanceledException)
        {
            throw new Exception($"保存 PUT 未发生：dirty={Field<bool>(view, "dirty")} saving={Field<bool>(view, "saving")} GeometryPuts={fixture.GeometryPuts}（基线 {saves}）");
        }
        var panelPayload = fixture.GeometryBody.Array("panels").Single(row => row.Text("panel_id") == "panel-1");
        var bounds = panelPayload.Element("bounds");
        Require(Math.Abs(bounds.Decimal("x") - 0.82) < 1e-6 && Math.Abs(bounds.Decimal("y") - 0.72) < 1e-6
            && Math.Abs(bounds.Decimal("width") - 0.03) < 1e-6 && Math.Abs(bounds.Decimal("height") - 0.03) < 1e-6,
            "整包 PUT 必须提交缩放后的 bounds");
        Require(fixture.GeometryBody.Text("request_id").Length > 0, "整包 PUT 必须携带 request_id");
        // 判据用点击前的 reads 基线：重载 GET 可能在上面首个 Until 的泵送期间
        // 就完成，这里只要求「点击后发生过至少一次读取」，不要求时序在其后。
        try { await Until(() => fixture.StoryboardGets > reads); }   // 保存后的整页重载完成
        catch (OperationCanceledException)
        {
            var status = Field<System.Windows.Controls.TextBlock>(view, "statusLine").Text;
            var conflict = Field<System.Windows.Controls.Border>(view, "conflictBar").Visibility.ToString();
            throw new Exception($"保存后重载未发生：StoryboardGets={fixture.StoryboardGets}（点击前基线 {reads}）statusLine={status} conflictBar={conflict}");
        }
        await Settle();
    }

    // ── 2. 新增气泡：POST 端点 + 载荷 + panel_version 乐观锁 ──
    private static async Task NewBubbleChecks(StoryboardView view, Fixture fixture)
    {
        view.SelectPanelForTest(0);
        Layout(view, 1400, 1000);
        var opener = Buttons(view, "新增气泡").Single();
        Require(opener.IsEnabled, "选中格后应可新增气泡");
        Click(opener);
        await Settle();
        Layout(view, 1400, 1000);
        var text = Descendants(view).OfType<TextBox>().Single(box => GetName(box) == "新增气泡文字");
        text.Text = "新台词";
        var speaker = Descendants(view).OfType<ComboBox>().Single(box => GetName(box) == "新增气泡说话人");
        SelectCombo(speaker, "c1");
        var posts = fixture.DialoguePosts;
        Click(Buttons(view, "新增气泡").Single(button => button.IsEnabled));
        await Until(() => fixture.DialoguePosts == posts + 1);
        var body = fixture.DialoguePostBody;
        Require(body.Number("panel_version") == 4, $"新增气泡必须带 panel_version 乐观锁（实际 {body.Number("panel_version")}）");
        Require(body.Text("target_text") == "新台词", "新增气泡载荷丢失 target_text");
        Require(body.Text("speaker_character_id") == "c1", "新增气泡载荷丢失说话人");
        Require(body.Text("text_direction") == "vertical", "新增气泡默认应竖排");
        Require(body.Flag("rewrite_forbidden"), "新增气泡默认应锁定文字");
        await Until(() => view.BubbleCountForTest == 2);   // 成功后重载画布，新气泡上版
        await Settle();
    }

    // ── 3. 对白编辑：PATCH 载荷与 409 保留输入 + 冲突条 ──
    private static async Task DialogueEditChecks(StoryboardView view, Fixture fixture)
    {
        Layout(view, 1400, 1000);
        var box = Descendants(view).OfType<TextBox>().Single(element => GetName(element) == "气泡 1 文字");
        box.Text = "改后台词";
        var speaker = Descendants(view).OfType<ComboBox>().Single(element => GetName(element) == "气泡 1 说话人");
        SelectCombo(speaker, "c1");
        Require(view.NarrativeDirtyForTest, "对白草稿必须点亮脏判定");
        fixture.FailDialoguePatch = true;
        var patches = fixture.DialoguePatches;
        Click(Buttons(view, "保存更改").First());
        await Until(() => fixture.DialoguePatches == patches + 1);
        await Settle();
        var body = fixture.DialoguePatchBody;
        Require(body.Number("panel_version") == 5, $"PATCH 应带新增气泡后升格的 panel_version（实际 {body.Number("panel_version")}）");
        Require(body.Text("target_text") == "改后台词" && body.Text("speaker_character_id") == "c1"
            && body.Text("text_direction") == "vertical" && body.Flag("rewrite_forbidden"), "PATCH 载荷字段与网页不一致");
        Require(box.Text == "改后台词", "409 冲突必须保留用户输入");
        Require(Descendants(view).OfType<Button>().Any(button => Equals(button.Content, "放弃草稿并重新加载")),
            "409 必须亮出冲突条（放弃草稿并重新加载）");

        // 重试成功：冲突条解除、草稿摘除、输入按服务端数据回填
        fixture.FailDialoguePatch = false;
        Click(Buttons(view, "保存更改").First());
        await Until(() => fixture.DialoguePatches == patches + 2);
        await Until(() => !view.NarrativeDirtyForTest);
        Require(Field<Border>(view, "conflictBar").Visibility == Visibility.Collapsed, "保存成功后冲突条应解除");
        Layout(view, 1400, 1000);
        Require(Descendants(view).OfType<TextBox>().Any(element => GetName(element) == "气泡 1 文字" && element.Text == "改后台词"),
            "成功保存后服务端文本应回填气泡卡");
        await Settle();
    }

    // ── 4. 出血/安全区叠加：开关渲染 + 画布缺失禁用 ──
    private static async Task OverlayChecks(StoryboardView view, Fixture fixture)
    {
        var page = Field<Canvas>(view, "page");
        var bleed = Descendants(view).OfType<ToggleButton>().Single(button => Equals(button.Content, "出血框"));
        var safe = Descendants(view).OfType<ToggleButton>().Single(button => Equals(button.Content, "安全区"));
        Require(bleed.IsEnabled && safe.IsEnabled, "页带 canvas 字段时出血/安全区开关应可用");
        Toggle(bleed);
        var ring = Tagged(page, "bleed-ring");
        Require(ring != null, "出血框开关后必须渲染叠加元素");
        Toggle(safe);
        var frame = Tagged(page, "safe-rect");
        Require(frame != null, "安全区开关后必须渲染叠加元素");
        Require(Math.Abs(Canvas.GetLeft(frame) - 5.0 / 182 * page.Width) < 0.5, "安全区应按 safe_mm 从页边内缩");
        Require(Math.Abs(Canvas.GetLeft(ring!) + 3.0 / 182 * page.Width) < 0.5, "出血框应按 bleed_mm 向页外扩张");
        // 页缺 canvas 字段：开关禁用（对齐网页 canvasKnown 门禁）
        fixture.CanvasKnown = false;
        var reads = fixture.StoryboardGets;
        await view.RefreshAsync();
        await Until(() => fixture.StoryboardGets > reads);
        await Settle();
        Layout(view, 1400, 1000);
        Require(!bleed.IsEnabled && !safe.IsEnabled, "页缺 canvas 字段时出血/安全区开关应禁用");
        fixture.CanvasKnown = true;
        reads = fixture.StoryboardGets;
        await view.RefreshAsync();
        await Until(() => fixture.StoryboardGets > reads);
        await Settle();
    }

    // ── 5. 专注模式：视觉树变化 ──
    private static void FocusModeChecks(StoryboardView view)
    {
        var focus = Descendants(view).OfType<ToggleButton>().Single(button => Equals(button.Content, "专注模式"));
        var pageBar = Field<StackPanel>(view, "pageBar");
        Require(pageBar.Visibility == Visibility.Visible, "进入专注模式前页面条应可见");
        Toggle(focus);
        Require(pageBar.Visibility == Visibility.Collapsed, "专注模式应隐藏页面条（对齐 focus-mode 隐藏 page-strip）");
        Toggle(focus);
        Require(pageBar.Visibility == Visibility.Visible, "退出专注模式应恢复页面条");
    }

    // ── 6. 从本页重新计算：端点 + 参数 + 脏确认 ──
    private static async Task ReplanChecks(StoryboardView view, Fixture fixture)
    {
        view.SelectPanelForTest(0);
        view.ResizeViaHandleForTest(0, "e", new Point(0.8, 0.35));   // 制造未保存几何草稿
        Require(view.CanUndoForTest, "重算前的脏态准备失败");
        var replan = Descendants(view).OfType<Button>().Single(button => Equals(button.Content, "从本页重新计算"));
        view.LeaveConfirmOverride = () => Task.FromResult(false);
        Click(replan);
        await Task.Delay(200);
        Require(fixture.Plans == 0, "拒绝离开确认后不得发出重算请求");
        view.LeaveConfirmOverride = () => Task.FromResult(true);
        // 基线在点击前捕获：重算后的刷新可能在 Plans==1 的 Until 泵送期间完成。
        var reads = fixture.StoryboardGets;
        Click(replan);
        await Until(() => fixture.Plans == 1);
        Require(fixture.PlanBody.Flag("replace_existing"), "重算载荷必须 replace_existing=true");
        Require(fixture.PlanBody.Number("from_page_number") == 3, $"重算必须从当前页号起算（实际 {fixture.PlanBody.Number("from_page_number")}）");
        await Until(() => fixture.StoryboardGets > reads);   // 成功后刷新回本页
        Require(!view.CanUndoForTest, "重算后的重载应清空几何草稿");
        view.LeaveConfirmOverride = null;
    }

    // ── 7. 键盘隔离（#339）：焦点在对白编辑 TextBox 内按 Backspace/Delete/方向键/Tab，
    // 不得触发气泡删除确认 / 面板微移 / 撤销入栈 / 选中切换；焦点在画布内快捷键照常工作。
    // 真实构造：视图挂进已显示的离屏窗口（KeyEventArgs 需要 PresentationSource），
    // 把 PreviewKeyDown 隧道事件直接 raise 在 TextBox 上——与真实按键同一路由路径
    // （根→…→TextBox）。处理器若错误地挂在整个 View 上（回归），View 在该路由上，
    // 处理器必然触发：e.Handled 翻真、删除确认被调、撤销栈入条，检查随之失败。
    private static async Task InspectorKeyIsolationChecks(StoryboardView view, Fixture fixture)
    {
        var owner = new Window
        {
            Width = 1400, Height = 1000, ShowInTaskbar = false, ShowActivated = false,
            WindowStartupLocation = WindowStartupLocation.Manual, Left = -2400, Top = 80,
            Content = view,
        };
        owner.Show();
        try
        {
            Layout(view, 1400, 1000);
            view.SelectBubbleForTest(0);
            var text = Descendants(view).OfType<TextBox>().Single(box => GetName(box) == "气泡 1 文字");
            text.Text = "键盘焦点中的台词";
            text.Focus();   // 让键盘焦点真实落在编辑器内（守卫分支也由此被覆盖）
            Require(Keyboard.FocusedElement == text, "测试窗口未取得键盘焦点（键盘隔离检查无法进行）");
            var confirms = 0;
            view.DeleteConfirmOverride = () => { confirms++; return false; };
            var undoBefore = view.CanUndoForTest;
            var rectBefore = view.PanelRectForTest(0);
            foreach (var key in new[] { Key.Back, Key.Delete, Key.Left, Key.Up, Key.Tab })
                Require(!Press(text, key), $"对白编辑 TextBox 内按 {key} 不得被画布快捷键劫持（e.Handled 必须保持 false）");
            Require(confirms == 0, "对白编辑 TextBox 内按 Backspace/Delete 不得触发气泡删除确认");
            Require(fixture.DialogueDeletes == 0, "对白编辑 TextBox 内按键不得发出服务端气泡删除");
            Require(view.CanUndoForTest == undoBefore, "对白编辑 TextBox 内按方向键不得向撤销栈推入面板微调条目");
            Require(view.PanelRectForTest(0) == rectBefore, "对白编辑 TextBox 内按方向键不得微移面板");
            Require(view.SelectedBubbleIdForTest == "dlg-1" && view.SelectedPanelIdForTest == "panel-1",
                "对白编辑 TextBox 内按 Tab 不得切换画布选中");
            Require(text.Text == "键盘焦点中的台词", "按键模拟不得吞掉编辑器文本");

            // 正向对照：焦点在画布内（选中即聚焦）时三类快捷键仍然生效。
            view.DeleteConfirmOverride = null;
            var page = Field<Canvas>(view, "page");
            view.SelectPanelForTest(0);
            Require(Keyboard.FocusedElement == page, "画布未取得键盘焦点（正向对照无法进行）");
            Require(view.CanUndoForTest == undoBefore, "正向对照基线被污染");
            Require(Press(page, Key.Left), "画布内按方向键应微移面板");
            Require(view.CanUndoForTest != undoBefore, "画布内方向键微调必须进入撤销栈");
            Require(view.PanelRectForTest(0) != rectBefore, "画布内方向键应移动面板");
            view.SelectPanelForTest(-1);
            Require(Press(page, Key.Tab), "画布内按 Tab 应切换选中格");
            Require(view.SelectedPanelIdForTest == "panel-1", "无选中起按 Tab 应选中第一格");
            view.SelectBubbleForTest(0);
            var deletes = fixture.DialogueDeletes;
            view.DeleteConfirmOverride = () => true;
            Require(Press(page, Key.Back), "画布内按 Backspace 应触发气泡删除确认");
            await Until(() => fixture.DialogueDeletes == deletes + 1);
            view.DeleteConfirmOverride = null;
        }
        finally { owner.Close(); }
    }

    // ── helpers（NativeAssetsLoopChecks 同款）──
    private static async Task Until(Func<bool> condition)
    {
        using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(5));
        while (!condition()) await Task.Delay(5, timeout.Token);
    }

    private static async Task Settle() => await Task.Delay(30);

    private static void Require(bool condition, string message)
    {
        if (!condition) throw new Exception(message);
    }

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
        Descendants(root).OfType<Button>().Where(button => Equals(button.Content, content));

    private static T Field<T>(object value, string name) => (T)value.GetType().GetField(name, BindingFlags.Instance | BindingFlags.NonPublic)!.GetValue(value)!;

    private static string GetName(UIElement element) => System.Windows.Automation.AutomationProperties.GetName(element) ?? "";

    private static FrameworkElement? Tagged(Canvas page, string tag) =>
        page.Children.OfType<FrameworkElement>().FirstOrDefault(element => element.Tag as string == tag);

    private static void SelectCombo(ComboBox box, string tag)
    {
        foreach (var item in box.Items.OfType<ComboBoxItem>())
            if ((string?)item.Tag == tag) { box.SelectedItem = item; return; }
    }

    private static void Click(ButtonBase button) => button.RaiseEvent(new RoutedEventArgs(ButtonBase.ClickEvent));

    // 合成 PreviewKeyDown（NativeDockChecks.Press 的同款）：挂在真实视觉树（已显示
    // 的离屏窗口）里的元素上 raise，与真实按键走相同的隧道路由；返回 e.Handled。
    private static bool Press(UIElement target, Key key)
    {
        var source = PresentationSource.FromVisual(target as Visual ?? throw new Exception("按键目标不在视觉树内"))
            ?? throw new Exception("按键目标未连接到 PresentationSource（先挂进已显示的窗口）");
        var args = new KeyEventArgs(Keyboard.PrimaryDevice, source, 0, key) { RoutedEvent = UIElement.PreviewKeyDownEvent };
        target.RaiseEvent(args);
        return args.Handled;
    }

    // ToggleButton 的合成点击：RaiseEvent(ClickEvent) 不会翻转 IsChecked——
    // 真实点击是「先翻转 IsChecked 再冒泡 Click」，读 IsChecked 的处理器
    // （出血框/安全区/专注模式）必须用这个等价序列驱动。
    private static void Toggle(ToggleButton button)
    {
        button.IsChecked = !(button.IsChecked ?? false);
        button.RaiseEvent(new RoutedEventArgs(ButtonBase.ClickEvent));
    }

    private static void Layout(FrameworkElement view, int width, int height)
    {
        view.Measure(new Size(width, height));
        view.Arrange(new Rect(0, 0, width, height));
        view.UpdateLayout();
    }

    private static HttpResponseMessage Json(string text) => new(HttpStatusCode.OK) { Content = new StringContent(text) };

    // 假 HttpMessageHandler 夹具：一张 182×257 页、一格（V4）一气泡（V7 页栅栏），
    // 记录全部写入的方法/路径/载荷，并按服务端语义推进版本锚点。
    private sealed class Fixture : HttpMessageHandler
    {
        public bool CanvasKnown = true;
        public bool FailDialoguePatch;
        public bool Created;
        public string DialogueText = "旧台词";
        public int PanelVersion = 4;
        public int PageVersion = 7;
        public int StoryboardGets;
        public int GeometryPuts, DialoguePosts, DialoguePatches, DialogueDeletes, Plans;
        public JsonElement GeometryBody, DialoguePostBody, DialoguePatchBody, PlanBody;

        protected override async Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken token)
        {
            var path = request.RequestUri!.AbsolutePath;
            if (request.Method == HttpMethod.Get)
            {
                if (path.EndsWith("/chapters/ch-1/pages")) return Json(Pages);
                if (path.EndsWith("/pages/pg-1/storyboard")) { StoryboardGets++; return Json(Storyboard()); }
                if (path.EndsWith("/projects/sb-edit/chapters"))
                    return Json("""[{"id":"ch-1","title":"第一章","ordinal":1,"page_count":1}]""");
                if (path.EndsWith("/projects/sb-edit/characters"))
                    return Json("""[{"id":"c1","primary_name":"樱","aliases":[],"locked_features":[],"forbidden_changes":[],"references":[]}]""");
                if (path.EndsWith("/projects/sb-edit/outfits")) return Json("[]");
                return Json("[]");
            }
            var body = JsonSerializer.Deserialize<JsonElement>(await request.Content!.ReadAsStringAsync(token));
            if (path.EndsWith("/storyboard-geometry"))
            {
                GeometryPuts++; GeometryBody = body; PageVersion += 1;
                return Json(Storyboard());
            }
            if (path.EndsWith("/panels/panel-1/dialogues"))
            {
                DialoguePosts++; DialoguePostBody = body; Created = true; PanelVersion += 1;   // 服务端 create 会升格 panel.version
                return Json("""{"id":"dlg-2","panel_id":"panel-1","speaker_character_id":null,"target_text":"新台词","reading_order":2,"text_direction":"vertical","rewrite_forbidden":true}""");
            }
            if (path.EndsWith("/dialogues/dlg-1") && request.Method == HttpMethod.Patch)
            {
                DialoguePatches++; DialoguePatchBody = body;
                if (FailDialoguePatch)
                    return new HttpResponseMessage(HttpStatusCode.Conflict) { Content = new StringContent("{\"detail\":\"分镜格已被更新，请刷新后重试\"}") };
                DialogueText = body.Text("target_text");
                PanelVersion += 1;
                return Json("""{"id":"dlg-1","panel_id":"panel-1","speaker_character_id":"c1","target_text":"改后台词","reading_order":1,"text_direction":"vertical","rewrite_forbidden":true}""");
            }
            if (path.EndsWith("/dialogues/dlg-1") && request.Method == HttpMethod.Delete)
            {
                DialogueDeletes++;
                return Json("{}");
            }
            if (path.EndsWith("/chapters/ch-1/plan"))
            {
                Plans++; PlanBody = body;
                return Json("""{"chapter_id":"ch-1","page_count":1,"source_segment_count":4,"covered_segment_count":4,"coverage_ratio":1.0,"pages":[{"id":"pg-1","page_number":3,"panel_count":1,"storyboard_version":8}]}""");
            }
            return Json("{}");
        }

        private static string Pages => """[{"id":"pg-1","chapter_id":"ch-1","page_number":3,"panel_count":1,"storyboard_version":1,"status":"","selected_candidate_id":"","continuity_status":""}]""";

        private string Storyboard()
        {
            var canvas = CanvasKnown ? ",\"canvas\":{\"width_mm\":182,\"height_mm\":257,\"bleed_mm\":3,\"safe_mm\":5}" : "";
            var second = Created
                ? ",{\"id\":\"dlg-2\",\"panel_id\":\"panel-1\",\"speaker_character_id\":null,\"target_text\":\"新台词\",\"reading_order\":2,\"text_direction\":\"vertical\",\"rewrite_forbidden\":true}"
                : "";
            return $$"""
              {"page":{"id":"pg-1","chapter_id":"ch-1","page_number":3,"storyboard_version":{{PageVersion}}{{canvas}},"status":""},
               "panels":[{"id":"panel-1","page_id":"pg-1","reading_order":1,"version":{{PanelVersion}},
                 "bounds":{"x":0.1,"y":0.1,"width":0.5,"height":0.4},
                 "geometry":{"type":"rect","rect":{"x":0.1,"y":0.1,"width":0.5,"height":0.4},"rotation":0,"z_order":1},
                 "shot_type":"medium_close_up","camera_angle":"eye_level","bleed":false,"borderless":false,
                 "actions":{"script_action":"雨夜，两人对望"},"background":"","props":[],"sound_effects":[],
                 "characters":[],"character_presence":{},"expressions":{},"outfits":{},
                 "dialogues":[{"id":"dlg-1","panel_id":"panel-1","speaker_character_id":null,"target_text":"{{DialogueText}}","reading_order":1,"text_direction":"vertical","rewrite_forbidden":true}{{second}}]}],
               "candidate_count":2}
              """;
        }
    }
}
