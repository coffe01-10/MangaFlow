using System.Windows;
using System.Windows.Controls;

namespace MangaFlow.Native;

// A shared page header/body boundary. Business views keep their own data context
// and async state; replacing a migration preview never requires rebuilding chrome.
public class WorkspacePageFrame : ContentControl
{
    public static readonly DependencyProperty PageProperty = DependencyProperty.Register(
        nameof(Page), typeof(ProjectPageDefinition), typeof(WorkspacePageFrame));
    public ProjectPageDefinition Page { get => (ProjectPageDefinition)GetValue(PageProperty); set => SetValue(PageProperty, value); }
}
