namespace MangaFlow.Native;

public enum ProjectPageId { Source, Assets, Script, Storyboard, Generate, Library, Jobs, Workflow, Settings }

public sealed record ProjectPageDefinition(ProjectPageId Id, string Index, string Title,
    string Description, string WebSection)
{
    public string IconData => Id switch
    {
        ProjectPageId.Source => "M2 3 L9 3 12 5 15 3 22 3 22 20 15 20 12 22 9 20 2 20 Z M12 5 L12 22 M5 7 L8 7 M5 11 L8 11 M16 7 L19 7",
        ProjectPageId.Assets => "M8 2 A4 4 0 1 1 7.9 2 M2 22 L2 18 Q2 13 8 13 Q14 13 14 18 L14 22 M16 3 Q24 7 16 11 M18 14 Q22 15 22 21",
        ProjectPageId.Script => "M2 9 L22 9 22 21 2 21 Z M2 9 L1 4 20 0 22 5 Z M7 3 L10 7 M14 2 L17 6",
        ProjectPageId.Storyboard => "M2 3 L22 3 22 21 2 21 Z M2 9 L22 9",
        ProjectPageId.Generate => "M12 2 L15 9 22 12 15 15 12 22 9 15 2 12 9 9 Z M3 0 L3 6 M0 3 L6 3",
        ProjectPageId.Library => "M2 2 L6 2 6 22 2 22 Z M9 2 L13 2 13 22 9 22 Z M16 3 L20 2 24 21 20 22 Z",
        ProjectPageId.Jobs => "M2 2 L6 2 6 6 2 6 Z M10 4 L22 4 M2 12 L4 14 7 10 M10 12 L22 12 M2 20 L4 22 7 18 M10 20 L18 20",
        ProjectPageId.Workflow => "M2 2 L9 2 9 9 2 9 Z M15 15 L22 15 22 22 15 22 Z M5 9 L5 16 Q5 19 8 19 L15 19",
        _ => "M3 5 L21 5 M3 12 L21 12 M3 19 L21 19 M8 2 L8 8 M16 9 L16 15 M8 16 L8 22",
    };
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
