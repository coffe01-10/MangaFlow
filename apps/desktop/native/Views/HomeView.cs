using System.Net.Http;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Controls.Primitives;
using System.Windows.Media;
using System.Windows.Shapes;
using MangaFlow.Native.Controls;
using MangaFlow.Native.Services;

namespace MangaFlow.Native.Views;

/// <summary>NUI-2B: dashboard home with the full project-creation drawer (name/mode/resolution).</summary>
public sealed class HomeView : WorkspaceView
{
    public event Action? CreateRequested;
    public event Action? SettingsRequested;

    private readonly DrawerOverlay drawer = new() { DrawerWidth = 510 };
    private readonly TextBox nameInput = new()
    {
        FontFamily = (FontFamily)Application.Current.FindResource("Serif"),
        FontSize = 17, Padding = new Thickness(12, 12, 12, 12),
    };
    private readonly StackPanel modeGroup = new();
    private readonly StackPanel resolutionGroup = new();
    private readonly Button createButton = new() { Content = "创建项目", Style = (Style)Application.Current.FindResource("InkButton") };
    private readonly TextBlock drawerError = new() { Foreground = (Brush)Application.Current.FindResource("Danger"), TextWrapping = TextWrapping.Wrap };
    private bool creating;

    public HomeView()
    {
        var scroller = new ScrollViewer { VerticalScrollBarVisibility = ScrollBarVisibility.Auto };
        var grid = new Grid();
        grid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        grid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(308) });
        var main = new StackPanel { Margin = new Thickness(36, 40, 36, 36) };
        BuildHero(main);
        BuildProjects(main);
        scroller.Content = main;
        grid.Children.Add(scroller);
        grid.Children.Add(BuildRail());
        var overlay = new Grid();
        overlay.Children.Add(grid);
        drawer.Content = BuildDrawer();
        overlay.Children.Add(drawer);
        Content = overlay;
    }

    private static void BuildHero(StackPanel main)
    {
        var hero = new Grid { Margin = new Thickness(0, 4, 0, 30) };
        hero.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        hero.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(0.62, GridUnitType.Star) });
        var left = new StackPanel();
        left.Children.Add(new TextBlock { Text = "工作区 / 00", Style = (Style)Application.Current.FindResource("SectionIndex") });
        var title = new TextBlock { FontFamily = (FontFamily)Application.Current.FindResource("Serif"), FontSize = 40, FontWeight = FontWeights.Bold, LineHeight = 47, Margin = new Thickness(0, 12, 0, 0) };
        title.Inlines.Add(new System.Windows.Documents.Run("从文字开始，\n"));
        var accent = new System.Windows.Documents.Run("把故事画出来。") { Foreground = (Brush)Application.Current.FindResource("Accent") };
        title.Inlines.Add(accent);
        left.Children.Add(title);
        hero.Children.Add(left);
        var note = new TextBlock
        {
            Text = "面向连续漫画生产的结构化工作流。角色、服装、分镜与对白，每一步都可确认、可锁定、可修复。",
            FontFamily = (FontFamily)Application.Current.FindResource("Serif"),
            Foreground = (Brush)Application.Current.FindResource("Muted"), FontSize = 13, LineHeight = 23,
            VerticalAlignment = VerticalAlignment.Bottom, Margin = new Thickness(26, 0, 0, 4), TextWrapping = TextWrapping.Wrap,
        };
        Grid.SetColumn(note, 1);
        hero.Children.Add(note);
        main.Children.Add(hero);

        var metricStrip = new Border
        {
            BorderBrush = (Brush)Application.Current.FindResource("Ink"),
            BorderThickness = new Thickness(1),
            Background = (Brush)Application.Current.FindResource("Surface"),
        };
        var metricGrid = new Grid { MinHeight = 112 };
        for (var i = 0; i < 4; i++) metricGrid.ColumnDefinitions.Add(new ColumnDefinition { Width = i == 3 ? new GridLength(1.45, GridUnitType.Star) : new GridLength(1, GridUnitType.Star) });
        string[] captions = ["活跃项目", "漫画页面", "待复查"];
        string[] metricPaths = ["ActiveProjectMetric", "PageMetric", "ReviewMetric"];
        string[] subs = ["PROJECTS", "{Binding SelectedPageLabel}", "按页面去重"];
        for (var i = 0; i < 3; i++)
        {
            var cell = new StackPanel { Margin = new Thickness(16, 14, 14, 12) };
            cell.Children.Add(new TextBlock { Text = captions[i], Style = (Style)Application.Current.FindResource("Caption") });
            var value = new TextBlock { Style = (Style)Application.Current.FindResource("MetricValue") };
            value.SetBinding(TextBlock.TextProperty, new System.Windows.Data.Binding(metricPaths[i]));
            cell.Children.Add(value);
            var sub = new TextBlock { Style = (Style)Application.Current.FindResource("Micro") };
            if (i == 1) sub.SetBinding(TextBlock.TextProperty, new System.Windows.Data.Binding("SelectedPageLabel"));
            else sub.Text = subs[i];
            cell.Children.Add(sub);
            var border = new Border
            {
                Child = cell,
                BorderBrush = (Brush)Application.Current.FindResource("LineDark"),
                BorderThickness = new Thickness(0, 0, 1, 0),
            };
            Grid.SetColumn(border, i);
            metricGrid.Children.Add(border);
        }
        var aiCell = new StackPanel { Margin = new Thickness(16, 14, 14, 12), VerticalAlignment = VerticalAlignment.Center };
        aiCell.Children.Add(new TextBlock { Text = "AI 模型目录", Foreground = new SolidColorBrush(Color.FromRgb(0xAA, 0xA6, 0x9D)), FontSize = 12 });
        var aiValue = new TextBlock();
        aiValue.SetBinding(TextBlock.TextProperty, new System.Windows.Data.Binding("ModelLabel"));
        aiValue.Foreground = Brushes.White;
        aiValue.FontWeight = FontWeights.Bold;
        aiValue.FontSize = 16;
        aiValue.Margin = new Thickness(0, 8, 0, 0);
        aiCell.Children.Add(aiValue);
        aiCell.Children.Add(new TextBlock { Text = "图片显式选择 · 文字可自动路由", Foreground = new SolidColorBrush(Color.FromRgb(0xAA, 0xA6, 0x9D)), FontSize = 11 });
        var aiBorder = new Border { Child = aiCell, Background = (Brush)Application.Current.FindResource("Ink") };
        Grid.SetColumn(aiBorder, 3);
        metricGrid.Children.Add(aiBorder);
        metricStrip.Child = metricGrid;
        main.Children.Add(metricStrip);
    }

    private void BuildProjects(StackPanel main)
    {
        var header = new Border
        {
            BorderBrush = (Brush)Application.Current.FindResource("Line"),
            BorderThickness = new Thickness(0, 0, 0, 1),
            Padding = new Thickness(0, 0, 0, 14),
            Margin = new Thickness(0, 36, 0, 18),
        };
        var dock = new DockPanel();
        var count = new TextBlock { Style = (Style)Application.Current.FindResource("Caption"), VerticalAlignment = VerticalAlignment.Bottom };
        count.SetBinding(TextBlock.TextProperty, new System.Windows.Data.Binding("ProjectCount") { StringFormat = "{}{0} 个项目" });
        DockPanel.SetDock(count, Dock.Right);
        dock.Children.Add(count);
        var heading = new StackPanel();
        heading.Children.Add(new TextBlock { Text = "项目 / PROJECTS", Style = (Style)Application.Current.FindResource("SectionIndex") });
        heading.Children.Add(new TextBlock
        {
            Text = "最近创作", FontFamily = (FontFamily)Application.Current.FindResource("Serif"),
            FontSize = 23, FontWeight = FontWeights.Bold, Margin = new Thickness(0, 5, 0, 0),
        });
        dock.Children.Add(heading);
        header.Child = dock;
        main.Children.Add(header);

        var cards = new ItemsControl();
        cards.SetBinding(ItemsControl.ItemsSourceProperty, new System.Windows.Data.Binding("DashboardProjects"));
        var panelTemplate = new FrameworkElementFactory(typeof(UniformGrid));
        panelTemplate.SetValue(UniformGrid.ColumnsProperty, 3);
        cards.ItemsPanel = new ItemsPanelTemplate(panelTemplate);
        cards.ItemTemplate = CreateCardTemplate();
        main.Children.Add(cards);

        var newCard = new Button
        {
            Content = new StackPanel
            {
                HorizontalAlignment = HorizontalAlignment.Center,
                Children =
                {
                    new TextBlock { Text = "＋", FontSize = 34, TextAlignment = TextAlignment.Center, Margin = new Thickness(0, 0, 0, 8) },
                    new TextBlock { Text = "建立新的漫画项目", FontFamily = (FontFamily)Application.Current.FindResource("Serif"), FontSize = 17, TextAlignment = TextAlignment.Center },
                    new TextBlock { Text = "设置项目，导入原作，开始创作", Style = (Style)Application.Current.FindResource("Caption"), Margin = new Thickness(0, 8, 0, 0), TextAlignment = TextAlignment.Center },
                },
            },
            Background = Brushes.Transparent,
            BorderBrush = (Brush)Application.Current.FindResource("LineDark"),
            MinHeight = 300, MinWidth = 300,
            HorizontalAlignment = HorizontalAlignment.Left,
            Margin = new Thickness(0, 0, 0, 18),
        };
        newCard.Click += (_, _) => OpenCreationDrawer();
        newCard.SetBinding(Button.IsEnabledProperty, new System.Windows.Data.Binding("Connected"));
        main.Children.Add(newCard);

        var pager = new DockPanel { Margin = new Thickness(0, 4, 16, 0) };
        var previous = Act("上一页", (_, _) => ChangePage(-1), "Compact");
        var next = Act("下一页", (_, _) => ChangePage(1), "Compact");
        DockPanel.SetDock(previous, Dock.Left);
        DockPanel.SetDock(next, Dock.Right);
        pager.Children.Add(previous);
        pager.Children.Add(next);
        var pageLabel = new TextBlock { Style = (Style)Application.Current.FindResource("Caption"), HorizontalAlignment = HorizontalAlignment.Center, VerticalAlignment = VerticalAlignment.Center };
        pageLabel.SetBinding(TextBlock.TextProperty, new System.Windows.Data.Binding("DashboardPageLabel"));
        pager.Children.Add(pageLabel);
        previous.SetBinding(Button.IsEnabledProperty, new System.Windows.Data.Binding("HasPreviousPage"));
        next.SetBinding(Button.IsEnabledProperty, new System.Windows.Data.Binding("HasNextPage"));
        main.Children.Add(pager);
    }

    private void ChangePage(int delta) => State?.ChangeDashboardPage(delta);

    public event Action<ProjectItem>? ProjectRequested;

    private DataTemplate CreateCardTemplate()
    {
        var buttonFactory = new FrameworkElementFactory(typeof(Button));
        buttonFactory.SetValue(Button.BackgroundProperty, Application.Current.FindResource("Surface"));
        buttonFactory.SetValue(Button.PaddingProperty, new Thickness(10));
        buttonFactory.SetValue(Button.MarginProperty, new Thickness(0, 0, 16, 20));
        buttonFactory.SetValue(Button.HorizontalContentAlignmentProperty, HorizontalAlignment.Stretch);
        buttonFactory.SetValue(Button.MinWidthProperty, 300d);
        buttonFactory.SetValue(Button.TagProperty, new System.Windows.Data.Binding());
        buttonFactory.AddHandler(Button.ClickEvent, new RoutedEventHandler((sender, _) =>
        {
            if (sender is Button { Tag: ProjectItem project })
                ProjectRequested?.Invoke(project);
        }));

        var stack = new FrameworkElementFactory(typeof(StackPanel));
        var cover = new FrameworkElementFactory(typeof(Border));
        cover.SetValue(Border.HeightProperty, 264d);
        cover.SetValue(Border.BorderBrushProperty, new SolidColorBrush(Color.FromRgb(0xC2, 0xBC, 0xAF)));
        cover.SetValue(Border.BorderThicknessProperty, new Thickness(1));
        cover.SetValue(Border.BackgroundProperty, new SolidColorBrush(Color.FromRgb(0xD8, 0xD3, 0xC8)));
        var coverGrid = new FrameworkElementFactory(typeof(Grid));
        var pattern = new FrameworkElementFactory(typeof(Rectangle));
        pattern.SetValue(Rectangle.FillProperty, Application.Current.FindResource("CoverPattern"));
        pattern.SetValue(Rectangle.OpacityProperty, 0.5);
        coverGrid.AppendChild(pattern);
        var mark = new FrameworkElementFactory(typeof(TextBlock));
        mark.SetValue(TextBlock.TextProperty, "MANGA PROJECT");
        mark.SetValue(TextBlock.FontSizeProperty, 11d);
        mark.SetValue(TextBlock.FontWeightProperty, FontWeights.Bold);
        mark.SetValue(TextBlock.HorizontalAlignmentProperty, HorizontalAlignment.Left);
        mark.SetValue(TextBlock.VerticalAlignmentProperty, VerticalAlignment.Top);
        mark.SetValue(TextBlock.MarginProperty, new Thickness(12));
        coverGrid.AppendChild(mark);
        var title = new FrameworkElementFactory(typeof(TextBlock));
        title.SetBinding(TextBlock.TextProperty, new System.Windows.Data.Binding("CoverTitle"));
        title.SetValue(TextBlock.FontFamilyProperty, Application.Current.FindResource("Serif"));
        title.SetValue(TextBlock.FontSizeProperty, 22d);
        title.SetValue(TextBlock.LineHeightProperty, 25d);
        title.SetValue(TextBlock.FontWeightProperty, FontWeights.Bold);
        title.SetValue(TextBlock.TextAlignmentProperty, TextAlignment.Center);
        title.SetValue(TextBlock.HorizontalAlignmentProperty, HorizontalAlignment.Center);
        title.SetValue(TextBlock.VerticalAlignmentProperty, VerticalAlignment.Center);
        title.SetValue(TextBlock.MarginProperty, new Thickness(0, 26, 0, 18));
        coverGrid.AppendChild(title);
        cover.AppendChild(coverGrid);
        stack.AppendChild(cover);

        var meta = new FrameworkElementFactory(typeof(DockPanel));
        meta.SetValue(DockPanel.MarginProperty, new Thickness(3, 12, 3, 8));
        var arrow = new FrameworkElementFactory(typeof(TextBlock));
        arrow.SetValue(TextBlock.TextProperty, "→");
        arrow.SetValue(TextBlock.FontSizeProperty, 18d);
        arrow.SetValue(TextBlock.MarginProperty, new Thickness(8, 0, 0, 0));
        arrow.SetValue(DockPanel.DockProperty, Dock.Right);
        meta.AppendChild(arrow);
        var nameStack = new FrameworkElementFactory(typeof(StackPanel));
        var name = new FrameworkElementFactory(typeof(TextBlock));
        name.SetBinding(TextBlock.TextProperty, new System.Windows.Data.Binding("Name"));
        name.SetValue(TextBlock.FontFamilyProperty, Application.Current.FindResource("Serif"));
        name.SetValue(TextBlock.FontWeightProperty, FontWeights.Bold);
        name.SetValue(TextBlock.FontSizeProperty, 16d);
        name.SetValue(TextBlock.TextTrimmingProperty, TextTrimming.CharacterEllipsis);
        name.SetValue(TextBlock.TextWrappingProperty, TextWrapping.NoWrap);
        nameStack.AppendChild(name);
        var mode = new FrameworkElementFactory(typeof(TextBlock));
        mode.SetBinding(TextBlock.TextProperty, new System.Windows.Data.Binding("ModeAndResolution"));
        mode.SetValue(TextBlock.StyleProperty, Application.Current.FindResource("Caption"));
        mode.SetValue(TextBlock.MarginProperty, new Thickness(0, 4, 0, 0));
        nameStack.AppendChild(mode);
        meta.AppendChild(nameStack);
        stack.AppendChild(meta);

        var progress = new FrameworkElementFactory(typeof(ProgressBar));
        progress.SetBinding(ProgressBar.ValueProperty, new System.Windows.Data.Binding("Progress") { Mode = System.Windows.Data.BindingMode.OneWay });
        progress.SetValue(ProgressBar.HeightProperty, 2d);
        stack.AppendChild(progress);
        var summary = new FrameworkElementFactory(typeof(TextBlock));
        summary.SetBinding(TextBlock.TextProperty, new System.Windows.Data.Binding("Summary"));
        summary.SetValue(TextBlock.StyleProperty, Application.Current.FindResource("Caption"));
        summary.SetValue(TextBlock.MarginProperty, new Thickness(3, 8, 3, 0));
        stack.AppendChild(summary);

        buttonFactory.AppendChild(stack);
        return new DataTemplate(typeof(ProjectItem)) { VisualTree = buttonFactory };
    }

    private Border BuildRail()
    {
        var rail = new Border
        {
            Background = (Brush)Application.Current.FindResource("PaperDeep"),
            BorderBrush = (Brush)Application.Current.FindResource("Line"),
            BorderThickness = new Thickness(1, 0, 0, 0),
            Padding = new Thickness(22, 26, 22, 20),
        };
        var panel = new StackPanel();

        var connections = new Border { Style = (Style)Application.Current.FindResource("Card"), BorderBrush = (Brush)Application.Current.FindResource("LineDark"), Padding = new Thickness(18) };
        var connectionPanel = new StackPanel();
        connectionPanel.Children.Add(new TextBlock { Text = "AI 连接 / CONNECTIONS", FontWeight = FontWeights.Bold, FontSize = 12 });
        var connectionTitle = new TextBlock
        {
            FontFamily = (FontFamily)Application.Current.FindResource("Serif"),
            FontSize = 19, FontWeight = FontWeights.Bold, Margin = new Thickness(0, 18, 0, 10),
        };
        connectionTitle.SetBinding(TextBlock.TextProperty, new System.Windows.Data.Binding("AiConnectionTitle"));
        connectionPanel.Children.Add(connectionTitle);
        connectionPanel.Children.Add(new TextBlock
        {
            Text = "统一管理账号型凭据与 API Key 连接；模型能力以目录验证结果为准。",
            Style = (Style)Application.Current.FindResource("Caption"), LineHeight = 21,
        });
        var stats = new Grid { Margin = new Thickness(0, 16, 0, 16) };
        stats.ColumnDefinitions.Add(new ColumnDefinition());
        stats.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        stats.RowDefinitions.Add(new RowDefinition { Height = new GridLength(30) });
        stats.RowDefinitions.Add(new RowDefinition { Height = new GridLength(30) });
        stats.RowDefinitions.Add(new RowDefinition { Height = new GridLength(30) });
        string[] labels = ["已配置连接", "健康连接", "可用模型"];
        string[] bindings = ["ConfiguredConnections", "HealthyConnections", "EnabledModels"];
        for (var i = 0; i < 3; i++)
        {
            var label = new TextBlock { Text = labels[i], Style = (Style)Application.Current.FindResource("Caption"), VerticalAlignment = VerticalAlignment.Center };
            Grid.SetRow(label, i);
            stats.Children.Add(label);
            var value = new TextBlock { FontWeight = FontWeights.Bold, VerticalAlignment = VerticalAlignment.Center };
            value.SetBinding(TextBlock.TextProperty, new System.Windows.Data.Binding(bindings[i]));
            Grid.SetRow(value, i);
            Grid.SetColumn(value, 1);
            stats.Children.Add(value);
        }
        connectionPanel.Children.Add(stats);
        var manage = Act("管理连接与验证", async (_, _) => await (Context?.NavigateSection("settings-global", "") ?? Task.CompletedTask), "Outline");
        connectionPanel.Children.Add(manage);
        connections.Child = connectionPanel;
        panel.Children.Add(connections);

        var loop = new Border { Style = (Style)Application.Current.FindResource("Card"), BorderBrush = (Brush)Application.Current.FindResource("LineDark"), Padding = new Thickness(18), Margin = new Thickness(0, 16, 0, 0) };
        var loopPanel = new StackPanel();
        loopPanel.Children.Add(new TextBlock { Text = "生产闭环 / MVP ROUTE", FontWeight = FontWeights.Bold, FontSize = 12, Margin = new Thickness(0, 0, 0, 10) });
        string[] steps = ["导入原作", "建立资产", "剧本改编", "分页分镜", "生成页面", "人工校对与采用", "连续导出"];
        for (var i = 0; i < steps.Length; i++)
        {
            var row = new TextBlock
            {
                Text = $"{i + 1:D2}     {steps[i]}",
                Margin = new Thickness(0, 9, 0, 9),
                Foreground = i == 0 ? (Brush)Application.Current.FindResource("AccentInk") : (Brush)Application.Current.FindResource("Ink"),
            };
            loopPanel.Children.Add(row);
        }
        loopPanel.Children.Add(new TextBlock
        {
            Text = "原作导入、动态分页、逐页抽卡、收藏采用与批次素材库均已接入真实工作流。",
            Style = (Style)Application.Current.FindResource("Micro"), LineHeight = 19, Margin = new Thickness(0, 6, 0, 0),
        });
        loop.Child = loopPanel;
        panel.Children.Add(loop);

        rail.Child = panel;
        return rail;
    }

    private FrameworkElement BuildDrawer()
    {
        var scroll = new ScrollViewer { VerticalScrollBarVisibility = ScrollBarVisibility.Auto };
        var panel = new StackPanel { Margin = new Thickness(28, 26, 28, 24) };
        var header = new DockPanel { Margin = new Thickness(0, 0, 0, 18) };
        var close = Act("✕", (_, _) => drawer.Open = false, "Ghost");
        close.MinWidth = 40;
        DockPanel.SetDock(close, Dock.Right);
        header.Children.Add(close);
        var heading = new StackPanel();
        heading.Children.Add(new TextBlock { Text = "NEW PROJECT / 01", Style = (Style)Application.Current.FindResource("SectionIndex") });
        heading.Children.Add(new TextBlock
        {
            Text = "建立漫画项目", FontFamily = (FontFamily)Application.Current.FindResource("Serif"),
            FontSize = 25, FontWeight = FontWeights.Bold, Margin = new Thickness(0, 6, 0, 0),
        });
        header.Children.Add(heading);
        panel.Children.Add(header);

        panel.Children.Add(FieldLabel("项目名称"));
        System.Windows.Automation.AutomationProperties.SetName(nameInput, "项目名称");
        nameInput.MaxLines = 2;
        panel.Children.Add(nameInput);

        panel.Children.Add(FieldLabel("工作方式"));
        modeGroup.Children.Clear();
        foreach (var mode in new[] { "SEMI_AUTO", "DIRECTOR", "AUTO" })
        {
            var badge = mode switch { "SEMI_AUTO" => "推荐", "DIRECTOR" => "逐步", _ => "快速" };
            var card = new RadioButton
            {
                GroupName = "create-mode",
                Tag = mode,
                Margin = new Thickness(0, 0, 0, 8),
                MinHeight = 58,
                Content = new StackPanel
                {
                    Children =
                    {
                        new TextBlock { Text = $"{Labels.Map(Labels.WorkflowModeShort, mode)} · {badge}", FontWeight = FontWeights.Bold, FontSize = 13.5 },
                        new TextBlock { Text = Labels.Map(Labels.WorkflowModeDetail, mode), Style = (Style)Application.Current.FindResource("Micro"), Margin = new Thickness(0, 3, 0, 0) },
                    },
                },
            };
            if (mode == "SEMI_AUTO") card.IsChecked = true;
            modeGroup.Children.Add(card);
        }
        panel.Children.Add(modeGroup);
        panel.Children.Add(new TextBlock
        {
            Text = "图片模型不设默认主次；进入工作区后必须显式选择，以保持项目画风一致。",
            Style = (Style)Application.Current.FindResource("Micro"), Margin = new Thickness(0, 6, 0, 16), TextWrapping = TextWrapping.Wrap,
        });

        panel.Children.Add(FieldLabel("正式输出清晰度"));
        BuildResolution("2K");
        panel.Children.Add(resolutionGroup);
        panel.Children.Add(new TextBlock
        {
            Text = "凭据仅保存在服务端，本项目不会把密钥发往浏览器。",
            Style = (Style)Application.Current.FindResource("Micro"), Margin = new Thickness(0, 6, 0, 18), TextWrapping = TextWrapping.Wrap,
        });
        drawerError.Margin = new Thickness(0, 0, 0, 10);
        panel.Children.Add(drawerError);
        var actions = new StackPanel { Orientation = Orientation.Horizontal, HorizontalAlignment = HorizontalAlignment.Right };
        var cancel = Act("取消", (_, _) => drawer.Open = false, "Ghost");
        cancel.Margin = new Thickness(0, 0, 10, 0);
        actions.Children.Add(cancel);
        createButton.Click += CreateProject;
        actions.Children.Add(createButton);
        panel.Children.Add(actions);
        scroll.Content = panel;
        return scroll;
    }

    private void BuildResolution(string current)
    {
        resolutionGroup.Children.Clear();
        resolutionGroup.Orientation = Orientation.Horizontal;
        foreach (var option in new[] { "1K", "2K", "4K" })
        {
            var toggle = new ToggleButton
            {
                Content = option == "4K" ? "4K · Preview" : option,
                Style = (Style)Application.Current.FindResource("Pill"),
                IsChecked = option == current, MinWidth = 72, Margin = new Thickness(0, 0, 8, 0),
            };
            toggle.Checked += (_, _) =>
            {
                foreach (var other in resolutionGroup.Children.OfType<ToggleButton>())
                    if (!ReferenceEquals(other, toggle)) other.IsChecked = false;
            };
            resolutionGroup.Children.Add(toggle);
        }
    }

    public void OpenCreationDrawer() => drawer.Open = true;
    private async void CreateProject(object sender, RoutedEventArgs e)
    {
        if (creating || Context == null) return;
        var name = nameInput.Text.Trim();
        if (name.Length == 0)
        {
            drawerError.Text = "请先填写项目名称";
            nameInput.Focus();
            return;
        }
        var mode = modeGroup.Children.OfType<RadioButton>().FirstOrDefault(r => r.IsChecked == true) is { Tag: string m } ? m : "SEMI_AUTO";
        var resolution = resolutionGroup.Children.OfType<ToggleButton>().FirstOrDefault(t => t.IsChecked == true) is { Content: string value }
            ? value.Split(' ')[0] : "2K";
        creating = true;
        createButton.IsEnabled = false;
        drawerError.Text = "";
        try
        {
            await Api.SendAsync("projects", HttpMethod.Post,
                new { name, workflow_mode = mode, default_resolution = resolution }, cancellation: lifetime.Token);
            Cache.Invalidate("dashboard", "projects");
            nameInput.Text = "";
            drawer.Open = false;
            State.Status = $"项目「{name}」已创建";
            CreateRequested?.Invoke();
        }
        catch (Exception error) when (error is not OperationCanceledException)
        {
            drawerError.Text = error.Message;
        }
        finally
        {
            creating = false;
            createButton.IsEnabled = true;
        }
    }

    public override void Activate(WorkspaceContext context)
    {
        base.Activate(context);
        ProjectRequested -= OnProjectRequested;
        ProjectRequested += OnProjectRequested;
    }

    private void OnProjectRequested(ProjectItem project) => Context?.NavigateSection("source", $"project:{project.Id}");
}
