using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;

namespace MangaFlow.Native.Views;

public sealed class HelpView : WorkspaceView
{
    public HelpView()
    {
        var scroller = new ScrollViewer { VerticalScrollBarVisibility = ScrollBarVisibility.Auto };
        var panel = new StackPanel { Margin = new Thickness(36, 32, 36, 28), MaxWidth = 780, HorizontalAlignment = HorizontalAlignment.Left };

        panel.Children.Add(new TextBlock { Text = "HELP / 01", Style = (Style)Application.Current.FindResource("SectionIndex") });
        panel.Children.Add(new TextBlock
        {
            Text = "把复杂的漫画生产，",
            FontFamily = (FontFamily)Application.Current.FindResource("Serif"),
            FontSize = 36, FontWeight = FontWeights.Bold, Margin = new Thickness(0, 10, 0, 0),
        });
        panel.Children.Add(new TextBlock
        {
            Text = "拆成可确认的每一步。",
            FontFamily = (FontFamily)Application.Current.FindResource("Serif"),
            FontSize = 36, FontWeight = FontWeights.Bold, Foreground = (Brush)Application.Current.FindResource("Accent"),
            Margin = new Thickness(0, 2, 0, 12),
        });
        panel.Children.Add(Caption("项目侧栏就是完整生产路径。每个入口都可以刷新和收藏，工作进度自动保存。"));
        panel.Children.Add(new TextBlock
        {
            Text = "键盘快捷键", FontSize = 17, FontWeight = FontWeights.Bold,
            FontFamily = (FontFamily)Application.Current.FindResource("Serif"), Margin = new Thickness(0, 28, 0, 12),
        });
        var shortcuts = new Border
        {
            Style = (Style)Application.Current.FindResource("Card"),
            Padding = new Thickness(22),
            Child = new TextBlock
            {
                Text = "Ctrl + K     搜索并切换项目\n\nCtrl + N     新建项目\n\nCtrl + B     折叠 / 展开侧栏\n\nF5                刷新当前页面\n\nEsc              关闭对话框",
                LineHeight = 24,
            },
        };
        panel.Children.Add(shortcuts);

        var stages = new[]
        {
            ("01", "导入原作", "粘贴文本或上传 TXT / Markdown，系统保留每次修订。"),
            ("02", "建立参考资产", "分别建立角色、服装和漫画风格档案，并绑定参考图。"),
            ("03", "编排与生产", "生成剧本和动态分页，逐页抽卡、收藏并采用满意版本。"),
            ("04", "检查与导出", "在任务中心查看进度，检查连续性后导出 PNG、PDF 或 JSON。"),
        };
        foreach (var (index, title, description) in stages)
        {
            var card = new StackPanel();
            card.Children.Add(new TextBlock { Text = index, Style = (Style)Application.Current.FindResource("SectionIndex") });
            card.Children.Add(new TextBlock
            {
                Text = title, FontFamily = (FontFamily)Application.Current.FindResource("Serif"),
                FontSize = 19, FontWeight = FontWeights.Bold, Margin = new Thickness(0, 4, 0, 6),
            });
            card.Children.Add(Caption(description));
            panel.Children.Add(new Border
            {
                Style = (Style)Application.Current.FindResource("Card"),
                Padding = new Thickness(22),
                Margin = new Thickness(0, 14, 0, 0),
                Child = card,
            });
        }

        panel.Children.Add(new TextBlock { Text = "AI 连接 / 故障排查", Style = (Style)Application.Current.FindResource("SectionIndex"), Margin = new Thickness(0, 30, 0, 8) });
        panel.Children.Add(new TextBlock
        {
            Text = "供应商连接显示断开时怎么办？",
            FontFamily = (FontFamily)Application.Current.FindResource("Serif"),
            FontSize = 19, FontWeight = FontWeights.Bold,
        });
        var steps = new List<(string, string)>
        {
            ("1", "打开“设置”，确认账号型环境凭据或连接 API Key 已就绪。"),
            ("2", "先运行无生成费用的凭据验证，再按需执行模型能力测试。"),
            ("3", "网络波动会显示为连接降级，不会清除人工启停与服务端配置。"),
        };
        foreach (var (n, text) in steps)
        {
            var row = new StackPanel { Orientation = Orientation.Horizontal, Margin = new Thickness(0, 10, 0, 0) };
            row.Children.Add(new TextBlock { Text = n, Style = (Style)Application.Current.FindResource("SectionIndex"), Margin = new Thickness(0, 0, 12, 0) });
            row.Children.Add(new TextBlock { Text = text, TextWrapping = TextWrapping.Wrap });
            panel.Children.Add(row);
        }
        var open = Act("打开系统设置", async (_, _) => await Context!.NavigateSection("settings-global", ""), "InkButton");
        open.Margin = new Thickness(0, 16, 0, 0);
        open.HorizontalAlignment = HorizontalAlignment.Left;
        panel.Children.Add(open);

        panel.Children.Add(new TextBlock
        {
            Text = "关于本机数据", FontSize = 17, FontWeight = FontWeights.Bold,
            FontFamily = (FontFamily)Application.Current.FindResource("Serif"), Margin = new Thickness(0, 28, 0, 10),
        });
        panel.Children.Add(Caption("关窗会停止本次客户端启动的本地服务。运行中的任务可能中断，请先在任务中心确认状态。已有数据不会因关闭窗口而删除。凭据保存在服务端，本项目不会把密钥发往界面。"));

        scroller.Content = panel;
        Content = scroller;
    }
}
