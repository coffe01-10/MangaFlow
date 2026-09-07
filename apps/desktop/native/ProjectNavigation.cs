namespace MangaFlow.Native;

public enum ProjectPageId { Source, Assets, Script, Storyboard, Generate, Library, Jobs, Workflow, Settings }

public sealed record ProjectPageDefinition(ProjectPageId Id, string Index, string Title,
    string Description, string WebSection)
{
    public string Availability => "已接通";
    public string AccessibleName => $"{Title}，{Availability}";
}

// Keep labels/order aligned with Web navigationItems (project-workspace/labels.ts).
public static class ProjectPages
{
    public static IReadOnlyList<ProjectPageDefinition> All { get; } = Array.AsReadOnly(new[]
    {
        new ProjectPageDefinition(ProjectPageId.Source, "01", "原作与修订", "导入、修改、撤回", "source"),
        new ProjectPageDefinition(ProjectPageId.Assets, "02", "参考资产", "人物 / 服装 / 场景 / 风格", "assets"),
        new ProjectPageDefinition(ProjectPageId.Script, "03", "漫画剧本", "场景、情节拍、对白", "script"),
        new ProjectPageDefinition(ProjectPageId.Storyboard, "04", "分页与分镜", "场景切页、格子脚本", "storyboard"),
        new ProjectPageDefinition(ProjectPageId.Generate, "05", "单页生成", "抽卡、收藏、采用", "generate"),
        new ProjectPageDefinition(ProjectPageId.Library, "06", "生成素材库", "按类型和批次归档", "library"),
        new ProjectPageDefinition(ProjectPageId.Jobs, "07", "任务中心", "进度、失败、取消重试", "jobs"),
        new ProjectPageDefinition(ProjectPageId.Workflow, "FL", "流程编排", "节点、连线、审批与运行", "workflow"),
        new ProjectPageDefinition(ProjectPageId.Settings, "ST", "项目设置", "制作策略与项目管理", "settings"),
    });

    public static ProjectPageDefinition Get(ProjectPageId id) =>
        All.First(page => page.Id == id);

    public static ProjectPageDefinition? FindBySection(string section) =>
        All.FirstOrDefault(page => page.WebSection == section);
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
