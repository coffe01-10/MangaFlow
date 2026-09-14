using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;
using MangaFlow.Native.Controls;

namespace MangaFlow.Native.Views;

public sealed partial class SettingsView
{
    private void BuildSystemPage()
    {
        var root = new DockPanel();
        var actions = new WrapPanel();
        var usage = Kit.Act("用量与成本看板", async (_, _) => { if (Context != null && await ConfirmLeaveAsync()) await Context.NavigateSection("usage", ""); }, "Ghost");
        var back = Kit.Act("返回项目", async (_, _) => { if (Context != null && await ConfirmLeaveAsync()) await Context.OpenDashboard(); }, "Ghost");
        foreach (var button in new[] { usage, back, runtimeSave }) { button.Margin = new Thickness(0, 0, 8, 0); actions.Children.Add(button); }
        runtimeSave.Click += SaveRuntime;
        var header = new Border { Padding = new Thickness(20, 12, 12, 12), BorderBrush = AssetPageUi.Brush("Line"), BorderThickness = new Thickness(0, 0, 0, 1),
            Child = new PageHeading(SystemHeading("系统设置与运行诊断", "SYSTEM / CONTROL ROOM"), actions) };
        DockPanel.SetDock(header, Dock.Top); root.Children.Add(header);
        var page = new StackPanel { Margin = new Thickness(28, 30, 28, 48) };
        page.Children.Add(BuildStatusStrip());
        var board = new Grid { Name = "SettingsBoard" };
        board.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1.65, GridUnitType.Star) });
        board.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(.75, GridUnitType.Star) });
        board.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        board.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        var main = new StackPanel(); main.Children.Add(BuildProviderBoard()); main.Children.Add(BuildRuntimeCard()); board.Children.Add(main);
        var side = new StackPanel { Name = "SettingsDiagnostics", Margin = new Thickness(18, 0, 0, 0) };
        side.Children.Add(BuildDiagnosticsCard());
        var storage = BuildStorageCard(); storage.Margin = new Thickness(0, 18, 0, 0); side.Children.Add(storage);
        Grid.SetColumn(side, 1); board.Children.Add(side);
        SizeChanged += (_, _) =>
        {
            bool narrow = ActualWidth < 1280;
            board.ColumnDefinitions[1].Width = narrow ? new GridLength(0) : new GridLength(.75, GridUnitType.Star);
            Grid.SetColumn(side, narrow ? 0 : 1); Grid.SetRow(side, narrow ? 1 : 0);
            side.Margin = narrow ? new Thickness(0, 18, 0, 0) : new Thickness(18, 0, 0, 0);
        };
        page.Children.Add(board); scroller.Content = page; root.Children.Add(scroller); Content = root;
    }

    private Border BuildStatusStrip()
    {
        var grid = new SystemSettingsTiles { Columns = 5, NarrowColumns = 2, Breakpoint = 760 };
        foreach (var (label, value) in new[] { ("AI 连接", healthLabel), ("执行器", executorLabel), ("数据库", databaseLabel), ("存储", storageLabel), ("最近检查", checkedAtLabel) })
        {
            var stack = new StackPanel();
            stack.Children.Add(new TextBlock { Text = label, FontSize = 12, Foreground = AssetPageUi.Brush("Muted"), FontWeight = FontWeights.SemiBold, Margin = new Thickness(0, 0, 0, 6) });
            value.FontWeight = FontWeights.SemiBold; value.FontSize = 14; value.Text = "读取中";
            value.TextWrapping = TextWrapping.NoWrap; value.TextTrimming = TextTrimming.CharacterEllipsis;
            value.SetBinding(ToolTipProperty, new System.Windows.Data.Binding("Text") { Source = value });
            stack.Children.Add(value);
            grid.Children.Add(new Border { Padding = new Thickness(13), MinHeight = 70, Child = stack,
                BorderBrush = AssetPageUi.Brush("Line"), BorderThickness = new Thickness(0, 0, 1, 0) });
        }
        return new Border { Child = grid, Background = AssetPageUi.Brush("Surface"), BorderBrush = AssetPageUi.Brush("LineDark"),
            BorderThickness = new Thickness(1), Margin = new Thickness(0, 0, 0, 20) };
    }

    private Border BuildRuntimeCard()
    {
        var content = new StackPanel(); content.Children.Add(runtimeForm);
        runtimeNotice.Visibility = Visibility.Collapsed; runtimeNotice.Margin = new Thickness(13, 6, 13, 6);
        runtimeError.Margin = new Thickness(13, 6, 13, 6);
        content.Children.Add(runtimeNotice); content.Children.Add(runtimeError);
        return SystemCard("运行参数", "WORKER / RUNTIME", content, new TextBlock { Text = "非敏感动态设置", FontSize = 12, Foreground = AssetPageUi.Brush("Muted") });
    }
    private Border BuildDiagnosticsCard()
    {
        recheckButton.Click += async (_, _) => await LoadDiagnosticsAsync();
        return SystemCard("分层诊断", "DIAGNOSTICS", diagnosticsList, recheckButton);
    }
    private Border BuildStorageCard()
    {
        var content = new StackPanel { Margin = new Thickness(13) };
        foreach (var (label, key) in new[] { ("数据库", "database_backend"), ("生成内容", "storage_root"), ("用户上传", "upload_root") })
        {
            var value = new TextBlock { Text = "—", FontSize = 12, FontFamily = (FontFamily)Application.Current.FindResource("Mono"), TextWrapping = TextWrapping.Wrap };
            storageValueBlocks[key] = value;
            content.Children.Add(new Border { Padding = new Thickness(0, 12, 0, 12), BorderBrush = AssetPageUi.Brush("Line"), BorderThickness = new Thickness(0, 0, 0, 1),
                Child = new PageHeading(new TextBlock { Text = label, FontSize = 12, Foreground = AssetPageUi.Brush("Muted") }, value) });
        }
        content.Children.Add(new TextBlock { Text = "凭据路径、私钥、令牌和 Redis 地址不会通过此接口返回。", FontSize = 12,
            Foreground = AssetPageUi.Brush("Muted"), TextWrapping = TextWrapping.Wrap, Margin = new Thickness(0, 14, 0, 0), LineHeight = 20 });
        return SystemCard("本地存储", "LOCAL STORAGE", content);
    }
    private static StackPanel SystemHeading(string title, string kicker)
    {
        var stack = new StackPanel();
        stack.Children.Add(new TextBlock { Text = kicker, FontSize = 10, Foreground = AssetPageUi.Brush("Muted"), Margin = new Thickness(0, 0, 0, 5) });
        stack.Children.Add(new TextBlock { Text = title, FontSize = 17, FontWeight = FontWeights.SemiBold, TextWrapping = TextWrapping.Wrap });
        return stack;
    }
    private static StackPanel SystemLabel(string title, string detail)
    {
        var stack = new StackPanel { VerticalAlignment = VerticalAlignment.Center };
        stack.Children.Add(new TextBlock { Text = title, FontSize = 13, FontWeight = FontWeights.SemiBold, TextWrapping = TextWrapping.Wrap });
        if (detail.Length > 0) stack.Children.Add(new TextBlock { Text = detail, FontSize = 12, Foreground = AssetPageUi.Brush("Muted"), TextWrapping = TextWrapping.Wrap,
            Margin = new Thickness(0, 5, 0, 0), LineHeight = 19 });
        return stack;
    }
    private static Border SystemCard(string title, string kicker, UIElement content, UIElement? action = null)
    {
        var panel = new DockPanel();
        var header = new Border { Padding = new Thickness(14, 12, 14, 12), BorderBrush = AssetPageUi.Brush("Line"), BorderThickness = new Thickness(0, 0, 0, 1),
            Child = new PageHeading(SystemHeading(title, kicker), action ?? new TextBlock()) };
        DockPanel.SetDock(header, Dock.Top); panel.Children.Add(header); panel.Children.Add(content);
        return new Border { Child = panel, Background = AssetPageUi.Brush("Surface"), BorderBrush = AssetPageUi.Brush("LineDark"), BorderThickness = new Thickness(1) };
    }
    private static (string, string) RuntimeLabel(string key) => key switch
    {
        "queue_mode" => ("队列模式", "自动、本地同步或强制 Redis"),
        "job_timeout_seconds" => ("任务超时", "30–3600 秒"),
        "job_lease_seconds" => ("任务租约", "30–3600 秒 · 需不超过任务超时"),
        "default_concurrency" => ("默认并发", "1–8 路"),
        "max_auto_repairs" => ("视觉修复重试", "不含文字校对 · 0–10 次"),
        "health_check_interval_seconds" => ("状态检查周期", "60–3600 秒"),
        "ui_poll_interval_seconds" => ("界面轮询周期", "毫秒 · 仅桌面客户端生效"),
        _ => (key, ""),
    };
}

/// <summary>Equal-width responsive rows measured against their actual available width.</summary>
internal sealed class SystemSettingsTiles : Panel
{
    internal int Columns { get; init; } = 2;
    internal int NarrowColumns { get; init; } = 1;
    internal double Breakpoint { get; init; } = 620;
    internal double Gap { get; init; }
    private double Layout(double width, bool arrange)
    {
        int columns = width >= Breakpoint ? Columns : NarrowColumns;
        double cell = Math.Max(0, (width - Gap * (columns - 1)) / columns), top = 0;
        for (int i = 0; i < Children.Count; i += columns)
        {
            var count = Math.Min(columns, Children.Count - i);
            for (int j = 0; j < count; j++) if (!arrange) Children[i+j].Measure(new Size(cell, double.PositiveInfinity));
            double height = Enumerable.Range(i, count).Max(n => Children[n].DesiredSize.Height);
            if (arrange) for (int j = 0; j < count; j++) Children[i+j].Arrange(new Rect(j * (cell + Gap), top, cell, height));
            top += height + Gap;
        }
        return Math.Max(0, top - Gap);
    }
    protected override Size MeasureOverride(Size available) { double width = double.IsFinite(available.Width) ? available.Width : 1000; return new Size(width, Layout(width, false)); }
    protected override Size ArrangeOverride(Size size) { Layout(size.Width, true); return size; }
}
