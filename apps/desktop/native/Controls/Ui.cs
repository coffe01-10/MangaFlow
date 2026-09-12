using System.IO;
using System.Net;
using System.Net.Http;
using System.Text.Json;
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
    public EaseOutQuint() => EasingMode = EasingMode.EaseIn;
    protected override double EaseInCore(double normalizedTime)
    {
        // Invert x(t), then evaluate y(t) for the actual web cubic-bezier(.2,.75,.2,1).
        double low = 0, high = 1, t = normalizedTime;
        for (var i = 0; i < 20; i++)
        {
            var x = 0.6 * (1 - t) * t + t * t * t;
            if (x < normalizedTime) low = t; else high = t;
            t = (low + high) / 2;
        }
        return 2.25 * (1 - t) * (1 - t) * t + 3 * (1 - t) * t * t + t * t * t;
    }
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
        var transform = new TranslateTransform(0, toOffset);
        element.RenderTransform = transform;
        element.Opacity = toOpacity;
        var move = new DoubleAnimation(fromOffset, toOffset, duration) { EasingFunction = new EaseOutQuint(), FillBehavior = FillBehavior.Stop };
        var fade = new DoubleAnimation(fromOpacity, toOpacity, duration) { EasingFunction = new EaseOutQuint(), FillBehavior = FillBehavior.Stop };
        fade.Completed += (_, _) =>
        {
            if (!ReferenceEquals(element.RenderTransform, transform)) return;
            element.BeginAnimation(UIElement.OpacityProperty, null);
            transform.BeginAnimation(TranslateTransform.YProperty, null);
            completed?.Invoke();
        };
        element.BeginAnimation(UIElement.OpacityProperty, fade);
        transform.BeginAnimation(TranslateTransform.YProperty, move);
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
    private FrameworkElement? pending;
    private EventHandler? pendingLayout;

    private void CancelEntrance()
    {
        if (pending != null && pendingLayout != null) pending.LayoutUpdated -= pendingLayout;
        pending = null;
        pendingLayout = null;
    }

    public TransitioningContentControl() => Unloaded += (_, _) => CancelEntrance();

    protected override void OnContentChanged(object oldContent, object newContent)
    {
        CancelEntrance();
        if (oldContent is FrameworkElement outgoing)
        {
            outgoing.BeginAnimation(UIElement.OpacityProperty, null);
            outgoing.Opacity = 1;
            outgoing.RenderTransform = null;
        }
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
        pending = incoming;
        pendingLayout = (_, _) =>
        {
            CancelEntrance();
            if (!ReferenceEquals(Content, incoming)) return;
            Motion.FadeTranslate(incoming, 10, 0, 0, 1, Motion.Base);
        };
        incoming.LayoutUpdated += pendingLayout;
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
        HorizontalContentAlignment = HorizontalAlignment.Stretch;
        VerticalContentAlignment = VerticalAlignment.Stretch;
        Content = Placeholder();
        Unloaded += (_, _) => load?.Cancel();
        Loaded += (_, _) => { if (load?.IsCancellationRequested == true) Reload(); };
    }

    private static Border Placeholder()
    {
        var background = Application.Current.TryFindResource("PaperDeep") as Brush ?? Brushes.LightGray;
        return new Border { Background = background, Child = new Spinner { Size = 18 } };
    }

    private static Border FailedPlaceholder(string message)
    {
        var background = Application.Current.TryFindResource("PaperDeep") as Brush ?? Brushes.LightGray;
        var text = new TextBlock
        {
            // #449-2：带上映射后的服务端 detail（或状态码回退），不再只有笼统一句。
            Text = message.Length > 0 ? message : "图片加载失败", FontSize = 11, TextWrapping = TextWrapping.Wrap,
            TextAlignment = TextAlignment.Center,
            HorizontalAlignment = HorizontalAlignment.Center, VerticalAlignment = VerticalAlignment.Center,
            Style = (Style)Application.Current.FindResource("Micro"),
        };
        return new Border { Background = background, Child = text };
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
                if (token.IsCancellationRequested) return;
                await Dispatcher.BeginInvoke(() =>
                {
                    if (token.IsCancellationRequested || SourceUrl != url) return;
                    // null = 下载到空数据/无法解码；同样离开 spinner 状态。
                    if (image != null) Present(image);
                    else Content = FailedPlaceholder("图片为空或无法解码。");
                });
            }
            // HttpClient 的 30s 超时表现为调用方令牌未取消的取消异常（镜像 ApiClient
            // 的判定）：留在原地就是永远转圈的缩略图。
            catch (OperationCanceledException) when (!token.IsCancellationRequested)
            {
                await Dispatcher.BeginInvoke(() =>
                {
                    if (token.IsCancellationRequested || SourceUrl != url) return;
                    Content = FailedPlaceholder("图片加载超时，请重试。");
                });
            }
            catch (OperationCanceledException) { }
            catch (Exception ex)
            {
                if (token.IsCancellationRequested) return;
                var message = MediaErrors.Localize(ex);
                await Dispatcher.BeginInvoke(() =>
                {
                    if (token.IsCancellationRequested || SourceUrl != url) return;
                    Content = FailedPlaceholder(message);
                });
            }
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

/// <summary>媒体直连 HttpClient 的错误映射（#449-2）：镜像 ApiClient.ThrowResponseError
/// 的语义（ApiClient.cs 不归本文件改动，就地复制）：JSON detail（字符串 / FastAPI 422
/// 数组 / {code,message,blockers} 对象）优先；否则本地化回退带状态码；409 追加冲突
/// 前缀，让后端 404/409 的 detail 对图片界面可达，而不是一行生成的英文。</summary>
internal static class MediaErrors
{
    public static string DescribeResponse(HttpResponseMessage response, string body, string fallbackLabel = "图片加载失败")
    {
        var detail = $"{fallbackLabel}（{(int)response.StatusCode}）";
        try
        {
            using var error = JsonDocument.Parse(body);
            if (error.RootElement.ValueKind == JsonValueKind.Object &&
                error.RootElement.TryGetProperty("detail", out var value))
                detail = DescribeDetail(value, detail);
            else if (error.RootElement.ValueKind == JsonValueKind.String)
                detail = error.RootElement.GetString() ?? detail;
        }
        catch (JsonException) { }
        if (response.StatusCode == HttpStatusCode.Conflict)
            detail = "数据已变化或操作条件不满足。请刷新后重试。\n" + detail;
        return detail.Length > 2000 ? detail[..2000] : detail;
    }

    /// <summary>已映射的 InvalidOperationException/TimeoutException 携带服务端
    /// detail 或状态码回退；其余（HttpRequestException 等）是原始英文，给本地化回退。</summary>
    public static string Localize(Exception error) => error switch
    {
        InvalidOperationException or TimeoutException => error.Message,
        _ => "图片加载失败，请检查本地服务后重试。",
    };

    private static string DescribeDetail(JsonElement value, string fallback)
    {
        switch (value.ValueKind)
        {
            case JsonValueKind.String:
                return value.GetString() ?? fallback;
            case JsonValueKind.Array:
            {
                var lines = new List<string>();
                foreach (var item in value.EnumerateArray())
                {
                    if (item.ValueKind == JsonValueKind.Object &&
                        item.TryGetProperty("msg", out var msg) && msg.ValueKind == JsonValueKind.String &&
                        item.TryGetProperty("loc", out var loc) && loc.ValueKind == JsonValueKind.Array)
                    {
                        var path = string.Join(".", loc.EnumerateArray().Skip(1).Select(part => part.ToString()));
                        lines.Add(path.Length > 0 ? $"{path}：{msg.GetString()}" : msg.GetString() ?? "");
                    }
                    else lines.Add(item.ToString());
                }
                return lines.Count > 0 ? string.Join("\n", lines) : fallback;
            }
            case JsonValueKind.Object:
            {
                var message = value.TryGetProperty("message", out var header) && header.ValueKind == JsonValueKind.String
                    ? header.GetString() : null;
                var blockers = new List<string>();
                if (value.TryGetProperty("blockers", out var list) && list.ValueKind == JsonValueKind.Array)
                    foreach (var blocker in list.EnumerateArray())
                        if (blocker.ValueKind == JsonValueKind.Object &&
                            blocker.TryGetProperty("message", out var text) && text.ValueKind == JsonValueKind.String &&
                            text.GetString() is { Length: > 0 } blockerMessage)
                            blockers.Add(blockerMessage);
                if (message is { Length: > 0 } && blockers.Count > 0)
                    return $"{message}：{string.Join("；", blockers)}";
                if (blockers.Count > 0) return string.Join("；", blockers);
                return message is { Length: > 0 } ? message : value.ToString();
            }
            default:
                return value.ToString();
        }
    }
}

/// <summary>Shared download+decode cache for thumbnails and originals.</summary>
public static class ImageStore
{
    /// <summary>native-tests 注入点（KeyValueStore.UseLocation 同款模式）：共享静态
    /// HttpClient 无法构造注入，检查用它回放 4xx/5xx 与成功图片响应；生产恒为 null。</summary>
    internal static Func<HttpRequestMessage, CancellationToken, Task<HttpResponseMessage>>? TestResponder { get; set; }

    private static readonly HttpClient http = new(new RoutedHandler()) { Timeout = TimeSpan.FromSeconds(30) };

    private sealed class RoutedHandler : DelegatingHandler
    {
        public RoutedHandler() => InnerHandler = new HttpClientHandler { UseProxy = false };
        protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken cancellation) =>
            TestResponder?.Invoke(request, cancellation) ?? base.SendAsync(request, cancellation);
    }

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
        using (var response = await http.SendAsync(request, cancellation).ConfigureAwait(false))
        {
            if (!response.IsSuccessStatusCode)
            {
                // #449-2：原 EnsureSuccessStatusCode 只会抛生成的英文行，后端 404/409
                // 的 detail（如素材归档冲突）到不了任何图片界面；改走 MediaErrors 映射。
                var text = await response.Content.ReadAsStringAsync(cancellation).ConfigureAwait(false);
                throw new InvalidOperationException(MediaErrors.DescribeResponse(response, text));
            }
            data = await response.Content.ReadAsByteArrayAsync(cancellation).ConfigureAwait(false);
        }
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
    private int transition;
    private IInputElement? returnFocus;

    public static readonly DependencyProperty OpenProperty = DependencyProperty.Register(
        nameof(Open), typeof(bool), typeof(DrawerOverlay),
        new PropertyMetadata(false, (d, e) => ((DrawerOverlay)d).Apply((bool)e.NewValue)));
    public bool Open { get => (bool)GetValue(OpenProperty); set => SetValue(OpenProperty, value); }

    public double DrawerWidth { get; set; } = 510;

    public event EventHandler? ClosedByUser;

    public DrawerOverlay()
    {
        Focusable = false;
        HorizontalContentAlignment = HorizontalAlignment.Stretch;
        VerticalContentAlignment = VerticalAlignment.Stretch;
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
        KeyboardNavigation.SetTabNavigation(surface, KeyboardNavigationMode.Cycle);
        KeyboardNavigation.SetControlTabNavigation(surface, KeyboardNavigationMode.Cycle);
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
        var version = ++transition;
        ClampWidth();
        var transform = surface.RenderTransform as TranslateTransform ?? new TranslateTransform();
        var from = root.Visibility == Visibility.Visible ? transform.X : surface.Width;
        surface.RenderTransform = transform;
        if (open)
        {
            returnFocus = Keyboard.FocusedElement;
            root.Visibility = Visibility.Visible;
            surface.IsEnabled = true;
            Dispatcher.BeginInvoke(System.Windows.Threading.DispatcherPriority.Input, new Action(() =>
            {
                if (Open && version == transition)
                    surface.MoveFocus(new TraversalRequest(FocusNavigationDirection.First));
            }));
        }
        else surface.IsEnabled = false;
        void Finish()
        {
            if (version != transition) return;
            transform.BeginAnimation(TranslateTransform.XProperty, null);
            transform.X = 0;
            backdrop.BeginAnimation(OpacityProperty, null);
            backdrop.Opacity = open ? 0.45 : 0;
            if (!open)
            {
                root.Visibility = Visibility.Collapsed;
                if (returnFocus is UIElement { IsVisible: true, IsEnabled: true } target) target.Focus();
            }
        }
        if (!Motion.Enabled || PresentationSource.FromVisual(this) == null)
        {
            Finish();
            return;
        }
        var slide = new DoubleAnimation(from, open ? 0 : surface.Width, open ? Motion.Slow : Motion.Base)
        { EasingFunction = new EaseOutQuint() };
        slide.Completed += (_, _) => Finish();
        var fade = new DoubleAnimation(backdrop.Opacity, open ? 0.45 : 0, Motion.Base);
        transform.BeginAnimation(TranslateTransform.XProperty, slide);
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
    // #486-3：原图加载失败不再吞掉——失败文案 + 重试取代永久空白黑窗（与 ImageBox
    // 的 图片加载失败 对齐，并透出 #449-2 的服务端 detail）。
    private readonly TextBlock failureText = new()
    {
        Foreground = Brushes.WhiteSmoke, FontSize = 14, TextWrapping = TextWrapping.Wrap,
        TextAlignment = TextAlignment.Center, MaxWidth = 560,
    };
    private readonly StackPanel failurePanel;
    private string pendingUrl = "";
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
        var retry = LightboxButton("重试加载", paper);
        retry.Margin = new Thickness(0, 14, 0, 0);
        retry.Click += (_, _) => { if (pendingUrl.Length > 0) _ = Load(pendingUrl); };
        failurePanel = new StackPanel
        {
            Visibility = Visibility.Collapsed,
            HorizontalAlignment = HorizontalAlignment.Center, VerticalAlignment = VerticalAlignment.Center,
        };
        failurePanel.Children.Add(failureText);
        failurePanel.Children.Add(retry);
        var viewer = new ScrollViewer
        {
            VerticalScrollBarVisibility = ScrollBarVisibility.Auto,
            HorizontalScrollBarVisibility = ScrollBarVisibility.Auto,
            Content = new Grid
            {
                Children =
                {
                    new Border
                    {
                        Child = image, HorizontalAlignment = HorizontalAlignment.Center,
                        VerticalAlignment = VerticalAlignment.Center,
                    },
                    failurePanel,
                },
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
        pendingUrl = url;
        await Dispatcher.BeginInvoke(() => ShowFailure(null));
        try
        {
            var bitmap = await Task.Run(() => ImageStore.LoadAsync(url, CancellationToken.None));
            // null = 空数据/无法解码：同样按失败处理，不再停在空白黑窗。
            if (bitmap == null) throw new InvalidOperationException("图片为空或无法解码。");
            await Dispatcher.BeginInvoke(() =>
            {
                image.Source = bitmap;
                SetZoom(1);
            });
        }
        // CancellationToken.None 永不被调用方取消；到这里即 HttpClient 30s 超时。
        catch (OperationCanceledException)
        {
            await Dispatcher.BeginInvoke(() => ShowFailure("图片加载超时，请重试。"));
        }
        catch (Exception ex)
        {
            var message = MediaErrors.Localize(ex);
            await Dispatcher.BeginInvoke(() => ShowFailure(message));
        }
    }

    /// <summary>null = 收起失败面板；非空 = 展示映射后的失败文案并清掉旧图。</summary>
    private void ShowFailure(string? message)
    {
        if (message == null)
        {
            failurePanel.Visibility = Visibility.Collapsed;
            return;
        }
        image.Source = null;
        failureText.Text = message;
        failurePanel.Visibility = Visibility.Visible;
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
