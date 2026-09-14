using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;
using MangaFlow.Native.Controls;

namespace MangaFlow.Native.Views;

public sealed partial class ProjectSettingsView
{
    private Border dangerZone = null!;
    private Button deleteButton = null!;

    private void BuildSettingsPage()
    {
        Resources.MergedDictionaries.Add(new ResourceDictionary { Source = new Uri("/MangaFlow.Native;component/Views/ProjectSettingsTheme.xaml", UriKind.Relative) });
        var root = new DockPanel();
        var title = new StackPanel();
        title.Children.Add(new TextBlock { Text = "PROJECT / CONTROL", FontSize = 10, Foreground = AssetPageUi.Brush("Muted"), Margin = new Thickness(0, 0, 0, 4) });
        title.Children.Add(projectTitle);
        var actions = new WrapPanel();
        var back = Act("返回工作区", async (_, _) =>
        {
            if (Context != null && await ConfirmLeaveAsync()) await Context.NavigateSection("source", null);
        }, "Ghost");
        actions.Children.Add(back); actions.Children.Add(saveButton);
        saveButton.Content = SourceIcon.Label("save", "保存项目设置");
        System.Windows.Automation.AutomationProperties.SetName(saveButton, "保存项目设置");
        saveButton.IsEnabled = false;
        back.Margin = new Thickness(0, 0, 8, 0);
        var header = new Border { Padding = new Thickness(20, 12, 20, 12),
            BorderBrush = AssetPageUi.Brush("Line"), BorderThickness = new Thickness(0, 0, 0, 1),
            Child = new PageHeading(title, actions) };
        DockPanel.SetDock(header, Dock.Top); root.Children.Add(header);
        var feedback = new StackPanel { Margin = new Thickness(20, 0, 20, 0) };
        feedback.Children.Add(saveSuccess); feedback.Children.Add(saveError);
        DockPanel.SetDock(feedback, Dock.Top); root.Children.Add(feedback);
        body.MaxWidth = 1088; body.Margin = new Thickness(28, 24, 28, 40);
        root.Children.Add(new ScrollViewer { Content = body, VerticalScrollBarVisibility = ScrollBarVisibility.Auto });
        dangerZone = BuildSettingsDangerZone();
        nameForDelete.TextChanged += (_, _) => UpdateDeleteAvailability();
        Content = root;
    }

    private static Border SettingsHero()
    {
        var stack = new StackPanel();
        stack.Children.Add(new TextBlock { Text = "PROJECT SETTINGS / 08", FontSize = 10,
            FontWeight = FontWeights.SemiBold, Foreground = AssetPageUi.Brush("Muted") });
        var headline = new TextBlock { Text = "控制每一次生成，\n不是控制你的故事。", FontSize = 42,
            FontFamily = (FontFamily)Application.Current.FindResource("Serif"), TextWrapping = TextWrapping.Wrap,
            Margin = new Thickness(0, 12, 0, 12), LineHeight = 48 };
        headline.SizeChanged += (_, e) =>
        {
            headline.FontSize = e.NewSize.Width < 420 ? 30 : 42;
            headline.LineHeight = e.NewSize.Width < 420 ? 38 : 48;
        };
        stack.Children.Add(headline);
        stack.Children.Add(new TextBlock { Text = "这里仅保存当前项目的制作策略。图片模型仍在每个候选生成前单独选择，不设置主次。",
            FontSize = 12, Foreground = AssetPageUi.Brush("Muted"), TextWrapping = TextWrapping.Wrap, MaxWidth = 620,
            HorizontalAlignment = HorizontalAlignment.Left, LineHeight = 21 });
        return new Border { Child = stack, BorderBrush = AssetPageUi.Brush("Ink"), BorderThickness = new Thickness(0, 0, 0, 1), Padding = new Thickness(0, 18, 0, 28) };
    }

    private static StackPanel SettingLabel(string title, string detail)
    {
        var stack = new StackPanel { VerticalAlignment = VerticalAlignment.Center };
        stack.Children.Add(new TextBlock { Text = title, FontSize = 13, FontWeight = FontWeights.SemiBold, TextWrapping = TextWrapping.Wrap });
        stack.Children.Add(new TextBlock { Text = detail, FontSize = 12, Foreground = AssetPageUi.Brush("Muted"),
            TextWrapping = TextWrapping.Wrap, Margin = new Thickness(0, 4, 0, 0) });
        return stack;
    }

    private static Border InlineSetting(string title, string detail, FrameworkElement control) => new()
    {
        Child = new PageHeading(SettingLabel(title, detail), control),
        Padding = new Thickness(0, 12, 0, 12), BorderBrush = AssetPageUi.Brush("Line"),
        BorderThickness = new Thickness(0, 0, 0, 1), MinHeight = 67,
    };

    private static Border Section(string title, string kicker, UIElement content)
    {
        var panel = new DockPanel();
        var label = new StackPanel();
        label.Children.Add(new TextBlock { Text = kicker, FontSize = 10, Foreground = AssetPageUi.Brush("Muted") });
        label.Children.Add(new TextBlock { Text = title, FontSize = 18, FontFamily = (FontFamily)Application.Current.FindResource("Serif"),
            Margin = new Thickness(0, 3, 0, 0) });
        var header = new Border { Child = label, Padding = new Thickness(14, 11, 14, 11), MinHeight = 64,
            BorderBrush = AssetPageUi.Brush("Line"), BorderThickness = new Thickness(0, 0, 0, 1) };
        DockPanel.SetDock(header, Dock.Top); panel.Children.Add(header);
        panel.Children.Add(new Border { Padding = new Thickness(12), Child = content });
        return new Border { Child = panel, Background = AssetPageUi.Brush("Surface"), BorderBrush = AssetPageUi.Brush("LineDark"), BorderThickness = new Thickness(1) };
    }

    private static Border PolicyNote(string? kicker, string title, string detail)
    {
        var text = new StackPanel();
        if (kicker != null) text.Children.Add(new TextBlock { Text = kicker, FontSize = 10, Foreground = new SolidColorBrush(Color.FromRgb(203, 199, 187)) });
        text.Children.Add(new TextBlock { Text = title, FontSize = 19, Foreground = Brushes.White,
            FontFamily = (FontFamily)Application.Current.FindResource("Serif"), Margin = new Thickness(0, 13, 0, 8), TextWrapping = TextWrapping.Wrap });
        text.Children.Add(new TextBlock { Text = detail, FontSize = 12, Foreground = new SolidColorBrush(Color.FromRgb(203, 199, 187)), TextWrapping = TextWrapping.Wrap, LineHeight = 22 });
        return new Border { Child = text, Background = AssetPageUi.Brush("Ink"), Padding = new Thickness(18) };
    }

    private Border BuildSettingsDangerZone()
    {
        var content = new StackPanel();
        content.Children.Add(new TextBlock { Text = "删除后项目将从工作台隐藏。数据库记录和生成文件暂时保留，避免误删；如需恢复可由维护工具处理。",
            TextWrapping = TextWrapping.Wrap, FontSize = 12, LineHeight = 22, Foreground = AssetPageUi.Brush("Muted"), Margin = new Thickness(0, 0, 0, 14) });
        nameForDelete.MinHeight = 40;
        System.Windows.Automation.AutomationProperties.SetName(nameForDelete, "输入项目名称确认删除");
        var entry = Labelled("输入项目名称确认", nameForDelete);
        deleteButton = Act("删除项目", DeleteProject, "DangerButton"); deleteButton.IsEnabled = false;
        deleteButton.Content = SourceIcon.Label("trash", "删除项目");
        deleteButton.Height = 44; deleteButton.VerticalAlignment = VerticalAlignment.Center;
        System.Windows.Automation.AutomationProperties.SetName(deleteButton, "删除项目");
        content.Children.Add(new PageHeading(entry, deleteButton));
        var card = Section("删除当前项目", "DANGER ZONE / 项目管理", content);
        card.BorderBrush = AssetPageUi.Brush("Danger"); card.Margin = new Thickness(0, 24, 0, 0);
        return card;
    }

    private void UpdateDeleteAvailability()
    {
        deleteButton.IsEnabled = project.ValueKind == System.Text.Json.JsonValueKind.Object
            && project.Text("name").Length > 0 && nameForDelete.Text == project.Text("name");
        nameForDelete.ToolTip = project.ValueKind == System.Text.Json.JsonValueKind.Object ? project.Text("name") : null;
    }
}

/// <summary>The web settings grid uses a 1.1:1 pair and collapses to a single column.</summary>
internal sealed class ProjectSettingsGrid : Panel
{
    private const double Gap = 15;
    private double Layout(double width, bool arrange)
    {
        bool paired = width >= 760;
        double left = paired ? (width - Gap) * 1.1 / 2.1 : width, right = paired ? width - Gap - left : width, top = 0;
        for (int i = 0; i < Children.Count; i += paired ? 2 : 1)
        {
            var a = Children[i]; UIElement? b = paired && i + 1 < Children.Count ? Children[i + 1] : null;
            if (!arrange) { a.Measure(new Size(left, double.PositiveInfinity)); b?.Measure(new Size(right, double.PositiveInfinity)); }
            var height = Math.Max(a.DesiredSize.Height, b?.DesiredSize.Height ?? 0);
            if (arrange) { a.Arrange(new Rect(0, top, left, height)); b?.Arrange(new Rect(left + Gap, top, right, height)); }
            top += height + Gap;
        }
        return Math.Max(0, top - Gap);
    }
    protected override Size MeasureOverride(Size available) { double width = double.IsFinite(available.Width) ? available.Width : 1088; return new Size(width, Layout(width, false)); }
    protected override Size ArrangeOverride(Size size) { Layout(size.Width, true); return size; }
}
