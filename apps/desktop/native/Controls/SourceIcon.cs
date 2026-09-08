using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;
using System.Windows.Shapes;

namespace MangaFlow.Native.Controls;

/// <summary>Small native vector icons matching the web source page's line icons.</summary>
public static class SourceIcon
{
    private static string Data(string name) => name switch
    {
        "edit" => "M16 3 L21 8 8 21 3 22 4 17 Z M14 5 L19 10",
        "trash" => "M3 6 L21 6 M9 6 L9 3 15 3 15 6 M5 6 L6 21 18 21 19 6 M10 10 L10 17 M14 10 L14 17",
        "upload" => "M12 16 L12 3 M6 9 L12 3 18 9 M3 15 L3 21 21 21 21 15",
        "save" => "M3 3 L18 3 21 6 21 21 3 21 Z M7 3 L7 9 17 9 17 3 M7 21 L7 14 17 14 17 21",
        "file" => "M5 2 L15 2 20 7 20 22 5 22 Z M15 2 L15 7 20 7 M7 18 L11 13 14 16 17 12",
        "sparkles" => "M12 3 L14.5 9.5 21 12 14.5 14.5 12 21 9.5 14.5 3 12 9.5 9.5 Z M4 1 L4 7 M1 4 L7 4",
        _ => "M3 3 L21 3 21 21 3 21 Z M3 9 L21 9",
    };
    public static FrameworkElement Create(string name, double size = 15)
    {
        var path = new Path { Data = Geometry.Parse(Data(name)), StrokeThickness = 1.7, StrokeStartLineCap = PenLineCap.Round, StrokeEndLineCap = PenLineCap.Round, StrokeLineJoin = PenLineJoin.Round };
        path.SetBinding(Shape.StrokeProperty, new System.Windows.Data.Binding("Foreground") { RelativeSource = new System.Windows.Data.RelativeSource(System.Windows.Data.RelativeSourceMode.FindAncestor, typeof(Control), 1) });
        return new Viewbox { Width = size, Height = size, Child = new Canvas { Width = 24, Height = 24, Children = { path } } };
    }
    public static FrameworkElement Label(string icon, string text)
    {
        var row = new StackPanel { Orientation = Orientation.Horizontal };
        row.Children.Add(Create(icon));
        row.Children.Add(new TextBlock { Text = text, Margin = new Thickness(8, 0, 0, 0), VerticalAlignment = VerticalAlignment.Center });
        return row;
    }
    public static Button Action(string icon, string name, RoutedEventHandler handler)
    {
        var button = new Button { Content = Create(icon, 13), Width = 40, Height = 40, MinHeight = 40, Padding = new Thickness(0), Style = (Style)Application.Current.FindResource("SourceIconButton"), ToolTip = name };
        System.Windows.Automation.AutomationProperties.SetName(button, name);
        button.Click += handler;
        return button;
    }
}
