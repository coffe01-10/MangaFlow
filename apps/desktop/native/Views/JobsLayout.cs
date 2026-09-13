using System.Windows;
using System.Windows.Controls;
using System.Windows.Data;
using System.Windows.Markup;
using System.Windows.Media;
using MangaFlow.Native.Controls;
using MangaFlow.Native.Services;

namespace MangaFlow.Native.Views;

public sealed partial class JobsView
{
    private readonly TextBlock countLabel = new() { VerticalAlignment = VerticalAlignment.Bottom };
    private readonly Button archiveSelected = new() { Content = "归档已选（0）", IsEnabled = false };
    private readonly WrapPanel bulkActions = new();

    private void BuildPage()
    {
        var panel = new StackPanel { Margin = new Thickness(4, 0, 24, 28) };
        var heading = new StackPanel();
        heading.Children.Add(new TextBlock { Text = "JOBS / 任务中心", FontSize = 10,
            FontWeight = FontWeights.Bold, Foreground = AssetPageUi.Brush("Muted") });
        heading.Children.Add(new TextBlock { Text = "每个生成任务都能看懂、取消和重试",
            FontFamily = (FontFamily)Application.Current.FindResource("Serif"), FontSize = 24,
            Margin = new Thickness(0, 12, 0, 0), TextWrapping = TextWrapping.Wrap });
        countLabel.Style = (Style)Application.Current.FindResource("Caption");
        panel.Children.Add(new Border { BorderBrush = AssetPageUi.Brush("Ink"), BorderThickness = new Thickness(0, 0, 0, 1),
            Padding = new Thickness(0, 0, 0, 22), Child = new PageHeading(heading, countLabel) });
        var tabs = new WrapPanel();
        foreach (var tab in new[] { recentTab, historyTab })
        {
            tab.MinHeight = 42; tab.Margin = new Thickness(0, 0, 6, 0); tabs.Children.Add(tab);
            tab.ContentTemplate = IconLabel(ReferenceEquals(tab, recentTab) ? "tasks" : "history");
        }
        foreach (var action in new[] { archiveSelected, archiveAll })
        {
            action.Style = (Style)Application.Current.FindResource("Compact");
            StyleAction(action);
            action.MinHeight = 42; action.Margin = new Thickness(0, 0, 6, 0); bulkActions.Children.Add(action);
        }
        panel.Children.Add(new PageHeading(tabs, bulkActions) { Margin = new Thickness(0, 0, 0, 14) });
        var noticeStyle = new Style(typeof(TextBlock), notice.Style);
        var hidden = new DataTrigger { Binding = new Binding("Text") { RelativeSource = RelativeSource.Self }, Value = "" };
        hidden.Setters.Add(new Setter(VisibilityProperty, Visibility.Collapsed)); noticeStyle.Triggers.Add(hidden);
        notice.Style = noticeStyle; notice.Margin = new Thickness(0, 0, 0, 12);
        panel.Children.Add(notice); panel.Children.Add(body); scroller.Content = panel; Content = scroller;
    }

    private void UpdateToolbar()
    {
        archiveSelected.Content = $"归档已选（{selected.Count}）";
        archiveSelected.IsEnabled = selected.Count > 0 && !bulkPending && pending.Count == 0;
        archiveAll.IsEnabled = !bulkPending && pending.Count == 0;
    }

    private static void StyleAction(Button button)
    {
        button.Template = ActionTemplate;
        HoverMotion.SetEnabled(button, false);
        string text = button.Content?.ToString() ?? "";
        var icon = text.StartsWith("归档") ? "archive" : text switch
        {
            "重试" or "恢复" => "history", "取消" => "cancel",
            "彻底删除" => "trash", "查看结果" => "image", _ => "ledger",
        };
        button.ContentTemplate = IconLabel(icon);
        button.FontWeight = FontWeights.Normal;
    }

    private static readonly Dictionary<string, DataTemplate> IconTemplates = [];

    private static DataTemplate IconLabel(string name)
    {
        if (IconTemplates.TryGetValue(name, out var cached)) return cached;
        string geometry = name switch
        {
            "archive" => "M3 3H21V7H3Z M5 7V21H19V7 M9 11H15",
            "tasks" => "M3 4L5 6 9 2 M12 4H22 M3 12L5 14 9 10 M12 12H22 M3 20L5 22 9 18 M12 20H22",
            "history" => "M3 3V9H9 M3 9A9 9 0 1 1 3 17 M12 7V13L16 15",
            "cancel" => "M5 5L19 19 M19 5L5 19",
            "trash" => "M3 6H21 M9 6V3H15V6 M5 6L6 21H18L19 6 M10 10V17 M14 10V17",
            "image" => "M3 3H21V21H3Z M3 17L9 11 13 15 17 10 21 14",
            _ => "M5 2H19V22H5Z M8 7H16 M8 12H16 M8 17H13",
        };
        var template = (DataTemplate)XamlReader.Parse($$$"""
            <DataTemplate xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation">
              <StackPanel Orientation="Horizontal">
                <Viewbox Width="14" Height="14" Margin="0,0,7,0">
                  <Canvas Width="24" Height="24">
                    <Path Data="{{{geometry}}}" StrokeThickness="1.6" StrokeStartLineCap="Round" StrokeEndLineCap="Round" StrokeLineJoin="Round"
                          Stroke="{Binding Foreground, RelativeSource={RelativeSource AncestorType=Control}}"/>
                  </Canvas>
                </Viewbox>
                <TextBlock Text="{Binding}" VerticalAlignment="Center"/>
              </StackPanel>
            </DataTemplate>
            """);
        IconTemplates[name] = template;
        return template;
    }

    private static readonly ControlTemplate ActionTemplate = (ControlTemplate)XamlReader.Parse("""
        <ControlTemplate xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
                         xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml" TargetType="Button">
          <Border x:Name="Surface" Background="{TemplateBinding Background}" BorderBrush="{TemplateBinding BorderBrush}"
                  BorderThickness="{TemplateBinding BorderThickness}" Padding="{TemplateBinding Padding}">
            <ContentPresenter HorizontalAlignment="Center" VerticalAlignment="Center"/>
          </Border>
          <ControlTemplate.Triggers>
            <Trigger Property="IsMouseOver" Value="True"><Setter TargetName="Surface" Property="Background" Value="#08000000"/></Trigger>
            <Trigger Property="IsPressed" Value="True"><Setter TargetName="Surface" Property="Background" Value="#18000000"/></Trigger>
            <Trigger Property="IsKeyboardFocused" Value="True"><Setter TargetName="Surface" Property="BorderBrush" Value="#BD492F"/></Trigger>
            <Trigger Property="IsEnabled" Value="False"><Setter TargetName="Surface" Property="Opacity" Value="0.45"/></Trigger>
          </ControlTemplate.Triggers>
        </ControlTemplate>
        """);

    private Border EmptyState()
    {
        var stack = new StackPanel { VerticalAlignment = VerticalAlignment.Center };
        stack.Children.Add(new TextBlock { Text = archivedView ? "还没有历史任务" : "当前没有任务",
            FontFamily = (FontFamily)Application.Current.FindResource("Serif"), FontSize = 22 });
        var hint = Caption(archivedView ? "归档后的已结束任务会保留在这里，可随时恢复。" : "剧本解析、页面生成、检查和修复都会列在这里。");
        hint.Margin = new Thickness(0, 12, 0, 0); hint.TextWrapping = TextWrapping.Wrap; stack.Children.Add(hint);
        return new Border { BorderBrush = AssetPageUi.Brush("Line"), BorderThickness = new Thickness(1),
            Background = AssetPageUi.Brush("Surface"), Padding = new Thickness(24), MinHeight = 200, Child = stack };
    }

    private static FrameworkElement GroupHeading(string title, string hint) => new PageHeading(
        new TextBlock { Text = title, FontWeight = FontWeights.Bold, FontSize = 16, VerticalAlignment = VerticalAlignment.Center },
        new TextBlock { Text = hint, Foreground = AssetPageUi.Brush("Muted"), FontWeight = FontWeights.SemiBold,
            FontSize = 12, TextWrapping = TextWrapping.Wrap, VerticalAlignment = VerticalAlignment.Center })
        { MinHeight = 46, Margin = new Thickness(12, 0, 12, 0) };

    private Expander Group(string key, string title, string hint, IEnumerable<JobItem> items, bool failed = false)
    {
        var rows = new StackPanel { Margin = new Thickness(8, 0, 8, 2) };
        foreach (var job in items) rows.Children.Add(Row(job));
        var group = new Expander { Tag = key, Header = GroupHeading(title, hint), Content = rows,
            IsExpanded = expandedDates.Contains(key), Margin = new Thickness(0, 0, 0, 14),
            BorderBrush = failed ? new SolidColorBrush(Color.FromRgb(215, 170, 161)) : AssetPageUi.Brush("Line"),
            Background = AssetPageUi.Brush("Surface"), Template = GroupTemplate };
        group.Expanded += (_, _) => expandedDates.Add(key);
        group.Collapsed += (_, _) => expandedDates.Remove(key);
        return group;
    }

    // Native keyboard/focus behavior with the web's full-width rectangular summary.
    private static readonly ControlTemplate GroupTemplate = (ControlTemplate)XamlReader.Parse("""
        <ControlTemplate xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
                         xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml" TargetType="Expander">
          <Border BorderBrush="{TemplateBinding BorderBrush}" BorderThickness="1" Background="{TemplateBinding Background}">
            <StackPanel>
              <ToggleButton x:Name="HeaderToggle" Content="{TemplateBinding Header}"
                            IsChecked="{Binding IsExpanded, RelativeSource={RelativeSource TemplatedParent}, Mode=TwoWay}"
                            HorizontalContentAlignment="Stretch" Background="Transparent" BorderThickness="0">
                <ToggleButton.Template>
                  <ControlTemplate TargetType="ToggleButton">
                    <Border x:Name="Summary" Background="{TemplateBinding Background}" BorderThickness="1" BorderBrush="Transparent">
                      <ContentPresenter HorizontalAlignment="Stretch"/>
                    </Border>
                    <ControlTemplate.Triggers>
                      <Trigger Property="IsMouseOver" Value="True"><Setter TargetName="Summary" Property="Background" Value="#08000000"/></Trigger>
                      <Trigger Property="IsKeyboardFocused" Value="True"><Setter TargetName="Summary" Property="BorderBrush" Value="#BD492F"/></Trigger>
                    </ControlTemplate.Triggers>
                  </ControlTemplate>
                </ToggleButton.Template>
              </ToggleButton>
              <ContentPresenter x:Name="Rows" ContentSource="Content" Visibility="Collapsed"/>
            </StackPanel>
          </Border>
          <ControlTemplate.Triggers>
            <Trigger Property="IsExpanded" Value="True"><Setter TargetName="Rows" Property="Visibility" Value="Visible"/></Trigger>
          </ControlTemplate.Triggers>
        </ControlTemplate>
        """);
}

/// <summary>Wide task rows use web columns; compact windows stack details and actions without clipping.</summary>
internal sealed class JobRowPanel : Panel
{
    internal JobRowPanel(UIElement select, UIElement type, UIElement detail, UIElement actions)
    {
        Children.Add(select); Children.Add(type); Children.Add(detail); Children.Add(actions);
    }

    private double Layout(double width, bool arrange)
    {
        bool wide = width >= 760;
        double selectWidth = 32, typeWidth = 130, gap = 14;
        var selection = Children[0]; var type = Children[1]; var detail = Children[2]; var actions = Children[3];
        double contentWidth = Math.Max(0, width - selectWidth);
        if (wide)
        {
            double actionWidth = 214, detailWidth = Math.Max(0, contentWidth - typeWidth - actionWidth - gap * 2);
            if (!arrange)
            {
                selection.Measure(new Size(selectWidth, double.PositiveInfinity)); type.Measure(new Size(typeWidth, double.PositiveInfinity));
                detail.Measure(new Size(detailWidth, double.PositiveInfinity)); actions.Measure(new Size(actionWidth, double.PositiveInfinity));
            }
            double height = Children.Cast<UIElement>().Max(c => c.DesiredSize.Height);
            if (arrange)
            {
                selection.Arrange(new Rect(0, 0, selectWidth, height)); type.Arrange(new Rect(selectWidth, 0, typeWidth, height));
                detail.Arrange(new Rect(selectWidth + typeWidth + gap, 0, detailWidth, height));
                actions.Arrange(new Rect(width - actionWidth, 0, actionWidth, height));
            }
            return height;
        }
        if (!arrange)
        {
            selection.Measure(new Size(selectWidth, double.PositiveInfinity));
            foreach (var child in new[] { type, detail, actions }) child.Measure(new Size(contentWidth, double.PositiveInfinity));
        }
        double top = 0;
        if (arrange) selection.Arrange(new Rect(0, 0, selectWidth, type.DesiredSize.Height));
        foreach (var child in new[] { type, detail, actions })
        {
            if (arrange) child.Arrange(new Rect(selectWidth, top, contentWidth, child.DesiredSize.Height));
            top += child.DesiredSize.Height + 10;
        }
        return top - 10;
    }
    protected override Size MeasureOverride(Size available) { var width = double.IsFinite(available.Width) ? available.Width : 1000; return new Size(width, Layout(width, false)); }
    protected override Size ArrangeOverride(Size finalSize) { Layout(finalSize.Width, true); return finalSize; }
}
