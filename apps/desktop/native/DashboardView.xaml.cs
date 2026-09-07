using System.Windows;
using System.Windows.Controls;

namespace MangaFlow.Native;

public partial class DashboardView : UserControl
{
    public event EventHandler? CreateRequested;
    public event EventHandler? SettingsRequested;
    public event Action<ProjectItem>? ProjectRequested;
    public DashboardView() => InitializeComponent();
    private void CreateProject(object sender, RoutedEventArgs e) => CreateRequested?.Invoke(this, EventArgs.Empty);
    private void OpenSettings(object sender, RoutedEventArgs e) => SettingsRequested?.Invoke(this, EventArgs.Empty);
    private void OpenProject(object sender, RoutedEventArgs e)
    {
        if (((Button)sender).Tag is ProjectItem project) ProjectRequested?.Invoke(project);
    }
    private void PreviousPage(object sender, RoutedEventArgs e) => ((WorkspaceState)DataContext).ChangeDashboardPage(-1);
    private void NextPage(object sender, RoutedEventArgs e) => ((WorkspaceState)DataContext).ChangeDashboardPage(1);
    private void OnSizeChanged(object sender, SizeChangedEventArgs e)
    {
        if (DashboardRail == null) return;
        var narrow = ActualWidth < 1180;
        RailColumn.Width = new GridLength(narrow ? 0 : 308);
        Grid.SetRow(DashboardRail, narrow ? 1 : 0);
        Grid.SetColumn(DashboardRail, narrow ? 0 : 1);
        DashboardRail.BorderThickness = narrow ? new Thickness(0, 1, 0, 0) : new Thickness(1, 0, 0, 0);
        NewProjectCard.Width = Math.Max(180, (ActualWidth - (narrow ? 0 : 308) - 90) / 3 - 16);
    }
}
