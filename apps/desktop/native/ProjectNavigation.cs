namespace MangaFlow.Native;

public enum ProjectPageId { Source, Assets, Script, Storyboard, Generate, Library, Jobs, Workflow, Settings }

public sealed record ProjectPageDefinition(ProjectPageId Id, string Index, string Title,
    string Description, string WebSection, string? ReadResource, string[] Areas)
{
    public bool IsConnected => ReadResource != null;
    public string Availability => IsConnected ? "部分功能已接通" : "待迁移";
    public string AccessibleName => $"{Title}，{Availability}";
}

// Keep labels/order aligned with Web navigationItems. ReadResource is explicit:
// adding a page must never accidentally send the jobs request as a fallback.
public static class ProjectPages
{
    public static IReadOnlyList<ProjectPageDefinition> All { get; } = Array.AsReadOnly(new[]
    {
        new ProjectPageDefinition(ProjectPageId.Source, "01", "原作与修订", "导入、修改、撤回", "source", "chapters", ["章节列表", "原文与修订"]),
        new ProjectPageDefinition(ProjectPageId.Assets, "02", "参考资产", "人物 / 服装 / 场景 / 风格", "assets", null, ["人物与角色模型包", "服装与绑定", "场景资产", "漫画风格", "参考图片"]),
        new ProjectPageDefinition(ProjectPageId.Script, "03", "漫画剧本", "场景、情节拍、对白", "script", null, ["章节与场景", "情节拍与对白", "解析与版本确认"]),
        new ProjectPageDefinition(ProjectPageId.Storyboard, "04", "分页与分镜", "场景切页、格子脚本", "storyboard", null, ["页面联系表", "分镜画布", "格子与对白检查器", "布局保存与确认"]),
        new ProjectPageDefinition(ProjectPageId.Generate, "05", "单页生成", "抽卡、收藏、采用", "generate", null, ["页面与生产门禁", "模型与参考选择", "候选比较与采用", "导演与局部重绘", "质检与导出"]),
        new ProjectPageDefinition(ProjectPageId.Library, "06", "生成素材库", "按类型和批次归档", "library", null, ["类型与批次筛选", "候选图片网格", "原图预览与血缘"]),
        new ProjectPageDefinition(ProjectPageId.Jobs, "07", "任务中心", "进度、失败、取消重试", "jobs", "jobs", ["任务筛选", "进度与操作"]),
        new ProjectPageDefinition(ProjectPageId.Workflow, "FL", "流程编排", "节点、连线、审批与运行", "workflow", null, ["节点工具栏", "工作流画布", "节点检查器", "运行监视器"]),
        new ProjectPageDefinition(ProjectPageId.Settings, "ST", "项目设置", "制作策略与项目管理", "settings", null, ["工作方式", "清晰度与并发", "检查开关", "文字任务默认路由", "项目管理"]),
    });

    public static ProjectPageDefinition Get(ProjectPageId id) =>
        All.First(page => page.Id == id);
}

public sealed class ProjectNavigation : Observable
{
    private ProjectPageDefinition current = ProjectPages.Get(ProjectPageId.Source);
    public ProjectPageDefinition Current => current;

    public bool Select(ProjectPageDefinition page)
    {
        if (!ProjectPages.All.Contains(page) || current == page) return false;
        current = page;
        Changed(nameof(Current));
        return true;
    }
}
