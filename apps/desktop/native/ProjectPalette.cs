using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;

namespace MangaFlow.Native;

/// <summary>Ctrl+K project quick switcher: type-to-filter, arrows, Enter opens.</summary>
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
