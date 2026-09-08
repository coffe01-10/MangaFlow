using System.Windows;
using System.Windows.Input;
using System.Windows.Media;
using System.Windows.Media.Animation;

namespace MangaFlow.Native.Controls;

/// <summary>Transform-only feedback; animation clocks are released and OS motion preferences respected.</summary>
public static class HoverMotion
{
    public static readonly DependencyProperty EnabledProperty = DependencyProperty.RegisterAttached(
        "Enabled", typeof(bool), typeof(HoverMotion), new PropertyMetadata(false, Changed));
    public static bool GetEnabled(DependencyObject value) => (bool)value.GetValue(EnabledProperty);
    public static void SetEnabled(DependencyObject value, bool enabled) => value.SetValue(EnabledProperty, enabled);

    private static void Changed(DependencyObject value, DependencyPropertyChangedEventArgs args)
    {
        if (value is not FrameworkElement element) return;
        element.MouseEnter -= Enter;
        element.MouseLeave -= Leave;
        element.Unloaded -= Unload;
        if ((bool)args.NewValue)
        {
            element.MouseEnter += Enter;
            element.MouseLeave += Leave;
            element.Unloaded += Unload;
        }
    }
    private static void Enter(object sender, MouseEventArgs args) => Move((FrameworkElement)sender, -1);
    private static void Leave(object sender, MouseEventArgs args) => Move((FrameworkElement)sender, 0);
    private static void Unload(object sender, RoutedEventArgs args)
    {
        if (((FrameworkElement)sender).RenderTransform is TranslateTransform transform)
        {
            transform.BeginAnimation(TranslateTransform.YProperty, null);
            transform.Y = 0;
        }
    }
    private static void Move(FrameworkElement element, double target)
    {
        if (element.RenderTransform is not TranslateTransform transform)
        {
            if (element.RenderTransform != null && !element.RenderTransform.Value.IsIdentity) return;
            element.RenderTransform = transform = new TranslateTransform();
        }
        var from = transform.Y;
        transform.BeginAnimation(TranslateTransform.YProperty, null);
        transform.Y = Motion.Enabled ? target : 0;
        if (!Motion.Enabled || !element.IsEnabled) return;
        transform.BeginAnimation(TranslateTransform.YProperty, new DoubleAnimation(from, target, Motion.Fast)
        { EasingFunction = new EaseOutQuint(), FillBehavior = FillBehavior.Stop });
    }
}
