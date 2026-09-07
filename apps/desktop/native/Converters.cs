using System.Globalization;
using System.Windows;
using System.Windows.Data;

namespace MangaFlow.Native;

public class InvertBoolConverter : IValueConverter
{
    public object Convert(object? value, Type targetType, object? parameter, CultureInfo culture) =>
        value is bool b ? !b : false;
    public object ConvertBack(object? value, Type targetType, object? parameter, CultureInfo culture) =>
        value is bool b ? !b : false;
}

public class NullToVisibilityConverter : IValueConverter
{
    public object Convert(object? value, Type targetType, object? parameter, CultureInfo culture) =>
        value == null ? Visibility.Collapsed : Visibility.Visible;
    public object ConvertBack(object? value, Type targetType, object? parameter, CultureInfo culture) =>
        throw new NotSupportedException();
}

public class EmptyToVisibilityConverter : IValueConverter
{
    // Empty string -> Visible (default) or Collapsed with parameter "collapse".
    public object Convert(object? value, Type targetType, object? parameter, CultureInfo culture)
    {
        var visible = string.IsNullOrEmpty(value as string);
        if (string.Equals(parameter as string, "collapse", StringComparison.OrdinalIgnoreCase))
            return visible ? Visibility.Collapsed : Visibility.Visible;
        return visible ? Visibility.Visible : Visibility.Collapsed;
    }
    public object ConvertBack(object? value, Type targetType, object? parameter, CultureInfo culture) =>
        throw new NotSupportedException();
}
