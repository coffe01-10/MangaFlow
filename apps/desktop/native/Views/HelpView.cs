using System.Windows;
using System.Windows.Controls;
using System.Windows.Controls.Primitives;
using System.Windows.Media;
using MangaFlow.Native.Controls;

namespace MangaFlow.Native.Views;

public sealed class HelpView : WorkspaceView
{
    public HelpView()
    {
        var scroll = new ScrollViewer { VerticalScrollBarVisibility = ScrollBarVisibility.Auto };
        var page = new StackPanel { MaxWidth = 1180, Margin = new Thickness(34, 52, 34, 90) };
        var hero = new Grid { MinHeight = 310 };
        hero.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(58, GridUnitType.Star) });
        hero.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(42, GridUnitType.Star) });
        var introduction = new StackPanel { Margin = new Thickness(0, 24, 0, 38) };
        introduction.Children.Add(Kicker("HELP / 01"));
        var firstLine = new TextBlock { Text = "把复杂的漫画生产，", FontFamily = (FontFamily)FindResource("Serif"), FontWeight = FontWeights.Bold, TextWrapping = TextWrapping.Wrap, Margin = new Thickness(0, 18, 0, 0) };
        var secondLine = new TextBlock { Text = "拆成可确认的每一步。", FontFamily = (FontFamily)FindResource("Serif"), FontWeight = FontWeights.Bold, TextWrapping = TextWrapping.Wrap, Foreground = (Brush)FindResource("Accent"), Margin = new Thickness(0, 0, 0, 18) };
        introduction.Children.Add(firstLine);
        introduction.Children.Add(secondLine);
        introduction.Children.Add(new TextBlock { Text = "项目侧栏就是完整生产路径。每一步都可确认、可修订，通过侧栏切换工作区，继续当前项目。", TextWrapping = TextWrapping.Wrap, FontFamily = (FontFamily)FindResource("Serif"), FontSize = 13, LineHeight = 24, Foreground = (Brush)FindResource("Muted") });
        hero.Children.Add(introduction);
        var helpIcon = Icon("M12,2 A10,10 0 1 0 12,22 A10,10 0 1 0 12,2 M9.1,9 A3,3 0 0 1 15,10 C15,12 12,12 12,14 M12,17 L12,17.2", 110);
        helpIcon.HorizontalAlignment = HorizontalAlignment.Center;
        helpIcon.VerticalAlignment = VerticalAlignment.Top;
        helpIcon.Margin = new Thickness(0, 30, 0, 0);
        Grid.SetColumn(helpIcon, 1);
        hero.Children.Add(helpIcon);
        page.Children.Add(new Border { Child = hero, BorderBrush = (Brush)FindResource("Ink"), BorderThickness = new Thickness(0, 0, 0, 1) });

        var stages = new UniformGrid { Columns = 4, Name = "HelpStages" };
        var stageCards = new List<Border>();
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
        page.Children.Add(new Border { Child = stages, BorderBrush = (Brush)FindResource("Ink"), BorderThickness = new Thickness(1, 0, 1, 1) });

        var troubleshooting = new Grid { Name = "HelpTroubleshooting" };
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
        page.Children.Add(new Border { Child = troubleshooting, Background = (Brush)FindResource("PaperDeep"), BorderBrush = (Brush)FindResource("Accent"), BorderThickness = new Thickness(4, 0, 0, 0), Padding = new Thickness(26), Margin = new Thickness(0, 30, 0, 0) });
        page.Children.Add(new Expander { Header = "桌面快捷键与本机数据", Margin = new Thickness(0, 24, 0, 0), Content = new TextBlock
        {
            Text = "Ctrl + K  搜索并切换项目    Ctrl + N  新建项目    Ctrl + B  折叠侧栏    F5  刷新当前页面\n\n关窗会停止本次客户端启动的本地服务，运行中的任务可能中断。已有数据不会因关闭窗口而删除。凭据保存在服务端。",
            TextWrapping = TextWrapping.Wrap, LineHeight = 24, Margin = new Thickness(0, 12, 0, 0),
        } });
        void Responsive()
        {
            var width = ActualWidth;
            firstLine.FontSize = secondLine.FontSize = Math.Clamp(width * .05, 38, 65);
            stages.Columns = width <= 760 ? 1 : width <= 900 ? 2 : 4;
            for (var i = 0; i < stageCards.Count; i++)
                stageCards[i].BorderThickness = new Thickness(0, 0, (i + 1) % stages.Columns == 0 ? 0 : 1, i + stages.Columns < 4 ? 1 : 0);
            var stacked = width <= 1100;
            for (var i = 0; i < troubleshooting.Children.Count; i++)
            {
                var item = (FrameworkElement)troubleshooting.Children[i];
                Grid.SetColumn(item, stacked ? 0 : i);
                Grid.SetRow(item, stacked ? i : 0);
                Grid.SetColumnSpan(item, stacked ? 3 : 1);
                item.Margin = stacked ? new Thickness(0, i == 0 ? 0 : 18, 0, 0) : new Thickness(0, 0, i == 2 ? 0 : 28, 0);
            }
            helpIcon.Visibility = width <= 760 ? Visibility.Collapsed : Visibility.Visible;
            hero.ColumnDefinitions[1].Width = width <= 760 ? new GridLength(0) : new GridLength(42, GridUnitType.Star);
        }
        SizeChanged += (_, _) => Responsive();
        scroll.Content = page;
        Content = scroll;
        Responsive();
    }

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
