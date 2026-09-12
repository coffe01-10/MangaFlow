using System.Collections.ObjectModel;
using System.Text.Json;
using MangaFlow.Native.Services;

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
    public string ConnectionBadgeLabel =>
        healthyConnections > 0 ? $"{healthyConnections} 个 AI 连接健康"
        : configuredConnections > 0 ? "供应商待验证"
        : "未配置";
    public string ConnectionBadgeTone =>
        healthyConnections > 0 ? "success" : configuredConnections > 0 ? "warning" : "danger";
    public string AiConnectionTitle => healthyConnections > 0 ? "AI 连接已就绪" : configuredConnections > 0 ? "连接等待验证" : "等待添加供应商";

    public void UpdateDashboard(JsonElement dashboard)
    {
        var totals = dashboard.Element("totals");
        var ai = dashboard.Element("ai_overview");
        totalPages = totals.Number("page_count"); selectedPages = totals.Number("selected_page_count");
        reviewPages = totals.Number("review_page_count"); enabledModels = ai.Number("enabled_model_count");
        configuredConnections = ai.Number("configured_connection_count"); healthyConnections = ai.Number("healthy_connection_count");
        ChangedAll(nameof(ActiveProjectMetric), nameof(PageMetric), nameof(SelectedPageLabel), nameof(ReviewMetric),
            nameof(ModelLabel), nameof(ConfiguredConnections), nameof(HealthyConnections), nameof(EnabledModels),
            nameof(AiConnectionTitle), nameof(ConnectionBadgeLabel), nameof(ConnectionBadgeTone));
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
        // #486-2：封面显示字段（ModeLabel/Resolution/NextSection/NextLabel）在
        // record 主构造参数之外。实测 record 合成等值今天已覆盖 body 属性（原始
        // 审计的「仅 ctor 参数」前提不成立，缺陷按描述不可复现）；这里改成
        // SameDisplay 显式比较是把显示契约写死——将来 ProjectItem 重构为 class、
        // 或显示字段挪出等值面时不再靠隐式合成等值兜底。
        if (DashboardProjects.Count != visible.Count
            || DashboardProjects.Zip(visible).Any(pair => !pair.First.SameDisplay(pair.Second)))
        {
            DashboardProjects.Clear();
            foreach (var item in visible) DashboardProjects.Add(item);
        }
        ChangedAll(nameof(DashboardPageLabel), nameof(HasPreviousPage), nameof(HasNextPage));
    }

    // Queue dock (web QueueDock): latest job + total/running/waiting/failed/completed counters.
    private JobItem? dockJob;
    private int runningJobs, waitingJobs, failedJobs, completedJobs, dockTotal;
    private bool dockHidden, dockActionPending;
    private string dockNotice = "";
    public JobItem? DockJob
    {
        get => dockJob;
        set => Set(ref dockJob, value, nameof(DockJob), nameof(DockJobLabel),
            nameof(DockCancelShown), nameof(DockRetryShown), nameof(DockStatusText));
    }
    public string DockJobLabel => DockJob is { } job ? $"{job.Name} · {job.StatusLabel}" : "";
    public int RunningJobs { get => runningJobs; set => Set(ref runningJobs, value, nameof(RunningJobs), nameof(DockSummary)); }
    public int WaitingJobs { get => waitingJobs; set => Set(ref waitingJobs, value, nameof(WaitingJobs), nameof(DockSummary), nameof(DockWaiting)); }
    public int FailedJobs { get => failedJobs; set => Set(ref failedJobs, value, nameof(FailedJobs), nameof(DockSummary)); }
    public int CompletedJobs { get => completedJobs; set => Set(ref completedJobs, value, nameof(CompletedJobs), nameof(DockSummary)); }
    public int DockTotal { get => dockTotal; set => Set(ref dockTotal, value, nameof(DockTotal), nameof(DockSummary), nameof(DockCountShown)); }
    public bool DockWaiting => WaitingJobs > 0;
    public bool DockCountShown => DockTotal > 0;
    public bool DockActionPending
    {
        get => dockActionPending;
        set => Set(ref dockActionPending, value, nameof(DockActionPending), nameof(DockCancelShown), nameof(DockRetryShown));
    }
    public string DockNotice { get => dockNotice; set => Set(ref dockNotice, value, nameof(DockNotice), nameof(DockNoticeShown)); }
    public bool DockNoticeShown => DockNotice.Length > 0;
    public bool DockCancelShown => DockJob is { CanCancel: true } && !dockActionPending;
    public bool DockRetryShown => DockJob is { CanRetry: true } && !dockActionPending;
    public string DockSummary => $"共 {dockTotal} 项 · {runningJobs} 运行 · {waitingJobs} 等待 · {failedJobs} 失败 · {completedJobs} 完成";
    public string DockIdleLabel => CurrentSection is "jobs" or "generate" ? "当前没有任务" : "查看生成、解析与检查进度";
    // Web dock headline: latest job label+status when present, idle hint otherwise (never both).
    public string DockStatusText => DockJob is { } job ? DockJobLabel : DockIdleLabel;
    private string currentSection = "home";
    public string CurrentSection
    {
        get => currentSection;
        set => Set(ref currentSection, value, nameof(CurrentSection), nameof(DockIdleLabel), nameof(DockStatusText));
    }
    private bool isWorkspace, sidebarCollapsed;
    public bool IsWorkspace { get => isWorkspace; set => Set(ref isWorkspace, value, nameof(IsWorkspace), nameof(DockShown), nameof(DockRestoreShown)); }
    public bool SidebarCollapsed { get => sidebarCollapsed; set => Set(ref sidebarCollapsed, value); }
    public bool DockHidden
    {
        get => dockHidden;
        set => Set(ref dockHidden, value, nameof(DockHidden), nameof(DockShown), nameof(DockRestoreShown));
    }
    public bool DockShown => IsWorkspace && !dockHidden;
    public bool DockRestoreShown => IsWorkspace && dockHidden;

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
    public ProjectItem? CurrentProject
    {
        get => currentProject;
        set
        {
            Set(ref currentProject, value);
            Changed(nameof(ProjectId));
        }
    }
    public string ProjectId => currentProject?.Id ?? "";
    public string DataPath { get; init; } = "";
    public string ProjectCount => Projects.Count.ToString();
    public void CountsChanged() => Changed(nameof(ProjectCount));
}
