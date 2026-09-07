using System.ComponentModel;
using System.IO;
using System.Text;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;
using Microsoft.Win32;

namespace MangaFlow.Native;

public sealed class EditorDialog : Window
{
    private readonly TextBox titleInput = new() { Margin = new Thickness(0, 8, 0, 18) };
    private readonly TextBox bodyInput = new()
    {
        AcceptsReturn = true, AcceptsTab = true, TextWrapping = TextWrapping.Wrap,
        VerticalScrollBarVisibility = ScrollBarVisibility.Auto, MinHeight = 180,
        VerticalContentAlignment = VerticalAlignment.Top,
    };
    private readonly TextBlock error = new() { Foreground = System.Windows.Media.Brushes.Firebrick, Margin = new Thickness(0, 12, 0, 12) };
    private readonly Button submit = new() { Content = "创建项目", MinWidth = 110 };
    private bool busy, saved;
    private string sourceType = "PASTE";

    public EditorDialog(Window owner, string title, string subtitle, bool source,
        Func<string, string, string, Task> onSubmit)
    {
        Owner = owner; Title = title; Width = source ? 740 : 480; Height = source ? 660 : 330;
        MinWidth = 420; MinHeight = source ? 500 : 330;
        WindowStartupLocation = WindowStartupLocation.CenterOwner;
        ShowInTaskbar = false;
        var grid = new Grid { Margin = new Thickness(26) };
        grid.RowDefinitions.Add(new() { Height = GridLength.Auto });
        grid.RowDefinitions.Add(new() { Height = GridLength.Auto });
        grid.RowDefinitions.Add(new() { Height = new GridLength(1, GridUnitType.Star) });
        grid.RowDefinitions.Add(new() { Height = GridLength.Auto });
        var header = new StackPanel();
        header.Children.Add(new TextBlock { Text = title, FontFamily = (System.Windows.Media.FontFamily)FindResource("Serif"), FontSize = 24, FontWeight = FontWeights.SemiBold });
        header.Children.Add(new TextBlock { Text = subtitle, Margin = new Thickness(0, 8, 0, 20), Foreground = System.Windows.Media.Brushes.DimGray });
        grid.Children.Add(header);
        var name = new StackPanel();
        name.Children.Add(new TextBlock { Text = source ? "原作标题" : "项目名称" });
        titleInput.MaxLength = source ? 200 : 120;
        System.Windows.Automation.AutomationProperties.SetName(titleInput, source ? "原作标题" : "项目名称");
        name.Children.Add(titleInput); Grid.SetRow(name, 1); grid.Children.Add(name);
        if (source)
        {
            var body = new DockPanel();
            var load = new Button { Content = "选择 TXT / Markdown 文件…", HorizontalAlignment = HorizontalAlignment.Left, Margin = new Thickness(0, 0, 0, 12) };
            load.Click += async (_, _) =>
            {
                var picker = new OpenFileDialog { Filter = "文本原作|*.txt;*.md;*.markdown", Title = "选择原作文件" };
                if (picker.ShowDialog(this) != true) return;
                try
                {
                    if (new FileInfo(picker.FileName).Length > 8_000_000) throw new IOException("文件过大，请选择不超过 8 MB 的文本文件。");
                    // Strict decoding avoids silently replacing the original story with mojibake.
                    var content = await File.ReadAllTextAsync(picker.FileName, new UTF8Encoding(false, true));
                    if (content.Length > 2_000_000) throw new IOException("正文超过 200 万字符，请分批导入。");
                    bodyInput.Text = content;
                    titleInput.Text = Path.GetFileNameWithoutExtension(picker.FileName);
                    sourceType = Path.GetExtension(picker.FileName).Equals(".txt", StringComparison.OrdinalIgnoreCase) ? "TXT" : "MARKDOWN";
                    error.Text = "";
                }
                catch (DecoderFallbackException) { error.Text = "文件不是有效的 UTF-8 编码。请先另存为 UTF-8 后导入。"; }
                catch (Exception reason) { error.Text = reason.Message; }
            };
            DockPanel.SetDock(load, Dock.Top); body.Children.Add(load);
            System.Windows.Automation.AutomationProperties.SetName(bodyInput, "原作正文");
            body.Children.Add(bodyInput); Grid.SetRow(body, 2); grid.Children.Add(body);
            submit.Content = "导入原作";
        }
        var footer = new StackPanel();
        footer.Children.Add(error);
        var actions = new StackPanel { Orientation = Orientation.Horizontal, HorizontalAlignment = HorizontalAlignment.Right };
        var cancel = new Button { Content = "取消", Margin = new Thickness(0, 0, 10, 0) };
        cancel.Click += (_, _) => Close();
        actions.Children.Add(cancel);
        submit.Style = (Style)FindResource("Primary");
        submit.Click += async (_, _) =>
        {
            if (busy) return;
            var nameText = titleInput.Text.Trim();
            if (nameText.Length == 0) { error.Text = "请填写名称。"; titleInput.Focus(); return; }
            if (source && (string.IsNullOrWhiteSpace(bodyInput.Text) || bodyInput.Text.Length > 2_000_000))
            { error.Text = "请输入 1–200 万字符的正文。"; return; }
            busy = true; submit.IsEnabled = false; titleInput.IsReadOnly = true; bodyInput.IsReadOnly = true;
            error.Text = "正在保存…";
            try
            {
                await onSubmit(nameText, bodyInput.Text, sourceType);
                saved = true; busy = false; DialogResult = true;
            }
            catch (Exception reason)
            {
                error.Text = reason is OperationCanceledException or TimeoutException
                    ? "请求超时，服务可能已保存。请先关闭此窗口并刷新确认，避免重复提交。" : reason.Message;
            }
            finally { busy = false; submit.IsEnabled = true; titleInput.IsReadOnly = false; bodyInput.IsReadOnly = false; }
        };
        actions.Children.Add(submit); footer.Children.Add(actions); Grid.SetRow(footer, 3); grid.Children.Add(footer);
        Content = grid;
        Loaded += (_, _) => titleInput.Focus();
        PreviewKeyDown += (_, e) => { if (e.Key == Key.Escape) { e.Handled = true; Close(); } };
        Closing += (_, e) =>
        {
            if (busy) { e.Cancel = true; error.Text = "正在保存，请稍候。"; return; }
            if (!saved && (!string.IsNullOrEmpty(titleInput.Text) || !string.IsNullOrEmpty(bodyInput.Text)) &&
                MessageBox.Show(this, "放弃尚未提交的内容？", "关闭", MessageBoxButton.YesNo, MessageBoxImage.Question) != MessageBoxResult.Yes) e.Cancel = true;
        };
    }
}

public sealed class ProjectPalette : Window
{
    public ProjectItem? SelectedProject { get; private set; }
    public ProjectPalette(Window owner, IReadOnlyList<ProjectItem> projects)
    {
        Owner = owner; Title = "快速切换项目"; Width = 560; Height = 430;
        WindowStartupLocation = WindowStartupLocation.CenterOwner; ShowInTaskbar = false;
        ResizeMode = ResizeMode.NoResize;
        var panel = new DockPanel { Margin = new Thickness(20) };
        var search = new TextBox { Margin = new Thickness(0, 0, 0, 14) };
        System.Windows.Automation.AutomationProperties.SetName(search, "搜索项目");
        var list = new ListBox { ItemsSource = projects, DisplayMemberPath = "Name" };
        var hint = new TextBlock { Text = "输入项目名称 · ↑↓ 选择 · Enter 打开 · Esc 关闭", FontSize = 12, Margin = new Thickness(0, 12, 0, 0) };
        DockPanel.SetDock(search, Dock.Top); DockPanel.SetDock(hint, Dock.Bottom);
        panel.Children.Add(search); panel.Children.Add(hint); panel.Children.Add(list); Content = panel;
        search.TextChanged += (_, _) =>
        {
            var matches = projects.Where(p => p.Name.Contains(search.Text.Trim(), StringComparison.OrdinalIgnoreCase)).ToList();
            list.ItemsSource = matches;
            list.SelectedIndex = matches.Count > 0 ? 0 : -1;
            hint.Text = matches.Count == 0 ? "没有匹配的项目，试试其他关键词。" : "↑↓ 选择 · Enter 打开 · Esc 关闭";
        };
        void Choose()
        {
            if (list.SelectedItem is not ProjectItem project) return;
            SelectedProject = project; DialogResult = true;
        }
        list.MouseDoubleClick += (_, _) => Choose();
        PreviewKeyDown += (_, e) =>
        {
            switch (e.Key)
            {
                case Key.Escape: Close(); e.Handled = true; break;
                case Key.Enter: Choose(); e.Handled = true; break;
                case Key.Down: list.SelectedIndex = Math.Min(list.Items.Count - 1, list.SelectedIndex + 1); list.ScrollIntoView(list.SelectedItem); e.Handled = true; break;
                case Key.Up: list.SelectedIndex = Math.Max(0, list.SelectedIndex - 1); list.ScrollIntoView(list.SelectedItem); e.Handled = true; break;
            }
        };
        Loaded += (_, _) => { search.Focus(); list.SelectedIndex = projects.Count > 0 ? 0 : -1; };
    }
}
