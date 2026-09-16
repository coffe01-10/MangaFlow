using System.Text.Json;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;
using System.Windows.Media;
using MangaFlow.Native.Controls;

namespace MangaFlow.Native.Views;

public sealed partial class UsageView
{
    private string trendMetric = "amount";
    private readonly List<Button> trendButtons = [];
    internal Action<JsonElement>? AttemptDetailOverride;

    private void OpenAttempt(JsonElement attempt)
    {
        if (AttemptDetailOverride is { } open) open(attempt);
        else ShowAttemptDrawer(attempt);
    }

    private void BuildUsageShell()
    {
        Resources.MergedDictionaries.Add(new ResourceDictionary { Source = new Uri("/MangaFlow.Native;component/Views/UsageTheme.xaml", UriKind.Relative) });
        var root = new DockPanel();
        var title = new StackPanel();
        title.Children.Add(new TextBlock { Text = "SYSTEM / USAGE & COST", FontSize = 10, Foreground = AssetPageUi.Brush("Muted"), Margin = new Thickness(0,0,0,5) });
        title.Children.Add(new TextBlock { Text = "系统设置 / 用量与成本看板", FontSize = 17, FontWeight = FontWeights.SemiBold });
        var actions = new WrapPanel();
        actions.Children.Add(Kit.Act("设置首页", async (_, _) => { if (Context != null) await Context.NavigateSection("settings", ""); }, "Ghost"));
        actions.Children.Add(Kit.Act("返回项目", async (_, _) => { if (Context != null) await Context.OpenDashboard(); }, "InkButton"));
        foreach (FrameworkElement child in actions.Children) child.Margin = new Thickness(8,0,0,0);
        var header = new Border { Padding = new Thickness(20,12,20,12), BorderBrush = AssetPageUi.Brush("Line"), BorderThickness = new Thickness(0,0,0,1),
            Child = new PageHeading(title, actions) };
        DockPanel.SetDock(header, Dock.Top); root.Children.Add(header); root.Children.Add(scroller); Content = root;
    }
    private static StackPanel FilterField(string label, FrameworkElement input)
    {
        var field = new StackPanel { Margin = new Thickness(0,0,12,8) };
        field.Children.Add(new TextBlock { Text = label, FontSize = 12, FontWeight = FontWeights.SemiBold, Foreground = AssetPageUi.Brush("Muted"), Margin = new Thickness(0,0,0,5) });
        field.Children.Add(input); return field;
    }
    private static Border WrapCard(string title, UIElement content, UIElement? actions = null)
    {
        var panel = new DockPanel();
        var header = new Border { Padding = new Thickness(14,12,14,12), BorderBrush = AssetPageUi.Brush("Line"), BorderThickness = new Thickness(0,0,0,1),
            Child = new PageHeading(new TextBlock { Text = title, FontSize = 14, FontWeight = FontWeights.Bold, VerticalAlignment = VerticalAlignment.Center }, actions ?? new TextBlock()) };
        DockPanel.SetDock(header, Dock.Top); panel.Children.Add(header);
        bool table = title is "供应商与模型分解" or "调用明细" or "账单对账记录";
        panel.Children.Add(table ? new ScrollViewer { Content = content, HorizontalScrollBarVisibility = ScrollBarVisibility.Auto,
            VerticalScrollBarVisibility = ScrollBarVisibility.Disabled } : new Border { Child = content, Padding = new Thickness(14) });
        return new Border { Background = AssetPageUi.Brush("Surface"), BorderBrush = AssetPageUi.Brush("LineDark"), BorderThickness = new Thickness(1),
            Margin = new Thickness(0,18,0,0), Child = panel };
    }
    private static Border KpiCard(string title, string value, string note) => new()
    {
        BorderBrush = AssetPageUi.Brush(title == "账单支出" ? "Success" : "LineDark"), BorderThickness = new Thickness(1),
        Background = AssetPageUi.Brush("Surface"), Padding = new Thickness(16),
        Child = new StackPanel { Children = {
            new TextBlock { Text = title, FontSize = 12, FontWeight = FontWeights.SemiBold, Foreground = AssetPageUi.Brush("Muted") },
            new TextBlock { Text = value, FontSize = 26, FontFamily = (FontFamily)Application.Current.FindResource("Serif"), Margin = new Thickness(0,12,0,10), TextWrapping = TextWrapping.Wrap },
            new TextBlock { Text = note, FontSize = 12, Foreground = AssetPageUi.Brush("Muted"), TextWrapping = TextWrapping.Wrap } } }
    };
    private static long? SumKnown(IEnumerable<JsonElement> groups, string key)
    {
        var known = groups.Where(g => g.Element(key).ValueKind == JsonValueKind.Number).ToArray();
        return known.Length == 0 ? null : known.Sum(g => g.Element(key).GetInt64());
    }
    private static Border TokenKpi(List<JsonElement> groups)
    {
        string Q(string key) => SumKnown(groups,key)?.ToString("N0") ?? "未知";
        var card = KpiCard("Token 与生图", "", "");
        var stack = (StackPanel)card.Child;
        stack.Children.RemoveAt(2); stack.Children.RemoveAt(1);
        foreach (var (title,key) in new[] { ("输入 Token","input_tokens"), ("输出 Token","output_tokens"), ("缓存命中","cached_input_tokens"), ("生成图片","output_images") })
        {
            var value = Q(key);
            if (key == "cached_input_tokens" && SumKnown(groups, "cached_input_tokens") is { } cached && SumKnown(groups, "input_tokens") is > 0)
                value += $" · {(double)cached / SumKnown(groups, "input_tokens")!.Value * 100:0.0}%";
            stack.Children.Add(new Border { Margin = new Thickness(0,8,0,0), Padding = new Thickness(0,0,0,4), BorderBrush = AssetPageUi.Brush("Line"), BorderThickness = new Thickness(0,0,0,1),
                Child = new PageHeading(new TextBlock { Text = title, FontSize = 12, Foreground = AssetPageUi.Brush("Muted") }, new TextBlock { Text = value, FontSize = 12 }) });
        }
        return card;
    }
    private static Border TableRow(string[] cells, double[] widths, bool header = false)
    {
        var grid = new Grid { MinWidth = widths.Sum() };
        for (int i=0; i<widths.Length; i++)
        {
            grid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(widths[i], GridUnitType.Star) });
            var text = new TextBlock { Text = cells[i], FontSize = header ? 11 : 12, TextWrapping = TextWrapping.Wrap,
                FontWeight = header ? FontWeights.SemiBold : FontWeights.Normal, Foreground = AssetPageUi.Brush(header ? "Muted" : "Ink"),
                Margin = new Thickness(10,10,10,10), VerticalAlignment = VerticalAlignment.Center };
            Grid.SetColumn(text,i); grid.Children.Add(text);
        }
        return new Border { Child = grid, BorderBrush = AssetPageUi.Brush("Line"), BorderThickness = new Thickness(0,0,0,1),
            Background = header ? AssetPageUi.Brush("PaperDeep") : Brushes.Transparent };
    }
    private static Border BreakdownRow(string providerModel,string channel,string calls,string outcomes,string tokens,string cached,string images,string costs,string mode) =>
        TableRow([providerModel,channel,calls,outcomes,tokens,cached,images,costs,mode], [245,85,70,140,160,100,75,200,100]);

    private void RenderAttempts()
    {
        attemptsFooterError = null;
        attemptsTable.Children.Clear();
        attemptsTable.Children.Add(new TextBlock { Text = $"已加载 {attempts.Count} 条 · 最新调用在前", FontSize = 12, Foreground = AssetPageUi.Brush("Muted"), Margin = new Thickness(14,10,14,10) });
        if (attempts.Count == 0) { attemptsTable.Children.Add(Kit.Caption("该范围暂无调用尝试记录。")); return; }
        double[] widths = [145,85,235,110,75,145,75,105,70];
        attemptsTable.Children.Add(TableRow(["开始时间","通道","供应商 / 模型","派发","结果","输入 / 输出 Token","图片","成本语义","操作"], widths,true));
        foreach (var attempt in attempts)
        {
            var raw = attempt.Text("started_at"); var time = raw.Length >= 16 ? raw.Replace('T',' ')[..16] : raw;
            string Q(string key) => attempt.Element(key).ValueKind == JsonValueKind.Number ? attempt.Element(key).ToString() : "未知";
            var mode = CostModeOf(attempt);
            var row = TableRow([time,attempt.Text("channel") == "CLI" ? "CLI" : "HTTP API",$"{attempt.Text("provider")}\n{attempt.Text("model_id")}",
                $"第 {attempt.Number("dispatch_no")} 次" + (attempt.Flag("route_switched") ? " · 换路" : ""),
                Labels.Map(Labels.AttemptOutcome,attempt.Text("outcome")), $"{Q("input_tokens")} / {Q("output_tokens")}",Q("output_images"),
                Labels.Map(Labels.CostMode,mode),""],widths);
            var grid = (Grid)row.Child;
            var outcome = (TextBlock)grid.Children[4]; outcome.Foreground = AssetPageUi.Brush(attempt.Text("outcome") == "SUCCEEDED" ? "Success" : attempt.Text("outcome") == "FAILED" ? "Danger" : "Muted");
            var detail = Kit.Act("详情", (_,e) => { e.Handled = true; OpenAttempt(attempt); }, "Compact");
            detail.Margin = new Thickness(6); detail.VerticalAlignment = VerticalAlignment.Center; Grid.SetColumn(detail,8); grid.Children.Add(detail);
            row.Focusable = true; row.Cursor = Cursors.Hand;
            System.Windows.Automation.AutomationProperties.SetName(row,$"查看 {attempt.Text("provider")} {attempt.Text("model_id")} 的调用尝试详情");
            row.KeyDown += (_,e) => { if (ReferenceEquals(e.Source, row) && e.Key is Key.Enter or Key.Space) { e.Handled = true; OpenAttempt(attempt); } };
            row.MouseLeftButtonUp += (_,e) =>
            {
                if (e.Handled) return;
                var node = e.OriginalSource as DependencyObject;
                while (node != null && !ReferenceEquals(node,row)) { if (node is Button) return; node = VisualTreeHelper.GetParent(node); }
                OpenAttempt(attempt);
            };
            attemptsTable.Children.Add(row);
        }
        if (nextCursor != null) attemptsTable.Children.Add(Kit.Act("加载更多", async (_,_)=>await LoadMoreAsync(),"Compact"));
        else attemptsTable.Children.Add(new TextBlock { Text = "已加载全部匹配记录", FontSize = 12, Foreground = AssetPageUi.Brush("Muted"), Margin = new Thickness(14,10,14,10) });
    }
    private void RenderBilled(List<JsonElement> billed)
    {
        billedTable.Children.Clear();
        if (billed.Count == 0) { billedTable.Children.Add(Kit.Caption("所选范围内暂无对账记录。")); return; }
        double[] widths = [240,300,220];
        billedTable.Children.Add(TableRow(["账单周期","供应商 / 模型","账单金额（原币种）"], widths,true));
        foreach (var b in billed)
        {
            // 未知金额 ≠ 0：billed_amount 缺失时显示“未知”，不渲染成 0.00。
            var amount = b.Element("billed_amount").ValueKind is JsonValueKind.Number or JsonValueKind.String
                ? $"{b.Text("currency")} {Money(b, "billed_amount"):0.00}" : "金额未知";
            billedTable.Children.Add(TableRow([$"{b.Text("period_start").Split('T')[0]} ~ {b.Text("period_end").Split('T')[0]}",
                $"{b.Text("provider")} · {b.Text("model_id")}", amount], widths));
        }
    }
}

internal sealed class UsageKpiPanel : Panel
{
    private double Layout(double width,bool arrange)
    {
        int cols = width >= 1000 ? 4 : width >= 550 ? 2 : 1; const double gap = 14;
        double cell = Math.Max(0,(width-gap*(cols-1))/cols), top=0;
        for(int i=0;i<Children.Count;i+=cols)
        {
            int count=Math.Min(cols,Children.Count-i);
            if(!arrange)for(int j=0;j<count;j++)Children[i+j].Measure(new Size(cell,double.PositiveInfinity));
            double height=Enumerable.Range(i,count).Max(n=>Children[n].DesiredSize.Height);
            if(arrange)for(int j=0;j<count;j++)Children[i+j].Arrange(new Rect(j*(cell+gap),top,cell,height));
            top+=height+gap;
        }
        return Math.Max(0,top-gap);
    }
    protected override Size MeasureOverride(Size available) { var w=double.IsFinite(available.Width)?available.Width:1200;return new Size(w,Layout(w,false)); }
    protected override Size ArrangeOverride(Size size){Layout(size.Width,true);return size;}
}
internal sealed class UsageBudgetRow : Panel
{
    protected override Size MeasureOverride(Size available)
    {
        double w=double.IsFinite(available.Width)?available.Width:1000;
        foreach(UIElement child in Children)child.Measure(new Size(w,double.PositiveInfinity));
        bool stack=Children.Cast<UIElement>().Sum(c=>c.DesiredSize.Width)+16>w;
        return new Size(w,stack?Children.Cast<UIElement>().Sum(c=>c.DesiredSize.Height)+10:Children.Cast<UIElement>().Max(c=>c.DesiredSize.Height));
    }
    protected override Size ArrangeOverride(Size size)
    {
        bool stack=Children.Cast<UIElement>().Sum(c=>c.DesiredSize.Width)+16>size.Width;
        double offset=0;
        foreach(UIElement child in Children){child.Arrange(stack?new Rect(0,offset,size.Width,child.DesiredSize.Height):new Rect(offset,0,child.DesiredSize.Width,size.Height));offset+=stack?child.DesiredSize.Height+10:child.DesiredSize.Width+16;}
        return size;
    }
}
