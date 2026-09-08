using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;
using MangaFlow.Native.Controls;
using MangaFlow.Native.Services;

namespace MangaFlow.Native.Views;

/// <summary>
/// Lifecycle contract for every workspace page. MainWindow activates exactly one
/// view at a time; views own their queries, polling predicates and dirty guards.
/// </summary>
public interface IWorkspaceView
{
    void Activate(WorkspaceContext context);
    void Deactivate();
    Task RefreshAsync();
    void PollTick();
    /// <summary>Ask the user before leaving with unsaved work; false cancels navigation.</summary>
    Task<bool> ConfirmLeaveAsync() => Task.FromResult(true);
}

public abstract class WorkspaceView : UserControl, IWorkspaceView
{
    protected WorkspaceView()
    {
        HorizontalContentAlignment = HorizontalAlignment.Stretch;
        VerticalContentAlignment = VerticalAlignment.Stretch;
        UseLayoutRounding = true;
        Background = (Brush)Application.Current.FindResource("Paper");
    }
    protected WorkspaceContext? Context { get; private set; }
    protected ApiClient Api => Context?.Api ?? throw new InvalidOperationException("视图尚未激活");
    protected ApiCache Cache => Context?.Cache ?? throw new InvalidOperationException("视图尚未激活");
    protected WorkspaceState State => Context?.State ?? throw new InvalidOperationException("视图尚未激活");
    protected ProjectItem? Project => Context?.Project;
    protected string ProjectId => Context?.ProjectId ?? "";
    protected Window Host => Context?.Window ?? Window.GetWindow(this);
    private CancellationTokenSource viewLifetime = new();
    protected CancellationTokenSource lifetime => viewLifetime;
    private readonly List<Action> bindings = [];

    public virtual void Activate(WorkspaceContext context)
    {
        Context = context;
        // Views live in a cache; re-activation after a cancelled pass needs a fresh token.
        if (viewLifetime.IsCancellationRequested)
        {
            viewLifetime.Dispose();
            viewLifetime = new CancellationTokenSource();
        }
    }

    public virtual void Deactivate()
    {
        // Navigation cancels in-flight reads so late responses cannot paint the next page.
        viewLifetime.Cancel();
        foreach (var unbind in bindings) unbind();
        bindings.Clear();
    }
    public virtual Task RefreshAsync() => Task.CompletedTask;
    public virtual void PollTick() { }
    public virtual Task<bool> ConfirmLeaveAsync() => Task.FromResult(true);

    protected void BindDisposed(Action unbind) => bindings.Add(unbind);

    // Common building blocks shared by every page (see Kit for non-view classes).
    protected static TextBlock Caption(string text) => Kit.Caption(text);
    protected static TextBlock Kicker(string text) => new() { Text = text, Style = (Style)Application.Current.FindResource("SectionIndex") };
    protected static TextBlock FieldLabel(string text) => Kit.FieldLabel(text);
    protected static Border Card(UIElement content) => new()
    {
        Style = (Style)Application.Current.FindResource("Card"),
        Child = content is Panel or Border ? content : new ScrollViewer { Content = content },
    };
    protected static Border Notice(string text, string tone) => Kit.Notice(text, tone);
    protected static StackPanel Row(params UIElement[] children)
    {
        var row = new StackPanel { Orientation = Orientation.Horizontal };
        foreach (var child in children) row.Children.Add(child);
        return row;
    }
    protected static StackPanel Column(double gap = 0, params UIElement[] children)
    {
        var panel = new StackPanel();
        if (gap > 0)
            foreach (var child in children)
            {
                panel.Children.Add(child);
                if (child is FrameworkElement element) element.Margin = new Thickness(0, 0, 0, gap);
            }
        else foreach (var child in children) panel.Children.Add(child);
        return panel;
    }
    protected static StackPanel Form(params UIElement[] children)
    {
        var panel = new StackPanel();
        foreach (var child in children)
        {
            panel.Children.Add(child);
            if (child is FrameworkElement element and not Button)
                element.Margin = new Thickness(element.Margin.Left, element.Margin.Top, element.Margin.Right, 14);
        }
        return panel;
    }
    protected static ComboBox Selector(string name, double width = 220) => Kit.Selector(name, width);
    protected static TextBox Input(string name, double width = double.NaN)
    {
        var box = new TextBox();
        if (!double.IsNaN(width)) box.Width = width;
        System.Windows.Automation.AutomationProperties.SetName(box, name);
        return box;
    }
    protected static Button Act(string text, RoutedEventHandler onClick, string style = "Ghost") => Kit.Act(text, onClick, style);
    protected static void Show(Window owner, string url, string label) => new Lightbox(owner, url, label).ShowDialog();

    protected void OpenImage(string? url, string label)
    {
        if (string.IsNullOrEmpty(url) || Context == null) return;
        new Lightbox(Host, Context.Api.OriginUrl(url), label).ShowDialog();
    }

    protected static string Loading => "正在读取…";
}

public static class Kit
{
    public static TextBlock Caption(string text) => new() { Text = text, Style = (Style)Application.Current.FindResource("Caption") };
    public static TextBlock FieldLabel(string text) => new() { Text = text, Style = (Style)Application.Current.FindResource("FieldLabel") };
    public static Button Act(string text, RoutedEventHandler onClick, string style = "Ghost")
    {
        var button = new Button { Content = text };
        button.Style = (Style)Application.Current.FindResource(style);
        button.Click += onClick;
        return button;
    }
    public static Border Notice(string text, string tone)
    {
        var brush = tone switch
        {
            "ok" => (Brush)Application.Current.FindResource("Success"),
            "warn" => (Brush)Application.Current.FindResource("Warning"),
            _ => (Brush)Application.Current.FindResource("Danger"),
        };
        return new Border
        {
            BorderBrush = brush,
            BorderThickness = new Thickness(0, 0, 0, 2),
            Padding = new Thickness(14, 9, 14, 9),
            Margin = new Thickness(0, 0, 0, 12),
            Child = new TextBlock { Text = text, FontSize = 12.5 },
        };
    }
    public static ComboBox Selector(string name, double width = 220)
    {
        var box = new ComboBox { Width = width, MaxDropDownHeight = 320 };
        System.Windows.Automation.AutomationProperties.SetName(box, name);
        return box;
    }
}

public static class ToneBrush
{
    public static Brush Of(string tone) => tone switch
    {
        "ok" or "ready" or "success" => (Brush)Application.Current.FindResource("Success"),
        "warn" or "pending" => (Brush)Application.Current.FindResource("Warning"),
        "danger" or "failed" => (Brush)Application.Current.FindResource("Danger"),
        "accent" => (Brush)Application.Current.FindResource("Accent"),
        _ => (Brush)Application.Current.FindResource("Muted"),
    };
}
