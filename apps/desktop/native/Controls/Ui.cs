using System.IO;
using System.Net.Http;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Data;
using System.Globalization;
using System.Windows.Input;
using System.Windows.Media;
using System.Windows.Media.Animation;
using System.Windows.Media.Imaging;
using System.Windows.Shapes;

namespace MangaFlow.Native.Controls;

// cubic-bezier(.2,.75,.2,1) — web --ease-out.
public sealed class EaseOutQuint : EasingFunctionBase
{
    protected override double EaseInCore(double normalizedTime) =>
        1 - Math.Pow(1 - normalizedTime, 4);
    protected override Freezable CreateInstanceCore() => new EaseOutQuint();
}

public sealed class ProgressToPercentConverter : IValueConverter
{
    public object Convert(object? value, Type targetType, object? parameter, CultureInfo culture) =>
        value is double d ? $"{Math.Clamp((int)Math.Round(d), 0, 100)}%" : "0%";
    public object ConvertBack(object? value, Type targetType, object? parameter, CultureInfo culture) =>
        throw new NotSupportedException();
}

public static class Motion
{
    // Respect the OS "show animations" setting.
    public static bool Enabled => SystemParameters.ClientAreaAnimation;

    public static TimeSpan Fast => TimeSpan.FromMilliseconds(120);
    public static TimeSpan Base => TimeSpan.FromMilliseconds(180);
    public static TimeSpan Slow => TimeSpan.FromMilliseconds(280);

    public static void FadeTranslate(FrameworkElement element, double fromOffset, double toOffset,
        double fromOpacity, double toOpacity, TimeSpan duration, Action? completed = null)
    {
        if (!Enabled)
        {
            element.BeginAnimation(UIElement.OpacityProperty, null);
            element.Opacity = toOpacity;
            element.RenderTransform = null;
            completed?.Invoke();
            return;
        }
        element.RenderTransform = new TranslateTransform(0, fromOffset);
        var move = new DoubleAnimation(fromOffset, toOffset, duration) { EasingFunction = new EaseOutQuint() };
        var fade = new DoubleAnimation(fromOpacity, toOpacity, duration) { EasingFunction = new EaseOutQuint() };
        if (completed != null)
        {
            var done = false;
            fade.Completed += (_, _) =>
            {
                if (done) return;
                done = true;
                completed();
            };
        }
        element.BeginAnimation(UIElement.OpacityProperty, fade);
        ((TranslateTransform)element.RenderTransform).BeginAnimation(TranslateTransform.YProperty, move);
    }
}

/// <summary>
/// Content presenter that animates when Content changes, mirroring the web workspace
/// section transition (180ms ease-out fade + 10px slide). Offscreen hosts never raise
/// Loaded, so the entrance animation is driven by LayoutUpdated instead.
/// </summary>
public sealed class TransitioningContentControl : ContentControl
{
    private bool first = true;

    protected override void OnContentChanged(object oldContent, object newContent)
    {
        base.OnContentChanged(oldContent, newContent);
        if (first || ReferenceEquals(oldContent, newContent)) { first = false; return; }
        if (newContent is not FrameworkElement incoming) return;
        first = false;
        // Only real windows carry a clock; offscreen hosts (render fixtures) would
        // capture the first animation frame at opacity 0, so present instantly there.
        if (PresentationSource.FromVisual(this) == null || !Motion.Enabled)
        {
            incoming.BeginAnimation(UIElement.OpacityProperty, null);
            incoming.Opacity = 1;
            incoming.RenderTransform = null;
            return;
        }
        incoming.Opacity = 0;
        EventHandler layoutHandler = null!;
        layoutHandler = (_, _) =>
        {
            incoming.LayoutUpdated -= layoutHandler;
            Motion.FadeTranslate(incoming, 10, 0, 0, 1, Motion.Base);
        };
        incoming.LayoutUpdated += layoutHandler;
    }
}

/// <summary>Rotating arc spinner, ink-toned for the paper background.</summary>
public sealed class Spinner : ContentControl
{
    public static readonly DependencyProperty SizeProperty = DependencyProperty.Register(
        nameof(Size), typeof(double), typeof(Spinner), new PropertyMetadata(16.0, (d, e) =>
        {
            var s = (Spinner)d;
            s.Width = s.Height = s.MinWidth = s.MinHeight = (double)e.NewValue;
        }));
    public double Size { get => (double)GetValue(SizeProperty); set => SetValue(SizeProperty, value); }

    private readonly RotateTransform rotate = new();
    private Storyboard? story;
    private bool running;

    public Spinner()
    {
        var stroke = TryFindResource("Muted") as Brush ?? Brushes.Gray;
        var arc = new System.Windows.Shapes.Path
        {
            Data = Geometry.Parse("M 8 1.5 A 6.5 6.5 0 0 1 14.5 8"),
            Stroke = stroke,
            StrokeThickness = 2.2,
            StrokeStartLineCap = PenLineCap.Round,
            StrokeEndLineCap = PenLineCap.Round,
            Stretch = Stretch.Uniform,
        };
        arc.RenderTransform = rotate;
        arc.RenderTransformOrigin = new Point(0.5, 0.5);
        Content = arc;
        Focusable = false;
        IsVisibleChanged += (_, e) => Update((bool)e.NewValue);
        Loaded += (_, _) => Update(true);
        Unloaded += (_, _) => Update(false);
        IsEnabledChanged += (_, e) => Update((bool)e.NewValue && IsVisible);
    }

    private void Update(bool visible)
    {
        running = visible && IsEnabled;
        if (story == null)
        {
            if (!running) return;
            if (!Motion.Enabled) return;
            var spin = new DoubleAnimation(0, 359, new Duration(TimeSpan.FromSeconds(0.9)))
            { RepeatBehavior = RepeatBehavior.Forever };
            story = new Storyboard();
            Storyboard.SetTarget(spin, rotate);
            Storyboard.SetTargetProperty(spin, new PropertyPath(RotateTransform.AngleProperty));
            story.Children.Add(spin);
        }
        if (running) story.Begin(this, true);
        else story.Stop(this);
    }
}

/// <summary>
/// Async image element with a shared LRU decode cache. Downloads are cancelled when
/// the control unloads or the bound URL changes.
/// </summary>
public sealed class ImageBox : ContentControl
{
    public static readonly DependencyProperty SourceUrlProperty = DependencyProperty.Register(
        nameof(SourceUrl), typeof(string), typeof(ImageBox),
        new PropertyMetadata(null, (d, _) => ((ImageBox)d).Reload()));
    public string? SourceUrl { get => (string?)GetValue(SourceUrlProperty); set => SetValue(SourceUrlProperty, value); }

    private CancellationTokenSource? load;

    static ImageBox() => DefaultStyleKeyProperty.OverrideMetadata(
        typeof(ImageBox), new FrameworkPropertyMetadata(typeof(ImageBox)));

    public ImageBox()
    {
        Focusable = false;
        Content = Placeholder();
        Unloaded += (_, _) => load?.Cancel();
    }

    private static Border Placeholder()
    {
        var background = Application.Current.TryFindResource("PaperDeep") as Brush ?? Brushes.LightGray;
        return new Border { Background = background, Child = new Spinner { Size = 18 } };
    }

    private void Reload()
    {
        load?.Cancel();
        var url = SourceUrl;
        if (string.IsNullOrEmpty(url)) { Content = Placeholder(); return; }
        Content = Placeholder();
        if (ImageStore.TryGet(url, out var cached))
        {
            Present(cached);
            return;
        }
        var request = new CancellationTokenSource();
        load = request;
        var token = request.Token;
        _ = Task.Run(async () =>
        {
            try
            {
                var image = await ImageStore.LoadAsync(url, token);
                if (token.IsCancellationRequested || image == null) return;
                await Dispatcher.BeginInvoke(() =>
                {
                    if (token.IsCancellationRequested || SourceUrl != url) return;
                    Present(image);
                });
            }
            catch (OperationCanceledException) { }
        }, token);
    }

    private void Present(BitmapSource image)
    {
        Content = new Image
        {
            Source = image,
            Stretch = Stretch.Uniform,
            HorizontalAlignment = HorizontalAlignment.Stretch,
            VerticalAlignment = VerticalAlignment.Stretch,
        };
    }
}

/// <summary>Shared download+decode cache for thumbnails and originals.</summary>
public static class ImageStore
{
    private static readonly HttpClient http = new(new HttpClientHandler { UseProxy = false })
    { Timeout = TimeSpan.FromSeconds(30) };
    private static readonly Dictionary<string, BitmapImage> cache = new();
    private static readonly LinkedList<string> order = new();
    private static readonly object gate = new();
    private const int Capacity = 220;
    private static long bytes;

    public static bool TryGet(string url, out BitmapImage image)
    {
        lock (gate)
        {
            if (cache.TryGetValue(url, out image!))
            {
                order.Remove(url);
                order.AddFirst(url);
                return true;
            }
            return false;
        }
    }

    public static async Task<BitmapImage?> LoadAsync(string url, CancellationToken cancellation)
    {
        if (TryGet(url, out var cached)) return cached;
        byte[] data;
        using (var request = new HttpRequestMessage(HttpMethod.Get, url))
            data = await (await http.SendAsync(request, cancellation).ConfigureAwait(false))
                .Content.ReadAsByteArrayAsync(cancellation).ConfigureAwait(false);
        if (data.Length == 0 || data.Length > 60 * 1024 * 1024) return null;
        var image = new BitmapImage();
        using (var stream = new MemoryStream(data))
        {
            image.BeginInit();
            image.CacheOption = BitmapCacheOption.OnLoad;
            image.StreamSource = stream;
            image.CreateOptions = BitmapCreateOptions.IgnoreColorProfile;
            image.EndInit();
        }
        image.Freeze();
        if (image.PixelWidth == 0 || image.PixelHeight == 0) return null;
        lock (gate)
        {
            if (cache.TryGetValue(url, out var existing)) return existing;
            cache[url] = image;
            order.AddFirst(url);
            bytes += (long)image.PixelWidth * image.PixelHeight * 4;
            while ((order.Count > Capacity || bytes > 240 * 1024 * 1024) && order.Count > 8)
            {
                var last = order.Last!;
                order.RemoveLast();
                if (cache.Remove(last.Value, out var dropped))
                    bytes -= (long)dropped.PixelWidth * dropped.PixelHeight * 4;
            }
        }
        return image;
    }
}

/// <summary>Modal confirm dialog with Esc/cancel, initial focus on the safe action.</summary>
public sealed class ConfirmDialog : Window
{    public ConfirmDialog(Window owner, string title, string message, string confirmText,
        bool danger = false, string? extra = null)
    {
        Owner = owner;
        Title = title;
        Width = 470;
        SizeToContent = SizeToContent.Height;
        MaxHeight = 640;
        ResizeMode = ResizeMode.NoResize;
        WindowStartupLocation = WindowStartupLocation.CenterOwner;
        ShowInTaskbar = false;
        Background = (Brush)Application.Current.FindResource("Paper");
        var ink = (Brush)Application.Current.FindResource("Ink");
        var body = new TextBlock { Text = message, LineHeight = 23, Margin = new Thickness(0, 10, 0, 0) };
        if (extra != null) body.Text += "\n\n" + extra;
        var cancel = new Button { Content = "取消", MinWidth = 96, Margin = new Thickness(0, 0, 10, 0) };
        var confirm = new Button { Content = confirmText, MinWidth = 120 };
        if (danger) confirm.Style = (Style)Application.Current.FindResource("DangerButton");
        else confirm.Style = (Style)Application.Current.FindResource("InkButton");
        cancel.Click += (_, _) => { DialogResult = false; Close(); };
        confirm.Click += (_, _) => { DialogResult = true; Close(); };
        var scroll = new ScrollViewer { VerticalScrollBarVisibility = ScrollBarVisibility.Auto };
        var panel = new StackPanel { Margin = new Thickness(26) };
        panel.Children.Add(new TextBlock
        {
            Text = title, FontFamily = (FontFamily)Application.Current.FindResource("Serif"),
            FontSize = 20, FontWeight = FontWeights.SemiBold, Foreground = ink,
        });
        panel.Children.Add(body);
        var actions = new StackPanel
        {
            Orientation = Orientation.Horizontal, HorizontalAlignment = HorizontalAlignment.Right,
            Margin = new Thickness(0, 22, 0, 0),
        };
        actions.Children.Add(cancel);
        actions.Children.Add(confirm);
        panel.Children.Add(actions);
        scroll.Content = panel;
        Content = scroll;
        PreviewKeyDown += (_, e) =>
        {
            if (e.Key == Key.Escape) { e.Handled = true; DialogResult = false; Close(); }
        };
        Loaded += (_, _) => cancel.Focus();
    }
}

/// <summary>Small modal text-input dialog (single field).</summary>
public sealed class InputDialog : Window
{
    private readonly TextBox input = new();
    public string Value => input.Text;

    public InputDialog(Window owner, string title, string label, string? placeholder = null, string preset = "")
    {
        Owner = owner;
        Title = title;
        Width = 440;
        SizeToContent = SizeToContent.Height;
        ResizeMode = ResizeMode.NoResize;
        WindowStartupLocation = WindowStartupLocation.CenterOwner;
        ShowInTaskbar = false;
        Background = (Brush)Application.Current.FindResource("Paper");
        input.Text = preset;
        System.Windows.Automation.AutomationProperties.SetName(input, label);
        var cancel = new Button { Content = "取消", MinWidth = 90, Margin = new Thickness(0, 0, 10, 0) };
        var confirm = new Button { Content = "确定", MinWidth = 100, Style = (Style)Application.Current.FindResource("InkButton") };
        cancel.Click += (_, _) => { DialogResult = false; Close(); };
        confirm.Click += (_, _) => { DialogResult = true; Close(); };
        var panel = new StackPanel { Margin = new Thickness(26) };
        panel.Children.Add(new TextBlock
        {
            Text = title, FontFamily = (FontFamily)Application.Current.FindResource("Serif"),
            FontSize = 19, FontWeight = FontWeights.SemiBold,
        });
        panel.Children.Add(new TextBlock
        {
            Text = placeholder ?? label, Style = (Style)Application.Current.FindResource("Caption"), Margin = new Thickness(0, 6, 0, 10),
        });
        panel.Children.Add(input);
        var actions = new StackPanel
        {
            Orientation = Orientation.Horizontal, HorizontalAlignment = HorizontalAlignment.Right,
            Margin = new Thickness(0, 16, 0, 0),
        };
        actions.Children.Add(cancel);
        actions.Children.Add(confirm);
        panel.Children.Add(actions);
        Content = panel;
        PreviewKeyDown += (_, e) =>
        {
            if (e.Key == Key.Escape) { e.Handled = true; DialogResult = false; Close(); }
            if (e.Key == Key.Enter) { e.Handled = true; DialogResult = true; Close(); }
        };
        Loaded += (_, _) => { input.Focus(); input.SelectAll(); };
    }
}

/// <summary>In-place drawer that slides in from the right over a dimmed backdrop.</summary>
public sealed class DrawerOverlay : ContentControl
{
    private readonly Grid root = new();
    private readonly Border backdrop = new()
    { Background = new SolidColorBrush(Color.FromRgb(0x15, 0x15, 0x12)), Opacity = 0 };
    private readonly Border surface;
    private bool hosting;

    public static readonly DependencyProperty OpenProperty = DependencyProperty.Register(
        nameof(Open), typeof(bool), typeof(DrawerOverlay),
        new PropertyMetadata(false, (d, e) => ((DrawerOverlay)d).Apply((bool)e.NewValue)));
    public bool Open { get => (bool)GetValue(OpenProperty); set => SetValue(OpenProperty, value); }

    public double DrawerWidth { get; set; } = 510;

    public event EventHandler? ClosedByUser;

    public DrawerOverlay()
    {
        Focusable = false;
        surface = new Border
        {
            Background = (Brush)Application.Current.FindResource("Paper"),
            BorderBrush = (Brush)Application.Current.FindResource("Ink"),
            BorderThickness = new Thickness(1, 0, 0, 0),
            Width = 510,
            HorizontalAlignment = HorizontalAlignment.Right,
            VerticalAlignment = VerticalAlignment.Stretch,
        };
        root.Children.Add(backdrop);
        root.Children.Add(surface);
        root.Visibility = Visibility.Collapsed;
        Content = root;
        backdrop.MouseLeftButtonDown += (_, _) => { Open = false; ClosedByUser?.Invoke(this, EventArgs.Empty); };
        PreviewKeyDown += (_, e) =>
        {
            if (e.Key == Key.Escape && Open)
            {
                e.Handled = true;
                Open = false;
                ClosedByUser?.Invoke(this, EventArgs.Empty);
            }
        };
        SizeChanged += (_, _) => ClampWidth();
    }

    private void ClampWidth()
    {
        surface.Width = Math.Min(DrawerWidth, Math.Max(300, ActualWidth - 24));
    }

    protected override void OnContentChanged(object oldContent, object newContent)
    {
        // Business content is hosted inside the sliding surface; our chrome stays intact.
        if (hosting) { base.OnContentChanged(oldContent, newContent); return; }
        if (!ReferenceEquals(newContent, root))
        {
            hosting = true;
            try
            {
                if (newContent is FrameworkElement content) surface.Child = content;
                Content = root;
            }
            finally { hosting = false; }
        }
        else base.OnContentChanged(oldContent, newContent);
    }

    private void Apply(bool open)
    {
        ClampWidth();
        root.Visibility = open ? Visibility.Visible : Visibility.Collapsed;
        if (!Motion.Enabled)
        {
            backdrop.Opacity = open ? 0.45 : 0;
            var still = surface.RenderTransform as TranslateTransform;
            if (still != null) still.X = 0;
            return;
        }
        var slideTransform = new TranslateTransform(open ? surface.Width : 0, 0);
        surface.RenderTransform = slideTransform;
        var slide = new DoubleAnimation(open ? surface.Width : 0, open ? 0 : surface.Width, Motion.Slow)
        { EasingFunction = new EaseOutQuint() };
        var fade = new DoubleAnimation(open ? 0 : 0.45, open ? 0.45 : 0, Motion.Base);
        slideTransform.BeginAnimation(TranslateTransform.XProperty, slide);
        backdrop.BeginAnimation(OpacityProperty, fade);
    }
}

/// <summary>Fullscreen image lightbox: Esc close, +/- zoom 50%–250%, click background to close.</summary>
public sealed class Lightbox : Window
{
    private readonly Image image = new() { Stretch = Stretch.None };
    private readonly TextBlock zoomLabel = new()
    {
        Text = "100%", Foreground = Brushes.WhiteSmoke, FontSize = 13,
        Margin = new Thickness(16, 0, 16, 0), VerticalAlignment = VerticalAlignment.Center,
    };
    private double zoom = 1;
    private readonly Action? onClose;

    public Lightbox(Window owner, string url, string label, Action? onClose = null)
    {
        this.onClose = onClose;
        Owner = owner;
        Title = label;
        WindowState = WindowState.Maximized;
        WindowStyle = WindowStyle.None;
        Background = new SolidColorBrush(Color.FromRgb(0x15, 0x15, 0x12));
        ShowInTaskbar = false;
        var paper = (Brush)Application.Current.FindResource("Paper");
        var header = new DockPanel { Margin = new Thickness(24, 18, 24, 8) };
        var title = new TextBlock
        {
            Text = label, Foreground = paper, FontSize = 16, FontWeight = FontWeights.Bold,
            TextTrimming = TextTrimming.CharacterEllipsis, TextWrapping = TextWrapping.NoWrap,
        };
        DockPanel.SetDock(title, Dock.Left);
        header.Children.Add(title);
        var zoomOut = LightboxButton("－", paper);
        var zoomIn = LightboxButton("＋", paper);
        var reset = LightboxButton("复位", paper);
        zoomOut.Click += (_, _) => SetZoom(zoom - 0.25);
        zoomIn.Click += (_, _) => SetZoom(zoom + 0.25);
        reset.Click += (_, _) => SetZoom(1);
        var actions = new StackPanel
        { Orientation = Orientation.Horizontal, HorizontalAlignment = HorizontalAlignment.Right };
        actions.Children.Add(zoomOut);
        actions.Children.Add(zoomLabel);
        actions.Children.Add(zoomIn);
        actions.Children.Add(reset);
        header.Children.Add(actions);
        var caption = new TextBlock
        {
            Text = "使用 ＋/－ 调整到 50%–250%，Esc 或点击背景关闭，关闭后回到原缩略图",
            Foreground = (Brush)Application.Current.FindResource("LineDark"),
            FontSize = 11, Margin = new Thickness(24, 8, 24, 18),
        };
        var viewer = new ScrollViewer
        {
            VerticalScrollBarVisibility = ScrollBarVisibility.Auto,
            HorizontalScrollBarVisibility = ScrollBarVisibility.Auto,
            Content = new Border
            {
                Child = image, HorizontalAlignment = HorizontalAlignment.Center,
                VerticalAlignment = VerticalAlignment.Center,
            },
        };
        viewer.MouseLeftButtonDown += (_, e) => { if (e.OriginalSource is ScrollViewer) Close(); };
        var panel = new DockPanel();
        DockPanel.SetDock(header, Dock.Top);
        DockPanel.SetDock(caption, Dock.Bottom);
        panel.Children.Add(header);
        panel.Children.Add(caption);
        panel.Children.Add(viewer);
        Content = panel;
        PreviewKeyDown += (_, e) =>
        {
            switch (e.Key)
            {
                case Key.Escape: e.Handled = true; Close(); break;
                case Key.OemPlus or Key.Add: e.Handled = true; SetZoom(zoom + 0.25); break;
                case Key.OemMinus or Key.Subtract: e.Handled = true; SetZoom(zoom - 0.25); break;
            }
        };
        _ = Load(url);
    }

    private static Button LightboxButton(string text, Brush brush) => new()
    {
        Content = text, MinWidth = 44, MinHeight = 36, Margin = new Thickness(4, 0, 0, 0),
        Background = Brushes.Transparent, Foreground = brush, BorderBrush = brush,
        FontSize = 14, FontWeight = FontWeights.Bold,
    };

    private async Task Load(string url)
    {
        try
        {
            var bitmap = await Task.Run(() => ImageStore.LoadAsync(url, CancellationToken.None));
            if (bitmap != null)
            {
                await Dispatcher.BeginInvoke(() =>
                {
                    image.Source = bitmap;
                    SetZoom(1);
                });
            }
        }
        catch (Exception) { }
    }

    private void SetZoom(double value)
    {
        zoom = Math.Clamp(value, 0.5, 2.5);
        image.LayoutTransform = new ScaleTransform(zoom, zoom);
        zoomLabel.Text = $"{(int)Math.Round(zoom * 100)}%";
    }

    protected override void OnClosed(EventArgs e)
    {
        base.OnClosed(e);
        onClose?.Invoke();
    }
}
