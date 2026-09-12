using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;
using MangaFlow.Native.Controls;

namespace MangaFlow.Native.Views;

internal static class ScriptPageUi
{
    internal static FrameworkElement RevisionRule()
    {
        var text = new StackPanel();
        text.Children.Add(new TextBlock { Text = "导演修订模式", FontSize = 13, FontWeight = FontWeights.Bold });
        text.Children.Add(new TextBlock { Text = "场景与情节拍可直接修改；来源区间保持只读，避免剧情丢失。", FontSize = 11, Foreground = AssetPageUi.Brush("Muted"), TextWrapping = TextWrapping.Wrap, Margin = new Thickness(0, 4, 0, 0) });
        return text;
    }
    internal static FrameworkElement Field(string label, FrameworkElement input)
    {
        System.Windows.Automation.AutomationProperties.SetName(input, label);
        var panel = new StackPanel(); panel.Children.Add(new TextBlock { Text = label, Foreground = AssetPageUi.Brush("Muted"), FontSize = 12, Margin = new Thickness(0, 0, 0, 6) }); panel.Children.Add(input); return panel;
    }
    internal static Border Band(UIElement child, string color = "Surface", Thickness? padding = null) => new()
    {
        Child = child, Background = AssetPageUi.Brush(color), Padding = padding ?? new Thickness(14), BorderBrush = AssetPageUi.Brush("Line"), BorderThickness = new Thickness(0, 0, 0, 1),
    };
    internal static Button Action(string text, RoutedEventHandler handler, string style = "Compact")
    {
        var button = Kit.Act(text, handler, style); button.MinHeight = 40; return button;
    }
}

/// <summary>Web scene header: ordinal, location, purpose and edit action; purpose wraps below on narrow screens.</summary>
internal sealed class ScriptSceneHeading : Panel
{
    internal ScriptSceneHeading(UIElement ordinal, UIElement title, UIElement purpose, UIElement action)
    { Children.Add(ordinal); Children.Add(title); Children.Add(purpose); Children.Add(action); }
    private Size Layout(double width, bool arrange)
    {
        width = double.IsFinite(width) ? width : 1000;
        bool narrow = width < 800;
        double number = narrow ? 68 : 85, action = 100, gap = 10;
        double middle = Math.Max(0, width - number - action - gap * 2);
        double title = narrow ? middle : middle * .54;
        double purpose = narrow ? Math.Max(0, width - number) : Math.Max(0, middle - title - gap);
        if (!arrange)
        {
            Children[0].Measure(new Size(number, double.PositiveInfinity)); Children[1].Measure(new Size(title, double.PositiveInfinity));
            Children[2].Measure(new Size(purpose, double.PositiveInfinity)); Children[3].Measure(new Size(action, double.PositiveInfinity));
        }
        double row = Math.Max(Children[0].DesiredSize.Height, Math.Max(Children[1].DesiredSize.Height, Children[3].DesiredSize.Height));
        if (!narrow) row = Math.Max(row, Children[2].DesiredSize.Height);
        double height = row + (narrow && Children[2].DesiredSize.Height > 0 ? gap + Children[2].DesiredSize.Height : 0);
        if (arrange)
        {
            Children[0].Arrange(new Rect(0, 0, number, row)); Children[1].Arrange(new Rect(number, 0, title, row));
            Children[2].Arrange(new Rect(narrow ? number : number + title + gap, narrow ? row + gap : 0, purpose, narrow ? Children[2].DesiredSize.Height : row));
            Children[3].Arrange(new Rect(Math.Max(0, width - action), 0, action, row));
        }
        return new Size(width, height);
    }
    protected override Size MeasureOverride(Size constraint) => Layout(constraint.Width, false);
    protected override Size ArrangeOverride(Size size) { Layout(size.Width, true); return size; }
}
