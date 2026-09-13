using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;
using MangaFlow.Native.Controls;

namespace MangaFlow.Native.Views;

public sealed partial class LibraryView
{
    private bool HasFilters => new[] { chapterSelector, characterSelector, kindSelector, modelSelector, resolutionSelector }.Any(c => !string.IsNullOrEmpty(Selected(c)))
        || favoriteOnly.IsChecked == true || dateFrom.SelectedDate != null || dateTo.SelectedDate != null;

    private StackPanel BuildLibraryLayout(out WrapPanel dates)
    {
        var panel = new StackPanel { Margin = new Thickness(0, 0, 14, 28), Background = AssetPageUi.Brush("Paper"), UseLayoutRounding = true, SnapsToDevicePixels = true };
        TextOptions.SetTextFormattingMode(panel, TextFormattingMode.Display); RenderOptions.SetClearTypeHint(panel, ClearTypeHint.Enabled);
        var title = new StackPanel();
        title.Children.Add(new TextBlock { Text = "LIBRARY / 批次素材库", Foreground = AssetPageUi.Brush("Muted"), FontSize = 10, FontWeight = FontWeights.Bold });
        title.Children.Add(new TextBlock { Text = "保存每一次值得比较的结果", FontFamily = (FontFamily)FindResource("Serif"), FontSize = 24, Margin = new Thickness(0, 10, 0, 0), TextWrapping = TextWrapping.Wrap });
        countLabel.VerticalAlignment = VerticalAlignment.Bottom; countLabel.FontSize = 13;
        panel.Children.Add(new Border { Child = new PageHeading(title, countLabel), Padding = new Thickness(0, 0, 0, 18), Margin = new Thickness(0, 0, 0, 16), BorderBrush = AssetPageUi.Brush("Ink"), BorderThickness = new Thickness(0, 0, 0, 1) });
        var filters = new LibraryFilterPanel { Margin = new Thickness(0, 0, 0, 8) };
        foreach (var control in new Control[] { chapterSelector, favoriteOnly, characterSelector, kindSelector, modelSelector, resolutionSelector })
        {
            control.Width = double.NaN; control.MinWidth = 0; control.MinHeight = 42; control.FontSize = 13;
            control.HorizontalAlignment = HorizontalAlignment.Stretch; filters.Children.Add(control);
            if (control is ComboBox selector)
                selector.SelectionChanged += (_, _) =>
                {
                    selector.BorderBrush = AssetPageUi.Brush(selector.SelectedIndex > 0 ? "Accent" : "Line");
                    selector.FontWeight = selector.SelectedIndex > 0 ? FontWeights.SemiBold : FontWeights.Normal;
                    selector.ToolTip = (selector.SelectedItem as ComboBoxItem)?.Content;
                };
        }
        panel.Children.Add(filters);
        dates = new WrapPanel { Margin = new Thickness(0, 0, 0, 12) };
        foreach (var (label, picker) in new[] { ("从", dateFrom), ("至", dateTo) })
        {
            picker.Width = 155; picker.MinHeight = 42; picker.FontSize = 13;
            var field = new StackPanel { Orientation = Orientation.Horizontal, Margin = new Thickness(0, 0, 8, 8) };
            field.Children.Add(new TextBlock { Text = label, Foreground = AssetPageUi.Brush("Muted"), VerticalAlignment = VerticalAlignment.Center, Margin = new Thickness(0, 0, 8, 0) });
            field.Children.Add(picker); dates.Children.Add(field);
        }
        panel.Children.Add(dates);
        var noticeStyle = new Style(typeof(TextBlock), notice.Style);
        var empty = new DataTrigger { Binding = new System.Windows.Data.Binding("Text") { RelativeSource = new System.Windows.Data.RelativeSource(System.Windows.Data.RelativeSourceMode.Self) }, Value = "" };
        empty.Setters.Add(new Setter(VisibilityProperty, Visibility.Collapsed)); noticeStyle.Triggers.Add(empty); notice.Style = noticeStyle;
        return panel;
    }
}

/// <summary>Six aligned filters with extra room for model names; dates remain on their own row.</summary>
internal sealed class LibraryFilterPanel : Panel
{
    private const double Gap = 8;
    private static int Columns(double width) => width >= 1080 ? 6 : width >= 680 ? 3 : 2;
    private double Layout(double width, bool arrange)
    {
        int columns = Columns(width);
        var unit = Math.Max(0, width - Gap * (columns - 1)) / (columns == 6 ? 8 : columns);
        double top = 0;
        for (int first = 0; first < InternalChildren.Count; first += columns)
        {
            double height = 0, left = 0;
            for (int i = first; i < Math.Min(first + columns, InternalChildren.Count); i++)
            {
                double cell = unit * (columns == 6 && i == 4 ? 3 : 1);
                if (!arrange) InternalChildren[i].Measure(new Size(cell, double.PositiveInfinity));
                height = Math.Max(height, InternalChildren[i].DesiredSize.Height);
            }
            for (int i = first; i < Math.Min(first + columns, InternalChildren.Count); i++)
            {
                double cell = unit * (columns == 6 && i == 4 ? 3 : 1);
                if (arrange) InternalChildren[i].Arrange(new Rect(left, top, cell, height));
                left += cell + Gap;
            }
            top += height + Gap;
        }
        return Math.Max(0, top - Gap);
    }
    protected override Size MeasureOverride(Size constraint) { double width = double.IsFinite(constraint.Width) ? constraint.Width : 1080; return new Size(width, Layout(width, false)); }
    protected override Size ArrangeOverride(Size size) { Layout(size.Width, true); return size; }
}

/// <summary>Web library groups span one to three image columns; compact windows stack whole batches.</summary>
internal sealed class LibraryBatchPanel : Panel
{
    private const double Gap = 16;
    private double Layout(double width, bool arrange)
    {
        int columns = width < 680 ? 1 : Math.Clamp((int)Math.Floor((width + Gap) / 286), 1, 4);
        double cell = Math.Max(0, width - Gap * (columns - 1)) / columns, top = 0, height = 0;
        int column = 0;
        foreach (UIElement child in InternalChildren)
        {
            int span = Math.Clamp(Grid.GetColumnSpan(child), 1, columns);
            if (column + span > columns) { top += height + Gap; height = 0; column = 0; }
            double childWidth = cell * span + Gap * (span - 1);
            if (!arrange) child.Measure(new Size(childWidth, double.PositiveInfinity));
            else child.Arrange(new Rect(column * (cell + Gap), top, childWidth, child.DesiredSize.Height));
            height = Math.Max(height, child.DesiredSize.Height); column += span;
        }
        return top + height;
    }
    protected override Size MeasureOverride(Size constraint) { double width = double.IsFinite(constraint.Width) ? constraint.Width : 1080; return new Size(width, Layout(width, false)); }
    protected override Size ArrangeOverride(Size size) { Layout(size.Width, true); return size; }
}

internal sealed class LibraryBatchHeading : Panel
{
    internal LibraryBatchHeading(UIElement title, TextBlock date) { Children.Add(title); date.TextWrapping = TextWrapping.Wrap; date.TextAlignment = TextAlignment.Right; date.VerticalAlignment = VerticalAlignment.Center; date.FontSize = 11; Children.Add(date); }
    protected override Size MeasureOverride(Size constraint)
    {
        double width = double.IsFinite(constraint.Width) ? constraint.Width : 270;
        Children[1].Measure(new Size(width * .54, double.PositiveInfinity));
        Children[0].Measure(new Size(Math.Max(0, width - Children[1].DesiredSize.Width - 10), double.PositiveInfinity));
        return new Size(width, Math.Max(Children[0].DesiredSize.Height, Children[1].DesiredSize.Height));
    }
    protected override Size ArrangeOverride(Size size)
    {
        double right = Children[1].DesiredSize.Width;
        Children[0].Arrange(new Rect(0, 0, Math.Max(0, size.Width - right - 10), size.Height));
        Children[1].Arrange(new Rect(size.Width - right, 0, right, size.Height)); return size;
    }
}
