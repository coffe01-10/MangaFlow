using System.Net.Http;
using System.Text.Json;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Controls.Primitives;
using System.Windows.Media;
using MangaFlow.Native.Controls;
using MangaFlow.Native.Services;

namespace MangaFlow.Native.Views;

/// <summary>NUI-2A: full native project settings — five sections + danger zone.</summary>
public sealed class ProjectSettingsView : WorkspaceView
{
    private readonly StackPanel body = new();
    private readonly TextBox nameForDelete = new() { MaxLength = 120 };
    private readonly TextBlock projectTitle = new() { Style = (Style)Application.Current.FindResource("SubHeading") };
    private readonly Button saveButton = new() { Content = "保存项目设置", Style = (Style)Application.Current.FindResource("InkButton") };
    private readonly TextBlock saveError = new() { Foreground = (Brush)Application.Current.FindResource("Danger"), TextWrapping = TextWrapping.Wrap, Visibility = Visibility.Collapsed };
    private readonly Border saveSuccess = Notice("项目设置已保存", "ok");

    // Set fresh on every Render(): these live nested inside Section cards, so
    // re-adding a reused instance would throw (single logical parent rule).
    private StackPanel modeGroup = null!;
    private StackPanel draftGroup = null!;
    private StackPanel finalGroup = null!;
    private TextBox concurrencyInput = null!;
    private CheckBox consistencySwitch = null!;
    private ComboBox modelSelector = null!;

    private JsonElement project;
    private int version;
    private List<ModelOption> textModels = [];
    private bool saving;
    private System.Timers.Timer? successTimer;
    // #469: 未保存编辑标记——本页是可编辑表单（含危险区），F5/切节/切项目/关窗
    // 都必须先过 ConfirmLeaveAsync，而不是静默丢弃。
    private bool dirty;

    // 测试缝：headless 检查无模态驱动离开确认（ScriptView/StoryboardView 的
    // LeaveConfirmOverride 同款目的；放在脏判定之后，干净表单不咨询）。
    internal Func<Task<bool>>? LeaveConfirmOverride;

    public ProjectSettingsView()
    {
        saveSuccess.Visibility = Visibility.Collapsed;
        // 危险区输入到一半的删除确认名同样是用户输入，纳入同一份未保存守护。
        nameForDelete.TextChanged += (_, _) => Dirty();
        var grid = new Grid { Margin = new Thickness(36, 32, 36, 24) };
        grid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        grid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        grid.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        grid.RowDefinitions.Add(new RowDefinition { Height = new GridLength(1, GridUnitType.Star) });
        var scroller = new ScrollViewer { VerticalScrollBarVisibility = ScrollBarVisibility.Auto, Content = body };
        Grid.SetRowSpan(scroller, 2);
        var danger = BuildDangerZone();
        Grid.SetRow(danger, 1);
        Grid.SetColumn(danger, 1);
        grid.Children.Add(scroller);
        grid.Children.Add(danger);
        Content = grid;
    }

    private Border BuildDangerZone()
    {
        var panel = new StackPanel();
        panel.Children.Add(new TextBlock { Text = "DANGER ZONE / 项目管理", Style = (Style)Application.Current.FindResource("SectionIndex"), Margin = new Thickness(0, 0, 0, 8) });
        panel.Children.Add(new TextBlock
        {
            Text = "删除当前项目",
            FontFamily = (FontFamily)Application.Current.FindResource("Serif"),
            FontSize = 19, FontWeight = FontWeights.Bold, Margin = new Thickness(0, 0, 0, 8),
        });
        panel.Children.Add(Caption("删除后项目将从工作台隐藏。数据库记录和生成文件暂时保留，避免误删；如需恢复可由维护工具处理。"));
        nameForDelete.Margin = new Thickness(0, 14, 0, 0);
        System.Windows.Automation.AutomationProperties.SetName(nameForDelete, "输入项目名称确认删除");
        panel.Children.Add(nameForDelete);
        var remove = Act("删除项目", DeleteProject, "DangerButton");
        remove.Margin = new Thickness(0, 12, 0, 0);
        panel.Children.Add(remove);
        return new Border
        {
            Style = (Style)Application.Current.FindResource("Card"),
            BorderBrush = (Brush)Application.Current.FindResource("Danger"),
            Padding = new Thickness(22),
            Margin = new Thickness(20, 24, 0, 0),
            VerticalAlignment = VerticalAlignment.Top,
            Child = panel,
        };
    }

    public override async void Activate(WorkspaceContext context)
    {
        base.Activate(context);
        saveButton.Click -= Save;
        saveButton.Click += Save;
        // Deactivation cancels in-flight reads; async void has no caller to observe the
        // cancellation, so swallow it here instead of crashing the dispatcher.
        try { await LoadAsync(); }
        catch (OperationCanceledException) { }
    }

    private async Task LoadAsync()
    {
        body.Children.Clear();
        projectTitle.Text = "读取项目设置…";
        body.Children.Add(projectTitle);
        var spinner = new Spinner { Size = 20, HorizontalAlignment = HorizontalAlignment.Left, Margin = new Thickness(0, 18, 0, 0) };
        body.Children.Add(spinner);
        try
        {
            var loaded = await Api.SendAsync($"projects/{ProjectId}", cancellation: lifetime.Token);
            var models = await Api.SendAsync("models", cancellation: lifetime.Token);
            if (lifetime.Token.IsCancellationRequested) return;
            project = loaded;
            version = loaded.Number("version");
            textModels = models.EnumerateArray()
                .Where(m => m.Text("model_type") == "TEXT"
                    && m.Array("operations").Any(o => o.ToString() == "structured_text")
                    && m.Array("operations").Any(o => o.ToString() == "multimodal_analysis"))
                .Select(ModelOption.From)
                .ToList();
            Render();
        }
         catch (OperationCanceledException) { }
        catch (Exception error) when (error is not OperationCanceledException)
        {
            body.Children.Clear();
            body.Children.Add(projectTitle);
            var stack = new StackPanel();
            stack.Children.Add(new TextBlock { Text = "项目设置读取失败", FontWeight = FontWeights.Bold, FontSize = 15 });
            stack.Children.Add(Caption(error.Message));
            var retry = Act("重试", async (_, _) => await LoadAsync(), "Outline");
            retry.Margin = new Thickness(0, 14, 0, 0);
            retry.HorizontalAlignment = HorizontalAlignment.Left;
            stack.Children.Add(retry);
            body.Children.Add(new Border { Style = (Style)Application.Current.FindResource("Card"), Padding = new Thickness(22), Child = stack });
        }
    }

    private void Render()
    {
        modeGroup = new StackPanel();
        draftGroup = new StackPanel();
        finalGroup = new StackPanel();
        concurrencyInput = new TextBox { Width = 120 };
        consistencySwitch = new CheckBox { Style = (Style)Application.Current.FindResource("Switch") };
        modelSelector = Selector("文字任务默认路由", 320);
        System.Windows.Automation.AutomationProperties.SetName(concurrencyInput, "任务并发");
        // Wire once per instance: the controls above are fresh on every Render.
        consistencySwitch.Checked += (_, _) => Dirty();
        consistencySwitch.Unchecked += (_, _) => Dirty();
        modelSelector.SelectionChanged += (_, _) => Dirty();
        concurrencyInput.TextChanged += (_, _) => Dirty();
        body.Children.Clear();
        saveSuccess.Visibility = Visibility.Collapsed;
        saveError.Visibility = Visibility.Collapsed;
        projectTitle.Text = project.Text("name");
        body.Children.Add(new TextBlock
        {
            Text = "这里仅保存当前项目的制作策略。图片模型仍在每个候选生成前单独选择，不设置主次。",
            Style = (Style)Application.Current.FindResource("Caption"),
            Margin = new Thickness(0, 6, 0, 22),
        });

        modeGroup.Children.Clear();
        foreach (var mode in new[] { "DIRECTOR", "SEMI_AUTO", "AUTO" })
        {
            var card = new RadioButton
            {
                GroupName = "workflow-mode",
                Tag = mode,
                Margin = new Thickness(0, 0, 0, 10),
                MinHeight = 62,
                Content = new StackPanel
                {
                    Children =
                    {
                        new TextBlock { Text = Labels.Map(Labels.WorkflowModeShort, mode) + (mode == "DIRECTOR" ? "模式" : ""), FontWeight = FontWeights.Bold, FontSize = 14 },
                        new TextBlock { Text = Labels.Map(Labels.WorkflowModeDetail, mode), Style = (Style)Application.Current.FindResource("Micro"), Margin = new Thickness(0, 4, 0, 0) },
                    },
                },
            };
            if (mode == project.Text("workflow_mode", "SEMI_AUTO")) card.IsChecked = true;
            card.Checked += (_, _) => Dirty();
            modeGroup.Children.Add(card);
        }
        body.Children.Add(Section("工作方式", "WORKFLOW MODE", modeGroup));

        BuildSegments(project.Text("draft_resolution", "1K"), project.Text("default_resolution", "2K"));
        concurrencyInput.Text = project.Number("default_concurrency").ToString();
        var outputPanel = new StackPanel
        {
            Children =
            {
                Labelled("草稿清晰度 / 抽卡和预览使用", draftGroup),
                Labelled("正式清晰度 / 导出前保持结构升清", finalGroup),
                Labelled("任务并发 / 同一项目最多并行任务数（1–8）", concurrencyInput),
            },
        };
        body.Children.Add(Section("清晰度与并发", "OUTPUT", outputPanel));

        consistencySwitch.Content = "连续性检查 / 检查角色、服装、道具和场景状态";
        consistencySwitch.IsChecked = project.Flag("consistency_check_enabled");
        var note = Notice("文字由人工校对 / 采用候选前必须明确确认页面文字，不再运行 OCR 或自动文字修复。", "neutral");
        note.Margin = new Thickness(0, 14, 0, 0);
        var gatePanel = new StackPanel { Children = { consistencySwitch, note } };
        body.Children.Add(Section("检查开关", "QUALITY GATES", gatePanel));

        body.Children.Add(new Border
        {
            Background = (Brush)Application.Current.FindResource("PaperDeep"),
            BorderBrush = (Brush)Application.Current.FindResource("Line"),
            BorderThickness = new Thickness(1),
            Padding = new Thickness(18),
            Margin = new Thickness(0, 16, 0, 0),
            Child = new StackPanel
            {
                Children =
                {
                    new TextBlock { Text = "MODEL POLICY / 模型策略", Style = (Style)Application.Current.FindResource("SectionIndex"), Margin = new Thickness(0, 0, 0, 6) },
                    new TextBlock { Text = "图片模型按任务选择 / 项目不绑定图片“主模型”。每次生成候选都必须明确选择供应商模型，以保持画风一致。", TextWrapping = TextWrapping.Wrap },
                },
            },
        });

        modelSelector.Items.Clear();
        modelSelector.Items.Add(new ComboBoxItem { Tag = "auto", Content = "自动路由 · 已验证文字/视觉模型" });
        foreach (var option in textModels)
            modelSelector.Items.Add(new ComboBoxItem
            {
                Tag = option.Value,
                Content = option.Label + (option.Hidden ? "（已隐藏）" : ""),
            });
        var currentTextModel = project.Text("default_text_model_id");
        var currentAlias = project.Text("text_model_alias");
        var known = textModels.FirstOrDefault(m => m.Value == currentTextModel || m.Value == currentAlias);
        if (known == null && (currentTextModel.Length > 0 || currentAlias.Length > 0))
        {
            var keep = currentTextModel.Length > 0 ? currentTextModel : currentAlias;
            modelSelector.Items.Add(new ComboBoxItem { Tag = keep, Content = $"当前配置 · {keep}" });
            Select(modelSelector, keep);
        }
        else if (known != null) Select(modelSelector, known.Value);
        else modelSelector.SelectedIndex = 0;
        var textPanel = new StackPanel { Children = { Labelled("剧本、风格分析与视觉检查 / 自动路由只使用已完成能力测试的模型", modelSelector) } };
        body.Children.Add(Section("文字任务默认路由", "TEXT MODEL", textPanel));

        saveButton.Margin = new Thickness(0, 22, 0, 6);
        saveButton.HorizontalAlignment = HorizontalAlignment.Left;
        body.Children.Add(saveButton);
        body.Children.Add(saveSuccess);
        body.Children.Add(saveError);
        // 程序化回填（上面的 IsChecked/Text/SelectedItem 赋值）会同步触发 Dirty；
        // 渲染完成即服务端状态，收尾清脏。危险区的删除确认名不随渲染重建，
        // 留在缓存视图里反而避免了跨导航丢失，不参与此重置。
        dirty = false;
    }

    private static void Select(ComboBox selector, string value)
    {
        foreach (var item in selector.Items.OfType<ComboBoxItem>())
            if ((string?)item.Tag == value) { selector.SelectedItem = item; return; }
        selector.SelectedIndex = 0;
    }

    private static void BuildSegment(StackPanel group, string[] options, string current)
    {
        group.Children.Clear();
        group.Orientation = Orientation.Horizontal;
        ToggleButton? previous = null;
        foreach (var option in options)
        {
            var toggle = new ToggleButton
            {
                Content = option, Style = (Style)Application.Current.FindResource("Pill"),
                IsChecked = option == current, Margin = new Thickness(0, 0, 8, 0), MinWidth = 64,
            };
            toggle.Checked += (_, _) =>
            {
                foreach (var other in group.Children.OfType<ToggleButton>())
                    if (!ReferenceEquals(other, toggle)) other.IsChecked = false;
            };
            group.Children.Add(toggle);
            previous = toggle;
        }
        _ = previous;
    }

    private void BuildSegments(string draft, string final)
    {
        BuildSegment(draftGroup, ["1K", "2K"], draft);
        BuildSegment(finalGroup, ["1K", "2K", "4K"], final);
        // 分辨率 Pill 选中即编辑（互斥处理器只负责收起兄弟项，不标脏）。
        foreach (var toggle in draftGroup.Children.OfType<ToggleButton>().Concat(finalGroup.Children.OfType<ToggleButton>()))
            toggle.Checked += (_, _) => Dirty();
    }

    private static StackPanel Labelled(string label, FrameworkElement control)
    {
        var panel = new StackPanel { Margin = new Thickness(0, 0, 0, 14) };
        panel.Children.Add(FieldLabel(label));
        control.HorizontalAlignment = HorizontalAlignment.Left;
        panel.Children.Add(control);
        return panel;
    }

    private static Border Section(string title, string kicker, UIElement content)
    {
        var panel = new StackPanel { Margin = new Thickness(2) };
        panel.Children.Add(new TextBlock { Text = kicker, Style = (Style)Application.Current.FindResource("SectionIndex"), Margin = new Thickness(0, 0, 0, 6) });
        panel.Children.Add(new TextBlock
        {
            Text = title, FontFamily = (FontFamily)Application.Current.FindResource("Serif"),
            FontSize = 19, FontWeight = FontWeights.Bold, Margin = new Thickness(0, 0, 0, 12),
        });
        panel.Children.Add(content);
        return new Border
        {
            Style = (Style)Application.Current.FindResource("Card"),
            Padding = new Thickness(22),
            Margin = new Thickness(0, 0, 0, 16),
            Child = panel,
        };
    }

    private void Dirty()
    {
        dirty = true;
        saveSuccess.Visibility = Visibility.Collapsed;
    }

    private string CheckedMode() =>
        modeGroup.Children.OfType<RadioButton>().FirstOrDefault(r => r.IsChecked == true) is { Tag: string mode } ? mode : "SEMI_AUTO";

    private static string CheckedSegment(StackPanel group) =>
        group.Children.OfType<ToggleButton>().FirstOrDefault(t => t.IsChecked == true) is { Content: string value } ? value : "1K";

    private async void Save(object sender, RoutedEventArgs e)
    {
        if (saving || project.ValueKind == JsonValueKind.Undefined) return;
        if (!int.TryParse(concurrencyInput.Text, out var concurrency) || concurrency is < 1 or > 8)
        {
            saveError.Text = "任务并发必须是 1–8 的整数。";
            saveError.Visibility = Visibility.Visible;
            return;
        }
        var route = modelSelector.SelectedItem is ComboBoxItem { Tag: string tag } ? tag : "auto";
        string? textModelId = null, textAlias = null;
        if (route != "auto")
        {
            if (textModels.Any(m => m.Value == route && m.Value == project.Text("default_text_model_id")))
                textModelId = route;
            else if (route == project.Text("default_text_model_id"))
                textModelId = route;
            else textAlias = route;
        }
        saving = true;
        saveButton.IsEnabled = false;
        saveError.Visibility = Visibility.Collapsed;
        try
        {
            var saved = await Api.SendAsync($"projects/{ProjectId}", HttpMethod.Patch, new
            {
                workflow_mode = CheckedMode(),
                draft_resolution = CheckedSegment(draftGroup),
                default_resolution = CheckedSegment(finalGroup),
                default_concurrency = concurrency,
                consistency_check_enabled = consistencySwitch.IsChecked == true,
                default_text_model_id = textModelId,
                text_model_alias = textAlias,
                version,
            }, cancellation: lifetime.Token);
            version = saved.Number("version");
            project = saved;
            dirty = false;
            saveSuccess.Visibility = Visibility.Visible;
            successTimer?.Stop();
            successTimer = new System.Timers.Timer(4000) { AutoReset = false };
            successTimer.Elapsed += (_, _) => Dispatcher.BeginInvoke(() => saveSuccess.Visibility = Visibility.Collapsed);
            successTimer.Start();
            Cache.Invalidate("project:" + ProjectId, "dashboard", "projects");
        }
        catch (OperationCanceledException)
        {
            // #471-1: 导航/关闭取消了在途 PATCH——服务端可能已落库，而本地 version
            // 未推进，直接重存会立刻 409。给出「提交结果未知」反馈而不是沉默；
            // 重新进入本页时 Activate→LoadAsync 会重读服务端，自然收敛。
            saveError.Text = "保存已取消，提交结果未知，请刷新后确认";
            saveError.Visibility = Visibility.Visible;
            State.Status = "项目设置保存已取消，提交结果未知，请刷新后确认";
        }
        catch (Exception error) when (error is not OperationCanceledException)
        {
            saveError.Text = error.Message;
            saveError.Visibility = Visibility.Visible;
        }
        finally
        {
            saving = false;
            saveButton.IsEnabled = true;
        }
    }

    private async void DeleteProject(object sender, RoutedEventArgs e)
    {
        if (project.ValueKind == JsonValueKind.Undefined || !State.Connected) return;
        var expected = project.Text("name");
        if (nameForDelete.Text.Trim() != expected)
        {
            MessageBox.Show(Host, "请输入完整项目名称", "删除项目", MessageBoxButton.OK, MessageBoxImage.Warning);
            return;
        }
        if (MessageBox.Show(Host, $"确认从工作台删除项目“{expected}”？", "删除项目", MessageBoxButton.YesNo, MessageBoxImage.Warning) != MessageBoxResult.Yes)
            return;
        ((Button)sender).IsEnabled = false;
        try
        {
            await Api.SendOptionalAsync($"projects/{ProjectId}?confirm_name={Uri.EscapeDataString(expected)}", HttpMethod.Delete, cancellation: lifetime.Token);
            Cache.Invalidate("dashboard", "projects");
            State.Status = $"项目「{expected}」已删除";
            await Context!.OpenDashboard();
        }
        catch (OperationCanceledException)
        {
            // #471-1: DELETE 同样可能已被服务端执行。导航已在进行中，弹模态只会
            // 打断它；用全局状态条说明结果未知。危险区按钮只在构造时创建、不随
            // Render 重建，必须恢复可用，否则缓存视图会留下永远禁用的删除按钮。
            State.Status = "项目删除已取消，提交结果未知，请刷新后确认";
            ((Button)sender).IsEnabled = true;
        }
        catch (Exception error) when (error is not OperationCanceledException)
        {
            MessageBox.Show(Host, error.Message, "删除未完成", MessageBoxButton.OK, MessageBoxImage.Warning);
            ((Button)sender).IsEnabled = true;
        }
    }

    public override async Task<bool> ConfirmLeaveAsync()
    {
        // #469: 侧栏切节（MainWindow:458）/ Ctrl+K 切项目（:416）/ 关窗（:655）
        // 都走这里；干净表单直接放行，不打扰。
        if (!dirty) return true;
        var leave = LeaveConfirmOverride is { } prompt
            ? await prompt()
            : MessageBox.Show(Host, "项目设置有未保存的修改（包括已输入的删除确认名称），离开会丢弃这些内容。确定离开吗？",
                "离开确认", MessageBoxButton.YesNo, MessageBoxImage.Question) == MessageBoxResult.Yes;
        if (!leave) return false;
        // 同意离开即弃稿：dirty 原样保留会让同一次弃稿在再次激活/重载前重复弹窗
        // （StoryboardView.ConfirmLeaveAsync 同款处理）。
        dirty = false;
        return true;
    }

    public override Task RefreshAsync()
    {
        if (saveSuccess.Visibility == Visibility.Visible) return Task.CompletedTask;
        // #469: F5（MainWindow.Refresh 不经过 ConfirmLeaveAsync）不得静默丢弃
        // 编辑——镜像 ScriptView.RefreshAsync 的 editingFormsOpen 守卫：有草稿时
        // 本轮跳过重载，保存或弃稿后的下一次刷新用新数据重绘。
        if (dirty) return Task.CompletedTask;
        _ = LoadAsync();
        return Task.CompletedTask;
    }
}
