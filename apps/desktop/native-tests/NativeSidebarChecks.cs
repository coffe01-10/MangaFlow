using System.IO;
using System.Windows;
using System.Windows.Automation;
using System.Windows.Automation.Peers;
using System.Windows.Automation.Provider;
using System.Windows.Controls;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using MangaFlow.Native;
using MangaFlow.Native.Services;

internal static class NativeSidebarChecks
{
    public static void Run(string output)
    {
        Exception? failure = null;
        var thread = new Thread(() =>
        {
            var app = new Application();
            app.Resources.MergedDictionaries.Add(new ResourceDictionary
            { Source = new Uri("/MangaFlow.Native;component/Theme.xaml", UriKind.Relative) });
            try { Verify(output); }
            catch (Exception error) { failure = error; }
            finally { app.Shutdown(); }
        });
        thread.SetApartmentState(ApartmentState.STA);
        thread.Start();
        thread.Join();
        if (failure != null) throw new Exception("Sidebar regression", failure);
    }

    public static void Verify(string output)
    {
        Directory.CreateDirectory(output);
        var root = Path.Combine(Path.GetTempPath(), "mangaflow-sidebar-" + Guid.NewGuid().ToString("N"));
        var previousLocation = (string)typeof(KeyValueStore).GetField("path",
            System.Reflection.BindingFlags.Static | System.Reflection.BindingFlags.NonPublic)!.GetValue(null)!;
        MainWindow? window = null;
        try
        {
            new Preferences { SidebarCollapsed = true }.Save(root);
            // Never Show(): these checks render real WPF templates without starting a backend.
            window = new MainWindow("", root);
            var state = (WorkspaceState)window.DataContext;
            state.CurrentProject = new ProjectItem("fixture", string.Concat(Enumerable.Repeat("一个不能挤掉导航和顶部按钮的长项目名称", 6)), "3 章 12 页", 0, 0);
            var sections = (ListBox)window.FindName("ProjectSections");
            var toggle = (Button)window.FindName("SidebarToggle");
            var panel = (Border)window.FindName("SidebarPanel");
            var content = (FrameworkElement)window.Content;
            sections.SelectedItem = ProjectPages.Get(ProjectPageId.Jobs);
            Drain();
            Require(state.SidebarCollapsed, "saved collapsed state is restored on entering a project");

            foreach (var size in new[] { new Size(1320, 860), new Size(960, 640), new Size(960, 600) })
            foreach (var collapsed in new[] { true, false, true })
            {
                if (state.SidebarCollapsed != collapsed)
                    toggle.RaiseEvent(new RoutedEventArgs(Button.ClickEvent));
                Layout(content, size);
                var title = (TextBlock)window.FindName("TopTitle");
                var actions = (FrameworkElement)window.FindName("WorkspaceActions");
                var titleBounds = title.TransformToAncestor(content).TransformBounds(new Rect(title.RenderSize));
                var actionBounds = actions.TransformToAncestor(content).TransformBounds(new Rect(actions.RenderSize));
                Require(titleBounds.Right <= actionBounds.Left && actionBounds.Right <= size.Width && title.ActualWidth > 50,
                    $"long project title leaves top actions visible ({titleBounds}, {actionBounds})");
                foreach (var page in ProjectPages.All)
                {
                    sections.ScrollIntoView(page);
                    Layout(content, size);
                    var item = (ListBoxItem?)sections.ItemContainerGenerator.ContainerFromItem(page)
                        ?? throw new Exception($"{page.Title}: scroll did not realize navigation item");
                    var icon = NativeParityChecks.Descendants(item).OfType<Viewbox>().Single();
                    var bounds = icon.TransformToAncestor(panel).TransformBounds(new Rect(icon.RenderSize));
                    Require(bounds.Width >= 14 && bounds.Height >= 14 && bounds.Left >= 0 && bounds.Right <= panel.ActualWidth
                        && bounds.Top >= 0 && bounds.Bottom <= panel.ActualHeight,
                        $"{size}/{collapsed}/{page.Title}: icon inside sidebar ({bounds}, panel {panel.RenderSize})");
                    // A Viewbox can retain its requested size outside its clipped presenter.
                    // Inspect every ancestor clip, not just the outer sidebar column.
                    for (DependencyObject? ancestor = icon; ancestor != panel; ancestor = VisualTreeHelper.GetParent(ancestor))
                    {
                        if (ancestor is not UIElement element || VisualTreeHelper.GetClip(element) is not { } clip) continue;
                        var local = icon.TransformToAncestor(element).TransformBounds(new Rect(icon.RenderSize));
                        Require(clip.Bounds.Contains(local), $"{size}/{collapsed}/{page.Title}: icon clipped by {element.GetType().Name} ({local}, clip {clip.Bounds})");
                    }
                    Require(Equals(item.ToolTip, page.Title) && AutomationProperties.GetName(item).Contains(page.Title),
                        "icon-only navigation keeps tooltip and accessible name");
                    var texts = NativeParityChecks.Descendants(item).OfType<TextBlock>().ToList();
                    Require(texts.Where(t => t.Text == page.Index || t.Text == page.Title)
                        .All(t => collapsed ? t.Visibility == Visibility.Collapsed : t.Visibility == Visibility.Visible),
                        "collapse hides both title and index; expand restores them");
                    var peer = new ListBoxItemAutomationPeer(page, new ListBoxAutomationPeer(sections));
                    ((ISelectionItemProvider)peer.GetPattern(PatternInterface.SelectionItem)).Select();
                    Drain();
                    Require(state.Navigation.Current == page && item.IsSelected, "rail selection opens the matching page");
                    var path = NativeParityChecks.Descendants(icon).OfType<System.Windows.Shapes.Path>().Single();
                    Require(path.Stroke is SolidColorBrush brush && brush.Color == Colors.White, "selected icon stays legible on ink background");
                }
                Require((string)toggle.ToolTip == (collapsed ? "展开侧栏 · Ctrl+B" : "折叠侧栏 · Ctrl+B"), "toggle tooltip describes its next action");
                Require(AutomationProperties.GetName(toggle) == (collapsed ? "展开侧栏" : "折叠侧栏"), "toggle accessibility label follows state");
                sections.ScrollIntoView(ProjectPages.All[0]);
                Layout(content, size);
                var bitmap = new RenderTargetBitmap((int)size.Width, (int)size.Height, 96, 96, PixelFormats.Pbgra32);
                var background = new DrawingVisual();
                using (var drawing = background.RenderOpen())
                    drawing.DrawRectangle((Brush)window.FindResource("Paper"), null, new Rect(size));
                bitmap.Render(background);
                bitmap.Render(content);
                var encoder = new PngBitmapEncoder();
                encoder.Frames.Add(BitmapFrame.Create(bitmap));
                using var file = File.Create(Path.Combine(output, $"sidebar-{size.Width}x{size.Height}-{(collapsed ? "collapsed" : "expanded")}.png"));
                encoder.Save(file);
            }
            foreach (var size in new[] { new Size(1320, 860), new Size(960, 640) })
            foreach (var page in new[] { ProjectPageId.Source, ProjectPageId.Assets })
            {
                // Layout-only fixtures bypass the settings view's unsaved-form leave dialog.
                var host = (ContentControl)window.FindName("ContentHost");
                host.Content = page == ProjectPageId.Source ? new MangaFlow.Native.Views.SourceView() : new MangaFlow.Native.Views.AssetsView();
                typeof(MainWindow).GetField("page", System.Reflection.BindingFlags.Instance | System.Reflection.BindingFlags.NonPublic)!
                    .SetValue(window, ProjectPages.Get(page).WebSection);
                typeof(MainWindow).GetMethod("ApplySidebar", System.Reflection.BindingFlags.Instance | System.Reflection.BindingFlags.NonPublic)!
                    .Invoke(window, null);
                Layout(content, size);
                var bounds = host.TransformToAncestor(content).TransformBounds(new Rect(host.RenderSize));
                var topbar = (RowDefinition)window.FindName("TopbarRow");
                // Render only the real shell: a synthetic paper backdrop would hide the white gutter bug.
                var bitmap = new RenderTargetBitmap((int)size.Width, (int)size.Height, 96, 96, PixelFormats.Pbgra32);
                bitmap.Render(content);
                var pixel = new byte[4];
                bitmap.CopyPixels(new Int32Rect((int)panel.ActualWidth + 8, (int)topbar.ActualHeight + 8, 1, 1), pixel, 4, 0);
                var paper = ((SolidColorBrush)window.FindResource("Paper")).Color;
                Require(Color.FromArgb(pixel[3], pixel[2], pixel[1], pixel[0]) == paper,
                    $"{page}: workspace gutter must paint the paper background without a test backdrop");
                Require(bounds.Left - panel.ActualWidth == 16 && bounds.Top - topbar.ActualHeight == 16
                    && size.Width - bounds.Right == 16, $"{page}: compact 16px page gutters at {size}");
                var encoder = new PngBitmapEncoder();
                encoder.Frames.Add(BitmapFrame.Create(bitmap));
                using var file = File.Create(Path.Combine(output, $"gutter-{page}-{size.Width}.png"));
                encoder.Save(file);
            }
            Console.WriteLine("PASS: sidebar icons unclipped at 1320/960 widths and 860/640/600 heights; nine navigation targets, labels, contrast, scroll, repeated toggle, saved state, long project titles and compact paper gutters");
        }
        finally
        {
            // An unshown layout fixture owns no backend or drafts to save on shutdown.
            if (window != null)
            {
                typeof(MainWindow).GetField("closed", System.Reflection.BindingFlags.Instance | System.Reflection.BindingFlags.NonPublic)!.SetValue(window, true);
                window.Close();
            }
            KeyValueStore.UseLocation(previousLocation);
            if (Directory.Exists(root))
            {
                File.Delete(Path.Combine(root, "window.json"));
                Directory.Delete(root);
            }
        }
    }

    private static void Layout(FrameworkElement root, Size size)
    {
        root.Measure(size);
        root.Arrange(new Rect(size));
        root.UpdateLayout();
        Drain();
    }
    private static void Drain() => System.Windows.Threading.Dispatcher.CurrentDispatcher.Invoke(() => { }, System.Windows.Threading.DispatcherPriority.ApplicationIdle);
    private static void Require(bool condition, string label) { if (!condition) throw new Exception(label); }
}
