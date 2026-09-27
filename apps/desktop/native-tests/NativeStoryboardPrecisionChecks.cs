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

// 分镜画布精准编辑（web 三刀全集的桌面等价面）回归检查：
// 多选与组手柄 / 对齐·分布·同尺寸 / 网格叠层与网格吸附 / 等距参考线 /
// 数字几何输入（mm+%·Enter·失焦·Esc）/ 气泡缩放·旋转·锚点·尾点 / 拟声词三手势 /
// 层序 / 几何复制粘贴 / 组等比缩放 / 保存载荷（拟声词 PATCH 先行 + z_order/rotation）/
// 冲突保草 / 多选方向键微调 / 保草稿刷新后选中态还原。
// 全部走生产提交函数（测试缝仅注入手势参数），断言归一化几何与撤销栈行为。
internal static class NativeStoryboardPrecisionChecks
{
    internal static void Run(string output)
    {
        var previous = (string)typeof(KeyValueStore).GetField("path", BindingFlags.Static | BindingFlags.NonPublic)!.GetValue(null)!;
        Directory.CreateDirectory(output);
        var prefs = Path.Combine(output, "storyboard-precision-test-prefs.json");
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
        if (failure != null) throw new Exception("Storyboard precision checks failed", failure);
    }

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
        var timeout = new DispatcherTimer { Interval = TimeSpan.FromSeconds(90) };
        timeout.Tick += (_, _) => { failure = new TimeoutException("Storyboard precision checks timed out"); frame.Continue = false; };
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
        if (failure != null) throw new Exception("Storyboard precision checks failed", failure);
    }

    private static async Task CheckAsync(string output)
    {
        var fixture = new Fixture();
        using var api = new ApiClient("http://127.0.0.1:12345", fixture);
        var view = new StoryboardView();
        view.Activate(new WorkspaceContext
        {
            Api = api, State = new WorkspaceState(), Window = null!,
            Project = new ProjectItem("sb-precision", "分镜精准编辑测试", "", 0, 0),
            NavigateSection = (_, _) => Task.CompletedTask, OpenDashboard = () => Task.CompletedTask,
        });
        try
        {
            await Until(() => view.PanelCountForTest == 4);
            await Settle();
            Require(view.BubbleCountForTest == 2, $"夹具应有 2 个气泡（1 结构化 + 1 派生），实际 {view.BubbleCountForTest}");
            Require(view.SfxCountForTest == 3, $"夹具应有 3 个拟声词节点（字符串+对象+无几何对象），实际 {view.SfxCountForTest}");

            CommandMotionChecks(view);
            MultiSelectChecks(view);
            AlignDistributeChecks(view);
            SameSizeChecks(view);
            GridChecks(view);
            EqualGapGuideChecks(view);
            await InspectorGeometryAndNudgeChecks(view);
            BubbleGeometryChecks(view);
            SfxGestureChecks(view);
            LayerOrderChecks(view);
            CopyPasteChecks(view);
            GroupScaleChecks(view);
            SfxAnchorChecks();
            await SavePayloadChecks(view, fixture);
            await ConflictKeepsDraftsChecks(view, fixture);
            await RefreshRestoresSelectionChecks(view, fixture);
            LibraryChecks(view);
        }
        finally { view.Deactivate(); }
        Console.WriteLine("PASS: storyboard precision checks (multi-select/align/distribute/same-size/grid/gap-guides/numeric-fields/bubble/sfx/layers/copy-paste/group-scale/save-payload/conflict/refresh/library) all match the web contract");
    }

    // ── 1. 多选与组手柄：≥2 选中挂组 bbox 手柄（group-handle:*），单选挂格手柄 ──
    private static void MultiSelectChecks(StoryboardView view)
    {
        Layout(view, 1400, 1000);
        Field<ToggleButton>(view, "snapButton").IsChecked = false;   // 位移断言要精确 offset，不让吸附介入
        view.SelectPanelsForTest(0, 2);
        Require(view.SelectedPanelIdsForTest.SequenceEqual(new[] { "panel-1", "panel-3" }),
            $"多选集合应按点击序记录（实际 {string.Join(",", view.SelectedPanelIdsForTest)}）");
        Require(view.GroupHandlesForTest, "两格选中应挂组缩放手柄");
        Require(view.HandleCountForTest == 8, $"组手柄应有 8 向（实际 {view.HandleCountForTest}）");

        view.SelectPanelForTest(1);
        Require(!view.GroupHandlesForTest && view.HandleCountForTest == 8, "单选矩形格应回落 8 向格手柄");
        Require(view.SelectedPanelIdsForTest.SequenceEqual(new[] { "panel-2" }), "单选后 selectedIds 应收缩为单元素");

        // 多选拖动：整组同位移（生产 PanelGesture.Compute+Commit）
        view.SelectPanelsForTest(0, 1);
        var before0 = view.PanelRectForTest(0);
        var before1 = view.PanelRectForTest(1);
        view.DragSelectionForTest(0.03, 0.02);
        Require(Near(view.PanelRectForTest(0), new Rect(before0.X + 0.03, before0.Y + 0.02, before0.Width, before0.Height)),
            $"多选拖动主格应平移 offset（实际 {view.PanelRectForTest(0)}）");
        Require(Near(view.PanelRectForTest(1), new Rect(before1.X + 0.03, before1.Y + 0.02, before1.Width, before1.Height)),
            $"多选拖动成员格应平移同一 offset（实际 {view.PanelRectForTest(1)}）");
        Require(view.CanUndoForTest, "多选拖动应入撤销栈");
        Undo(view);
        Require(Near(view.PanelRectForTest(0), before0) && Near(view.PanelRectForTest(1), before1), "撤销多选拖动应整组还原");
    }

    // ── 2. 对齐/分布（web alignRects/distributeRects 直译）──
    private static void AlignDistributeChecks(StoryboardView view)
    {
        // 基线：p1(0.02,0.05,0.20,0.20) p2(0.32,0.05,0.20,0.25) p3(0.62,0.05,0.20,0.15)
        view.SelectPanelsForTest(0, 1, 2);
        view.AlignForTest("left");
        Require(Near(view.PanelRectForTest(0).X, 0.02) && Near(view.PanelRectForTest(1).X, 0.02) && Near(view.PanelRectForTest(2).X, 0.02),
            "左对齐应向组 bbox 左边收拢");
        Undo(view);
        view.AlignForTest("right");
        Require(Near(view.PanelRectForTest(0).X, 0.62) && Near(view.PanelRectForTest(2).X, 0.62), "右对齐应向 bbox 右边收拢");
        Undo(view);
        view.AlignForTest("middleY");
        // bbox y: 0.05..0.30 → 高 0.25；p1 Y=0.05+(0.25-0.20)/2=0.075，p3 Y=0.10
        Require(Near(view.PanelRectForTest(0).Y, 0.075) && Near(view.PanelRectForTest(2).Y, 0.10),
            $"垂直居中应向 bbox 中线收拢（实际 p1.y={view.PanelRectForTest(0).Y:F4} p3.y={view.PanelRectForTest(2).Y:F4}）");
        Undo(view);

        // 分布 x：先把 p2 推离等距位（0.32→0.35），再等距——gap=(0.82-0.02-0.60)/2=0.10 → p2 回到 0.32
        view.CommitPanelRectForTest(1, new Rect(0.35, 0.05, 0.20, 0.25));
        view.SelectPanelsForTest(0, 1, 2);
        view.DistributeForTest("x");
        Require(Near(view.PanelRectForTest(1).X, 0.32),
            $"水平等距应把中间格推到 gap=0.10 处（实际 {view.PanelRectForTest(1).X:F4}）");
        Undo(view);   // 分布
        Undo(view);   // 手动推移
        Require(Near(view.PanelRectForTest(1).X, 0.32), "撤销两步后 p2 应回到基线 0.32");
        view.SelectPanelsForTest(0, 1);
        view.DistributeForTest("x");
        Require(Near(view.PanelRectForTest(0).X, 0.02) && Near(view.PanelRectForTest(1).X, 0.32),
            "两格选中时分布不得产生任何变更（web ids<3 → 空）");
    }

    // ── 0. 命令动效（最先跑：需要「元素上无任何先验动画」的干净上下文）──
    // 离散命令走 170ms 动画叠层（对齐 web --cmd-dur）：模型立即到目标位，
    // 基础值停在旧位证明叠层未瞬写；手势直写瞬写接管；层序透明度脉冲；
    // 参考线松手转淡出池。
    private static void CommandMotionChecks(StoryboardView view)
    {
        Layout(view, 1400, 1000);
        // 层序脉冲（对齐 web z-flash）：此处 p1 元素上还没有任何动画，
        // HasAnimatedProperties 为真只能来自 Opacity 脉冲。
        view.SelectPanelForTest(0);
        view.ZOrderForTest("top");
        Require(view.PanelAnimatingForTest(0), "层序命令应触发透明度脉冲（z-flash 对齐）");
        Require(view.PanelZOrderForTest(0) == 5, "置顶应为 maxZ+1=5");
        Undo(view);   // 回放 PanelZChange 同样脉冲；恢复档位

        // 对齐命令：模型立即到目标位，基础值停在旧位 → 动画叠层未瞬写。
        view.SelectPanelsForTest(0, 1);
        var beforeBase = view.PanelElementBaseLeftForTest(1)!.Value;
        view.AlignForTest("left");
        Require(Near(view.PanelRectForTest(1).X, 0.02), "对齐后模型应立即到目标位");
        Require(Math.Abs(view.PanelElementBaseLeftForTest(1)!.Value - beforeBase) < 1e-9,
            "动画叠层不应改写基础值（基础值应仍是旧位）");
        Undo(view);
        Require(Near(view.PanelRectForTest(1).X, 0.32), "撤销后模型应立即回还原位");

        // 手势瞬写接管：拖动后动画被摘除，元素位置=模型位置。
        view.DragSelectionForTest(0.01, 0);
        var dragged = view.PanelRectForTest(1);
        Require(Math.Abs(view.PanelElementBaseLeftForTest(1)!.Value - dragged.X * view.PageWidthForTest) < 1e-6 &&
            Math.Abs(view.PanelElementLeftForTest(1)!.Value - dragged.X * view.PageWidthForTest) < 1e-6,
            "手势直写应瞬写接管（清动画+直接写值）");
        Undo(view);

        // 参考线：拖动吸附出活动线，松手转淡出池（web .leaving 对齐）。
        view.SelectPanelForTest(0);
        Field<ToggleButton>(view, "snapButton").IsChecked = true;
        view.DragSelectionForTest(0.10, 0, keepGuides: true);
        Require(view.ActiveGuideCountForTest() > 0, "吸附应产生活动参考线");
        view.DragSelectionForTest(0, 0);
        Require(view.ActiveGuideCountForTest() == 0 && view.FadingGuideCountForTest() > 0,
            $"松手后参考线应转淡出池（活动 {view.ActiveGuideCountForTest()} 淡出 {view.FadingGuideCountForTest()}）");
        Undo(view);   // 还原 +0.10 拖动
        Field<ToggleButton>(view, "snapButton").IsChecked = false;
    }

    // ── 3. 同尺寸（web sameSizeRects：首个选中格为参照）──
    private static void SameSizeChecks(StoryboardView view)
    {
        view.SelectPanelsForTest(0, 1);   // 参照 = p1（0.20×0.20）
        view.SameSizeForTest("height");
        Require(Near(view.PanelRectForTest(1).Height, 0.20) && Near(view.PanelRectForTest(1).Width, 0.20),
            $"同高只应改 p2 高度（实际 {view.PanelRectForTest(1)}）");
        Undo(view);
        view.SameSizeForTest("size");
        Require(Near(view.PanelRectForTest(1), new Rect(0.32, 0.05, 0.20, 0.20)), "同大小应让 p2 取 p1 的宽高");
        Undo(view);
        // 顺序敏感：先选 p2 再选 p1 → 参照是 p2（0.20×0.25）
        view.SelectPanelsForTest(1, 0);
        view.SameSizeForTest("height");
        Require(Near(view.PanelRectForTest(0).Height, 0.25), "参照格应跟随首个选中（p2 高 0.25）");
        Undo(view);
    }

    // ── 4. 网格叠层 + 网格吸附（对齐 web gridLinesFor + snap 目标合并）──
    private static void GridChecks(StoryboardView view)
    {
        var snap = Field<ToggleButton>(view, "snapButton");
        snap.IsChecked = true;
        view.SetGridForTest(true, 10);
        // 182×257mm：竖线 10..180 共 18 条，横线 10..250 共 25 条
        Require(view.GridLineCountForTest == 43, $"10mm 网格应有 18+25=43 条叠层线（实际 {view.GridLineCountForTest}）");
        view.SetGridForTest(false);
        Require(view.GridLineCountForTest == 0, "关闭网格后叠层线应清空");

        // 无网格时同一拖拽不进吸附（最近边距 0.028 > 阈值 6/640≈0.0094）
        view.SelectPanelForTest(0);
        view.DragSelectionForTest(0.028, 0);
        Require(Near(view.PanelRectForTest(0).X, 0.048), $"无网格/无命中时不应吸附（实际 {view.PanelRectForTest(0).X:F4}）");
        Undo(view);

        // 开 5mm 网格：拖到 x=0.025 时左边距对 5mm 线（5/182≈0.0275，间距 0.0025）
        // 是全局最近对 → 吸附上线（web computeMoveSnap 取所有边×所有目标的最小 delta）。
        view.SetGridForTest(true, 5);
        view.DragSelectionForTest(0.005, 0);
        Require(Near(view.PanelRectForTest(0).X, 5.0 / 182.0),
            $"网格开启应吸附到最近格线（实际 {view.PanelRectForTest(0).X:F4}，期望 {5.0 / 182.0:F4}）");
        Undo(view);
        view.SetGridForTest(false);
    }

    // ── 5. 等距智能参考线（web equalGapSnap）：单格拖到两邻格正中触发 gap 吸附 ──
    private static void EqualGapGuideChecks(StoryboardView view)
    {
        var snap = Field<ToggleButton>(view, "snapButton");
        snap.IsChecked = true;
        // p1.right=0.22 与 p3.x=0.62 夹 p2（宽 0.20）：等距位 x=0.32，拖到 0.328 应吸回
        view.SelectPanelForTest(1);
        view.DragSelectionForTest(0.008, 0, keepGuides: true);
        Require(Near(view.PanelRectForTest(1).X, 0.32),
            $"等距吸附应把格推到两邻格正中（实际 {view.PanelRectForTest(1).X:F4}）");
        Require(view.GapGuideCountForTest >= 2, $"等距吸附应画 gap 参考线（实际 {view.GapGuideCountForTest} 条）");
        // 回到等距位后矩形未变 → 本手势不入撤销栈
        view.DragSelectionForTest(0, 0);   // 清参考线不走撤销路径
        Require(!view.CanUndoForTest, "等距吸附回原点不得产生撤销条目");
    }

    // ── 6. 检查器数字输入 + 画布方向键（需要已显示窗口供给 PresentationSource）──
    private static async Task InspectorGeometryAndNudgeChecks(StoryboardView view)
    {
        var owner = new Window
        {
            ShowInTaskbar = false, ShowActivated = false,
            WindowStartupLocation = WindowStartupLocation.Manual,
            Left = Math.Max(8, SystemParameters.WorkArea.Right - 1120),
            Top = Math.Max(8, SystemParameters.WorkArea.Bottom - 780),
            Width = 1100, Height = 760,
            Content = view,
        };
        owner.Show();
        try
        {
            Layout(view, 1100, 760);
            view.SelectPanelForTest(0);
            await Settle();
            var inspector = Field<StackPanel>(view, "inspector");
            // 提交/撤销都会重建检查器（RenderInspector）——boxes 引用随重建失效，
            // 每次断言前重新查询（与 web 测试里 rerender 后重新 getByRole 同款）。
            List<TextBox> Boxes() =>
                Descendants(inspector).OfType<TextBox>().Where(b => b.Tag as string == "几何").ToList();
            var boxes = Boxes();
            Require(boxes.Count == 4, $"检查器应有 X/Y/宽/高 四个几何输入（实际 {boxes.Count}）");
            Require(boxes[0].Text == "3.6", $"X 初值应为 0.02×182=3.6mm（实际 {boxes[0].Text}）");

            // mm 失焦提交（web commitRect → blur 提交同款路径）
            boxes[0].Text = "18.2";
            boxes[0].RaiseEvent(new RoutedEventArgs(UIElement.LostFocusEvent));
            await Settle();
            Require(Near(view.PanelRectForTest(0).X, 0.1), $"mm 输入 18.2 应落位 x=0.1（实际 {view.PanelRectForTest(0).X:F4}）");
            Require(view.CanUndoForTest, "数字输入应入撤销栈");
            Undo(view);
            Require(Near(view.PanelRectForTest(0).X, 0.02), "撤销数字输入应还原");

            // % 单位切换（重建后检查器回 mm 默认，先切单位再驱动 Esc/Enter）
            var pct = Descendants(inspector).OfType<ToggleButton>().Single(t => Equals(t.Content, "%"));
            Toggle(pct);
            await Settle();
            boxes = Boxes();
            Require(boxes[0].Text == "2.0", $"切 % 后 X 应重填 0.02×100=2.0（实际 {boxes[0].Text}）");

            // Esc 放弃草稿：改动不落位并回种当前值（先于 Enter 提交，避免中间重建）
            boxes[1].Text = "88";
            KeyDownOn(boxes[1], Key.Escape);
            await Settle();
            Require(Near(view.PanelRectForTest(0).Y, 0.05), "Esc 不得提交输入");
            Require(boxes[1].Text == "5.0", $"Esc 后应回种当前值 5.0%（实际 {boxes[1].Text}）");

            // % 输入 Enter 提交
            boxes[0].Text = "10";
            KeyDownOn(boxes[0], Key.Enter);
            await Settle();
            Require(Near(view.PanelRectForTest(0).X, 0.1), $"% 输入 10 应落位 x=0.1（实际 {view.PanelRectForTest(0).X:F4}）");
            Undo(view);
            Require(Near(view.PanelRectForTest(0).X, 0.02), "撤销 % 输入应还原");

            // 多选方向键微调（一条命令、整组同位移）
            view.SelectPanelsForTest(0, 1);
            var page = Field<Canvas>(view, "page");
            var x0 = view.PanelRectForTest(0).X;
            var x1 = view.PanelRectForTest(1).X;
            Require(!view.CanUndoForTest, "微调前撤销栈应为空（前序检查已全部还原）");
            Require(Press(page, Key.Right), "画布内方向键应被处理");
            var step = view.PanelRectForTest(0).X - x0;
            Require(step > 0, "方向键应右移选中格");
            Require(Near(view.PanelRectForTest(1).X, x1 + step), "多选微调应整组同位移");
            Require(view.CanUndoForTest, "多选微调应入撤销栈");
            Undo(view);
            Require(Near(view.PanelRectForTest(0).X, x0) && Near(view.PanelRectForTest(1).X, x1), "撤销多选微调应整组还原");
        }
        finally { owner.Close(); }
    }

    // ── 7. 气泡：缩放/旋转/锚点尾点手柄集 + 数字几何区 + 位移夹宿主格 ──
    private static void BubbleGeometryChecks(StoryboardView view)
    {
        Layout(view, 1400, 1000);
        view.SelectBubbleForTest(0);   // 结构化气泡（带 anchor+tail_target）
        Require(view.SelectedBubbleIdForTest == "dlg-1", "应选中结构化气泡");
        Require(view.SelectedPanelIdForTest == "panel-1", "选中气泡应把格选中收缩为宿主格");
        Require(view.BubbleHandleCountForTest == 7, $"带锚点/尾点的气泡应有 4 缩放+旋转+锚点+尾点=7 个手柄（实际 {view.BubbleHandleCountForTest}）");

        view.SelectBubbleForTest(1);   // 派生气泡：无 anchor/tail_target → 只有 4+1
        Require(view.BubbleHandleCountForTest == 5, $"派生气泡应只有 5 个手柄（实际 {view.BubbleHandleCountForTest}）");
        view.SelectBubbleForTest(0);

        // 位移夹宿主格（对齐 web bubbles never leave their panel 的拖拽内 clamp）
        view.MoveBubbleForTest(0, 0.5, 0);
        var geom = view.BubbleGeometryForTest(0)!.Value;
        Require(Near(geom.Rect.X, 0.12), $"气泡右移应夹进宿主格右界（实际 {geom.Rect.X:F4}，期望 0.12）");
        Undo(view);
        Require(Near(view.BubbleGeometryForTest(0)!.Value.Rect.X, 0.04), "撤销气泡拖动应还原");

        // 手势旋转（NormalizeRotation 域）与字段旋转（±360 clamp + round4）分路径验证
        view.RotateBubbleForTest(0, 47.3);
        Require(Near(view.BubbleGeometryForTest(0)!.Value.Rotation, 47.3), "气泡旋转应落位");
        Undo(view);
        view.CommitBubbleRotationForTest(0, 400);
        Require(Near(view.BubbleGeometryForTest(0)!.Value.Rotation, 360), "字段旋转应 clamp 到 +360");
        Undo(view);
        view.CommitBubbleRotationForTest(0, -400);
        Require(Near(view.BubbleGeometryForTest(0)!.Value.Rotation, -360), "字段旋转应 clamp 到 -360");
        Undo(view);
        Require(Near(view.BubbleGeometryForTest(0)!.Value.Rotation, 5), "撤销应还原服务端角度 5");

        // 气泡数字几何（CommitBubbleRectField：ClampRect 到页界，MinBubble 下限）
        view.CommitBubbleRectForTest(0, new Rect(0.05, 0.06, 0.10, 0.06));
        Require(Near(view.BubbleGeometryForTest(0)!.Value.Rect, new Rect(0.05, 0.06, 0.10, 0.06)), "气泡几何字段应落位");
        Undo(view);
        view.CommitBubbleRectForTest(0, new Rect(0.9, 0.9, 0.5, 0.5));   // 溢出页界应回挪
        var clamped = view.BubbleGeometryForTest(0)!.Value.Rect;
        Require(clamped.Right <= 1.0001 && clamped.Bottom <= 1.0001, $"气泡几何应夹回页内（实际 {clamped}）");
        Undo(view);
    }

    // ── 8. 拟声词三手势 + 仅动过的条目带草稿 ──
    private static void SfxGestureChecks(StoryboardView view)
    {
        view.SelectSfxForTest(0);   // 字符串条目「咚」：落位为派生默认位
        Require(view.SelectedSfxTextForTest == "咚", "应选中拟声词「咚」");
        Require(view.SelectedPanelIdForTest == "panel-1", "选中拟声词应把格选中收缩为宿主格");
        Require(view.HandleCountForTest == 0, "拟声词选中不得残留面板手柄");
        var before = view.SfxGeometryForTest(0)!.Value;
        view.MoveSfxForTest(0, 0.05, -0.05);
        var moved = view.SfxGeometryForTest(0)!.Value;
        Require(Near(moved.X, before.X + 0.05) && Near(moved.Y, before.Y - 0.05),
            $"拟声词移动应平移归一化中心（实际 {moved.X:F4},{moved.Y:F4}）");
        Require(view.CanUndoForTest, "拟声词移动应入撤销栈");
        Undo(view);

        view.RotateSfxForTest(1, 45);
        Require(Near(view.SfxGeometryForTest(1)!.Value.Rotation, 45), "拟声词旋转应落位");
        Undo(view);
        Require(Near(view.SfxGeometryForTest(1)!.Value.Rotation, 30), "撤销应还原存储角度 30");

        view.ScaleSfxForTest(2, 0.02);
        Require(Near(view.SfxGeometryForTest(2)!.Value.Size, 0.07), $"拟声词缩放应落位（实际 {view.SfxGeometryForTest(2)!.Value.Size:F4}）");
        Undo(view);
    }

    // ── 9. 层序（web zOrderChanges 直译：交换/up·down、maxZ+1/top、minZ-1 或重排/bottom）──
    private static void LayerOrderChecks(StoryboardView view)
    {
        view.SelectPanelForTest(0);   // z=1
        view.ZOrderForTest("up");
        Require(view.PanelZOrderForTest(0) == 2 && view.PanelZOrderForTest(1) == 1, "上移一层应与最近邻交换");
        Undo(view);
        view.ZOrderForTest("top");
        Require(view.PanelZOrderForTest(0) == 5, "置顶应为 maxZ+1=5");
        Undo(view);
        Require(view.PanelZOrderForTest(0) == 1, "撤销层序应还原");
        // bottom 的 z≥1 重排兜底：p4(z=4) 置底 → 其余各升一档、p4 落 1
        view.SelectPanelForTest(3);
        view.ZOrderForTest("bottom");
        Require(view.PanelZOrderForTest(3) == 1 && view.PanelZOrderForTest(0) == 2 && view.PanelZOrderForTest(2) == 4,
            $"置底遇 minZ=1 应整体重排（实际 p1={view.PanelZOrderForTest(0)} p3={view.PanelZOrderForTest(2)} p4={view.PanelZOrderForTest(3)}）");
        Undo(view);
        Require(view.PanelZOrderForTest(3) == 4 && view.PanelZOrderForTest(0) == 1, "撤销重排应还原全部档位");
    }

    // ── 10. 几何复制粘贴（Ctrl+C/V 的语义核心，跨型别降维对齐 web）──
    private static void CopyPasteChecks(StoryboardView view)
    {
        view.SelectPanelForTest(0);
        view.CopyGeometryForTest();
        view.SelectPanelForTest(2);
        Require(view.PasteGeometryForTest(), "面板矩形应可粘贴到另一格");
        Require(Near(view.PanelRectForTest(2), view.PanelRectForTest(0)), "粘贴后 p3 应复制 p1 的矩形");
        Undo(view);

        // 气泡几何粘贴到面板：降维为 rect（web CopiedBubble→面板分支同款）
        view.SelectBubbleForTest(0);
        view.CopyGeometryForTest();
        view.SelectPanelForTest(2);
        view.PasteGeometryForTest();
        Require(Near(view.PanelRectForTest(2), new Rect(0.04, 0.07, 0.10, 0.06)), "气泡矩形应可粘贴到面板");
        Undo(view);

        // 面板矩形粘贴到拟声词：降维为中心点（web Rect→sfx 分支同款）
        view.SelectPanelForTest(0);
        view.CopyGeometryForTest();
        view.SelectSfxForTest(0);
        view.PasteGeometryForTest();
        var sfx = view.SfxGeometryForTest(0)!.Value;
        Require(Near(sfx.X, 0.12) && Near(sfx.Y, 0.15), $"面板矩形粘贴到拟声词应取中心点（实际 {sfx.X:F4},{sfx.Y:F4}）");
        Undo(view);
    }

    // ── 11. 组等比缩放（web scaleRectWithBounds：成员相对组 bbox 等比映射）──
    private static void GroupScaleChecks(StoryboardView view)
    {
        view.SelectPanelsForTest(0, 1, 2);
        // bbox (0.02,0.05)-(0.82,0.30)：se 拖到 (0.90,0.60) → scaleX=1.1 scaleY=2.2
        view.GroupResizeForTest("se", new Point(0.90, 0.60));
        Require(Near(view.PanelRectForTest(0), new Rect(0.02, 0.05, 0.22, 0.44)),
            $"组缩放 p1 应等比映射（实际 {view.PanelRectForTest(0)}）");
        Require(Near(view.PanelRectForTest(2), new Rect(0.68, 0.05, 0.22, 0.33)),
            $"组缩放 p3 应等比映射（实际 {view.PanelRectForTest(2)}）");
        Require(view.CanUndoForTest, "组缩放应入撤销栈");
        Undo(view);
        Require(Near(view.PanelRectForTest(2), new Rect(0.62, 0.05, 0.20, 0.15)), "撤销组缩放应整组还原");
    }

    // ── 12. PanelEditDialog 拟声词锚定（web anchorSoundEffects 直译，离线驱动）──
    private static void SfxAnchorChecks()
    {
        var previous = JsonDocument.Parse("""["砰",{"text":"轰","x":0.3,"y":0.2,"rotation":30,"size":0.06},{"text":"咚","x":0.5,"y":0.5}]""")
            .RootElement.EnumerateArray().Select(e => e.Clone()).ToList();
        // 改名（砰→哇）按同索引回退：但「砰」是字符串不占对象槽 → index0 无对象可借 → 裸 {text}
        var renamed = PanelEditDialog.AnchorSoundEffects(["哇", "轰", "咚"], previous);
        Require(renamed[0].Count == 1 && renamed[0]["text"]!.ToString() == "哇", "字符串条目的改名位应落成裸 {text}");
        Require(renamed[1]["x"]!.ToString() == "0.3" && renamed[1]["rotation"]!.ToString() == "30", "未变对象条目应原样保留几何");
        Require(renamed[2]["x"]!.ToString() == "0.5", "第二对象条目应原样保留");
        // 换位改名：轰→锵 落同索引几何（positional fallback）
        var swapped = PanelEditDialog.AnchorSoundEffects(["砰", "锵", "咚"], previous);
        Require(swapped[1]["x"]!.ToString() == "0.3" && swapped[1]["text"]!.ToString() == "锵", "改名条目应沿用同索引几何");
        // 新增条目无槽可借 → 裸 {text}
        var appended = PanelEditDialog.AnchorSoundEffects(["砰", "轰", "咚", "新"], previous);
        Require(appended[3].Count == 1 && appended[3]["text"]!.ToString() == "新", "新增条目应落成裸 {text}");
        // 重复文本从左到右消费，每个旧对象只用一次
        var dupes = PanelEditDialog.AnchorSoundEffects(["轰", "轰"], previous);
        Require(dupes[0]["x"]!.ToString() == "0.3" && dupes[1].Count == 1, "重复文本只应消耗一个匹配对象");
    }

    // ── 13. 保存载荷：拟声词 PATCH 先行 + z_order/rotation/anchor 透传 ──
    private static async Task SavePayloadChecks(StoryboardView view, Fixture fixture)
    {
        // 造脏：p1 挪到 x=0.04 + 拟声词「咚」移动（字符串条目首次落几何）
        view.CommitPanelRectForTest(0, new Rect(0.04, 0.05, 0.20, 0.20));
        view.MoveSfxForTest(0, 0.02, 0.01);
        var puts = fixture.GeometryPuts;
        var patches = fixture.PanelPatchCalls.Count;
        Click(Buttons(view, "保存本页").First());
        await Until(() => fixture.GeometryPuts > puts);
        await Settle();

        Require(fixture.PanelPatchCalls.Count == patches + 1, "保存应先对含移动拟声词的格发 PATCH");
        var (patchPath, patchBody) = fixture.PanelPatchCalls[^1];
        Require(patchPath.EndsWith("/panels/panel-1"), $"PATCH 应落在宿主格（实际 {patchPath}）");
        Require(patchBody.Number("version") == 4, "PATCH 应携带面板版本锚点 4");
        var sfxArray = patchBody.Array("sound_effects");
        Require(sfxArray.Count == 3, $"PATCH 应携带完整 sound_effects 数组（实际 {sfxArray.Count} 条）");
        Require(Near(sfxArray[0].Decimal("x"), 0.10) && Near(sfxArray[0].Decimal("y"), 0.12) && sfxArray[0].Text("text") == "咚",
            $"动过的字符串条目应升格带几何的对象（实际 {sfxArray[0]}）");
        Require(Near(sfxArray[1].Decimal("x"), 0.3) && Near(sfxArray[1].Decimal("rotation"), 30),
            "未动对象条目应保持存储几何不被覆写");
        Require(sfxArray[2].Text("text") == "旧" && sfxArray[2].Element("x").ValueKind != JsonValueKind.Number,
            "未动无几何对象不得凭空虚增坐标");

        var putBody = fixture.GeometryBody;
        var panelsPayload = putBody.Array("panels");
        var p1 = panelsPayload[0];
        Require(Near(p1.Element("bounds").Decimal("x"), 0.04), "PUT bounds 应携带新矩形");
        Require(p1.Element("geometry").Decimal("z_order") == 1, "PUT geometry 应携带 z_order");
        Require(p1.Element("geometry").Decimal("rotation") == 0, "PUT geometry 应携带 rotation");
        var bubblesPayload = putBody.Array("dialogues");
        var bubble = bubblesPayload[0].Element("bubble");
        Require(Near(bubble.Element("rect").Decimal("x"), 0.04) && Near(bubble.Decimal("rotation"), 5),
            "未动结构化气泡应原样透传 rect/rotation");
        Require(Near(bubble.Element("anchor").Decimal("x"), 0.09) && bubble.Element("tail_target").ValueKind == JsonValueKind.Object,
            "anchor/tail_target 应随气泡透传");
        // Element() 把 Null 值折成 default，这里必须直读 TryGetProperty
        Require(bubblesPayload[1].Text("dialogue_id") == "dlg-2"
            && bubblesPayload[1].TryGetProperty("bubble", out var rawBubble)
            && rawBubble.ValueKind == JsonValueKind.Null,
            $"未定位的派生气泡应提交 null（实际 id={bubblesPayload[1].Text("dialogue_id")}）");
        // 顺序断言：PATCH 先于 PUT（fixture.CallOrder 记录全部写入）
        var patchAt = fixture.CallOrder.IndexOf("PATCH");
        var putAt = fixture.CallOrder.IndexOf("PUT");
        Require(patchAt >= 0 && putAt > patchAt, "拟声词 PATCH 必须先于整页 PUT");
        Require(!view.CanUndoForTest, "保存成功应清空撤销栈");
    }

    // ── 14. 409 冲突：草稿保留 + 同 request_id 幂等重放 + 冲突条 ──
    private static async Task ConflictKeepsDraftsChecks(StoryboardView view, Fixture fixture)
    {
        view.CommitPanelRectForTest(1, new Rect(0.36, 0.05, 0.20, 0.25));
        fixture.FailPut = true;
        var puts = fixture.GeometryPuts;
        Click(Buttons(view, "保存本页").First());
        await Until(() => fixture.GeometryPuts > puts);
        await Settle();
        Require(view.CanUndoForTest, "409 后撤销栈必须保留草稿");
        Require(Near(view.PanelRectForTest(1).X, 0.36), "409 后画布草稿应保持");
        var conflictBar = Field<Border>(view, "conflictBar");
        Require(conflictBar.Visibility == Visibility.Visible, "409 应亮出冲突条");
        var firstRequest = fixture.GeometryBody.Text("request_id");

        fixture.FailPut = false;
        puts = fixture.GeometryPuts;
        Click(Buttons(view, "保存本页").First());
        await Until(() => fixture.GeometryPuts > puts);
        await Settle();
        Require(fixture.GeometryBody.Text("request_id") == firstRequest, "同草稿重试必须重放同一 request_id（幂等）");
        Require(!view.CanUndoForTest, "重试成功应清空撤销栈");
        Require(conflictBar.Visibility == Visibility.Collapsed, "重试成功的整页重载应解除冲突条");
    }

    // ── 15. 保草稿刷新：多选选中态与拟声词草稿跨重载还原 ──
    private static async Task RefreshRestoresSelectionChecks(StoryboardView view, Fixture fixture)
    {
        // 先动拟声词再多选：SelectSfx 会把选中收缩为宿主格，反向序保住多选集合；
        // 拟声词草稿按 (PanelId,Index) 快照还原，选中态按 selectedIds 还原。
        view.MoveSfxForTest(0, 0.01, 0.01);
        var moved = view.SfxGeometryForTest(0)!.Value;
        view.SelectPanelsForTest(0, 1);
        var reads = fixture.StoryboardGets;
        await view.RefreshAsync();
        await Until(() => fixture.StoryboardGets > reads);
        await Settle();
        Require(view.SelectedPanelIdsForTest.SequenceEqual(new[] { "panel-1", "panel-2" }),
            $"保草稿刷新应还原多选集合（实际 {string.Join(",", view.SelectedPanelIdsForTest)}）");
        var kept = view.SfxGeometryForTest(0)!.Value;
        Require(Near(kept.X, moved.X) && Near(kept.Y, moved.Y), "保草稿刷新应还原拟声词草稿");
        Require(view.CanUndoForTest, "保草稿刷新应保留撤销栈");
    }

    // ── 16. 功能库（V02-33）：布局模板 / 版本快照+幽灵对比 / 制作回放 ──
    // 与 web storyboard-history 同一契约：模板按 reading_order 配对 cells、气泡
    // 随宿主仿射映射、快照恢复走撤销栈、回放时间线沿命令历史录制且播放期只读。
    private static void LibraryChecks(StoryboardView view)
    {
        // 快照：捕获 → 改动 → 恢复（普通命令，可撤销）
        var baseRect0 = view.PanelRectForTest(0);
        view.SaveSnapshotForTest("基准版式");
        Require(view.SnapshotCountForTest == 1 && view.SnapshotNameForTest(0) == "基准版式",
            "快照应落本地存储并可读回");
        view.CommitPanelRectForTest(0, new Rect(0.30, 0.30, 0.20, 0.20));
        view.RestoreSnapshotForTest(0);
        Require(Near(view.PanelRectForTest(0), baseRect0),
            $"恢复快照应还原捕获态几何（实际 {view.PanelRectForTest(0)}）");
        Undo(view);
        Require(Near(view.PanelRectForTest(0).X, 0.30), "快照恢复本身应可撤销");
        Undo(view);   // 撤销手动改动
        Require(Near(view.PanelRectForTest(0), baseRect0), "两级撤销应回到基线");

        // 对比幽灵：快照的 4 格 + 2 气泡虚线轮廓叠在当前画布上
        view.ToggleCompareForTest(0);
        Require(view.GhostCountForTest == 6,
            $"对比快照应叠加 4 格 + 2 气泡的幽灵轮廓（实际 {view.GhostCountForTest}）");
        view.ToggleCompareForTest(0);
        Require(view.GhostCountForTest == 0, "取消对比应清空幽灵层");

        // 第二份快照追加、删除只移除目标
        view.SaveSnapshotForTest("第二版");
        Require(view.SnapshotCountForTest == 2, "第二份快照应追加存储");
        view.DeleteSnapshotForTest(1);
        Require(view.SnapshotCountForTest == 1 && view.SnapshotNameForTest(0) == "基准版式",
            "删除快照应只移除目标条目");

        // A/B 版式对比：两份快照各标一侧（A 紫 / B 青），双色幽灵同屏；
        // 采用其一为普通撤销命令，采用后退出对比。
        view.SaveSnapshotForTest("方案 A");
        view.CommitPanelRectForTest(0, new Rect(0.30, 0.10, 0.20, 0.20));
        view.SaveSnapshotForTest("方案 B");
        view.CommitPanelRectForTest(0, new Rect(0.45, 0.45, 0.20, 0.20));   // 当前态偏离两侧
        view.MarkCompareForTest(1, sideA: true);
        view.MarkCompareForTest(2, sideA: false);
        Require(view.GhostTaggedCountForTest("ghost:a") == 6 &&
                view.GhostTaggedCountForTest("ghost:b") == 6,
            $"A/B 对比应各叠 6 个幽灵（A {view.GhostTaggedCountForTest("ghost:a")} / B {view.GhostTaggedCountForTest("ghost:b")}）");
        Require(view.CompareBarOpenForTest, "两侧标记齐全后应出现 A/B 选择条");
        view.AdoptCompareForTest(false);   // 采用 B
        Require(!view.CompareBarOpenForTest && view.GhostCountForTest == 0,
            "采用后应退出 A/B 对比并清空幽灵层");
        Require(Near(view.PanelRectForTest(0), new Rect(0.30, 0.10, 0.20, 0.20)),
            $"采用 B 应落到 B 快照态（实际 {view.PanelRectForTest(0)}）");
        Undo(view);
        Require(Near(view.PanelRectForTest(0).X, 0.45), "采用快照应可撤销");
        // 单侧标记：幽灵叠层在但选择条不出；退出清空。
        view.MarkCompareForTest(1, sideA: true);
        Require(view.GhostTaggedCountForTest("ghost:a") == 6 && !view.CompareBarOpenForTest,
            "只有单侧标记时应只叠幽灵不出选择条");
        view.ExitCompareABForTest();
        Require(view.GhostCountForTest == 0, "退出对比应清空幽灵层");
        view.DeleteSnapshotForTest(2);
        view.DeleteSnapshotForTest(1);
        Require(view.SnapshotCountForTest == 1, "A/B 用快照删除后应回到单份");
        Undo(view);   // 回退 0.45 探测提交
        Undo(view);   // 回退 0.30 提交，panel0 回到基准

        // 模板：内置预设按 reading_order 落位（preset-two-column = 索引 4），
        // 气泡随宿主格做同仿射映射，多余格不动；套用可撤销。
        var panel0Before = view.PanelRectForTest(0);
        var panel2Before = view.PanelRectForTest(2);
        var bubble0Before = view.BubbleGeometryForTest(0)!.Value;
        view.ApplyTemplateForTest(4);
        Require(Near(view.PanelRectForTest(0), new Rect(0.06, 0.06, 0.42, 0.88)) &&
            Near(view.PanelRectForTest(1), new Rect(0.52, 0.06, 0.42, 0.88)),
            $"左右对开应把前两格落入预设格位（实际 {view.PanelRectForTest(0)} / {view.PanelRectForTest(1)}）");
        Require(Near(view.PanelRectForTest(2), panel2Before), "超出模板格数的格不应被改动");
        var mapped = view.BubbleGeometryForTest(0)!.Value.Rect;
        var expectX = 0.06 + (bubble0Before.Rect.X - panel0Before.X) / panel0Before.Width * 0.42;
        var expectY = 0.06 + (bubble0Before.Rect.Y - panel0Before.Y) / panel0Before.Height * 0.88;
        Require(Near(mapped.X, expectX) && Near(mapped.Y, expectY),
            $"宿主格内的气泡应随模板做同仿射映射（实际 {mapped}，期望 x≈{expectX:F4} y≈{expectY:F4}）");
        Undo(view);
        Require(Near(view.PanelRectForTest(0), panel0Before), "模板套用应可整体撤销");

        // 自定义模板：本页矩形格按 reading_order 收成 cells，打乱后可套回
        view.SaveTemplateForTest("我的版式");
        Require(view.TemplateCountForTest == 1, "自定义模板应落本地存储");
        view.CommitPanelRectForTest(0, new Rect(0.40, 0.40, 0.20, 0.20));
        view.ApplyTemplateForTest(6);   // 内置 6 + 自定义 1 → 索引 6
        Require(Near(view.PanelRectForTest(0), panel0Before),
            $"自定义模板应把格子套回保存时布局（实际 {view.PanelRectForTest(0)}）");
        Undo(view);   // 模板套用
        Undo(view);   // 打乱

        // 回放：帧 0 是页面初始版式（夹具基线），末帧是实时态；播放期只读。
        view.CommitPanelRectForTest(1, new Rect(0.40, 0.40, 0.20, 0.20));
        var frames = view.TimelineCountForTest;
        Require(frames >= 2, $"回放至少需要初始帧+命令帧（实际 {frames}）");
        var liveRect = view.PanelRectForTest(1);
        view.ToggleReplayForTest();
        Require(view.ReplayOpenForTest, "应进入回放模式");
        Require(view.ReplayIndexForTest == 0, "进入回放应落在首帧");
        Require(Near(view.PanelRectForTest(0), new Rect(0.02, 0.05, 0.20, 0.20)),
            $"首帧应是页面初始版式（实际 {view.PanelRectForTest(0)}）");
        view.ReplaySeekForTest(frames - 1);
        Require(view.ReplayIndexForTest == frames - 1, "步进应落在末帧");
        Require(view.ReplayLabelForTest == "输入几何", $"末帧标签应来自命令（实际 {view.ReplayLabelForTest}）");
        Require(Near(view.PanelRectForTest(1), liveRect), "末帧应等于开回放前的实时态");
        Require(view.TimelineCountForTest == frames, "回放写回节点不得新增时间线帧");

        // 只读：回放中提交/撤销/键盘全拦
        view.CommitPanelRectForTest(0, new Rect(0.50, 0.50, 0.20, 0.20));
        Require(view.PanelRectForTest(0).X < 0.50, "回放中几何提交应被拦截");
        Undo(view);
        Require(Near(view.PanelRectForTest(1), liveRect), "回放中撤销应被拦截");

        view.CloseReplayForTest();
        Require(!view.ReplayOpenForTest, "退出应离开回放");
        Require(Near(view.PanelRectForTest(1), liveRect), "退出回放应恢复实时态而非停在帧上");
        Undo(view);   // 退出后撤销栈照常：回退那条探测提交
        Require(Near(view.PanelRectForTest(1), new Rect(0.32, 0.05, 0.20, 0.25)),
            $"退出后撤销应回退实时命令（实际 {view.PanelRectForTest(1)}）");
    }

    // ── helpers（NativeStoryboardEditChecks 同款最小集）──
    private static void Undo(StoryboardView view) => Click(Buttons(view, "撤销").First());
    private static bool Near(double actual, double expected) => Math.Abs(actual - expected) < 5e-4;
    private static bool Near(Rect actual, Rect expected) =>
        Near(actual.X, expected.X) && Near(actual.Y, expected.Y) && Near(actual.Width, expected.Width) && Near(actual.Height, expected.Height);

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
    private static void Click(ButtonBase button) => button.RaiseEvent(new RoutedEventArgs(ButtonBase.ClickEvent));

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

    private static bool Press(UIElement target, Key key)
    {
        var source = PresentationSource.FromVisual(target as Visual ?? throw new Exception("按键目标不在视觉树内"))
            ?? throw new Exception("按键目标未连接到 PresentationSource");
        var args = new KeyEventArgs(Keyboard.PrimaryDevice, source, 0, key) { RoutedEvent = UIElement.PreviewKeyDownEvent };
        target.RaiseEvent(args);
        return args.Handled;
    }

    // 检查器 TextBox 的 KeyDown 是冒泡事件（web 的 Enter/Escape 提交同款冒泡），
    // 与画布 PreviewKeyDown 隧道分开驱动。
    private static void KeyDownOn(UIElement target, Key key)
    {
        var source = PresentationSource.FromVisual(target as Visual ?? throw new Exception("按键目标不在视觉树内"))
            ?? throw new Exception("按键目标未连接到 PresentationSource");
        target.RaiseEvent(new KeyEventArgs(Keyboard.PrimaryDevice, source, 0, key) { RoutedEvent = UIElement.KeyDownEvent });
    }

    private static HttpResponseMessage Json(string text) => new(HttpStatusCode.OK) { Content = new StringContent(text) };

    // 夹具：182×257 页、四格（z 1-4）、格 1 带结构化气泡（rotation/anchor/tail）与
    // 三条拟声词（字符串/带几何对象/无几何对象）、格 3 带派生气泡；记录全部写入。
    private sealed class Fixture : HttpMessageHandler
    {
        public bool FailPut;
        public int StoryboardGets, GeometryPuts;
        public JsonElement GeometryBody;
        public readonly List<(string Path, JsonElement Body)> PanelPatchCalls = [];
        public readonly List<string> CallOrder = [];
        private int pageVersion = 7;

        protected override async Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken token)
        {
            var path = request.RequestUri!.AbsolutePath;
            if (request.Method == HttpMethod.Get)
            {
                if (path.EndsWith("/chapters/ch-1/pages")) return Json(Pages);
                if (path.EndsWith("/pages/pg-1/storyboard")) { StoryboardGets++; return Json(Storyboard()); }
                if (path.EndsWith("/projects/sb-precision/chapters"))
                    return Json("""[{"id":"ch-1","title":"第一章","ordinal":1,"page_count":1}]""");
                if (path.EndsWith("/projects/sb-precision/characters")) return Json("[]");
                if (path.EndsWith("/projects/sb-precision/outfits")) return Json("[]");
                return Json("[]");
            }
            var body = JsonSerializer.Deserialize<JsonElement>(await request.Content!.ReadAsStringAsync(token));
            if (path.EndsWith("/storyboard-geometry"))
            {
                CallOrder.Add("PUT");
                GeometryPuts++; GeometryBody = body;
                if (FailPut)
                    return new HttpResponseMessage(HttpStatusCode.Conflict) { Content = new StringContent("{\"detail\":\"分镜版本已更新\"}") };
                pageVersion += 1;
                return Json(Storyboard());
            }
            if (request.Method == HttpMethod.Patch && path.Contains("/panels/"))
            {
                CallOrder.Add("PATCH");
                PanelPatchCalls.Add((path, body));
                return Json("{}");
            }
            if (request.Method == HttpMethod.Patch && path.Contains("/dialogues/")) return Json("{}");
            if (request.Method == HttpMethod.Delete && path.Contains("/dialogues/")) return Json("{}");
            return Json("{}");
        }

        private static string Pages => """[{"id":"pg-1","chapter_id":"ch-1","page_number":3,"panel_count":4,"storyboard_version":1,"status":"","selected_candidate_id":"","continuity_status":""}]""";

        private string Storyboard() => $$$$"""
          {"page":{"id":"pg-1","chapter_id":"ch-1","page_number":3,"storyboard_version":{{{{pageVersion}}}},
            "canvas":{"width_mm":182,"height_mm":257,"bleed_mm":3,"safe_mm":5},"status":""},
           "panels":[
            {"id":"panel-1","page_id":"pg-1","reading_order":1,"version":4,
             "bounds":{"x":0.02,"y":0.05,"width":0.2,"height":0.2},
             "geometry":{"type":"rect","rect":{"x":0.02,"y":0.05,"width":0.2,"height":0.2},"rotation":0,"z_order":1},
             "shot_type":"medium_close_up","camera_angle":"eye_level","bleed":false,"borderless":false,
             "actions":{"script_action":""},"background":"","props":[],
             "sound_effects":["咚",{"text":"轰","x":0.3,"y":0.2,"rotation":30,"size":0.06},{"text":"旧"}],
             "characters":[],"character_presence":{},"expressions":{},"outfits":{},
             "dialogues":[{"id":"dlg-1","panel_id":"panel-1","speaker_character_id":null,"target_text":"旧台词","reading_order":1,"text_direction":"vertical","rewrite_forbidden":true,
               "bubble":{"type":"rect","rect":{"x":0.04,"y":0.07,"width":0.1,"height":0.06},"rotation":5,
                 "anchor":{"x":0.09,"y":0.13},"tail_target":{"x":0.18,"y":0.2}}}]},
            {"id":"panel-2","page_id":"pg-1","reading_order":2,"version":2,
             "bounds":{"x":0.32,"y":0.05,"width":0.2,"height":0.25},
             "geometry":{"type":"rect","rect":{"x":0.32,"y":0.05,"width":0.2,"height":0.25},"rotation":0,"z_order":2},
             "shot_type":"medium_close_up","camera_angle":"eye_level","bleed":false,"borderless":false,
             "actions":{"script_action":""},"background":"","props":[],"sound_effects":[],
             "characters":[],"character_presence":{},"expressions":{},"outfits":{},"dialogues":[]},
            {"id":"panel-3","page_id":"pg-1","reading_order":3,"version":3,
             "bounds":{"x":0.62,"y":0.05,"width":0.2,"height":0.15},
             "geometry":{"type":"rect","rect":{"x":0.62,"y":0.05,"width":0.2,"height":0.15},"rotation":0,"z_order":3},
             "shot_type":"medium_close_up","camera_angle":"eye_level","bleed":false,"borderless":false,
             "actions":{"script_action":""},"background":"","props":[],"sound_effects":[],
             "characters":[],"character_presence":{},"expressions":{},"outfits":{},
             "dialogues":[{"id":"dlg-2","panel_id":"panel-3","speaker_character_id":null,"target_text":"派生","reading_order":1,"text_direction":"vertical","rewrite_forbidden":true}]},
            {"id":"panel-4","page_id":"pg-1","reading_order":4,"version":1,
             "bounds":{"x":0.02,"y":0.55,"width":0.2,"height":0.35},
             "geometry":{"type":"rect","rect":{"x":0.02,"y":0.55,"width":0.2,"height":0.35},"rotation":0,"z_order":4},
             "shot_type":"medium_close_up","camera_angle":"eye_level","bleed":false,"borderless":false,
             "actions":{"script_action":""},"background":"","props":[],"sound_effects":[],
             "characters":[],"character_presence":{},"expressions":{},"outfits":{},"dialogues":[]}],
           "candidate_count":0}
          """;
    }
}
