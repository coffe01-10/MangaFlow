using System.Collections.ObjectModel;
using System.Text.Json;

namespace MangaFlow.Native;

public sealed class WorkspaceState : Observable
{
    public ProjectNavigation Navigation { get; } = new();
    public ObservableCollection<ProjectItem> Projects { get; } = [];
    public ObservableCollection<ProjectItem> DashboardProjects { get; } = [];
    private int dashboardPage;
    public int DashboardPageSize => 12;
    public string DashboardPageLabel => $"{dashboardPage + 1} / {Math.Max(1, (Projects.Count + DashboardPageSize - 1) / DashboardPageSize)}";
    public bool HasPreviousPage => dashboardPage > 0;
    public bool HasNextPage => (dashboardPage + 1) * DashboardPageSize < Projects.Count;
    private int totalPages, selectedPages, reviewPages, enabledModels, configuredConnections, healthyConnections;
    public string ActiveProjectMetric => Projects.Count.ToString("D2");
    public string PageMetric => totalPages.ToString("D2");
    public string SelectedPageLabel => $"{selectedPages} 页已采用";
    public string ReviewMetric => reviewPages.ToString("D2");
    public string ModelLabel => $"{enabledModels} 个可用模型";
    public string ConfiguredConnections => configuredConnections.ToString();
    public string HealthyConnections => healthyConnections.ToString();
    public string EnabledModels => enabledModels.ToString();
    public string AiConnectionTitle => healthyConnections > 0 ? "AI 连接已就绪" : configuredConnections > 0 ? "连接等待验证" : "等待添加供应商";
    public void UpdateDashboard(JsonElement dashboard)
    {
        var totals = dashboard.GetProperty("totals");
        var ai = dashboard.GetProperty("ai_overview");
        totalPages = totals.Number("page_count"); selectedPages = totals.Number("selected_page_count");
        reviewPages = totals.Number("review_page_count"); enabledModels = ai.Number("enabled_model_count");
        configuredConnections = ai.Number("configured_connection_count"); healthyConnections = ai.Number("healthy_connection_count");
        foreach (var name in new[] { nameof(ActiveProjectMetric), nameof(PageMetric), nameof(SelectedPageLabel), nameof(ReviewMetric), nameof(ModelLabel), nameof(ConfiguredConnections), nameof(HealthyConnections), nameof(EnabledModels), nameof(AiConnectionTitle) }) Changed(name);
        RefreshDashboardCards();
    }
    public void ChangeDashboardPage(int delta)
    {
        dashboardPage += delta;
        RefreshDashboardCards();
    }
    private void RefreshDashboardCards()
    {
        dashboardPage = Math.Clamp(dashboardPage, 0, Math.Max(0, (Projects.Count - 1) / DashboardPageSize));
        var visible = Projects.Skip(dashboardPage * DashboardPageSize).Take(DashboardPageSize).ToList();
        if (!DashboardProjects.SequenceEqual(visible))
        {
            DashboardProjects.Clear();
            foreach (var item in visible) DashboardProjects.Add(item);
        }
        Changed(nameof(DashboardPageLabel)); Changed(nameof(HasPreviousPage)); Changed(nameof(HasNextPage));
    }
    public ObservableCollection<ChapterItem> Chapters { get; } = [];
    public ObservableCollection<JobItem> VisibleJobs { get; } = [];
    private bool connected, busy;
    private string connectionLabel = "正在启动本地服务…", error = "", status = "正在准备创作空间…";
    private string breadcrumb = "项目概览", overview = "正在读取项目", emptyMessage = "";
    private string readerTitle = "选择一个章节", readerText = "从左侧选择章节阅读原文，或导入新的故事。";
    private string chapterHint = "", jobHint = "";
    private ProjectItem? currentProject;
    public bool Connected { get => connected; set => Set(ref connected, value); }
    public bool Busy { get => busy; set => Set(ref busy, value); }
    public string ConnectionLabel { get => connectionLabel; set => Set(ref connectionLabel, value); }
    public string Error { get => error; set { Set(ref error, value); Changed(nameof(HasError)); } }
    public bool HasError => Error.Length > 0;
    public string Status { get => status; set => Set(ref status, value); }
    public string Breadcrumb { get => breadcrumb; set => Set(ref breadcrumb, value); }
    public string Overview { get => overview; set => Set(ref overview, value); }
    public string EmptyMessage { get => emptyMessage; set => Set(ref emptyMessage, value); }
    public string ReaderTitle { get => readerTitle; set => Set(ref readerTitle, value); }
    public string ReaderText { get => readerText; set => Set(ref readerText, value); }
    public string ChapterHint { get => chapterHint; set => Set(ref chapterHint, value); }
    public string JobHint { get => jobHint; set => Set(ref jobHint, value); }
    public ProjectItem? CurrentProject { get => currentProject; set => Set(ref currentProject, value); }
    public string DataPath { get; init; } = "";
    public string ProjectCount => Projects.Count.ToString();
    public void CountsChanged() => Changed(nameof(ProjectCount));
}
