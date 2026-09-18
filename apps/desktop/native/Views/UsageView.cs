using System.IO;
using System.Net.Http;
using System.Text;
using System.Text.Json;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Shapes;
using System.Windows.Input;
using System.Windows.Media;
using MangaFlow.Native.Controls;
using MangaFlow.Native.Services;

namespace MangaFlow.Native.Views;

/// <summary>NUI-6: usage &amp; cost dashboard \u2014 filters, KPI, trend, attempts keyset paging, budget, CSV.</summary>
public sealed partial class UsageView : WorkspaceView
{
    private readonly ScrollViewer scroller = new() { VerticalScrollBarVisibility = ScrollBarVisibility.Auto };
    private readonly ComboBox rangeSelector = Selector("时间范围", 130);
    private readonly ComboBox projectSelector = Selector("按项目筛选", 170);
    private readonly ComboBox providerSelector = Selector("按供应商筛选", 150);
    private readonly ComboBox modelSelector = Selector("按模型筛选", 160);
    private readonly ComboBox channelSelector = Selector("按通道筛选", 110);
    private readonly UsageKpiPanel kpiRow = new();
    // Web usage-dashboard order: KPI \u2192 budget banner \u2192 trend \u2192 per-model breakdown.
    private readonly StackPanel budgetHost = new() { Margin = new Thickness(0, 14, 0, 0) };
    private readonly StackPanel breakdownHost = new();
    private readonly WrapPanel customRange = new() { Visibility = Visibility.Collapsed, Margin = new Thickness(0, 0, 0, 12) };
    private readonly DatePicker sinceDate = new() { SelectedDate = DateTime.Today.AddDays(-30), Width = 145 };
    private readonly DatePicker untilDate = new() { SelectedDate = DateTime.Today, Width = 145 };
    private readonly StackPanel trendHost = new();
    private readonly StackPanel attemptsTable = new();
    private readonly StackPanel billedTable = new();
    private readonly TextBlock summaryLine = new() { Style = (Style)Application.Current.FindResource("Caption") };
    // A14：汇总分区失败时的就地重试（与明细分区各自的错误 UI 互不顶替）。
    private readonly Button summaryRetry;
    private JsonElement summary;
    private readonly UsageAttemptFeed feed = new();
    private List<JsonElement> attempts => feed.Items;
    private string? nextCursor => feed.NextCursor;
    private bool updatingFilters;
    private int loadRevision;
    // A12/A13：维度（供应商/模型）与项目列表的独立来源与失败标记。
    private readonly StackPanel dimensionBar = new();
    private JsonElement facets;
    private string projectDimensionError = "", facetDimensionError = "";
    private UsageFilter? currentFilter;
    private StackPanel? attemptsFooterError;
    private readonly DrawerOverlay attemptDrawer = new() { DrawerWidth = 560 };

    public UsageView()
    {
        var panel = new StackPanel { Margin = new Thickness(28, 30, 28, 40), MaxWidth = 1480 };
        var filters = new WrapPanel { Margin = new Thickness(0, 0, 0, 14) };
        foreach (var (key, label) in new[] { ("7d", "近 7 天"), ("30d", "近 30 天"), ("month", "本月"), ("custom", "自定义") })
            rangeSelector.Items.Add(new ComboBoxItem { Tag = key, Content = label });
        rangeSelector.SelectedIndex = 1;
        rangeSelector.SelectionChanged += (_, _) =>
        {
            customRange.Visibility = (rangeSelector.SelectedItem as ComboBoxItem)?.Tag as string == "custom" ? Visibility.Visible : Visibility.Collapsed;
            _ = LoadAsync();
        };
        filters.Children.Add(FilterField("时间范围", rangeSelector));
        projectSelector.Margin = new Thickness(0);
        projectSelector.SelectionChanged += (_, _) => _ = LoadAsync();
        filters.Children.Add(FilterField("项目", projectSelector));
        providerSelector.Margin = new Thickness(0);
        providerSelector.SelectionChanged += (_, _) =>
        {
            if (updatingFilters) return;
            updatingFilters = true;
            modelSelector.SelectedIndex = 0;
            updatingFilters = false;
            // A12：供应商切换即时联动模型选项（facets 来源），清除不兼容模型。
            RenderDimensionOptions();
            _ = LoadAsync();
        };
        filters.Children.Add(FilterField("供应商", providerSelector));
        modelSelector.Margin = new Thickness(0);
        modelSelector.SelectionChanged += (_, _) => _ = LoadAsync();
        filters.Children.Add(FilterField("模型", modelSelector));
        channelSelector.Items.Add(new ComboBoxItem { Tag = "", Content = "全部通道" });
        channelSelector.Items.Add(new ComboBoxItem { Tag = "HTTP_API", Content = "HTTP API" });
        channelSelector.Items.Add(new ComboBoxItem { Tag = "CLI", Content = "CLI" });
        channelSelector.SelectedIndex = 0;
        channelSelector.SelectionChanged += (_, _) => _ = LoadAsync();
        channelSelector.Margin = new Thickness(0);
        filters.Children.Add(FilterField("通道", channelSelector));
        // A14：汇总分区独立的失败提示 + 重试；平时收起。
        summaryRetry = Kit.Act("重试汇总", async (_, _) => await ReloadSummaryPartitionAsync(), "Compact");
        summaryRetry.Visibility = Visibility.Collapsed;
        summaryRetry.Margin = new Thickness(0, 4, 0, 0);
        var refresh = Kit.Act("刷新", async (_, _) =>
        {
            // A13：刷新同时重取项目列表与维度（facets）——新建项目/新供应商
            // 不再要求重进页面才出现在下拉里。
            await LoadProjectsAsync();
            await LoadFacetsAsync();
            await LoadAsync();
        }, "Compact");
        refresh.Margin = new Thickness(12, 0, 0, 0);
        var export = Kit.Act("导出 CSV", (_, _) => ExportCsv(), "Compact");
        export.Margin = new Thickness(8, 0, 0, 0);
        refresh.VerticalAlignment = VerticalAlignment.Bottom; export.VerticalAlignment = VerticalAlignment.Bottom;
        var filterActions = new WrapPanel { VerticalAlignment = VerticalAlignment.Bottom, Margin = new Thickness(0, 0, 0, 8) };
        filterActions.Children.Add(refresh); filterActions.Children.Add(export); filters.Children.Add(filterActions);
        var filterCard = new StackPanel();
        filterCard.Children.Add(filters);
        // A12/A13：项目/维度读取失败的就地提示与重试入口；无错误时零占位。
        dimensionBar.Visibility = Visibility.Collapsed;
        filterCard.Children.Add(dimensionBar);
        panel.Children.Add(new Border { Padding = new Thickness(14), BorderBrush = AssetPageUi.Brush("LineDark"), BorderThickness = new Thickness(1),
            Background = AssetPageUi.Brush("Surface"), Child = filterCard, Margin = new Thickness(0, 0, 0, 14) });
        customRange.Children.Add(Kit.FieldLabel("从 "));
        customRange.Children.Add(sinceDate);
        customRange.Children.Add(Kit.FieldLabel(" 至 "));
        customRange.Children.Add(untilDate);
        customRange.Children.Add(Kit.Act("应用日期", async (_, _) => await LoadAsync(), "Compact"));
        System.Windows.Automation.AutomationProperties.SetName(sinceDate, "开始日期");
        System.Windows.Automation.AutomationProperties.SetName(untilDate, "结束日期（含当天）");
        panel.Children.Add(customRange);
        panel.Children.Add(summaryLine);
        panel.Children.Add(summaryRetry);
        kpiRow.Margin = new Thickness(0, 10, 0, 0);
        panel.Children.Add(kpiRow);
        panel.Children.Add(budgetHost);
        panel.Children.Add(WrapCard("费用与调用趋势", trendHost, BuildTrendActions()));
        panel.Children.Add(WrapCard("供应商与模型分解", breakdownHost));
        panel.Children.Add(WrapCard("调用明细", attemptsTable));
        panel.Children.Add(WrapCard("账单对账记录", billedTable));
        panel.Children.Add(new TextBlock
        {
            Text = "计量语义：账单（对账导入）与估算（价格表推算）永不相加；不同币种不做隐式换算；未知 \u2260 0；CLI 通道费用未知 \u2260 免费。通道筛选作用于调用明细；汇总接口按时间/项目/供应商/模型聚合。",
            Style = (Style)Application.Current.FindResource("Micro"), Margin = new Thickness(0, 12, 0, 0), TextWrapping = TextWrapping.Wrap,
        });
        scroller.Content = panel;
        BuildUsageShell();
        var shell = Content;
        Content = null;
        var overlay = new Grid();
        if (shell is UIElement host) overlay.Children.Add(host);
        overlay.Children.Add(attemptDrawer);
        Content = overlay;
    }

    public override async void Activate(WorkspaceContext context)
    {
        base.Activate(context);
        // A13：项目列表读取失败不中止用量数据加载（两者数据独立），失败也不
        // 伪装成“没有项目”——下拉保留现状，就地给出可重试的错误。
        await LoadProjectsAsync();
        await LoadFacetsAsync();
        await LoadAsync();
    }

    private async Task LoadProjectsAsync()
    {
        try
        {
            var projects = await Api.SendAsync("projects", cancellation: lifetime.Token);
            if (lifetime.Token.IsCancellationRequested) return;
            updatingFilters = true;
            try
            {
                var selected = (projectSelector.SelectedItem as ComboBoxItem)?.Tag as string;
                projectSelector.Items.Clear();
                projectSelector.Items.Add(new ComboBoxItem { Tag = "", Content = "全部项目" });
                foreach (var project in projects.EnumerateArray())
                    projectSelector.Items.Add(new ComboBoxItem { Tag = project.Text("id"), Content = project.Text("name") });
                projectSelector.SelectedItem = projectSelector.Items.OfType<ComboBoxItem>().FirstOrDefault(i => (string?)i.Tag == selected)
                    ?? projectSelector.Items[0];
            }
            finally { updatingFilters = false; }
            projectDimensionError = "";
            RenderDimensionErrors();
        }
        catch (OperationCanceledException) { }
        catch (Exception error) when (error is not OperationCanceledException)
        {
            projectDimensionError = error.Message.Split('\n')[0];
            RenderDimensionErrors();
        }
    }

    /// <summary>A12：维度（供应商/模型）的独立来源——不带筛选的汇总（facets），
    /// 对应 web usage-facets 查询。选项完整稳定，不随当前汇总结果增减。</summary>
    private async Task LoadFacetsAsync()
    {
        try
        {
            var loaded = await Api.SendAsync(UsageFilter.FacetsPath(), cancellation: lifetime.Token);
            if (lifetime.Token.IsCancellationRequested) return;
            facets = loaded;
            facetDimensionError = "";
            RenderDimensionErrors();
            RenderDimensionOptions();
        }
        catch (OperationCanceledException) { }
        catch (Exception error) when (error is not OperationCanceledException)
        {
            facetDimensionError = error.Message.Split('\n')[0];
            RenderDimensionErrors();
        }
    }

    private void RenderDimensionErrors()
    {
        dimensionBar.Children.Clear();
        if (projectDimensionError.Length == 0 && facetDimensionError.Length == 0)
        {
            dimensionBar.Visibility = Visibility.Collapsed;
            return;
        }
        var row = new WrapPanel { Margin = new Thickness(0, 10, 0, 0) };
        var label = "筛选选项读取失败"
            + (projectDimensionError.Length > 0 ? "（项目）" : "")
            + (facetDimensionError.Length > 0 ? "（供应商 / 模型）" : "")
            + "，下拉可能缺少选项";
        row.Children.Add(new TextBlock
        {
            Text = label,
            Style = (Style)Application.Current.FindResource("Caption"),
            Foreground = (Brush)Application.Current.FindResource("Danger"),
            VerticalAlignment = VerticalAlignment.Center,
            TextWrapping = TextWrapping.Wrap,
        });
        var details = string.Join("\n", new[] { projectDimensionError, facetDimensionError }.Where(m => m.Length > 0));
        row.ToolTip = details;
        if (projectDimensionError.Length > 0)
        {
            var retryProjects = Kit.Act("重试项目", async (_, _) => await LoadProjectsAsync(), "Compact");
            retryProjects.Margin = new Thickness(10, 0, 0, 0);
            row.Children.Add(retryProjects);
        }
        if (facetDimensionError.Length > 0)
        {
            var retryFacets = Kit.Act("重试供应商 / 模型", async (_, _) => await LoadFacetsAsync(), "Compact");
            retryFacets.Margin = new Thickness(10, 0, 0, 0);
            row.Children.Add(retryFacets);
        }
        dimensionBar.Children.Add(row);
        dimensionBar.Visibility = Visibility.Visible;
    }

    /// <summary>A12：按 facets 重建供应商/模型选项；模型跟随所选供应商联动，
    /// 不兼容的已选模型回退“全部”。updatingFilters 抑制选择器的重入加载。</summary>
    private void RenderDimensionOptions()
    {
        if (facets.ValueKind != JsonValueKind.Object) return;
        updatingFilters = true;
        try
        {
            var groups = facets.Array("groups");
            var selectedProvider = (providerSelector.SelectedItem as ComboBoxItem)?.Tag as string ?? "";
            var selectedModel = (modelSelector.SelectedItem as ComboBoxItem)?.Tag as string ?? "";
            void Fill(ComboBox selector, string all, IEnumerable<string> values, string current)
            {
                selector.Items.Clear();
                selector.Items.Add(new ComboBoxItem { Tag = "", Content = all });
                foreach (var value in values.Where(v => v.Length > 0).Distinct().OrderBy(v => v, StringComparer.Ordinal))
                    selector.Items.Add(new ComboBoxItem { Tag = value, Content = value });
                selector.SelectedItem = selector.Items.OfType<ComboBoxItem>().FirstOrDefault(i => (string?)i.Tag == current)
                    ?? selector.Items[0];
            }
            Fill(providerSelector, "全部供应商", groups.Select(g => g.Text("provider")), selectedProvider);
            var modelGroups = selectedProvider.Length == 0
                ? groups
                : groups.Where(g => g.Text("provider") == selectedProvider).ToList();
            Fill(modelSelector, "全部模型", modelGroups.Select(g => g.Text("model_id")), selectedModel);
        }
        finally { updatingFilters = false; }
    }

    private UsageFilter CaptureFilter()
    {
        var key = (rangeSelector.SelectedItem as ComboBoxItem)?.Tag as string ?? "30d";
        var now = DateTimeOffset.Now;
        var since = key switch
        {
            "7d" => now.AddDays(-7),
            "month" => new DateTimeOffset(new DateTime(now.Year, now.Month, 1), now.Offset),
            "custom" => sinceDate.SelectedDate is { } start ? new DateTimeOffset(start) : throw new ArgumentException("请选择开始日期。"),
            _ => now.AddDays(-30),
        };
        var until = key == "custom" ? untilDate.SelectedDate is { } end ? new DateTimeOffset(end.AddDays(1))
            : throw new ArgumentException("请选择结束日期。") : now;
        static string? Selected(ComboBox selector) => (selector.SelectedItem as ComboBoxItem)?.Tag as string;
        var filter = new UsageFilter(since, until, Selected(projectSelector), Selected(providerSelector), Selected(modelSelector), Selected(channelSelector));
        filter.Validate();
        return filter;
    }

    private async Task LoadAsync()
    {
        if (Context == null || updatingFilters || lifetime.IsCancellationRequested) return;
        var request = ++loadRevision;
        var token = lifetime.Token;
        feed.Reset();
        attemptsTable.Children.Clear();
        summary = default;
        kpiRow.Children.Clear();
        budgetHost.Children.Clear();
        trendHost.Children.Clear();
        breakdownHost.Children.Clear();
        billedTable.Children.Clear();
        budgetFormOpen = false;
        attemptsFooterError = null;
        summaryRetry.Visibility = Visibility.Collapsed;
        UsageFilter filter;
        try
        {
            filter = CaptureFilter();
        }
        catch (ArgumentException error)
        {
            if (!token.IsCancellationRequested && request == loadRevision)
                summaryLine.Text = $"用量数据加载失败：{error.Message}";
            return;
        }
        currentFilter = filter;
        summaryLine.Text = "正在读取用量\u2026";
        // A14：汇总与明细是独立分区（web 的两个 query）。任一失败不阻止另一块
        // 渲染，失败分区就地提示并提供重试；过时响应仍由 loadRevision 隔离。
        var summaryTask = Api.SendAsync(filter.SummaryPath(), cancellation: token);
        var attemptsTask = feed.LoadAsync(Api, filter, token);
        Exception? summaryError = null, attemptsError = null;
        try { await summaryTask; } catch (Exception error) when (error is not OperationCanceledException) { summaryError = error; }
        try { await attemptsTask; } catch (Exception error) when (error is not OperationCanceledException) { attemptsError = error; }
        if (token.IsCancellationRequested || request != loadRevision) return;
        if (summaryError == null)
        {
            summary = summaryTask.Result;
            RenderSummary();
        }
        else
        {
            summaryLine.Text = $"用量汇总读取失败：{summaryError.Message.Split('\n')[0]}";
            summaryRetry.Visibility = Visibility.Visible;
        }
        if (attemptsError == null) RenderAttempts();
        else RenderAttemptsError(attemptsError.Message.Split('\n')[0]);
    }

    private async Task ReloadSummaryPartitionAsync()
    {
        if (currentFilter == null || lifetime.IsCancellationRequested) return;
        var request = loadRevision;
        var token = lifetime.Token;
        try
        {
            var loaded = await Api.SendAsync(currentFilter.SummaryPath(), cancellation: token);
            if (!token.IsCancellationRequested && request == loadRevision)
            {
                summary = loaded;
                RenderSummary();
                summaryRetry.Visibility = Visibility.Collapsed;
            }
        }
        catch (OperationCanceledException) { }
        catch (Exception error) when (error is not OperationCanceledException)
        {
            if (!token.IsCancellationRequested && request == loadRevision)
            {
                summaryLine.Text = $"用量汇总读取失败：{error.Message.Split('\n')[0]}";
                summaryRetry.Visibility = Visibility.Visible;
            }
        }
    }

    private async Task ReloadAttemptsPartitionAsync()
    {
        if (currentFilter == null || lifetime.IsCancellationRequested) return;
        var request = loadRevision;
        var token = lifetime.Token;
        try
        {
            feed.Reset();
            attemptsTable.Children.Clear();
            attemptsFooterError = null;
            var loaded = await feed.LoadAsync(Api, currentFilter, token);
            if (!token.IsCancellationRequested && request == loadRevision && loaded) RenderAttempts();
        }
        catch (OperationCanceledException) { }
        catch (Exception error) when (error is not OperationCanceledException)
        {
            if (!token.IsCancellationRequested && request == loadRevision)
                RenderAttemptsError(error.Message.Split('\n')[0]);
        }
    }

    private void RenderAttemptsError(string message)
    {
        attemptsTable.Children.Clear();
        attemptsFooterError = null;
        var row = new StackPanel { Margin = new Thickness(14, 10, 14, 10) };
        row.Children.Add(new TextBlock
        {
            Text = $"调用明细读取失败：{message}",
            Style = (Style)Application.Current.FindResource("Caption"),
            Foreground = (Brush)Application.Current.FindResource("Danger"),
            TextWrapping = TextWrapping.Wrap,
        });
        var retry = Kit.Act("重试明细", async (_, _) => await ReloadAttemptsPartitionAsync(), "Compact");
        retry.Margin = new Thickness(0, 8, 0, 0);
        retry.HorizontalAlignment = HorizontalAlignment.Left;
        row.Children.Add(retry);
        attemptsTable.Children.Add(row);
    }

    private void RenderSummary()
    {
        kpiRow.Children.Clear();
        trendHost.Children.Clear();
        billedTable.Children.Clear();
        var groups = summary.Array("groups");
        var billed = summary.Array("billed");
        if (groups.Count == 0 && billed.Count == 0)
        {
            summaryLine.Text = "暂无调用记录。发起剧本分析或单页生成后即可在此查看用量统计。";
            RenderBudget(groups);
            RenderTrend(groups);
            RenderBreakdown(groups);
            RenderBilled(billed);
            return;
        }
        summaryLine.Text = "";
        var attemptsTotal = groups.Sum(g => g.Number("attempt_count"));
        var succeeded = groups.Sum(g => g.Number("succeeded_count"));
        var failed = groups.Sum(g => g.Number("failed_count"));
        var pending = groups.Sum(g => g.Number("pending_count"));
        var rate = attemptsTotal > 0 ? (double)succeeded / attemptsTotal * 100 : double.NaN;
        var estimated = groups.SelectMany(g => g.Array("estimated_costs"))
            .GroupBy(c => c.Text("currency"))
            .OrderBy(c => c.Key)
            .Select(currency => (Currency: currency.Key, Sum: currency.Sum(c => Money(c))))
            .ToList();
        var billedBy = billed.GroupBy(b => b.Text("currency"))
            .OrderBy(b => b.Key)
            .Select(currency => (Currency: currency.Key, Sum: currency.Sum(b => Money(b, "billed_amount"))))
            .ToList();
        kpiRow.Children.Add(KpiCard("调用总览", $"{attemptsTotal} 次",
            $"成功 {succeeded} / 失败 {failed} / 未决 {pending} · 成功率 {(double.IsNaN(rate) ? "未知" : $"{rate:0.#}%")}"));
        kpiRow.Children.Add(KpiCard("估算支出",
            estimated.Count == 0 ? "无估算数据" : string.Join("\n", estimated.Select(e => $"\u2248 {Symbol(e.Currency)}{e.Sum:0.00}")),
            "估算值不等于供应商账单"));
        kpiRow.Children.Add(KpiCard("账单支出",
            billedBy.Count == 0 ? "暂无对账记录" : string.Join("\n", billedBy.Select(b => $"{Symbol(b.Currency)}{b.Sum:0.00}")),
            "账单事实与估算永不相加"));
        kpiRow.Children.Add(TokenKpi(groups));
        RenderBudget(groups);
        RenderTrend(groups);
        RenderBreakdown(groups);
        RenderBilled(billed);
    }

    // ============ 预算横幅（web components/usage/usage-budget-banner.tsx） ============
    // Web stores the budget in localStorage key "mangaflow.usage-budget" as
    // {"currency":"CNY","amount":"120"}; the desktop keeps the same key and JSON
    // shape in KeyValueStore. Only the estimated totals of the chosen currency
    // are compared \u2014 billed facts never enter the ratio.

    private const string BudgetStorageKey = "mangaflow.usage-budget";
    private bool budgetFormOpen;

    /// <summary>
    /// 金额读取：pydantic 把 Decimal 序列化为 JSON 字符串（"60.00"），网页端
    /// 统一用 Number() 解析；全局 Decimal() 扩展只认 Number 值，字符串金额
    /// 会静默归零（预算对比与明细因此失效）。这里接受 Number 与可解析字符串
    /// 两种形态（Invariant），与网页语义一致。
    /// </summary>
    internal static double Money(JsonElement element, string name = "amount")
    {
        if (element.ValueKind != JsonValueKind.Object || !element.TryGetProperty(name, out var value)) return 0;
        return value.ValueKind switch
        {
            JsonValueKind.Number when value.TryGetDouble(out var number) => number,
            JsonValueKind.String when double.TryParse(value.GetString(), System.Globalization.NumberStyles.Float,
                System.Globalization.CultureInfo.InvariantCulture, out var parsed) => parsed,
            _ => 0,
        };
    }

    internal readonly record struct UsageBudget(string Currency, double Amount);

    /// <summary>Same acceptance rule as web parseBudget: uppercase 3-letter currency and a finite positive amount.</summary>
    internal static UsageBudget? ParseBudget(string? raw)
    {
        if (raw is not { Length: > 0 }) return null;
        try
        {
            using var parsed = JsonDocument.Parse(raw);
            var root = parsed.RootElement;
            if (root.ValueKind != JsonValueKind.Object) return null;
            var currency = root.Text("currency");
            var amount = root.Text("amount");
            if (currency.Length != 3 || currency.Any(c => c is < 'A' or > 'Z')) return null;
            if (!double.TryParse(amount, System.Globalization.NumberStyles.Float, System.Globalization.CultureInfo.InvariantCulture, out var value)
                || double.IsNaN(value) || double.IsInfinity(value) || value <= 0) return null;
            return new UsageBudget(currency, value);
        }
        catch (JsonException) { return null; }
    }

    /// <summary>
    /// Web banner states: idle (no budget / no spend in that currency), over
    /// (ratio &gt; 1, role=alert), near (ratio &gt;= 0.8), ok otherwise. The tuple's
    /// first item is the CSS-class-like tone, the second the exact status line.
    /// </summary>
    internal static (string Tone, string Text) BudgetStatus(UsageBudget? budget, double spentInCurrency)
    {
        if (budget is not { } current) return ("idle", "尚未设置预算提醒");
        var budgetText = $"{Symbol(current.Currency)}{current.Amount:0.00}";
        if (spentInCurrency <= 0)
            return ("idle", $"所选范围无 {current.Currency} 估算支出，无法对比预算 {budgetText}");
        var ratio = spentInCurrency / current.Amount;
        if (ratio > 1) return ("over", $"估算支出 {Symbol(current.Currency)}{spentInCurrency:0.00} 已超出预算 {budgetText}");
        if (ratio >= 0.8) return ("near", $"估算支出 {Symbol(current.Currency)}{spentInCurrency:0.00} 已接近预算 {budgetText}");
        return ("ok", $"估算支出 {Symbol(current.Currency)}{spentInCurrency:0.00} 在预算 {budgetText} 内");
    }

    private void RenderBudget(List<JsonElement> groups)
    {
        budgetHost.Children.Clear();
        var budget = ParseBudget(KeyValueStore.Get(BudgetStorageKey));
        var spent = groups.SelectMany(g => g.Array("estimated_costs"))
            .Where(c => c.Text("currency") == budget?.Currency)
            .Sum(c => Money(c));
        var (tone, status) = BudgetStatus(budget, spent);
        var banner = new Border
        {
            Style = (Style)Application.Current.FindResource("Card"),
            Padding = new Thickness(14),
            Tag = tone,
        };
        System.Windows.Automation.AutomationProperties.SetName(banner, "预算提醒");
        var row = new UsageBudgetRow();
        var toneBrush = tone switch
        {
            "over" => (Brush)Application.Current.FindResource("Danger"),
            "near" => (Brush)Application.Current.FindResource("AccentInk"),
            "ok" => (Brush)Application.Current.FindResource("Success"),
            _ => (Brush)Application.Current.FindResource("Muted"),
        };
        row.Children.Add(new TextBlock
        {
            Text = status + (budget is not null ? "（仅对比估算支出，不含账单事实）" : ""),
            FontWeight = FontWeights.Bold,
            Foreground = toneBrush,
            VerticalAlignment = VerticalAlignment.Center,
            TextWrapping = TextWrapping.Wrap,
        });
        if (!budgetFormOpen)
        {
            var open = Kit.Act("设置预算", (_, _) => { budgetFormOpen = true; RenderBudget(groups); }, "Compact");
            open.Margin = new Thickness(12, 0, 0, 0);
            open.VerticalAlignment = VerticalAlignment.Center;
            row.Children.Add(open);
            banner.Child = row;
            budgetHost.Children.Add(banner);
            return;
        }
        banner.Child = row;
        budgetHost.Children.Add(banner);
        var currencyInput = new TextBox { Width = 64, MaxLength = 3, Text = budget?.Currency ?? "" };
        System.Windows.Automation.AutomationProperties.SetName(currencyInput, "预算币种");
        var amountInput = new TextBox { Width = 110, Text = budget is { } kept ? kept.Amount.ToString("0.##", System.Globalization.CultureInfo.InvariantCulture) : "" };
        System.Windows.Automation.AutomationProperties.SetName(amountInput, "预算金额");
        var hint = new TextBlock { Style = (Style)Application.Current.FindResource("Micro"), VerticalAlignment = VerticalAlignment.Center };
        var form = new WrapPanel { Margin = new Thickness(0, 10, 0, 0) };
        form.Children.Add(FilterField("币种", currencyInput));
        form.Children.Add(new TextBlock { Text = " ", Width = 6 });
        form.Children.Add(FilterField("预算金额", amountInput));
        var save = Kit.Act("保存", (_, _) =>
        {
            var currency = currencyInput.Text.Trim().ToUpperInvariant();
            if (currency.Length != 3 || currency.Any(c => c is < 'A' or > 'Z')
                || !double.TryParse(amountInput.Text.Trim(), System.Globalization.NumberStyles.Float, System.Globalization.CultureInfo.InvariantCulture, out var amount)
                || double.IsNaN(amount) || double.IsInfinity(amount) || amount <= 0)
            {
                // Web save() silently keeps the form; surface the same acceptance rule.
                hint.Text = "币种为 3 位大写字母，金额须大于 0。";
                return;
            }
            var stored = "{\"currency\":\"" + currency + "\",\"amount\":\""
                + amount.ToString("0.##########", System.Globalization.CultureInfo.InvariantCulture) + "\"}";
            KeyValueStore.Set(BudgetStorageKey, stored);
            budgetFormOpen = false;
            RenderBudget(groups);
        }, "Compact");
        save.Margin = new Thickness(8, 0, 0, 0);
        form.Children.Add(save);
        if (budget is not null)
        {
            var clear = Kit.Act("清除", (_, _) =>
            {
                KeyValueStore.Remove(BudgetStorageKey);
                budgetFormOpen = false;
                RenderBudget(groups);
            }, "Compact");
            clear.Margin = new Thickness(6, 0, 0, 0);
            form.Children.Add(clear);
        }
        form.Children.Add(hint);
        banner.Child = null;
        var column = new StackPanel { Children = { row, form } };
        banner.Child = column;
    }

    // ============ 供应商与模型分解（web components/usage/usage-breakdown-table.tsx） ============
    // Aggregates the summary groups to provider/model/channel rows; token and
    // image sums stay null ("未知") unless at least one group measured them,
    // amounts never merge across currencies, and mixed cost modes show 混合.

    private void RenderBreakdown(List<JsonElement> groups)
    {
        breakdownHost.Children.Clear();
        if (groups.Count == 0)
        {
            breakdownHost.Children.Add(Kit.Caption("所选范围内暂无供应商与模型汇总。"));
            return;
        }
        var header = BreakdownRow("供应商 / 模型", "通道", "调用", "成功 / 失败 / 未决", "输入 / 输出 Token", "缓存命中", "图片", "估算金额（原币种）", "成本语义");
        header.Background = AssetPageUi.Brush("PaperDeep");
        breakdownHost.Children.Add(header);
        var rows = groups.GroupBy(g => (Provider: g.Text("provider"), Model: g.Text("model_id"), Channel: g.Text("channel")))
            .OrderBy(r => r.Key.Provider, StringComparer.Ordinal)
            .ThenBy(r => r.Key.Model, StringComparer.Ordinal)
            .ThenBy(r => r.Key.Channel, StringComparer.Ordinal);
        foreach (var entry in rows)
        {
            var cells = entry.ToList();
            var attempts = cells.Sum(g => g.Number("attempt_count"));
            var succeeded = cells.Sum(g => g.Number("succeeded_count"));
            var failed = cells.Sum(g => g.Number("failed_count"));
            var pending = cells.Sum(g => g.Number("pending_count"));
            static long? SumPresent(List<JsonElement> list, string field)
            {
                long total = 0;
                var present = false;
                foreach (var group in list)
                    if (group.Element(field).ValueKind == JsonValueKind.Number)
                    {
                        total += group.Number(field);
                        present = true;
                    }
                return present ? total : null;
            }
            var input = SumPresent(cells, "input_tokens");
            var output = SumPresent(cells, "output_tokens");
            var cached = SumPresent(cells, "cached_input_tokens");
            var images = SumPresent(cells, "output_images");
            var costs = cells.SelectMany(g => g.Array("estimated_costs"))
                .GroupBy(c => c.Text("currency"))
                .OrderBy(c => c.Key)
                .Select(currency => $"\u2248 {Symbol(currency.Key)}{currency.Sum(c => Money(c)):0.0000}")
                .ToList();
            var modes = cells.Select(GroupCostMode).Distinct().ToList();
            var mode = modes.Count == 1 ? modes[0] : "MIXED";
            breakdownHost.Children.Add(BreakdownRow(
                $"{entry.Key.Provider} · {entry.Key.Model}",
                entry.Key.Channel == "CLI" ? "CLI" : "HTTP API",
                attempts.ToString(),
                $"{succeeded} / {failed} / {pending}",
                input is null && output is null ? "未知" : $"{input?.ToString() ?? "未知"} / {output?.ToString() ?? "未知"}",
                cached?.ToString() ?? "未知",
                images?.ToString() ?? "未知",
                costs.Count == 0 ? "无估算数据" : string.Join("\n", costs),
                mode == "MIXED" ? "混合" : Labels.Map(Labels.CostMode, mode)));
        }
        breakdownHost.Children.Add(new TextBlock
        {
            Text = "估算金额按币种分行展示，不同币种永不相加；估算值不等于供应商账单。",
            Style = (Style)Application.Current.FindResource("Micro"), Margin = new Thickness(0, 8, 0, 0), TextWrapping = TextWrapping.Wrap,
        });
    }

    /// <summary>Group-level cost semantics driven by summary aggregates only (web groupCostMode).</summary>
    internal static string GroupCostMode(JsonElement group)
    {
        if (group.Array("estimated_costs").Count > 0) return "ESTIMATED";
        if (group.Element("input_tokens").ValueKind == JsonValueKind.Number
            || group.Element("output_tokens").ValueKind == JsonValueKind.Number
            || group.Element("cached_input_tokens").ValueKind == JsonValueKind.Number
            || group.Element("output_images").ValueKind == JsonValueKind.Number) return "USAGE_ONLY";
        return "UNKNOWN";
    }

    private static string Trim(string value, int width) => value.Length <= width ? value : value[..(width - 1)] + "\u2026";

    private static string Symbol(string currency) => currency switch
    {
        "CNY" => "\u00a5", "USD" => "$", "EUR" => "\u20ac", "GBP" => "\u00a3", "JPY" => "JP\u00a5", "HKD" => "HK$", _ => currency + " ",
    };

    private async Task LoadMoreAsync()
    {
        var request = loadRevision;
        var token = lifetime.Token;
        try
        {
            if (await feed.LoadMoreAsync(Api, token) && request == loadRevision) RenderAttempts();
        }
        catch (OperationCanceledException) { }
        catch (Exception error)
        {
            // A14：分页错误就地显示在明细表尾（用户视线下方），不再只写页面顶部。
            if (!token.IsCancellationRequested && request == loadRevision)
                ShowAttemptsFooterError($"加载更多失败，可重试：{error.Message.Split('\n')[0]}");
        }
    }

    private void ShowAttemptsFooterError(string message)
    {
        ClearAttemptsFooterError();
        var row = new StackPanel { Margin = new Thickness(14, 8, 14, 10) };
        row.Children.Add(new TextBlock
        {
            Text = message,
            Style = (Style)Application.Current.FindResource("Caption"),
            Foreground = (Brush)Application.Current.FindResource("Danger"),
            TextWrapping = TextWrapping.Wrap,
        });
        var retry = Kit.Act("重试加载更多", async (_, _) => await LoadMoreAsync(), "Compact");
        retry.Margin = new Thickness(0, 6, 0, 0);
        retry.HorizontalAlignment = HorizontalAlignment.Left;
        row.Children.Add(retry);
        attemptsFooterError = row;
        attemptsTable.Children.Add(row);
    }

    private void ClearAttemptsFooterError()
    {
        if (attemptsFooterError == null) return;
        attemptsTable.Children.Remove(attemptsFooterError);
        attemptsFooterError = null;
    }

    private static string CostModeOf(JsonElement attempt)
    {
        if (attempt.Text("usage_source") == "OPERATOR_BILLED") return "BILLED";
        if (attempt.Element("input_tokens").ValueKind == JsonValueKind.Number
            || attempt.Element("output_tokens").ValueKind == JsonValueKind.Number
            || attempt.Element("output_images").ValueKind == JsonValueKind.Number) return "USAGE_ONLY";
        if (attempt.Element("usage").ValueKind is JsonValueKind.Null or JsonValueKind.Undefined
            && attempt.Text("outcome") == "SUCCEEDED") return "UNAVAILABLE";
        return "UNKNOWN";
    }

    /// <summary>A11：金额单元格的稳定序列化。pydantic Decimal 序列化为字符串
    /// （"60.00"）——原样透传（web csvCell(String(value)) 同款）；数字形态用
    /// InvariantCulture 定点格式，逗号小数文化不再破坏列。未知金额输出空串，
    /// 不写成 0。</summary>
    internal static string AmountText(JsonElement element, string name)
    {
        if (element.ValueKind != JsonValueKind.Object || !element.TryGetProperty(name, out var value)) return "";
        return value.ValueKind switch
        {
            JsonValueKind.Number => value.TryGetDouble(out var number)
                ? number.ToString("0.####################", System.Globalization.CultureInfo.InvariantCulture) : "",
            JsonValueKind.String => value.GetString() ?? "",
            _ => "",
        };
    }

    /// <summary>A11：CSV 序列化的纯函数（web buildUsageCsv 逐块对齐）——估算区块
    /// 每币种一行；账单对账记录作为第二区块（有账单才出现表头），账单事实永不
    /// 与估算相加。行尾 \r\n、区块间空行、UTF-8 BOM 由调用方写入。</summary>
    internal static string BuildUsageCsv(JsonElement summary)
    {
        var builder = new StringBuilder();
        void Row(params string[] cells)
        {
            for (var i = 0; i < cells.Length; i++)
            {
                if (i > 0) builder.Append(',');
                builder.Append(Csv(cells[i]));
            }
            builder.Append("\r\n");
        }
        Row("日期", "供应商", "模型ID", "通道", "调用次数", "成功", "失败", "未决",
            "输入Token", "输出Token", "缓存命中Token", "输出图片张数", "计量状态分布", "估算币种", "估算金额（原币种）");
        foreach (var group in summary.Array("groups"))
        {
            var costs = group.Array("estimated_costs");
            var rows = costs.Count == 0
                ? [("", "")]
                : costs.Select(c => (c.Text("currency"), AmountText(c, "amount"))).ToList();
            foreach (var (currency, amount) in rows)
                Row(group.Text("day"), group.Text("provider"), group.Text("model_id"), group.Text("channel"),
                    group.Number("attempt_count").ToString(), group.Number("succeeded_count").ToString(),
                    group.Number("failed_count").ToString(), group.Number("pending_count").ToString(),
                    NumberOrNull(group, "input_tokens"), NumberOrNull(group, "output_tokens"),
                    NumberOrNull(group, "cached_input_tokens"), NumberOrNull(group, "output_images"),
                    string.Join(" ", group.Element("usage_status_counts").EnumerateObject().Select(p => $"{p.Name}:{p.Value}")),
                    currency.Length == 0 ? "无估算数据" : currency, amount);
        }
        var billed = summary.Array("billed");
        if (billed.Count > 0)
        {
            builder.Append("\r\n");
            Row("类型", "账期开始", "账期结束", "供应商", "模型ID", "通道", "账单账户", "账单币种", "账单金额", "录入人", "备注");
            foreach (var item in billed)
                Row("账单对账", item.Text("period_start"), item.Text("period_end"), item.Text("provider"),
                    item.Text("model_id"), item.Text("channel"), item.Text("billing_account_id"),
                    item.Text("currency"), AmountText(item, "billed_amount"), item.Text("entered_by"), item.Text("source_note"));
        }
        return builder.ToString();
    }

    private void ExportCsv()
    {
        // Billed-only dashboards can export too (web)：账单事实往往正是运营下载
        // 文件的原因。
        if (summary.ValueKind != JsonValueKind.Object
            || (summary.Array("groups").Count == 0 && summary.Array("billed").Count == 0))
        {
            MessageBox.Show(Host, "尚无可导出的数据。", "导出 CSV");
            return;
        }
        var dialog = new Microsoft.Win32.SaveFileDialog
        {
            Title = "导出用量 CSV", Filter = "CSV|*.csv", FileName = $"usage-{DateTime.Now:yyyy-MM-dd}.csv",
        };
        if (dialog.ShowDialog(Host) != true) return;
        try
        {
            File.WriteAllText(dialog.FileName, "\uFEFF" + BuildUsageCsv(summary), new UTF8Encoding(false));
            MessageBox.Show(Host, "CSV 已导出。", "导出完成");
        }
        catch (Exception error)
        {
            MessageBox.Show(Host, error.Message, "导出未完成", MessageBoxButton.OK, MessageBoxImage.Warning);
        }
    }

    private static string NumberOrNull(JsonElement element, string name) =>
        element.Element(name).ValueKind == JsonValueKind.Number ? element.Number(name).ToString() : "";

    // CSV injection guard: prefix dangerous leading chars with ', quote when needed.
    private static string Csv(string value)
    {
        var dangerous = value.StartsWith("=") || value.StartsWith("+") || value.StartsWith("-") || value.StartsWith("@") || value.StartsWith("\t");
        var safe = dangerous ? "'" + value : value;
        return safe.Contains('"') || safe.Contains(',') || safe.Contains('\n') || safe.Contains('\r')
            ? "\"" + safe.Replace("\"", "\"\"") + "\""
            : safe;
    }

    public override Task RefreshAsync()
    {
        _ = LoadAsync();
        return Task.CompletedTask;
    }

    private void ShowAttemptDrawer(JsonElement attempt)
    {
        var scroll = new ScrollViewer { VerticalScrollBarVisibility = ScrollBarVisibility.Auto };
        var panel = new StackPanel { Margin = new Thickness(24) };
        var close = Kit.Act("关闭", (_, _) => attemptDrawer.Open = false, "Ghost");
        close.HorizontalAlignment = HorizontalAlignment.Right;
        System.Windows.Automation.AutomationProperties.SetName(close, "关闭");
        panel.Children.Add(close);
            panel.Children.Add(new TextBlock
            {
                Text = "调用尝试详情", FontFamily = (FontFamily)Application.Current.FindResource("Serif"),
                FontSize = 20, FontWeight = FontWeights.SemiBold,
            });
            panel.Children.Add(new TextBlock { Text = "CALL ATTEMPT", Style = (Style)Application.Current.FindResource("SectionIndex"), Margin = new Thickness(0, 4, 0, 12) });
            void Field(string label, string value)
            {
                panel.Children.Add(new TextBlock { Text = label, Style = (Style)Application.Current.FindResource("FieldLabel"), Margin = new Thickness(0, 10, 0, 2) });
                panel.Children.Add(new TextBlock { Text = value.Length > 0 ? value : "\u2014", TextWrapping = TextWrapping.Wrap, FontFamily = (FontFamily)Application.Current.FindResource("Mono"), FontSize = 12 });
            }
            var costMode = CostModeOf(attempt);
            Field("成本语义", $"{Labels.Map(Labels.CostMode, costMode)} · {Labels.Map(Labels.CostModeHint, costMode)}");
            Field("计量状态", Labels.Map(Labels.UsageStatus, attempt.Text("usage_status", "UNKNOWN")));
            Field("供应商 / 模型", $"{attempt.Text("provider")} · {attempt.Text("model_id")}");
            Field("通道", attempt.Text("channel"));
            Field("上游 Request ID", attempt.Text("request_id", "未返回"));
            Field("尝试序号", $"调度尝试 {attempt.Number("job_attempt")} · 第 {attempt.Number("dispatch_no")} 次派发" + (attempt.Flag("route_switched") ? " · 换路" : ""));
            Field("输入 / 输出 Token", $"{AttemptNumberOrNull(attempt, "input_tokens")} / {AttemptNumberOrNull(attempt, "output_tokens")}");
            Field("输出图片", attempt.Element("output_images").ValueKind == JsonValueKind.Number ? $"{attempt.Number("output_images")} 张" : "未知");
            Field("耗时", attempt.Element("duration_ms").ValueKind == JsonValueKind.Number ? $"{attempt.Number("duration_ms")} ms" : "未知");
            Field("结果", $"{Labels.Map(Labels.AttemptOutcome, attempt.Text("outcome"))}{(attempt.Text("error_code").Length > 0 ? $" · {attempt.Text("error_code")}" : "")}");
            if (attempt.Text("error_message").Length > 0)
                Field("错误信息", attempt.Text("error_message"));
            panel.Children.Add(new TextBlock
            {
                Text = "数据来自模型调用账本（已脱敏）· 未知 \u2260 0，CLI 通道费用未知 \u2260 免费",
                Style = (Style)Application.Current.FindResource("Micro"), Margin = new Thickness(0, 14, 0, 0), TextWrapping = TextWrapping.Wrap,
            });
        scroll.Content = panel;
        attemptDrawer.Content = scroll;
        attemptDrawer.Open = true;
    }

    private static string AttemptNumberOrNull(JsonElement element, string name) =>
        element.Element(name).ValueKind == JsonValueKind.Number ? element.Number(name).ToString() : "未知";
}
