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
public sealed class UsageView : WorkspaceView
{
    private readonly ScrollViewer scroller = new() { VerticalScrollBarVisibility = ScrollBarVisibility.Auto };
    private readonly ComboBox rangeSelector = Selector("时间范围", 130);
    private readonly ComboBox projectSelector = Selector("按项目筛选", 170);
    private readonly ComboBox providerSelector = Selector("按供应商筛选", 150);
    private readonly ComboBox modelSelector = Selector("按模型筛选", 160);
    private readonly ComboBox channelSelector = Selector("按通道筛选", 110);
    private readonly WrapPanel kpiRow = new();
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
    private JsonElement summary;
    private readonly UsageAttemptFeed feed = new();
    private List<JsonElement> attempts => feed.Items;
    private string? nextCursor => feed.NextCursor;
    private bool updatingFilters;
    private int loadRevision;

    public UsageView()
    {
        var panel = new StackPanel { Margin = new Thickness(36, 30, 36, 28) };
        panel.Children.Add(new TextBlock { Text = "SYSTEM / USAGE & COST", Style = (Style)Application.Current.FindResource("SectionIndex") });
        panel.Children.Add(new TextBlock
        {
            Text = "系统设置 / 用量与成本看板",
            FontFamily = (FontFamily)Application.Current.FindResource("Serif"),
            FontSize = 26, FontWeight = FontWeights.Bold, Margin = new Thickness(0, 6, 0, 16),
        });
        var filters = new WrapPanel { Margin = new Thickness(0, 0, 0, 14) };
        foreach (var (key, label) in new[] { ("7d", "近 7 天"), ("30d", "近 30 天"), ("month", "本月"), ("custom", "自定义") })
            rangeSelector.Items.Add(new ComboBoxItem { Tag = key, Content = label });
        rangeSelector.SelectedIndex = 1;
        rangeSelector.SelectionChanged += (_, _) =>
        {
            customRange.Visibility = (rangeSelector.SelectedItem as ComboBoxItem)?.Tag as string == "custom" ? Visibility.Visible : Visibility.Collapsed;
            _ = LoadAsync();
        };
        filters.Children.Add(rangeSelector);
        projectSelector.Margin = new Thickness(8, 0, 0, 0);
        projectSelector.SelectionChanged += (_, _) => _ = LoadAsync();
        filters.Children.Add(projectSelector);
        providerSelector.Margin = new Thickness(8, 0, 0, 0);
        providerSelector.SelectionChanged += (_, _) =>
        {
            if (updatingFilters) return;
            updatingFilters = true;
            modelSelector.SelectedIndex = 0;
            updatingFilters = false;
            _ = LoadAsync();
        };
        filters.Children.Add(providerSelector);
        modelSelector.Margin = new Thickness(8, 0, 0, 0);
        modelSelector.SelectionChanged += (_, _) => _ = LoadAsync();
        filters.Children.Add(modelSelector);
        channelSelector.Items.Add(new ComboBoxItem { Tag = "", Content = "通道" });
        channelSelector.Items.Add(new ComboBoxItem { Tag = "HTTP_API", Content = "HTTP API" });
        channelSelector.Items.Add(new ComboBoxItem { Tag = "CLI", Content = "CLI" });
        channelSelector.SelectedIndex = 0;
        channelSelector.SelectionChanged += (_, _) => _ = LoadAsync();
        channelSelector.Margin = new Thickness(8, 0, 0, 0);
        filters.Children.Add(channelSelector);
        var refresh = Kit.Act("刷新", async (_, _) => await LoadAsync(), "Compact");
        refresh.Margin = new Thickness(12, 0, 0, 0);
        filters.Children.Add(refresh);
        var export = Kit.Act("导出 CSV", (_, _) => ExportCsv(), "Compact");
        export.Margin = new Thickness(8, 0, 0, 0);
        filters.Children.Add(export);
        panel.Children.Add(filters);
        customRange.Children.Add(Kit.FieldLabel("从 "));
        customRange.Children.Add(sinceDate);
        customRange.Children.Add(Kit.FieldLabel(" 至 "));
        customRange.Children.Add(untilDate);
        customRange.Children.Add(Kit.Act("应用日期", async (_, _) => await LoadAsync(), "Compact"));
        System.Windows.Automation.AutomationProperties.SetName(sinceDate, "开始日期");
        System.Windows.Automation.AutomationProperties.SetName(untilDate, "结束日期（含当天）");
        panel.Children.Add(customRange);
        panel.Children.Add(summaryLine);
        kpiRow.Margin = new Thickness(0, 10, 0, 0);
        panel.Children.Add(kpiRow);
        panel.Children.Add(budgetHost);
        trendHost.Margin = new Thickness(0, 16, 0, 0);
        panel.Children.Add(WrapCard("费用与调用趋势", trendHost));
        panel.Children.Add(WrapCard("供应商与模型分解", breakdownHost));
        panel.Children.Add(WrapCard("调用明细", attemptsTable));
        panel.Children.Add(WrapCard("账单对账记录", billedTable));
        panel.Children.Add(new TextBlock
        {
            Text = "计量语义：账单（对账导入）与估算（价格表推算）永不相加；不同币种不做隐式换算；未知 \u2260 0；CLI 通道费用未知 \u2260 免费。通道筛选作用于调用明细；汇总接口按时间/项目/供应商/模型聚合。",
            Style = (Style)Application.Current.FindResource("Micro"), Margin = new Thickness(0, 12, 0, 0), TextWrapping = TextWrapping.Wrap,
        });
        scroller.Content = panel;
        Content = scroller;
    }

    private static Border WrapCard(string title, UIElement content) => new()
    {
        Style = (Style)Application.Current.FindResource("Card"),
        Padding = new Thickness(18),
        Margin = new Thickness(0, 0, 0, 14),
        Child = new StackPanel { Children = { new TextBlock { Text = title, FontWeight = FontWeights.Bold, FontSize = 14, Margin = new Thickness(0, 0, 0, 8) }, content } },
    };

    public override async void Activate(WorkspaceContext context)
    {
        base.Activate(context);
        try
        {
            var token = lifetime.Token;
            var projects = await Api.SendAsync("projects", cancellation: token);
            if (token.IsCancellationRequested) return;
            updatingFilters = true;
            var selected = (projectSelector.SelectedItem as ComboBoxItem)?.Tag as string;
            projectSelector.Items.Clear();
            projectSelector.Items.Add(new ComboBoxItem { Tag = "", Content = "全部项目" });
            foreach (var project in projects.EnumerateArray())
                projectSelector.Items.Add(new ComboBoxItem { Tag = project.Text("id"), Content = project.Text("name") });
            projectSelector.SelectedItem = projectSelector.Items.OfType<ComboBoxItem>().FirstOrDefault(i => (string?)i.Tag == selected)
                ?? projectSelector.Items[0];
            updatingFilters = false;
            await LoadAsync();
        }
        catch (OperationCanceledException) { }
        catch (Exception error) when (error is not OperationCanceledException)
        {
            summaryLine.Text = $"用量数据加载失败：{error.Message}";
        }
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
        try
        {
            var filter = CaptureFilter();
            summaryLine.Text = "正在读取用量\u2026";
            var summaryTask = Api.SendAsync(filter.SummaryPath(), cancellation: token);
            var attemptsTask = feed.LoadAsync(Api, filter, token);
            await Task.WhenAll(summaryTask, attemptsTask);
            if (token.IsCancellationRequested || request != loadRevision) return;
            summary = await summaryTask;
            RenderSummary();
            RenderAttempts();
            updatingFilters = true;
            try
            {
                void AddChoices(ComboBox selector, string all, IEnumerable<string> values)
                {
                    if (selector.Items.Count == 0) selector.Items.Add(new ComboBoxItem { Tag = "", Content = all });
                    foreach (var value in values.Distinct().OrderBy(v => v))
                        if (!selector.Items.OfType<ComboBoxItem>().Any(i => (string?)i.Tag == value))
                            selector.Items.Add(new ComboBoxItem { Tag = value, Content = value });
                    if (selector.SelectedIndex < 0) selector.SelectedIndex = 0;
                }
                AddChoices(providerSelector, "全部供应商", summary.Array("groups").Select(g => g.Text("provider")));
                AddChoices(modelSelector, "全部模型", summary.Array("groups").Select(g => g.Text("model_id")));
            }
            finally { updatingFilters = false; }
        }
        catch (OperationCanceledException) { }
        catch (Exception error)
        {
            if (!token.IsCancellationRequested && request == loadRevision)
                summaryLine.Text = $"用量数据加载失败：{error.Message}";
        }
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
        var row = new StackPanel { Orientation = Orientation.Horizontal };
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
        var amountInput = new TextBox { Width = 110, Text = budget is { } kept ? kept.Amount.ToString("0.##") : "" };
        System.Windows.Automation.AutomationProperties.SetName(amountInput, "预算金额");
        var hint = new TextBlock { Style = (Style)Application.Current.FindResource("Micro"), VerticalAlignment = VerticalAlignment.Center };
        var form = new StackPanel { Orientation = Orientation.Horizontal, Margin = new Thickness(0, 10, 0, 0) };
        form.Children.Add(currencyInput);
        form.Children.Add(new TextBlock { Text = " ", Width = 6 });
        form.Children.Add(amountInput);
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
        header.FontWeight = FontWeights.Bold;
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
                costs.Count == 0 ? "无估算数据" : string.Join("　", costs),
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

    private static TextBlock BreakdownRow(string providerModel, string channel, string calls, string outcomes,
        string tokens, string cached, string images, string costs, string mode) => new()
    {
        Text = $"{Trim(providerModel, 44),-44}  {channel,-9}  {calls,7}  {Trim(outcomes, 17),-17}  {Trim(tokens, 19),-19}  {Trim(cached, 8),-8}  {images,4}  {Trim(costs, 30),-30}  {mode}",
        FontFamily = (FontFamily)Application.Current.FindResource("Mono"),
        FontSize = 11.5,
        TextWrapping = TextWrapping.NoWrap,
        Margin = new Thickness(0, 3, 0, 3),
    };

    private static string Trim(string value, int width) => value.Length <= width ? value : value[..(width - 1)] + "\u2026";

    private static string Symbol(string currency) => currency switch
    {
        "CNY" => "\u00a5", "USD" => "$", "EUR" => "\u20ac", "GBP" => "\u00a3", "JPY" => "JP\u00a5", "HKD" => "HK$", _ => currency + " ",
    };

    private static Border KpiCard(string title, string value, string note) => new()
    {
        Style = (Style)Application.Current.FindResource("Card"),
        Padding = new Thickness(16),
        Margin = new Thickness(0, 0, 12, 0),
        MinWidth = 210,
        VerticalAlignment = VerticalAlignment.Top,
        Child = new StackPanel
        {
            Children =
            {
                new TextBlock { Text = title, Style = (Style)Application.Current.FindResource("Micro") },
                new TextBlock { Text = value, FontFamily = (FontFamily)Application.Current.FindResource("Serif"), FontSize = 22, FontWeight = FontWeights.Bold, Margin = new Thickness(0, 6, 0, 4) },
                new TextBlock { Text = note, Style = (Style)Application.Current.FindResource("Micro") },
            },
        },
    };

    private void RenderTrend(List<JsonElement> groups)
    {
        trendHost.Children.Clear();
        var byDay = groups.GroupBy(g => g.Text("day")).OrderBy(g => g.Key).ToList();
        if (byDay.Count == 0)
        {
            trendHost.Children.Add(Kit.Caption("所选范围内无用量记录。"));
            return;
        }
        const double chartWidth = 640, chartHeight = 140;
        var canvas = new Canvas { Width = chartWidth, Height = chartHeight, Background = (Brush)Application.Current.FindResource("PaperDeep") };
        var slot = chartWidth / byDay.Count;
        var max = byDay.Max(day => day.Sum(g => g.Number("attempt_count")));
        var palette = new[] { "#D34A2F", "#3F6D4E", "#3F5E8C", "#A8842C", "#6D4A7E" };
        var index = 0;
        foreach (var day in byDay)
        {
            var count = day.Sum(g => g.Number("attempt_count"));
            var barHeight = max == 0 ? 0 : Math.Max(2, count / (double)max * (chartHeight - 24));
            var bar = new Rectangle
            {
                Width = Math.Max(3, slot * 0.6),
                Height = barHeight,
                Fill = new SolidColorBrush((Color)ColorConverter.ConvertFromString(palette[index % palette.Length])),
                ToolTip = $"{day.Key} · {count} 次调用",
            };
            Canvas.SetLeft(bar, index * slot + slot * 0.2);
            Canvas.SetTop(bar, chartHeight - 16 - barHeight);
            canvas.Children.Add(bar);
            if (index % 3 == 0)
            {
                var label = new TextBlock { Text = day.Key.Length >= 10 ? day.Key[5..10] : day.Key, FontSize = 9, Foreground = (Brush)Application.Current.FindResource("Muted") };
                Canvas.SetLeft(label, index * slot);
                Canvas.SetTop(label, chartHeight - 14);
                canvas.Children.Add(label);
            }
            index++;
        }
        trendHost.Children.Add(canvas);
        trendHost.Children.Add(new TextBlock
        {
            Text = "按日调用次数分组柱状图；金额与 Token 明细见调用明细表。未知不等于 0。",
            Style = (Style)Application.Current.FindResource("Micro"), Margin = new Thickness(0, 8, 0, 0), TextWrapping = TextWrapping.Wrap,
        });
    }

    private void RenderAttempts()
    {
        attemptsTable.Children.Clear();
        attemptsTable.Children.Add(new TextBlock
        {
            Text = $"已加载 {attempts.Count} 条 · 最新调用在前",
            Style = (Style)Application.Current.FindResource("Micro"), Margin = new Thickness(0, 0, 0, 8),
        });
        if (attempts.Count == 0)
        {
            attemptsTable.Children.Add(Kit.Caption("该范围暂无调用尝试记录。"));
            return;
        }
        foreach (var attempt in attempts)
        {
            var row = new StackPanel
            {
                Orientation = Orientation.Horizontal, Margin = new Thickness(0, 4, 0, 4), Cursor = Cursors.Hand,
            };
            var started = attempt.Text("started_at");
            var time = started.Length >= 16 ? started.Replace('T', ' ')[..16] : started;
            var costMode = CostModeOf(attempt);
            row.Children.Add(new TextBlock { Text = time, Width = 108, FontFamily = (FontFamily)Application.Current.FindResource("Mono"), FontSize = 11.5, VerticalAlignment = VerticalAlignment.Center });
            row.Children.Add(new TextBlock { Text = attempt.Text("channel"), Width = 64, Style = (Style)Application.Current.FindResource("Micro"), VerticalAlignment = VerticalAlignment.Center });
            row.Children.Add(new TextBlock
            {
                Text = $"{attempt.Text("provider")} · {attempt.Text("model_id")}", Width = 220,
                Style = (Style)Application.Current.FindResource("Micro"), TextTrimming = TextTrimming.CharacterEllipsis, VerticalAlignment = VerticalAlignment.Center,
            });
            row.Children.Add(new TextBlock
            {
                Text = $"第 {attempt.Number("dispatch_no")} 次派发", Width = 84,
                Style = (Style)Application.Current.FindResource("Micro"), VerticalAlignment = VerticalAlignment.Center,
            });
            row.Children.Add(new TextBlock
            {
                Text = Labels.Map(Labels.AttemptOutcome, attempt.Text("outcome")), Width = 44,
                Foreground = attempt.Text("outcome") == "SUCCEEDED" ? (Brush)Application.Current.FindResource("Success")
                    : attempt.Text("outcome") == "FAILED" ? (Brush)Application.Current.FindResource("Danger")
                    : (Brush)Application.Current.FindResource("Muted"),
                VerticalAlignment = VerticalAlignment.Center,
            });
            row.Children.Add(new TextBlock
            {
                Text = Labels.Map(Labels.CostMode, costMode), Width = 70,
                Style = (Style)Application.Current.FindResource("Micro"),
                ToolTip = Labels.Map(Labels.CostModeHint, costMode), VerticalAlignment = VerticalAlignment.Center,
            });
            var detail = Kit.Act("详情", (_, _) => new AttemptDrawer(Host, attempt).ShowDialog(), "Compact");
            row.Children.Add(detail);
            row.MouseLeftButtonDown += (_, _) => new AttemptDrawer(Host, attempt).ShowDialog();
            attemptsTable.Children.Add(row);
        }
        if (nextCursor != null)
        {
            var more = Kit.Act("加载更多", async (_, _) => await LoadMoreAsync(), "Compact");
            more.Margin = new Thickness(0, 8, 0, 0);
            attemptsTable.Children.Add(more);
        }
        else
        {
            attemptsTable.Children.Add(new TextBlock { Text = "已加载全部匹配记录", Style = (Style)Application.Current.FindResource("Micro"), Margin = new Thickness(0, 8, 0, 0) });
        }
    }

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
            if (!token.IsCancellationRequested && request == loadRevision)
                summaryLine.Text = $"加载更多失败，可重试：{error.Message}";
        }
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

    private void RenderBilled(List<JsonElement> billed)
    {
        billedTable.Children.Clear();
        if (billed.Count == 0)
        {
            billedTable.Children.Add(Kit.Caption("所选范围内暂无对账记录。"));
            return;
        }
        foreach (var row in billed)
        {
            var item = new StackPanel { Orientation = Orientation.Horizontal, Margin = new Thickness(0, 4, 0, 4) };
            item.Children.Add(new TextBlock
            {
                Text = $"{row.Text("period_start")[..Math.Min(10, row.Text("period_start").Length)]} ~ {row.Text("period_end")[..Math.Min(10, row.Text("period_end").Length)]}",
                Width = 140, Style = (Style)Application.Current.FindResource("Micro"), VerticalAlignment = VerticalAlignment.Center,
            });
            item.Children.Add(new TextBlock
            {
                Text = $"{row.Text("provider")} · {row.Text("model_id")}", Width = 230,
                Style = (Style)Application.Current.FindResource("Micro"), TextTrimming = TextTrimming.CharacterEllipsis, VerticalAlignment = VerticalAlignment.Center,
            });
            item.Children.Add(new TextBlock
            {
                Text = $"账单 {Symbol(row.Text("currency"))}{Money(row, "billed_amount"):0.00}",
                FontWeight = FontWeights.Bold, VerticalAlignment = VerticalAlignment.Center,
            });
            billedTable.Children.Add(item);
        }
    }

    private void ExportCsv()
    {
        if (summary.ValueKind != JsonValueKind.Object)
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
            var builder = new StringBuilder("\uFEFF");
            builder.AppendLine("日期,供应商,模型ID,通道,调用次数,成功,失败,未决,输入Token,输出Token,缓存命中Token,输出图片张数,计量状态分布,估算币种,估算金额（原币种）");
            foreach (var group in summary.Array("groups"))
            {
                var costs = group.Array("estimated_costs");
                var rows = costs.Count == 0 ? [("", "")] : costs.Select(c => (c.Text("currency"), Money(c).ToString())).ToList();
                foreach (var (currency, amount) in rows)
                {
                    var line = string.Join(",",
                        Csv(group.Text("day")), Csv(group.Text("provider")), Csv(group.Text("model_id")), Csv(group.Text("channel")),
                        group.Number("attempt_count"), group.Number("succeeded_count"), group.Number("failed_count"), group.Number("pending_count"),
                        NumberOrNull(group, "input_tokens"), NumberOrNull(group, "output_tokens"), NumberOrNull(group, "cached_input_tokens"),
                        NumberOrNull(group, "output_images"), Csv(string.Join(" ", group.Element("usage_status_counts").EnumerateObject().Select(p => $"{p.Name}:{p.Value}"))),
                        currency.Length == 0 ? "无估算数据" : currency, amount);
                    builder.AppendLine(line);
                }
            }
            File.WriteAllText(dialog.FileName, builder.ToString(), new UTF8Encoding(false));
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

    private sealed class AttemptDrawer : Window
    {
        public AttemptDrawer(Window owner, JsonElement attempt)
        {
            Owner = owner;
            Title = "调用尝试详情";
            Width = 560;
            SizeToContent = SizeToContent.Height;
            MaxHeight = 700;
            WindowStartupLocation = WindowStartupLocation.CenterOwner;
            ShowInTaskbar = false;
            Background = (Brush)Application.Current.FindResource("Paper");
            var scroll = new ScrollViewer { VerticalScrollBarVisibility = ScrollBarVisibility.Auto };
            var panel = new StackPanel { Margin = new Thickness(24) };
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
            Field("输入 / 输出 Token", $"{NumberOrNull(attempt, "input_tokens")} / {NumberOrNull(attempt, "output_tokens")}");
            Field("输出图片", attempt.Element("output_images").ValueKind == JsonValueKind.Number ? $"{attempt.Number("output_images")} 张" : "未知");
            Field("耗时", $"{attempt.Number("duration_ms")} ms");
            Field("结果", $"{Labels.Map(Labels.AttemptOutcome, attempt.Text("outcome"))}{(attempt.Text("error_code").Length > 0 ? $" · {attempt.Text("error_code")}" : "")}");
            if (attempt.Text("error_message").Length > 0)
                Field("错误信息", attempt.Text("error_message"));
            panel.Children.Add(new TextBlock
            {
                Text = "数据来自模型调用账本（已脱敏）· 未知 \u2260 0，CLI 通道费用未知 \u2260 免费",
                Style = (Style)Application.Current.FindResource("Micro"), Margin = new Thickness(0, 14, 0, 0), TextWrapping = TextWrapping.Wrap,
            });
            scroll.Content = panel;
            Content = scroll;
            PreviewKeyDown += (_, e) => { if (e.Key == Key.Escape) Close(); };
        }

        private static string NumberOrNull(JsonElement element, string name) =>
            element.Element(name).ValueKind == JsonValueKind.Number ? element.Number(name).ToString() : "未知";

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
    }
}
