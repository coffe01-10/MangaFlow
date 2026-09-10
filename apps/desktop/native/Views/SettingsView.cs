using System.Net.Http;
using System.Text.Json;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Controls.Primitives;
using System.Windows.Input;
using System.Windows.Media;
using MangaFlow.Native.Controls;
using MangaFlow.Native.Services;

namespace MangaFlow.Native.Views;

/// <summary>NUI-2C: settings — provider platform, runtime params, layered diagnostics, storage.</summary>
public sealed class SettingsView : WorkspaceView
{
    private readonly ScrollViewer scroller = new() { VerticalScrollBarVisibility = ScrollBarVisibility.Auto };
    private readonly StackPanel providerList = new();
    private readonly TextBox searchInput = new() { MinWidth = 260 };
    private readonly ComboBox capabilityFilter = Selector("能力", 150);
    private readonly ComboBox sortFilter = Selector("排序", 130);
    private readonly CheckBox verifiedOnly = new() { Content = "仅已验证" };
    private readonly CheckBox showHidden = new() { Content = "显示已隐藏" };
    private readonly TextBlock providerSummary = new() { Style = (Style)Application.Current.FindResource("Caption") };
    private readonly StackPanel runtimeForm = new();
    private readonly Button runtimeSave = new() { Content = "保存运行设置", Style = (Style)Application.Current.FindResource("InkButton") };
    private readonly TextBlock runtimeError = new() { Foreground = (Brush)Application.Current.FindResource("Danger"), TextWrapping = TextWrapping.Wrap };
    private readonly Border runtimeNotice = Notice("运行设置已保存并应用到后续任务", "ok");
    private readonly StackPanel diagnosticsList = new();
    private readonly Button recheckButton = new() { Content = "重新检测", Style = (Style)Application.Current.FindResource("Compact") };
    private readonly TextBlock executorLabel = new();
    private readonly TextBlock healthLabel = new();
    private readonly TextBlock databaseLabel = new();
    private readonly TextBlock storageLabel = new();
    private readonly TextBlock checkedAtLabel = new();
    private readonly Dictionary<string, TextBlock> storageValueBlocks = new();

    private List<JsonElement> providers = [];
    private List<JsonElement> catalog = [];
    private readonly Dictionary<string, bool> expanded = new();
    private string search = "";
    private string capability = "ALL", modelType = "ALL", sort = "RECOMMENDED";
    private bool verified, hidden;
    private JsonElement runtime;
    private bool runtimeSaving;

    public SettingsView()
    {
        var page = new StackPanel { Margin = new Thickness(34, 32, 34, 40) };
        page.Children.Add(BuildStatusStrip());
        var board = new Grid { Name = "SettingsBoard" };
        board.ColumnDefinitions.Add(new ColumnDefinition());
        board.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(342) });
        board.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        board.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        var main = new StackPanel();
        main.Children.Add(BuildProviderBoard());
        main.Children.Add(BuildRuntimeCard());
        board.Children.Add(main);
        var side = new StackPanel { Name = "SettingsDiagnostics", Margin = new Thickness(22, 0, 0, 0) };
        var diagnostics = BuildDiagnosticsCard();
        side.Children.Add(diagnostics);
        var storage = BuildStorageCard();
        storage.Margin = new Thickness(0, 18, 0, 0);
        side.Children.Add(storage);
        Grid.SetColumn(side, 1);
        board.Children.Add(side);
        SizeChanged += (_, _) =>
        {
            var narrow = ActualWidth < 1280;
            board.ColumnDefinitions[1].Width = new GridLength(narrow ? 0 : 342);
            Grid.SetColumn(side, narrow ? 0 : 1);
            Grid.SetRow(side, narrow ? 1 : 0);
            side.Margin = narrow ? new Thickness(0, 20, 0, 0) : new Thickness(22, 0, 0, 0);
        };
        page.Children.Add(board);
        scroller.Content = page;
        Content = scroller;
    }

    private Border BuildStatusStrip()
    {
        var strip = new Border
        {
            Background = (Brush)Application.Current.FindResource("PaperDeep"),
            BorderBrush = (Brush)Application.Current.FindResource("Line"),
            BorderThickness = new Thickness(1),
            Padding = new Thickness(18, 12, 18, 12),
            Margin = new Thickness(0, 0, 0, 18),
        };
        var grid = new Grid();
        for (var i = 0; i < 5; i++) grid.ColumnDefinitions.Add(new ColumnDefinition());
        (string, TextBlock)[] cells =
        [
            ("AI 连接", healthLabel), ("执行器", executorLabel), ("数据库", databaseLabel),
            ("存储", storageLabel), ("最近检查", checkedAtLabel),
        ];
        for (var i = 0; i < cells.Length; i++)
        {
            var (label, value) = cells[i];
            var panel = new StackPanel { Margin = new Thickness(0, 0, 14, 0) };
            panel.Children.Add(new TextBlock { Text = label, Style = (Style)Application.Current.FindResource("Micro") });
            value.FontWeight = FontWeights.Bold;
            value.FontSize = 13;
            value.Text = "读取中";
            panel.Children.Add(value);
            Grid.SetColumn(panel, i);
            grid.Children.Add(panel);
        }
        strip.Child = grid;
        return strip;
    }

    private Border BuildProviderBoard()
    {
        var board = new Border
        {
            Style = (Style)Application.Current.FindResource("Card"),
            Padding = new Thickness(22),
            Margin = new Thickness(0, 0, 0, 16),
        };
        var panel = new StackPanel();
        var header = new DockPanel { Margin = new Thickness(0, 0, 0, 14) };
        var summary = providerSummary;
        summary.VerticalAlignment = VerticalAlignment.Bottom;
        DockPanel.SetDock(summary, Dock.Right);
        header.Children.Add(summary);
        header.Children.Add(new TextBlock { Text = "AI 供应商与模型", FontWeight = FontWeights.Bold, FontSize = 16 });
        panel.Children.Add(header);

        searchInput.TextChanged += (_, _) => { search = searchInput.Text.Trim().ToLowerInvariant(); RenderProviders(); };
        System.Windows.Automation.AutomationProperties.SetName(searchInput, "筛选供应商");
        searchInput.Margin = new Thickness(0, 0, 0, 10);
        panel.Children.Add(searchInput);

        var filters = new WrapPanel { Margin = new Thickness(0, 0, 0, 10) };
        var textPill = new ToggleButton { Content = "文字", Style = (Style)Application.Current.FindResource("Pill"), Margin = new Thickness(0, 0, 6, 6) };
        var imagePill = new ToggleButton { Content = "图片", Style = (Style)Application.Current.FindResource("Pill"), Margin = new Thickness(0, 0, 14, 6) };
        textPill.Click += (_, _) => { modelType = modelType == "TEXT" ? "ALL" : "TEXT"; imagePill.IsChecked = false; RenderProviders(); };
        imagePill.Click += (_, _) => { modelType = modelType == "IMAGE" ? "ALL" : "IMAGE"; textPill.IsChecked = false; RenderProviders(); };
        filters.Children.Add(textPill);
        filters.Children.Add(imagePill);
        capabilityFilter.Items.Add(new ComboBoxItem { Tag = "ALL", Content = "全部能力" });
        foreach (var (key, label) in Labels.ModelOperation)
            capabilityFilter.Items.Add(new ComboBoxItem { Tag = key, Content = label });
        capabilityFilter.SelectedIndex = 0;
        capabilityFilter.SelectionChanged += (_, _) =>
        {
            capability = (capabilityFilter.SelectedItem as ComboBoxItem)?.Tag as string ?? "ALL";
            RenderProviders();
        };
        capabilityFilter.Margin = new Thickness(0, 0, 10, 6);
        filters.Children.Add(capabilityFilter);
        verifiedOnly.ToolTip = "自动路由只使用已验证模型";
        verifiedOnly.Margin = new Thickness(0, 0, 14, 6);
        verifiedOnly.VerticalAlignment = VerticalAlignment.Center;
        verifiedOnly.Click += (_, _) => { verified = verifiedOnly.IsChecked == true; RenderProviders(); };
        filters.Children.Add(verifiedOnly);
        showHidden.ToolTip = "隐藏只影响创作界面的模型选择，不影响调用或路由";
        showHidden.Margin = new Thickness(0, 0, 14, 6);
        showHidden.VerticalAlignment = VerticalAlignment.Center;
        showHidden.Click += (_, _) => { hidden = showHidden.IsChecked == true; RenderProviders(); };
        filters.Children.Add(showHidden);
        foreach (var (key, label) in new[] { ("RECOMMENDED", "推荐"), ("NAME", "名称"), ("HEALTH", "健康"), ("MODELS", "模型数量"), ("LATENCY", "延迟") })
            sortFilter.Items.Add(new ComboBoxItem { Tag = key, Content = label });
        sortFilter.SelectedIndex = 0;
        sortFilter.SelectionChanged += (_, _) =>
        {
            sort = (sortFilter.SelectedItem as ComboBoxItem)?.Tag as string ?? "RECOMMENDED";
            RenderProviders();
        };
        sortFilter.Margin = new Thickness(0, 0, 10, 6);
        filters.Children.Add(sortFilter);
        var add = Kit.Act("＋ 添加供应商", (_, _) => new ProviderCreateDialog(this).ShowDialog(), "CompactInk");
        add.Margin = new Thickness(0, 0, 6, 6);
        filters.Children.Add(add);
        panel.Children.Add(filters);
        panel.Children.Add(new TextBlock
        {
            Text = "可添加兼容连接；账号型凭据由服务端环境管理，CLI 登录由外部工具管理，Key 型连接在各自连接卡内录入。",
            Style = (Style)Application.Current.FindResource("Micro"), Margin = new Thickness(0, 0, 0, 12), TextWrapping = TextWrapping.Wrap,
        });
        panel.Children.Add(providerList);
        board.Child = panel;
        return board;
    }

    private Border BuildRuntimeCard()
    {
        var card = new Border
        {
            Style = (Style)Application.Current.FindResource("Card"),
            Padding = new Thickness(22),
            Margin = new Thickness(0, 0, 0, 0),
        };
        var panel = new StackPanel();
        panel.Children.Add(new TextBlock { Text = "WORKER / RUNTIME", Style = (Style)Application.Current.FindResource("SectionIndex"), Margin = new Thickness(0, 0, 0, 4) });
        panel.Children.Add(new TextBlock { Text = "运行参数", FontWeight = FontWeights.Bold, FontSize = 16, Margin = new Thickness(0, 0, 0, 4) });
        panel.Children.Add(new TextBlock { Text = "非敏感动态设置", Style = (Style)Application.Current.FindResource("Micro"), Margin = new Thickness(0, 0, 0, 14) });
        panel.Children.Add(runtimeForm);
        runtimeNotice.Visibility = Visibility.Collapsed;
        runtimeNotice.Margin = new Thickness(0, 6, 0, 6);
        panel.Children.Add(runtimeNotice);
        runtimeError.Margin = new Thickness(0, 6, 0, 6);
        panel.Children.Add(runtimeError);
        runtimeSave.Margin = new Thickness(0, 8, 0, 0);
        runtimeSave.HorizontalAlignment = HorizontalAlignment.Left;
        runtimeSave.Click += SaveRuntime;
        panel.Children.Add(runtimeSave);
        card.Child = panel;
        return card;
    }

    private Border BuildDiagnosticsCard()
    {
        var card = new Border { Style = (Style)Application.Current.FindResource("Card"), Padding = new Thickness(22) };
        var panel = new StackPanel();
        var header = new DockPanel { Margin = new Thickness(0, 0, 0, 12) };
        recheckButton.Click += async (_, _) => await LoadDiagnosticsAsync();
        DockPanel.SetDock(recheckButton, Dock.Right);
        header.Children.Add(recheckButton);
        header.Children.Add(new TextBlock { Text = "分层诊断", FontWeight = FontWeights.Bold, FontSize = 16 });
        panel.Children.Add(header);
        panel.Children.Add(diagnosticsList);
        card.Child = panel;
        return card;
    }

    private Border BuildStorageCard()
    {
        var card = new Border { Style = (Style)Application.Current.FindResource("Card"), Padding = new Thickness(22) };
        var panel = new StackPanel();
        panel.Children.Add(new TextBlock { Text = "本地存储", FontWeight = FontWeights.Bold, FontSize = 16, Margin = new Thickness(0, 0, 0, 12) });
        var grid = new Grid();
        grid.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        grid.ColumnDefinitions.Add(new ColumnDefinition());
        var rows = new[] { ("数据库", "database"), ("生成内容", "storage_root"), ("用户上传", "upload_root") };
        for (var i = 0; i < rows.Length; i++)
        {
            var (label, key) = rows[i];
            var labelBlock = new TextBlock { Text = label, Style = (Style)Application.Current.FindResource("Caption"), Margin = new Thickness(0, 6, 18, 6) };
            Grid.SetRow(labelBlock, i);
            grid.Children.Add(labelBlock);
            var value = new TextBlock { FontFamily = (FontFamily)Application.Current.FindResource("Mono"), FontSize = 12, Text = "—" };
            storageValueBlocks[key] = value;
            Grid.SetRow(value, i);
            Grid.SetColumn(value, 1);
            grid.RowDefinitions.Add(new RowDefinition());
            grid.Children.Add(value);
        }
        panel.Children.Add(grid);
        panel.Children.Add(new TextBlock
        {
            Text = "凭据路径、私钥、令牌和 Redis 地址不会通过此接口返回。",
            Style = (Style)Application.Current.FindResource("Micro"), Margin = new Thickness(0, 12, 0, 0), TextWrapping = TextWrapping.Wrap,
        });
        card.Child = panel;
        return card;
    }

    public override async void Activate(WorkspaceContext context)
    {
        base.Activate(context);
        // Deactivation cancels in-flight reads; async void has no caller to observe the
        // cancellation, so swallow it here instead of crashing the dispatcher.
        try { await LoadAllAsync(); }
        catch (OperationCanceledException) { }
    }

    private async Task LoadAllAsync()
    {
        await Task.WhenAll(
            LoadProvidersAsync(),
            LoadRuntimeAsync(),
            LoadDiagnosticsAsync());
    }

    private async Task LoadProvidersAsync()
    {
        try
        {
            var loaded = await Api.SendAsync("providers", cancellation: lifetime.Token);
            var models = await Api.SendAsync("models", cancellation: lifetime.Token);
            if (lifetime.Token.IsCancellationRequested) return;
            providers = loaded.EnumerateArray().ToList();
            catalog = models.EnumerateArray().ToList();
            var configured = providers.Count(p => p.Array("connections").Any(c => c.Flag("configured")));
            providerSummary.Text = $"{providers.Count} 家供应商 · {configured} 已配置";
            RenderProviders();
        }
         catch (OperationCanceledException) { }
        catch (Exception error) when (error is not OperationCanceledException)
        {
            providerList.Children.Clear();
            var stack = new StackPanel();
            stack.Children.Add(new TextBlock { Text = "供应商列表读取失败", FontWeight = FontWeights.Bold });
            stack.Children.Add(Caption(error.Message));
            var retry = Kit.Act("重试", async (_, _) => await LoadProvidersAsync(), "Outline");
            retry.Margin = new Thickness(0, 10, 0, 0);
            retry.HorizontalAlignment = HorizontalAlignment.Left;
            stack.Children.Add(retry);
            providerList.Children.Add(new Border
            {
                Background = (Brush)Application.Current.FindResource("DangerBg"),
                Padding = new Thickness(16), Margin = new Thickness(0, 10, 0, 0),
                Child = stack,
            });
        }
    }

    private static int HealthRank(JsonElement connection)
    {
        var state = connection.Text("health_state");
        return state switch { "HEALTHY" => 0, "DEGRADED" => 1, "UNKNOWN" => 2, "UNCONFIGURED" => 3, "OFFLINE" => 4, _ => 5 };
    }

    private List<JsonElement> SortedProviders()
    {
        var list = providers.ToList();
        list.Sort((a, b) => sort switch
        {
            "NAME" => string.Compare(a.Text("name"), b.Text("name"), StringComparison.CurrentCulture),
            "HEALTH" => a.Array("connections").Select(HealthRank).DefaultIfEmpty(5).Min()
                .CompareTo(b.Array("connections").Select(HealthRank).DefaultIfEmpty(5).Min()),
            "MODELS" => b.Array("connections").Sum(c => c.Number("model_count"))
                .CompareTo(a.Array("connections").Sum(c => c.Number("model_count"))),
            "LATENCY" => a.Array("connections").Where(c => c.Number("latency_ms") > 0).Select(c => c.Number("latency_ms")).DefaultIfEmpty(int.MaxValue).Min()
                .CompareTo(b.Array("connections").Where(c => c.Number("latency_ms") > 0).Select(c => c.Number("latency_ms")).DefaultIfEmpty(int.MaxValue).Min()),
            _ => Score(b).CompareTo(Score(a)),
        });
        return list;
        static int Score(JsonElement p)
        {
            var configured = p.Array("connections").Any(c => c.Flag("configured")) ? 4 : 0;
            var healthy = p.Array("connections").Count > 0 && p.Array("connections").All(c => c.Text("health_state") == "HEALTHY") ? 2 : 0;
            var models = p.Array("connections").Any(c => c.Number("model_count") > 0) ? 1 : 0;
            return configured + healthy + models;
        }
    }

    private void RenderProviders()
    {
        providerList.Children.Clear();
        if (providers.Count == 0)
        {
            providerList.Children.Add(Caption("还没有供应商。先添加自定义连接，或检查服务端预设。"));
            return;
        }
        var sorted = SortedProviders();
        List<JsonElement> visible = sorted.Where(p => MatchesSearch(p)).ToList();
        var groups = new (string Key, string Label)[]
        {
            ("configured", "已配置"), ("unconfigured", "未配置"), ("disabled", "已停用"),
        };
        if (visible.Count == 0)
        {
            providerList.Children.Add(Caption("没有符合当前搜索或筛选的供应商"));
            return;
        }
        foreach (var (key, label) in groups)
        {
            List<JsonElement> members = key switch
            {
                "configured" => visible.Where(p => p.Flag("enabled") && p.Array("connections").Any(c => c.Flag("configured"))).ToList(),
                "unconfigured" => visible.Where(p => p.Flag("enabled") && !p.Array("connections").Any(c => c.Flag("configured"))).ToList(),
                _ => visible.Where(p => !p.Flag("enabled")).ToList(),
            };
            if (members.Count == 0) continue;
            providerList.Children.Add(new TextBlock
            {
                Text = $"{label} · {members.Count}", Style = (Style)Application.Current.FindResource("SectionIndex"),
                Margin = new Thickness(0, 14, 0, 8),
            });
            foreach (var provider in members) providerList.Children.Add(new ProviderCard(this, provider, catalog, expanded, hidden, verified, modelType, capability));
        }
    }

    internal bool MatchesSearch(JsonElement provider)
    {
        if (search.Length == 0) return true;
        if (provider.Text("name").ToLowerInvariant().Contains(search)) return true;
        if (provider.Text("preset_key").ToLowerInvariant().Contains(search)) return true;
        foreach (var connection in provider.Array("connections"))
        {
            if (connection.Text("protocol").ToLowerInvariant().Contains(search)) return true;
        }
        return false;
    }

    private async Task LoadRuntimeAsync()
    {
        runtimeForm.Children.Clear();
        runtimeForm.Children.Add(new Spinner { Size = 16, HorizontalAlignment = HorizontalAlignment.Left });
        try
        {
            runtime = await Api.SendAsync("settings/runtime", cancellation: lifetime.Token);
            if (lifetime.Token.IsCancellationRequested) return;
            RenderRuntime();
        }
         catch (OperationCanceledException) { }
        catch (Exception error) when (error is not OperationCanceledException)
        {
            runtimeForm.Children.Clear();
            runtimeForm.Children.Add(Caption($"读取设置失败：{error.Message}"));
        }
    }

    private void RenderRuntime()
    {
        runtimeForm.Children.Clear();
        databaseLabel.Text = runtime.Text("database_backend", "—");
        AddRuntimeField("队列模式", "queue_mode", runtime.Text("queue_mode", "AUTO"), "AUTO|自动回退,LOCAL|本地同步,REDIS|Redis 队列");
        AddRuntimeField("任务超时（30–3600 秒）", "job_timeout_seconds", runtime.Text("job_timeout_seconds", "900"));
        AddRuntimeField("任务租约（30–3600 秒）", "job_lease_seconds", runtime.Text("job_lease_seconds", "300"));
        AddRuntimeField("默认并发（1–8 路）", "default_concurrency", runtime.Text("default_concurrency", "2"));
        AddRuntimeField("视觉修复重试（0–10 次）", "max_auto_repairs", runtime.Text("max_auto_repairs", "2"));
        AddRuntimeField("状态检查周期（秒）", "health_check_interval_seconds", runtime.Text("health_check_interval_seconds", "300"));
        AddRuntimeField("界面轮询周期（毫秒）", "ui_poll_interval_seconds", runtime.Text("ui_poll_interval_seconds", "3000"));
        storageLabel.Text = runtime.Text("storage_root", "—");
        // 本地存储卡三行在运行设置读取成功后填充
        if (storageValueBlocks.TryGetValue("database_backend", out var backend)) backend.Text = runtime.Text("database_backend", "—");
        if (storageValueBlocks.TryGetValue("storage_root", out var storageRoot)) storageRoot.Text = runtime.Text("storage_root", "—");
        if (storageValueBlocks.TryGetValue("upload_root", out var uploadRoot)) uploadRoot.Text = runtime.Text("upload_root", "—");
    }

    private readonly Dictionary<string, FrameworkElement> runtimeInputs = new();

    // 数字项的合法区间（与 web 设置表单一致）。钳制只发生在失焦与保存前：
    // 按键级夹值会把 "45"（区间 30–3600）的首键 "4" 立刻改写成 "30"，
    // 用户将永远无法通过键盘输入低于当前值的数字，清空输入也会被立即顶回最小值。
    private static readonly Dictionary<string, (int Min, int Max)> RuntimeRanges = new()
    {
        ["job_timeout_seconds"] = (30, 3600),
        ["job_lease_seconds"] = (30, 3600),
        ["default_concurrency"] = (1, 8),
        ["max_auto_repairs"] = (0, 10),
        ["health_check_interval_seconds"] = (60, 3600),
        ["ui_poll_interval_seconds"] = (1000, 60000),
    };
    // 每个数字项最近一次的有效值：失焦时清空/非法输入回退到这里，而不是把越界值交给服务端 422。
    private readonly Dictionary<string, int> runtimeCommitted = new();

    private void AddRuntimeField(string label, string key, string value, string? options = null)
    {
        var panel = new StackPanel { Margin = new Thickness(0, 0, 0, 12) };
        panel.Children.Add(FieldLabel(label));
        FrameworkElement input = options == null
            ? BuildRuntimeNumber(key, value)
            : BuildOptionSelect(key, value, options);
        input.SetValue(HorizontalAlignmentProperty, HorizontalAlignment.Left);
        panel.Children.Add(input);
        runtimeInputs[key] = input;
        runtimeForm.Children.Add(panel);
    }

    private TextBox BuildRuntimeNumber(string key, string value)
    {
        var box = new TextBox { Text = value, Width = 180, Tag = key };
        if (RuntimeRanges.TryGetValue(key, out var range))
        {
            runtimeCommitted[key] = int.TryParse(value, out var initial) ? Math.Clamp(initial, range.Min, range.Max) : range.Min;
            // 失焦才钳制：输入期间原文展示，离开输入框时提交区间内结果（web ClampedNumberInput 的语义）。
            box.LostFocus += (_, _) => ClampRuntimeInput(box, key);
        }
        return box;
    }

    private void ClampRuntimeInput(TextBox box, string key)
    {
        var range = RuntimeRanges[key];
        // S-3: 与 web ClampedNumberInput 同语义 —— 可解析的小数（"900.5"）按 Math.round
        // 取整后钳制提交，只有空/非法输入才回退旧值；int.TryParse 会把小数当非法丢掉。
        if (TryParseRuntimeNumber(box.Text, out var parsed))
        {
            var clamped = Math.Clamp(parsed, range.Min, range.Max);
            runtimeCommitted[key] = clamped;
            box.Text = clamped.ToString();
            return;
        }
        // 清空或非法输入放弃修改，回显上一个有效值。
        box.Text = runtimeCommitted.GetValueOrDefault(key, range.Min).ToString();
    }

    // 数字项输入的统一解析：接受小数与科学计数法（web 的 Number(raw)），四舍五入到整数。
    private static bool TryParseRuntimeNumber(string value, out int number)
    {
        number = 0;
        if (!double.TryParse(value.Trim(), System.Globalization.NumberStyles.Float,
                System.Globalization.CultureInfo.InvariantCulture, out var parsed))
            return false;
        number = (int)Math.Round(parsed, MidpointRounding.AwayFromZero);
        return true;
    }

    private static ComboBox BuildOptionSelect(string key, string value, string options)
    {
        var box = new ComboBox { Width = 180, Tag = key };
        foreach (var pair in options.Split(','))
        {
            var parts = pair.Split('|');
            var item = new ComboBoxItem { Tag = parts[0], Content = parts.Length > 1 ? parts[1] : parts[0] };
            box.Items.Add(item);
            if (parts[0] == value) box.SelectedItem = item;
        }
        return box;
    }

    public void SaveRuntimeSettings() => SaveRuntime(runtimeSave, new RoutedEventArgs());

    private async void SaveRuntime(object sender, RoutedEventArgs e)
    {
        if (runtimeSaving || runtime.ValueKind != JsonValueKind.Object) return;
        runtimeSaving = true;
        runtimeSave.IsEnabled = false;
        runtimeError.Text = "";
        runtimeNotice.Visibility = Visibility.Collapsed;
        try
        {
            var payload = new Dictionary<string, object?> { ["version"] = runtime.Number("version") };
            foreach (var (key, input) in runtimeInputs)
            {
                var value = input switch
                {
                    TextBox box => box.Text,
                    ComboBox combo => (combo.SelectedItem as ComboBoxItem)?.Tag as string ?? "",
                    _ => "",
                };
                if (RuntimeRanges.TryGetValue(key, out var range))
                {
                    // 保存前再钳一次：失焦钩子可能被绕过（未失焦直接点保存），
                    // 越界值必须在此截住，不能依赖服务端 422 兜底；小数与失焦路径
                    // 同语义（TryParseRuntimeNumber：可解析小数取整后钳制）。
                    var parsed = TryParseRuntimeNumber(value, out var number) ? number : runtimeCommitted.GetValueOrDefault(key, range.Min);
                    var clamped = Math.Clamp(parsed, range.Min, range.Max);
                    runtimeCommitted[key] = clamped;
                    if (input is TextBox box) box.Text = clamped.ToString();
                    payload[key] = clamped;
                    continue;
                }
                payload[key] = int.TryParse(value, out var raw) && key != "queue_mode" ? raw : value;
            }
            runtime = await Api.SendAsync("settings/runtime", HttpMethod.Patch, payload, cancellation: lifetime.Token);
            RenderRuntime();
            runtimeNotice.Visibility = Visibility.Visible;
            await LoadDiagnosticsAsync();
        }
         catch (OperationCanceledException) { }
        catch (Exception error) when (error is not OperationCanceledException)
        {
            runtimeError.Text = error.Message;
        }
        finally
        {
            runtimeSaving = false;
            runtimeSave.IsEnabled = true;
        }
    }

    private async Task LoadDiagnosticsAsync()
    {
        diagnosticsList.Children.Clear();
        var spinner = new Spinner { Size = 16, HorizontalAlignment = HorizontalAlignment.Left };
        diagnosticsList.Children.Add(spinner);
        try
        {
            var diagnostics = await Api.SendAsync("settings/diagnostics", cancellation: lifetime.Token);
            if (lifetime.Token.IsCancellationRequested) return;
            diagnosticsList.Children.Clear();
            executorLabel.Text = diagnostics.Element("queue").Text("actual_executor", "—");
            healthLabel.Text = providers.Count == 0 ? "读取中"
                : $"{providers.SelectMany(p => p.Array("connections")).Count(c => c.Text("health_state") == "HEALTHY")} 健康";
            var checkedAt = diagnostics.TextOrNull("checked_at");
            checkedAtLabel.Text = checkedAt == null ? "尚未记录"
                : DateTime.TryParse(checkedAt, null, System.Globalization.DateTimeStyles.RoundtripKind, out var time)
                    ? time.ToString("MM-dd HH:mm:ss") : "时间格式异常";
            foreach (var check in diagnostics.Array("checks"))
            {
                var status = check.Text("status", "NOT_CHECKED");
                var brush = status switch
                {
                    "OK" => (Brush)Application.Current.FindResource("Success"),
                    "WARNING" => (Brush)Application.Current.FindResource("Warning"),
                    "FAILED" => (Brush)Application.Current.FindResource("Danger"),
                    _ => (Brush)Application.Current.FindResource("Muted"),
                };
                var row = new StackPanel { Orientation = Orientation.Horizontal, Margin = new Thickness(0, 7, 0, 7) };
                row.Children.Add(new Border
                {
                    Width = 8, Height = 8, CornerRadius = new CornerRadius(4),
                    Background = brush, VerticalAlignment = VerticalAlignment.Center, Margin = new Thickness(0, 0, 10, 0),
                });
                var text = new StackPanel();
                text.Children.Add(new TextBlock { Text = check.Text("label"), FontWeight = FontWeights.Bold, FontSize = 13 });
                var message = check.Text("message");
                if (message.Length > 0) text.Children.Add(new TextBlock { Text = message, Style = (Style)Application.Current.FindResource("Micro") });
                row.Children.Add(text);
                var latency = check.Element("latency_ms").ValueKind == JsonValueKind.Number
                    ? $"{check.Number("latency_ms")} ms" : "—";
                row.Children.Add(new TextBlock
                {
                    Text = latency, Style = (Style)Application.Current.FindResource("Micro"),
                    VerticalAlignment = VerticalAlignment.Center, Margin = new Thickness(16, 0, 0, 0),
                });
                diagnosticsList.Children.Add(row);
            }
        }
         catch (OperationCanceledException) { }
        catch (Exception error) when (error is not OperationCanceledException)
        {
            diagnosticsList.Children.Clear();
            diagnosticsList.Children.Add(Caption($"诊断读取失败：{error.Message}。请稍后重试。"));
        }
    }

    internal new ApiClient Api => base.Api;
    internal new ApiCache Cache => base.Cache;
    internal new Window Host => base.Host;

    internal void OnProvidersChanged()
    {
        _ = LoadProvidersAsync();
        Cache.Invalidate("providers", "models", "dashboard");
    }

    public override Task RefreshAsync()
    {
        _ = LoadAllAsync();
        return Task.CompletedTask;
    }
}

/// <summary>A provider card with its connections; rendering driven by SettingsView filters.</summary>
internal sealed class ProviderCard : Border
{
    private readonly JsonElement provider;
    private readonly List<JsonElement> catalog;
    private readonly SettingsView owner;
    private readonly Dictionary<string, bool> expanded;
    private readonly bool showHidden, verifiedOnly;
    private readonly string modelType, capability;

    public ProviderCard(SettingsView owner, JsonElement provider, List<JsonElement> catalog,
        Dictionary<string, bool> expanded, bool showHidden, bool verifiedOnly, string modelType, string capability)
    {
        this.owner = owner;
        this.provider = provider;
        this.catalog = catalog;
        this.expanded = expanded;
        // 筛选语义与 web provider-management 对齐：类型/能力/仅已验证/显示已隐藏只过滤
        // 各连接内的模型行，绝不整卡隐藏供应商——供应商分组仍由搜索与配置状态决定。
        this.showHidden = showHidden;
        this.verifiedOnly = verifiedOnly;
        this.modelType = modelType;
        this.capability = capability;
        Style = (Style)Application.Current.FindResource("Card");
        Padding = new Thickness(0);
        Margin = new Thickness(0, 0, 0, 10);
        Render();
    }

    private void Render()
    {
        var id = provider.Text("id");
        var open = expanded.GetValueOrDefault(id, provider.Array("connections").Any(c => c.Flag("configured")));
        var panel = new StackPanel();
        var header = new Button
        {
            Style = (Style)Application.Current.FindResource("Nav"),
            HorizontalContentAlignment = HorizontalAlignment.Left,
            Padding = new Thickness(18, 12, 14, 12),
            Background = Brushes.Transparent,
            Content = BuildHeader(),
        };
        header.Click += (_, _) => { expanded[id] = !open; Render(); };
        panel.Children.Add(header);
        if (open)
        {
            foreach (var connection in provider.Array("connections"))
                panel.Children.Add(new ConnectionPanel(owner, provider, connection, catalog, showHidden, verifiedOnly, modelType, capability));
        }
        Child = panel;
    }

    private FrameworkElement BuildHeader()
    {
        var grid = new Grid();
        grid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        grid.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        var info = new StackPanel();
        var category = $"{Labels.Map(Labels.ProviderCategory, provider.Text("category", "CUSTOM"))} · {Labels.Map(Labels.ProviderRisk, provider.Text("risk_label", "LOW"))}";
        info.Children.Add(new TextBlock { Text = category, Style = (Style)Application.Current.FindResource("Micro") });
        info.Children.Add(new TextBlock { Text = provider.Text("name"), FontWeight = FontWeights.Bold, FontSize = 15, Margin = new Thickness(0, 2, 0, 0) });
        var description = provider.Text("description");
        if (description.Length > 0)
            info.Children.Add(new TextBlock { Text = description, Style = (Style)Application.Current.FindResource("Micro") });
        grid.Children.Add(info);
        var counts = new StackPanel { VerticalAlignment = VerticalAlignment.Center };
        var connections = provider.Array("connections");
        var configured = connections.Count(c => c.Flag("configured"));
        counts.Children.Add(new TextBlock
        {
            Text = $"{configured}/{connections.Count} 连接 · {connections.Sum(c => c.Number("model_count"))} 模型",
            Style = (Style)Application.Current.FindResource("Micro"), HorizontalAlignment = HorizontalAlignment.Right,
        });
        grid.Children.Add(counts);
        Grid.SetColumn(counts, 1);
        return grid;
    }
}

/// <summary>Connection panel: credentials, actions, advanced endpoints, model catalog.</summary>
internal sealed class ConnectionPanel : Border
{
    private readonly SettingsView owner;
    private readonly JsonElement provider;
    private JsonElement connection;
    private readonly List<JsonElement> catalog;
    // 模型行筛选（web filterModels 的四条规则）；默认值等价于"不过滤"，供重渲染冒烟等直接构造场景使用。
    private readonly bool showHidden, verifiedOnly;
    private readonly string modelType, capability;
    // Fresh on every Render(): re-parenting a reused control would throw, and the
    // pane re-renders on enable/disable toggles and manual-form switches.
    private StackPanel modelsPanel = null!;
    private TextBlock statusLine = null!;
    private StackPanel keyList = null!;
    private TextBox keyLabel = null!;
    private PasswordBox keyValue = null!;
    private ComboBox manualType = null!;
    private TextBox manualId = null!;
    private TextBox manualName = null!;
    private bool busy;

    public ConnectionPanel(SettingsView owner, JsonElement provider, JsonElement connection, List<JsonElement> catalog,
        bool showHidden = false, bool verifiedOnly = false, string modelType = "ALL", string capability = "ALL")
    {
        this.owner = owner;
        this.provider = provider;
        this.connection = connection;
        this.catalog = catalog;
        this.showHidden = showHidden;
        this.verifiedOnly = verifiedOnly;
        this.modelType = modelType;
        this.capability = capability;
        BorderBrush = (Brush)Application.Current.FindResource("Line");
        BorderThickness = new Thickness(0, 1, 0, 0);
        Padding = new Thickness(18, 14, 16, 16);
        Render();
    }

    internal bool IsCliConnection => connection.Text("credential_source") == "CLI_SESSION";
    internal SettingsView Owner => owner;

    private void Render()
    {
        modelsPanel = new StackPanel();
        statusLine = new TextBlock { Style = (Style)Application.Current.FindResource("Micro"), TextWrapping = TextWrapping.Wrap };
        keyList = new StackPanel();
        keyLabel = new TextBox { Width = 120, Text = "default" };
        keyValue = new PasswordBox { Width = 260 };
        manualType = new ComboBox();
        manualId = new TextBox { Width = 220 };
        manualName = new TextBox { Width = 220 };
        manualOpen = false;   // 重渲染会重建面板，旧手工表单已随之消失
        var panel = new StackPanel();
        var health = connection.Text("health_state", "UNKNOWN");
        var header = new DockPanel { Margin = new Thickness(0, 0, 0, 8) };
        var badge = new Border
        {
            CornerRadius = new CornerRadius(99), Padding = new Thickness(10, 3, 10, 3),
            Background = health == "HEALTHY" ? (Brush)Application.Current.FindResource("SuccessBg")
                : health == "DEGRADED" ? (Brush)Application.Current.FindResource("WarningBg")
                : (Brush)Application.Current.FindResource("PaperDeep"),
            VerticalAlignment = VerticalAlignment.Center,
        };
        badge.Child = new TextBlock { Text = Labels.Map(Labels.ProviderHealth, health), FontSize = 11, FontWeight = FontWeights.Bold };
        DockPanel.SetDock(badge, Dock.Right);
        header.Children.Add(badge);
        var title = new StackPanel();
        title.Children.Add(new TextBlock { Text = connection.Text("name"), FontWeight = FontWeights.Bold, FontSize = 14 });
        title.Children.Add(new TextBlock
        {
            Text = $"{connection.Text("protocol")} · {connection.Text("base_url")}",
            Style = (Style)Application.Current.FindResource("Micro"), FontFamily = (FontFamily)Application.Current.FindResource("Mono"),
        });
        header.Children.Add(title);
        panel.Children.Add(header);

        statusLine.Text = ConnectionMetrics();
        panel.Children.Add(statusLine);
        panel.Children.Add(BuildCredentials());
        panel.Children.Add(BuildActions());
        panel.Children.Add(new Separator());
        var modelsHeader = new DockPanel { Margin = new Thickness(0, 10, 0, 6) };
        modelsHeader.Children.Add(new TextBlock { Text = "模型目录", FontWeight = FontWeights.Bold, FontSize = 13.5 });
        panel.Children.Add(modelsHeader);
        panel.Children.Add(modelsPanel);
        var addManual = Kit.Act("＋ 手工添加模型", ToggleManualForm, "Compact");
        addManual.Margin = new Thickness(0, 6, 0, 0);
        panel.Children.Add(addManual);
        Child = panel;
        _ = LoadModelsAsync();
    }

    private string ConnectionMetrics()
    {
        var parts = new List<string>
        {
            $"{connection.Number("key_count")} 个密钥",
            $"{connection.Number("model_count")} 个模型",
        };
        parts.Add(connection.Number("latency_ms") > 0 ? $"{connection.Number("latency_ms")} ms" : "延迟未测");
        var message = connection.Text("message");
        if (message.Length > 0) parts.Add(message);
        return string.Join(" · ", parts);
    }

    private FrameworkElement BuildCredentials()
    {
        var source = connection.Text("credential_source");
        if (source == "CLI_SESSION" || source == "ENV_SERVICE_ACCOUNT")
        {
            var ready = health_ready();
            return new TextBlock
            {
                Text = source == "CLI_SESSION"
                    ? $"登录由外部 CLI 会话管理；当前{(ready ? "已就绪" : "未就绪")}，应用不会读取或代理登录凭据。"
                    : $"凭据由服务端环境管理；当前{(ready ? "已就绪" : "未就绪")}，不会显示凭据路径或内容。",
                Style = (Style)Application.Current.FindResource("Micro"), TextWrapping = TextWrapping.Wrap, Margin = new Thickness(0, 6, 0, 0),
            };
        }
        var panel = new StackPanel { Margin = new Thickness(0, 10, 0, 0) };
        var writable = connection.Flag("credential_writable");
        var row = new StackPanel { Orientation = Orientation.Horizontal };
        System.Windows.Automation.AutomationProperties.SetName(keyLabel, "密钥标签");
        System.Windows.Automation.AutomationProperties.SetName(keyValue, "API Key");
        keyValue.Tag = connection.Text("id");
        row.Children.Add(keyLabel);
        keyValue.Margin = new Thickness(8, 0, 8, 0);
        keyValue.IsEnabled = writable;
        row.Children.Add(keyValue);
        var save = Kit.Act("保存密钥", async (_, _) => await SaveKey(), "Compact");
        save.IsEnabled = writable;
        row.Children.Add(save);
        panel.Children.Add(row);
        if (!writable)
            panel.Children.Add(new TextBlock { Text = "服务端未配置凭据主密钥", Style = (Style)Application.Current.FindResource("Micro"), Margin = new Thickness(0, 6, 0, 0) });
        panel.Children.Add(keyList);
        RenderKeys();
        return panel;

        bool health_ready() => connection.Text("health_state") is "HEALTHY" or "AVAILABLE";
    }

    private void RenderKeys()
    {
        keyList.Children.Clear();
        foreach (var key in connection.Array("keys"))
        {
            var row = new StackPanel { Orientation = Orientation.Horizontal, Margin = new Thickness(0, 8, 0, 0) };
            var state = Labels.Map(Labels.ProviderHealth, key.Text("health_state", "UNKNOWN"));
            var cooldown = key.Text("cooldown_until");
            var label = $"{key.Text("label")} {key.Text("key_hint")} · {state}";
            if (DateTime.TryParse(cooldown, out var until)) label += $" · 冷却至 {until:HH:mm}";
            row.Children.Add(new TextBlock { Text = label, Style = (Style)Application.Current.FindResource("Micro"), VerticalAlignment = VerticalAlignment.Center });
            var remove = Kit.Act("删除", async (_, _) => await DeleteKey(key), "CompactDanger");
            remove.Margin = new Thickness(12, 0, 0, 0);
            System.Windows.Automation.AutomationProperties.SetName(remove, $"删除 {key.Text("label")}");
            row.Children.Add(remove);
            keyList.Children.Add(row);
        }
    }

    private FrameworkElement BuildActions()
    {
        var configured = connection.Flag("configured");
        var row = new WrapPanel { Margin = new Thickness(0, 12, 0, 0) };
        row.Children.Add(ActionButton("测试连接", async () => await Verify("CREDENTIALS"), enabled: configured));
        if (connection.Flag("supports_model_discovery"))
            row.Children.Add(ActionButton("同步模型", Discover, enabled: configured, margin: 8));
        row.Children.Add(ActionButton(connection.Flag("enabled") ? "停用连接" : "启用连接", ToggleConnection, enabled: true, margin: 8));
        if (connection.Flag("supports_balance"))
            row.Children.Add(ActionButton("查询余额", QueryBalance, enabled: configured, margin: 8));
        return row;
    }

    private Button ActionButton(string text, Func<Task> action, bool enabled, int margin = 0)
    {
        Button button = new() { Content = text, Style = (Style)Application.Current.FindResource("Compact"), IsEnabled = enabled };
        button.Click += async (_, _) =>
        {
            if (busy || !button.IsEnabled) return;
            busy = true;
            button.IsEnabled = false;
            try { await action(); }
            finally { busy = false; button.IsEnabled = enabled; }
        };
        button.Margin = new Thickness(margin, 0, 0, 0);
        return button;
    }

    private async Task Verify(string level, string? modelId = null, string? operation = null, bool acknowledgeCost = false)
    {
        try
        {
            object payload = level == "CREDENTIALS"
                ? new { level }
                : new { level, catalog_model_id = modelId, operation, acknowledge_cost = acknowledgeCost };
            var result = await owner.Api.SendAsync($"providers/connections/{connection.Text("id")}/verify",
                HttpMethod.Post, payload, cancellation: default);
            var probe = result.Element("probe");
            var latency = probe.Number("latency_ms");
            var probeType = probe.Text("probe_type");
            var health = result.Element("health");
            var healthScalar = health.ValueKind == JsonValueKind.String ? health.ToString() : health.Text("state");
            if (probeType.StartsWith("CLI_", StringComparison.Ordinal))
                statusLine.Text = $"CLI 探测完成 · {(healthScalar == "HEALTHY" ? "就绪" : "未就绪")}";
            else if (probeType == "MODEL_SMOKE")
                statusLine.Text = probe.Text("status") == "PASSED" ? "模型测试通过" : $"模型测试完成 · {latency} ms";
            else
                statusLine.Text = $"连接测试完成 · {(latency > 0 ? $"{latency} ms" : "—")}";
            owner.OnProvidersChanged();
        }
        catch (Exception error)
        {
            statusLine.Text = error.Message;
        }
    }

    private async Task Discover()
    {
        try
        {
            var result = await owner.Api.SendAsync($"providers/connections/{connection.Text("id")}/discover", HttpMethod.Post);
            statusLine.Text = $"模型目录已同步 · {result.GetArrayLength()} 个模型";
            owner.OnProvidersChanged();
        }
        catch (Exception error) { statusLine.Text = error.Message; }
    }

    private async Task ToggleConnection()
    {
        try
        {
            connection = await owner.Api.SendAsync($"providers/connections/{connection.Text("id")}", HttpMethod.Patch,
                new { version = connection.Number("version"), enabled = !connection.Flag("enabled") });
            owner.OnProvidersChanged();
            Render();
        }
        catch (Exception error) { statusLine.Text = error.Message; }
    }

    private async Task QueryBalance()
    {
        try
        {
            var result = await owner.Api.SendAsync($"providers/connections/{connection.Text("id")}/balance");
            statusLine.Text = result.Flag("configured")
                ? $"余额：{result.Text("value", "—")} {result.Text("currency")}"
                : result.Text("message");
        }
        catch (Exception error) { statusLine.Text = error.Message; }
    }

    private async Task SaveKey()
    {
        if (keyValue.SecurePassword.Length == 0 || keyLabel.Text.Trim().Length == 0)
        {
            statusLine.Text = "请填写密钥标签和 API Key。";
            return;
        }
        try
        {
            await owner.Api.SendAsync($"providers/connections/{connection.Text("id")}/keys", HttpMethod.Put,
                new { label = keyLabel.Text.Trim(), api_key = keyValue.Password });
            keyValue.Clear();
            statusLine.Text = "密钥已保存";
            owner.OnProvidersChanged();
        }
        catch (Exception error) { statusLine.Text = error.Message; }
    }

    private async Task DeleteKey(JsonElement key)
    {
        var dialog = new ConfirmDialog(owner.Host, "删除密钥",
            $"删除密钥「{key.Text("label")}」？已保存的密文将无法恢复。", "确认删除", danger: true);
        if (dialog.ShowDialog() != true) return;
        try
        {
            await owner.Api.SendOptionalAsync($"providers/connections/{connection.Text("id")}/keys/{key.Text("id")}", HttpMethod.Delete);
            owner.OnProvidersChanged();
        }
        catch (Exception error) { statusLine.Text = error.Message; }
    }

    private async Task LoadModelsAsync()
    {
        modelsPanel.Children.Clear();
        var loading = new TextBlock { Text = "正在读取模型目录…", Style = (Style)Application.Current.FindResource("Micro") };
        modelsPanel.Children.Add(loading);
        try
        {
            var models = await owner.Api.SendAsync($"providers/connections/{connection.Text("id")}/models", cancellation: default);
            modelsPanel.Children.Clear();
            var rows = models.EnumerateArray().ToList();
            if (rows.Count == 0)
            {
                modelsPanel.Children.Add(new TextBlock
                {
                    Text = connection.Flag("supports_model_discovery")
                        ? "还没有模型。先同步模型列表，或手工添加上游 ID。"
                        : "此连接不支持自动发现模型，请手工添加上游 ID。",
                    Style = (Style)Application.Current.FindResource("Micro"), TextWrapping = TextWrapping.Wrap,
                });
                return;
            }
            // 与 web filterModels 逐条对齐：隐藏、类型、能力、仅已验证。目录为空与"筛完为空"
            // 是两种状态，后者要提示用户清除筛选而不是误以为连接没有模型。
            var visible = rows.Where(ModelMatchesFilters).ToList();
            if (visible.Count == 0)
            {
                modelsPanel.Children.Add(new TextBlock
                {
                    Text = "没有符合筛选的模型",
                    Style = (Style)Application.Current.FindResource("Micro"), FontWeight = FontWeights.Bold,
                });
                modelsPanel.Children.Add(new TextBlock
                {
                    Text = "可清除类型、能力、仅已验证筛选，或开启“显示已隐藏”。",
                    Style = (Style)Application.Current.FindResource("Micro"), TextWrapping = TextWrapping.Wrap,
                });
                return;
            }
            foreach (var model in visible) modelsPanel.Children.Add(new ModelRow(this, model, catalog));
        }
        catch (Exception error)
        {
            modelsPanel.Children.Clear();
            modelsPanel.Children.Add(Kit.Caption($"模型目录读取失败：{error.Message}"));
        }
    }

    /// <summary>模型行是否通过当前筛选：display_enabled / model_type / operations / confidence。</summary>
    private bool ModelMatchesFilters(JsonElement model)
    {
        if (!showHidden && !model.Flag("display_enabled")) return false;
        if (modelType != "ALL" && model.Text("model_type") != modelType) return false;
        if (capability != "ALL" && !model.Array("operations").Any(operation => operation.ToString() == capability)) return false;
        if (verifiedOnly && model.Text("confidence", "MANUAL") != "VERIFIED") return false;
        return true;
    }

    internal void RefreshModels() => _ = LoadModelsAsync();

    internal Task RunSmokeTest(JsonElement model, string operation, bool imageModel)
    {
        if (!imageModel) return Verify("MODEL_SMOKE", model.Text("id"), operation, false);
        var dialog = new ConfirmDialog(owner.Host, "图片能力测试可能产生费用",
            $"将向当前连接发起一次图片模型冒烟调用。模型：{model.Text("display_name", model.Text("provider_model_id"))}。请确认已了解供应商计费规则。",
            "确认测试");
        if (dialog.ShowDialog() != true) return Task.CompletedTask;
        return Verify("MODEL_SMOKE", model.Text("id"), operation, true);
    }

    private bool manualOpen;

    private void ToggleManualForm(object sender, RoutedEventArgs e)
    {
        manualOpen = !manualOpen;
        if (!manualOpen)
        {
            Render();
            return;
        }
        var form = new StackPanel { Margin = new Thickness(0, 10, 0, 0) };
        var row = new StackPanel { Orientation = Orientation.Horizontal };
        System.Windows.Automation.AutomationProperties.SetName(manualId, "上游模型 ID");
        manualId.Tag = "上游模型 ID";
        row.Children.Add(manualId);
        manualName.Margin = new Thickness(8, 0, 8, 0);
        System.Windows.Automation.AutomationProperties.SetName(manualName, "显示名");
        row.Children.Add(manualName);
        manualType.Items.Clear();
        manualType.Items.Add(new ComboBoxItem { Tag = "TEXT", Content = "文字模型" });
        if (connection.Array("supported_model_types").Any(t => t.ToString() == "IMAGE"))
            manualType.Items.Add(new ComboBoxItem { Tag = "IMAGE", Content = "图片模型" });
        manualType.SelectedIndex = 0;
        manualType.Width = 120;
        row.Children.Add(manualType);
        var submit = Kit.Act("添加模型", async (_, _) => await AddManualModel(), "CompactInk");
        submit.Margin = new Thickness(8, 0, 0, 0);
        row.Children.Add(submit);
        form.Children.Add(row);
        var host = new Border
        {
            BorderBrush = (Brush)Application.Current.FindResource("Line"),
            BorderThickness = new Thickness(1), Padding = new Thickness(12), Margin = new Thickness(0, 8, 0, 0),
            Child = form,
        };
        modelsPanel.Children.Add(host);
    }

    private async Task AddManualModel()
    {
        var id = manualId.Text.Trim();
        if (id.Length == 0)
        {
            statusLine.Text = "请填写上游模型 ID。";
            return;
        }
        var type = (manualType.SelectedItem as ComboBoxItem)?.Tag as string ?? "TEXT";
        var name = manualName.Text.Trim();
        object payload = type == "TEXT"
            ? new
            {
                provider_model_id = id, display_name = name.Length > 0 ? name : (string?)null,
                model_type = "TEXT", input_modalities = new[] { "TEXT" }, output_modalities = new[] { "TEXT" },
                operations = new[] { "structured_text" },
                api_surfaces = new[] { connection.Flag("use_responses_api") ? "RESPONSES" : "CHAT" },
                capabilities = new Dictionary<string, object> { ["structured_output_mode"] = "JSON_MODE" },
            }
            : new
            {
                provider_model_id = id, display_name = name.Length > 0 ? name : (string?)null,
                model_type = "IMAGE", input_modalities = new[] { "TEXT", "IMAGE" }, output_modalities = new[] { "IMAGE" },
                operations = new[] { "image_generate", "image_edit" },
                api_surfaces = new[] { "IMAGES" },
                capabilities = new Dictionary<string, object> { ["resolutions"] = new[] { "1K" }, ["max_reference_images"] = 1 },
            };
        try
        {
            await owner.Api.SendAsync($"providers/connections/{connection.Text("id")}/models", HttpMethod.Post, payload);
            statusLine.Text = "已添加。测试通过前不会进入自动路由。";
            manualId.Text = "";
            manualName.Text = "";
            owner.OnProvidersChanged();
            RefreshModels();
        }
        catch (Exception error) { statusLine.Text = error.Message; }
    }
}

/// <summary>One catalog model row: visibility toggle, operation smoke tests.</summary>
internal sealed class ModelRow : Border
{
    private readonly ConnectionPanel panel;
    private JsonElement model;
    private readonly List<JsonElement> catalog;

    public ModelRow(ConnectionPanel panel, JsonElement model, List<JsonElement> catalog)
    {
        this.panel = panel;
        this.model = model;
        this.catalog = catalog;
        BorderBrush = (Brush)Application.Current.FindResource("Line");
        BorderThickness = new Thickness(0, 0, 0, 1);
        Padding = new Thickness(0, 10, 0, 10);
        Render();
    }

    private void Render()
    {
        var grid = new Grid();
        grid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        grid.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        var info = new StackPanel();
        var nameRow = new StackPanel { Orientation = Orientation.Horizontal };
        nameRow.Children.Add(new TextBlock { Text = model.Text("display_name", model.Text("provider_model_id")), FontWeight = FontWeights.Bold, FontSize = 13.5 });
        foreach (var (tag, text) in new[]
                 {
                     (!model.Flag("display_enabled"), "已隐藏"),
                     (!model.Flag("enabled"), "不可调用"),
                 })
        {
            if (!tag) continue;
            var chip = new Border
            {
                Background = (Brush)Application.Current.FindResource("PaperDeep"),
                Padding = new Thickness(7, 2, 7, 2), CornerRadius = new CornerRadius(3), Margin = new Thickness(8, 0, 0, 0),
            };
            chip.Child = new TextBlock { Text = text, FontSize = 10.5, Foreground = (Brush)Application.Current.FindResource("Muted") };
            nameRow.Children.Add(chip);
        }
        info.Children.Add(nameRow);
        var typeLabel = model.Text("model_type") == "IMAGE" ? "图片" : "文字";
        var secondLine = $"{typeLabel} · {model.Text("provider_model_id")}";
        info.Children.Add(new TextBlock
        {
            Text = secondLine, Style = (Style)Application.Current.FindResource("Micro"),
            FontFamily = (FontFamily)Application.Current.FindResource("Mono"),
        });
        var operations = model.Array("operations").Select(o => Labels.Map(Labels.ModelOperation, o.ToString())).ToList();
        var confidence = Labels.Map(Labels.ModelConfidence, model.Text("confidence", "MANUAL"));
        info.Children.Add(new TextBlock
        {
            Text = $"{confidence} · 来源 {model.Text("source", "manual")}" + (operations.Count > 0 ? " · " + string.Join(" · ", operations) : ""),
            Style = (Style)Application.Current.FindResource("Micro"),
        });
        grid.Children.Add(info);

        var actions = new StackPanel { Orientation = Orientation.Horizontal };
        var visible = model.Flag("display_enabled");
        var toggle = Kit.Act(visible ? "隐藏" : "显示", async (_, _) => await ToggleVisibility(), "Compact");
        actions.Children.Add(toggle);
        var isCli = panel.IsCliConnection;
        var image = model.Text("model_type") == "IMAGE";
        foreach (var operation in model.Array("operations"))
        {
            var key = operation.ToString();
            var label = key switch
            {
                "structured_text" => "测试文本", "multimodal_analysis" => "测试视觉",
                "image_generate" => "测试图片", "image_edit" => "测试编辑", _ => key,
            };
            var test = new Button
            {
                Content = label, Style = (Style)Application.Current.FindResource("Compact"),
                Margin = new Thickness(6, 0, 0, 0), IsEnabled = !isCli && model.Flag("enabled"),
            };
            if (isCli) test.ToolTip = "CLI 图片能力只在有持久任务与审计行的生成流程中验证";
            else if (image)
            {
                test.ToolTip = "图片模型冒烟测试可能计费";
                test.BorderBrush = (Brush)Application.Current.FindResource("Warning");
            }
            test.Click += (_, _) => _ = panel.RunSmokeTest(model, key, image);
            actions.Children.Add(test);
        }
        grid.Children.Add(actions);
        Grid.SetColumn(actions, 1);
        Child = grid;
    }

    private async Task ToggleVisibility()
    {
        try
        {
            model = await panel.Owner.Api.SendAsync($"providers/models/{model.Text("id")}", HttpMethod.Patch,
                new { display_enabled = !model.Flag("display_enabled"), version = model.Number("version") });
            panel.Owner.Cache.Invalidate("providers", "models", "dashboard");
            Render();
        }
        catch (Exception error)
        {
            MessageBox.Show(panel.Owner.Host, error.Message, "展示偏好未保存", MessageBoxButton.OK, MessageBoxImage.Warning);
        }
    }
}

internal sealed class ProviderCreateDialog : Window
{
    public ProviderCreateDialog(SettingsView owner)
    {
        var parent = owner;
        Owner = parent.Host;
        Title = "添加供应商";
        Width = 470;
        SizeToContent = SizeToContent.Height;
        WindowStartupLocation = WindowStartupLocation.CenterOwner;
        ShowInTaskbar = false;
        Background = (Brush)Application.Current.FindResource("Paper");
        var name = new TextBox();
        System.Windows.Automation.AutomationProperties.SetName(name, "供应商名称");
        var protocol = new ComboBox();
        protocol.Items.Add(new ComboBoxItem { Tag = "OPENAI", Content = "OpenAI 协议" });
        protocol.Items.Add(new ComboBoxItem { Tag = "ANTHROPIC", Content = "Anthropic 协议" });
        protocol.SelectedIndex = 0;
        var baseUrl = new TextBox();
        System.Windows.Automation.AutomationProperties.SetName(baseUrl, "Base URL");
        var responses = new CheckBox { Content = "文本优先使用 Responses API", Margin = new Thickness(0, 4, 0, 0) };
        var error = new TextBlock { Foreground = (Brush)Application.Current.FindResource("Danger"), TextWrapping = TextWrapping.Wrap, Margin = new Thickness(0, 10, 0, 0) };
        var submit = new Button { Content = "创建", Style = (Style)Application.Current.FindResource("InkButton"), MinWidth = 110 };
        var cancel = new Button { Content = "取消", MinWidth = 96, Margin = new Thickness(0, 0, 10, 0) };
        cancel.Click += (_, _) => Close();
        bool ValidUrl(string url)
        {
            if (!Uri.TryCreate(url, UriKind.Absolute, out var uri)) return false;
            if (uri.Scheme is not ("http" or "https")) return false;
            if (uri.UserInfo.Length > 0 || uri.Query.Length > 0 || uri.Fragment.Length > 0) return false;
            if (uri.Scheme == "https") return uri.Host.Length > 0;
            return uri.Host is "localhost" or "127.0.0.1";
        }
        submit.Click += async (_, _) =>
        {
            var providerName = name.Text.Trim();
            var url = baseUrl.Text.Trim();
            if (providerName.Length == 0) { error.Text = "请填写供应商名称。"; return; }
            if (url.Length == 0 || !ValidUrl(url)) { error.Text = "供应商 Base URL 必须是 HTTP(S) 地址"; return; }
            submit.IsEnabled = false;
            try
            {
                await parent.Api.SendAsync("providers", HttpMethod.Post, new
                {
                    name = providerName,
                    protocol = (protocol.SelectedItem as ComboBoxItem)?.Tag as string ?? "OPENAI",
                    base_url = url,
                    use_responses_api = responses.IsChecked == true,
                });
                parent.OnProvidersChanged();
                Close();
            }
            catch (Exception reason)
            {
                error.Text = reason.Message;
                submit.IsEnabled = true;
            }
        };
        var panel = new StackPanel { Margin = new Thickness(26) };
        panel.Children.Add(new TextBlock
        {
            Text = "添加供应商", FontFamily = (FontFamily)Application.Current.FindResource("Serif"),
            FontSize = 21, FontWeight = FontWeights.SemiBold,
        });
        panel.Children.Add(new TextBlock { Text = "可添加 OpenAI / Anthropic 兼容连接。", Style = (Style)Application.Current.FindResource("Caption"), Margin = new Thickness(0, 6, 0, 16) });
        panel.Children.Add(new TextBlock { Text = "供应商名称", Style = (Style)Application.Current.FindResource("FieldLabel") });
        panel.Children.Add(name);
        panel.Children.Add(new TextBlock { Text = "协议", Style = (Style)Application.Current.FindResource("FieldLabel"), Margin = new Thickness(0, 12, 0, 6) });
        panel.Children.Add(protocol);
        panel.Children.Add(new TextBlock { Text = "Base URL", Style = (Style)Application.Current.FindResource("FieldLabel"), Margin = new Thickness(0, 12, 0, 6) });
        panel.Children.Add(baseUrl);
        panel.Children.Add(responses);
        panel.Children.Add(error);
        var actions = new StackPanel { Orientation = Orientation.Horizontal, HorizontalAlignment = HorizontalAlignment.Right, Margin = new Thickness(0, 16, 0, 0) };
        actions.Children.Add(cancel);
        actions.Children.Add(submit);
        panel.Children.Add(actions);
        Content = panel;
        Loaded += (_, _) => name.Focus();
        PreviewKeyDown += (_, e) => { if (e.Key == Key.Escape) Close(); };
    }
}
