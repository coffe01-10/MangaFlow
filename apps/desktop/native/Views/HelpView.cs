using System.Windows;
using System.Windows.Controls;
using System.Windows.Controls.Primitives;
using System.Windows.Media;
using MangaFlow.Native.Controls;

namespace MangaFlow.Native.Views;

/// <summary>
/// NUI-6B: help page mirrored from apps/web/app/help/page.tsx — same hero copy, four
/// stage cards and troubleshooting block, plus anchor navigation between them. Breakpoints
/// follow globals.css: 900px (2-column stages, stacked troubleshooting) and 760px
/// (single column, static 42px hero icon, 165px cards). Desktop adds a shortcut
/// reference the web page does not need.
/// </summary>
public sealed class HelpView : WorkspaceView
{
    private readonly ScrollViewer scroll = new() { VerticalScrollBarVisibility = ScrollBarVisibility.Auto };
    private readonly StackPanel page = new() { MaxWidth = 1180, Margin = new Thickness(34, 52, 34, 90) };
    private readonly Grid hero = new() { MinHeight = 310 };
    private readonly TextBlock firstLine;
    private readonly TextBlock secondLine;
    private readonly StackPanel introduction = new();
    private readonly FrameworkElement heroIcon;
    private readonly FrameworkElement heroIconNarrow;
    private readonly UniformGrid stages = new() { Columns = 4, Name = "HelpStages" };
    private readonly List<Border> stageCards = [];
    private readonly Grid troubleshooting = new() { Name = "HelpTroubleshooting" };
    private readonly Expander shortcuts = new();
    private readonly List<FrameworkElement> anchors = [];
    private bool navigatedAway;

    public HelpView()
    {
        // Web topbar brand link (← back to the project list) is the page's way out.
        var back = Act("← 返回项目", async (_, _) =>
        {
            if (Context != null) { navigatedAway = true; await Context.OpenDashboard(); }
        }, "Ghost");
        back.Margin = new Thickness(0, 0, 0, 18);
        back.HorizontalAlignment = HorizontalAlignment.Left;
        page.Children.Add(back);

        hero.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(58, GridUnitType.Star) });
        hero.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(42, GridUnitType.Star) });
        introduction.Margin = new Thickness(0, 24, 0, 38);
        introduction.Children.Add(Kicker("HELP / 01"));
        firstLine = new TextBlock { Text = "把复杂的漫画生产，", FontFamily = (FontFamily)FindResource("Serif"), FontWeight = FontWeights.Bold, TextWrapping = TextWrapping.Wrap, Margin = new Thickness(0, 18, 0, 0) };
        secondLine = new TextBlock { Text = "拆成可确认的每一步。", FontFamily = (FontFamily)FindResource("Serif"), FontWeight = FontWeights.Bold, TextWrapping = TextWrapping.Wrap, Foreground = (Brush)FindResource("Accent"), Margin = new Thickness(0, 0, 0, 18) };
        introduction.Children.Add(firstLine);
        introduction.Children.Add(secondLine);
        // Verbatim web copy (app/help/page.tsx hero paragraph).
        introduction.Children.Add(new TextBlock
        {
            Text = "项目侧栏就是完整生产路径。每个入口都拥有独立网址，可以刷新、收藏和通过浏览器前进后退。",
            TextWrapping = TextWrapping.Wrap, FontFamily = (FontFamily)FindResource("Serif"), FontSize = 13, LineHeight = 24,
            Foreground = (Brush)FindResource("Muted"),
        });
        heroIcon = Icon("M12,2 A10,10 0 1 0 12,22 A10,10 0 1 0 12,2 M9.1,9 A3,3 0 0 1 15,10 C15,12 12,12 12,14 M12,17 L12,17.2", 110);
        heroIcon.HorizontalAlignment = HorizontalAlignment.Center;
        heroIcon.VerticalAlignment = VerticalAlignment.Top;
        heroIcon.Margin = new Thickness(0, 30, 0, 0);
        Grid.SetColumn(heroIcon, 1);
        hero.Children.Add(introduction);
        hero.Children.Add(heroIcon);
        var heroFrame = new Border { Child = hero, BorderBrush = (Brush)FindResource("Ink"), BorderThickness = new Thickness(0, 0, 0, 1) };
        page.Children.Add(heroFrame);

        // Anchor navigation: the web page is one long document with in-page links;
        // the native page jumps between the same sections.
        var toc = new StackPanel { Orientation = Orientation.Horizontal, Margin = new Thickness(0, 16, 0, 0) };
        var sections = new[] { "01 导入原作", "02 建立参考资产", "03 编排与生产", "04 检查与导出", "故障排查", "桌面快捷键" };
        for (var i = 0; i < sections.Length; i++)
        {
            var index = i;
            var jump = Act(sections[i], (_, _) => JumpTo(anchors[index]), "Compact");
            jump.Margin = new Thickness(i == 0 ? 0 : 6, 0, 0, 0);
            System.Windows.Automation.AutomationProperties.SetName(jump, "跳转到" + sections[i]);
            toc.Children.Add(jump);
        }
        page.Children.Add(toc);

        var descriptions = new[]
        {
            ("01", "导入原作", "粘贴文本或上传 TXT / Markdown，系统保留每次修订。", "M3,3 L10,3 12,5 14,3 21,3 21,20 14,20 12,22 10,20 3,20 Z M12,5 L12,22 M6,7 L9,7 M6,11 L9,11 M15,7 L18,7"),
            ("02", "建立参考资产", "分别建立角色、服装和漫画风格档案，并绑定参考图。", "M12,2 L22,7 22,17 12,22 2,17 2,7 Z M2,7 L12,12 22,7 M12,12 L12,22 M7,4.5 L17,9.5"),
            ("03", "编排与生产", "生成剧本和动态分页，逐页抽卡、收藏并采用满意版本。", "M3,3 L9,3 9,9 3,9 Z M15,15 L21,15 21,21 15,21 Z M9,6 L18,6 18,15 M6,9 L6,18 15,18"),
            ("04", "检查与导出", "在任务中心查看进度，检查连续性后导出 PNG、PDF 或 JSON。", "M3,6 L5,8 9,4 M12,6 L21,6 M3,14 L5,16 9,12 M12,14 L21,14 M12,21 L21,21"),
        };
        foreach (var (index, title, description, geometry) in descriptions)
        {
            var column = new StackPanel();
            column.Children.Add(new TextBlock { Text = index, FontSize = 18, FontFamily = (FontFamily)FindResource("Serif"), Foreground = (Brush)FindResource("Muted"), HorizontalAlignment = HorizontalAlignment.Right });
            var icon = Icon(geometry, 22);
            icon.HorizontalAlignment = HorizontalAlignment.Left;
            icon.Margin = new Thickness(0, 5, 0, 18);
            column.Children.Add(icon);
            column.Children.Add(new TextBlock { Text = title, FontFamily = (FontFamily)FindResource("Serif"), FontSize = 17, FontWeight = FontWeights.Bold, Margin = new Thickness(0, 0, 0, 9) });
            column.Children.Add(Caption(description));
            var card = new Border { Child = column, Padding = new Thickness(20), MinHeight = 210, BorderBrush = (Brush)FindResource("LineDark") };
            stageCards.Add(card);
            stages.Children.Add(card);
        }
        // Anchor targets: the four stage cards in reading order.
        anchors.AddRange(stageCards);
        var stageFrame = new Border { Child = stages, BorderBrush = (Brush)FindResource("Ink"), BorderThickness = new Thickness(1, 0, 1, 1) };
        page.Children.Add(stageFrame);

        troubleshooting.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        troubleshooting.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1.5, GridUnitType.Star) });
        troubleshooting.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        troubleshooting.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        troubleshooting.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        troubleshooting.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        var troubleshootingTitle = new StackPanel { VerticalAlignment = VerticalAlignment.Center };
        troubleshootingTitle.Children.Add(Kicker("AI 连接 / 故障排查"));
        troubleshootingTitle.Children.Add(new TextBlock { Text = "供应商连接显示断开时怎么办？", FontFamily = (FontFamily)FindResource("Serif"), FontSize = 20, TextWrapping = TextWrapping.Wrap, Margin = new Thickness(0, 10, 0, 0) });
        var instructions = new TextBlock { Text = "1. 打开“设置”，确认账号型环境凭据或连接 API Key 已就绪。\n2. 先运行无生成费用的凭据验证，再按需执行模型能力测试。\n3. 网络波动会显示为连接降级，不会清除人工启停与服务端配置。", TextWrapping = TextWrapping.Wrap, FontSize = 12, LineHeight = 24, VerticalAlignment = VerticalAlignment.Center, Foreground = (Brush)FindResource("Muted") };
        var open = Act("打开系统设置  →", async (_, _) => { if (Context != null) await Context.NavigateSection("settings-global", ""); }, "InkButton");
        open.VerticalAlignment = VerticalAlignment.Center;
        open.HorizontalAlignment = HorizontalAlignment.Left;
        troubleshooting.Children.Add(troubleshootingTitle);
        troubleshooting.Children.Add(instructions);
        troubleshooting.Children.Add(open);
        var troubleshootFrame = new Border { Child = troubleshooting, Background = (Brush)FindResource("PaperDeep"), BorderBrush = (Brush)FindResource("Accent"), BorderThickness = new Thickness(4, 0, 0, 0), Padding = new Thickness(26), Margin = new Thickness(0, 30, 0, 0) };
        anchors.Add(troubleshootFrame);
        page.Children.Add(troubleshootFrame);

        shortcuts.Header = "桌面快捷键与本机数据";
        shortcuts.Margin = new Thickness(0, 24, 0, 0);
        shortcuts.Content = new TextBlock
        {
            Text = "Ctrl + K  搜索并切换项目    Ctrl + N  新建项目    Ctrl + B  折叠侧栏    F5  刷新当前页面    Esc  关闭抽屉、弹窗、详情窗口与图片预览\n\n关窗会停止本次客户端启动的本地服务，运行中的任务可能中断。已有数据不会因关闭窗口而删除。凭据保存在服务端。",
            TextWrapping = TextWrapping.Wrap, LineHeight = 24, Margin = new Thickness(0, 12, 0, 0),
        };
        anchors.Add(shortcuts);
        page.Children.Add(shortcuts);

        // Static narrow-mode icon (web ≤760px: position static, 42px). Web DOM order puts
        // the glyph between the kicker and the heading, so it slots in there when static.
        heroIconNarrow = Icon("M12,2 A10,10 0 1 0 12,22 A10,10 0 1 0 12,2 M9.1,9 A3,3 0 0 1 15,10 C15,12 12,12 12,14 M12,17 L12,17.2", 42);
        heroIconNarrow.HorizontalAlignment = HorizontalAlignment.Left;
        heroIconNarrow.Margin = new Thickness(0, 24, 0, 0);
        heroIconNarrow.Visibility = Visibility.Collapsed;
        introduction.Children.Insert(1, heroIconNarrow);

        void Responsive()
        {
            var width = ActualWidth;
            var narrow = width <= 760;
            firstLine.FontSize = secondLine.FontSize = narrow ? 38 : Math.Clamp(width * .05, 38, 65);
            stages.Columns = narrow ? 1 : width <= 900 ? 2 : 4;
            for (var i = 0; i < stageCards.Count; i++)
            {
                stageCards[i].MinHeight = narrow ? 165 : 210;
                stageCards[i].BorderThickness = new Thickness(0, 0, (i + 1) % stages.Columns == 0 ? 0 : 1, i + stages.Columns < 4 ? 1 : 0);
            }
            // Web: troubleshooting drops to one column at 900px (not 1100).
            var stacked = width <= 900;
            for (var i = 0; i < troubleshooting.Children.Count; i++)
            {
                var item = (FrameworkElement)troubleshooting.Children[i];
                Grid.SetColumn(item, stacked ? 0 : i);
                Grid.SetRow(item, stacked ? i : 0);
                Grid.SetColumnSpan(item, stacked ? 3 : 1);
                item.Margin = stacked ? new Thickness(0, i == 0 ? 0 : 18, 0, 0) : new Thickness(0, 0, i == 2 ? 0 : 28, 0);
            }
            // Web ≤760px: hero loses its reserved icon column and fixed height; the icon
            // becomes a static 42px glyph in flow after the copy.
            heroIcon.Visibility = narrow ? Visibility.Collapsed : Visibility.Visible;
            heroIconNarrow.Visibility = narrow ? Visibility.Visible : Visibility.Collapsed;
            hero.ColumnDefinitions[1].Width = narrow ? new GridLength(0) : new GridLength(42, GridUnitType.Star);
            hero.MinHeight = narrow ? 0 : 310;
            introduction.Margin = narrow ? new Thickness(0, 12, 0, 32) : new Thickness(0, 24, 0, 38);
            page.Margin = narrow ? new Thickness(16, 28, 16, 74) : new Thickness(34, 52, 34, 90);
        }
        SizeChanged += (_, _) => Responsive();
        scroll.Content = page;
        Content = scroll;
        Responsive();
    }

    private void JumpTo(FrameworkElement target)
    {
        if (target == null || scroll.Content is not FrameworkElement content) return;
        // TranslatePoint(target → content) already yields the document offset: both move
        // together when the viewer scrolls, so adding VerticalOffset would double-count.
        scroll.ScrollToVerticalOffset(Math.Max(0, target.TranslatePoint(new Point(), content).Y - 12));
        // Anchor jumps move keyboard focus with them so Tab continues from the section.
        // Borders/Expanders are not focusable by default; land on the first focusable child.
        var focusTarget = FirstFocusable(target);
        if (focusTarget == null) { target.Focusable = true; focusTarget = target; }
        focusTarget.Focus();
    }

    private static UIElement? FirstFocusable(FrameworkElement root)
    {
        if (root is { Focusable: true, IsEnabled: true }) return root;
        for (var i = 0; i < VisualTreeHelper.GetChildrenCount(root); i++)
            if (VisualTreeHelper.GetChild(root, i) is FrameworkElement child && FirstFocusable(child) is { } found) return found;
        return null;
    }

    /// <summary>Whether the back-to-projects entry has been used (regression hook).</summary>
    public bool NavigatedAway => navigatedAway;

    private static FrameworkElement Icon(string data, double size) => new Viewbox
    {
        Width = size, Height = size, Child = new System.Windows.Shapes.Path
        {
            Data = Geometry.Parse(data), Stroke = (Brush)Application.Current.FindResource("Accent"),
            StrokeThickness = size > 30 ? .8 : 1.5, StrokeStartLineCap = PenLineCap.Round,
            StrokeEndLineCap = PenLineCap.Round, StrokeLineJoin = PenLineJoin.Round,
        },
    };
}
