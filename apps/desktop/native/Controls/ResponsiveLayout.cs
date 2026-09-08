using System.Windows;
using System.Windows.Controls;

namespace MangaFlow.Native.Controls;

/// <summary>Preserves the web artwork's 3:4 ratio while its grid cell changes width.</summary>
public sealed class ArtworkFrame : Decorator
{
    protected override Size MeasureOverride(Size constraint)
    {
        var width = double.IsFinite(constraint.Width) ? Math.Max(0, constraint.Width) : 210;
        var size = new Size(width, width * 4 / 3);
        Child?.Measure(size);
        return size;
    }
    protected override Size ArrangeOverride(Size size)
    {
        Child?.Arrange(new Rect(size));
        return size;
    }
}

/// <summary>Batch groups occupy one to three 280-DIP columns, in source order like the web grid.</summary>
public sealed class BatchPanel : Panel
{
    private const double Gap = 16;
    private double Layout(double width, bool arrange)
    {
        var columns = Math.Max(1, (int)Math.Floor((width + Gap) / (280 + Gap)));
        var cell = Math.Max(0, (width - Gap * (columns - 1)) / columns);
        double top = 0, height = 0;
        var column = 0;
        foreach (UIElement child in InternalChildren)
        {
            var span = Math.Clamp(Grid.GetColumnSpan(child), 1, columns);
            if (column + span > columns) { top += height + Gap; height = 0; column = 0; }
            var childWidth = cell * span + Gap * (span - 1);
            if (!arrange) child.Measure(new Size(childWidth, double.PositiveInfinity));
            else child.Arrange(new Rect(column * (cell + Gap), top, childWidth, child.DesiredSize.Height));
            height = Math.Max(height, child.DesiredSize.Height);
            column += span;
        }
        return top + height;
    }
    protected override Size MeasureOverride(Size constraint)
    {
        var width = double.IsFinite(constraint.Width) ? Math.Max(0, constraint.Width) : 280;
        return new Size(width, Layout(width, false));
    }
    protected override Size ArrangeOverride(Size size) { Layout(size.Width, true); return size; }
}

/// <summary>Equal-width web-style cards; wrapping never gives a child more width than its cell.</summary>
public sealed class TilePanel : Panel
{
    public double MinimumTileWidth { get; set; } = 180;
    public int MaximumColumns { get; set; } = 3;
    public double Gap { get; set; } = 20;

    private int Columns(double width) => Math.Max(1, Math.Min(MaximumColumns,
        (int)Math.Floor((width + Gap) / (MinimumTileWidth + Gap))));

    protected override Size MeasureOverride(Size availableSize)
    {
        var width = double.IsFinite(availableSize.Width) ? availableSize.Width : MinimumTileWidth;
        var columns = Columns(width);
        var cellWidth = Math.Max(0, (width - Gap * (columns - 1)) / columns);
        double total = 0, rowHeight = 0;
        var index = 0;
        foreach (UIElement child in InternalChildren)
        {
            child.Measure(new Size(cellWidth, double.PositiveInfinity));
            rowHeight = Math.Max(rowHeight, child.DesiredSize.Height);
            if (++index % columns != 0) continue;
            total += rowHeight + Gap;
            rowHeight = 0;
        }
        if (index % columns != 0) total += rowHeight + Gap;
        return new Size(width, Math.Max(0, total - Gap));
    }

    protected override Size ArrangeOverride(Size finalSize)
    {
        var columns = Columns(finalSize.Width);
        var width = Math.Max(0, (finalSize.Width - Gap * (columns - 1)) / columns);
        double y = 0;
        for (var first = 0; first < InternalChildren.Count; first += columns)
        {
            var count = Math.Min(columns, InternalChildren.Count - first);
            double height = 0;
            for (var i = 0; i < count; i++) height = Math.Max(height, InternalChildren[first + i].DesiredSize.Height);
            for (var i = 0; i < count; i++)
                InternalChildren[first + i].Arrange(new Rect(i * (width + Gap), y, width, height));
            y += height + Gap;
        }
        return finalSize;
    }
}

/// <summary>A title and actions share a row when they fit; narrow windows retain both.</summary>
public sealed class PageHeading : Panel
{
    public PageHeading(UIElement title, UIElement actions)
    {
        Children.Add(title);
        Children.Add(actions);
    }

    protected override Size MeasureOverride(Size availableSize)
    {
        var width = double.IsFinite(availableSize.Width) ? availableSize.Width : 1000;
        Children[1].Measure(new Size(width, double.PositiveInfinity));
        Children[0].Measure(new Size(width, double.PositiveInfinity));
        var stack = Children[0].DesiredSize.Width + Children[1].DesiredSize.Width + 24 > width;
        return new Size(width, stack ? Children[0].DesiredSize.Height + Children[1].DesiredSize.Height + 14
            : Math.Max(Children[0].DesiredSize.Height, Children[1].DesiredSize.Height));
    }

    protected override Size ArrangeOverride(Size finalSize)
    {
        var title = Children[0].DesiredSize;
        var actions = Children[1].DesiredSize;
        if (title.Width + actions.Width + 24 > finalSize.Width)
        {
            Children[0].Arrange(new Rect(0, 0, finalSize.Width, title.Height));
            Children[1].Arrange(new Rect(0, title.Height + 14, finalSize.Width, actions.Height));
        }
        else
        {
            Children[0].Arrange(new Rect(0, 0, finalSize.Width - actions.Width - 24, finalSize.Height));
            Children[1].Arrange(new Rect(finalSize.Width - actions.Width, 0, actions.Width, finalSize.Height));
        }
        return finalSize;
    }
}
