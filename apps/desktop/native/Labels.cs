namespace MangaFlow.Native;

// Enum → Chinese mappings ported from apps/web/components/project-workspace/labels.ts.
// Unknown values fall back to the raw enum, exactly like the web labels helper.
public static class Labels
{
    public static string Map(IReadOnlyDictionary<string, string> table, string? value) =>
        value is null ? "" : table.TryGetValue(value, out var label) ? label : value;

    public static readonly IReadOnlyDictionary<string, string> WorkflowMode = new Dictionary<string, string>
    {
        ["SEMI_AUTO"] = "半自动（推荐）",
        ["DIRECTOR"] = "导演逐步",
        ["AUTO"] = "自动快速",
    };
    public static readonly IReadOnlyDictionary<string, string> WorkflowModeShort = new Dictionary<string, string>
    {
        ["SEMI_AUTO"] = "半自动", ["DIRECTOR"] = "导演", ["AUTO"] = "自动",
    };
    public static readonly IReadOnlyDictionary<string, string> WorkflowModeDetail = new Dictionary<string, string>
    {
        ["SEMI_AUTO"] = "AI 先完成准备步骤，保留人工采用与逐页确认",
        ["DIRECTOR"] = "每个关键阶段都等待确认后再继续",
        ["AUTO"] = "自动推进文字环节，图片仍逐页确认",
    };

    public static readonly IReadOnlyDictionary<string, string> AssetKinds = new Dictionary<string, string>
    {
        ["CHARACTER_REFERENCE"] = "人物参考",
        ["OUTFIT_REFERENCE"] = "服装参考",
        ["STYLE_REFERENCE"] = "漫画风格",
        ["SCENE_REFERENCE"] = "场景参考",
    };
    public static string AssetKindShort(string? kind) => kind switch
    {
        "CHARACTER_REFERENCE" => "人物",
        "OUTFIT_REFERENCE" => "服装",
        "STYLE_REFERENCE" => "风格",
        "SCENE_REFERENCE" => "场景",
        _ => kind ?? "",
    };

    public static readonly IReadOnlyDictionary<string, string> Jobs = new Dictionary<string, string>
    {
        ["SOURCE_PARSE"] = "解析剧本",
        ["PAGE_GENERATE"] = "生成页面",
        ["PAGE_REPAIR"] = "修复页面",
        ["PAGE_UPSCALE"] = "保持结构升清",
        ["ASSET_GENERATE"] = "生成角色/服装素材",
        ["PAGE_INSPECT"] = "检查页面",
        ["STYLE_ANALYZE"] = "分析漫画风格",
        ["WORKFLOW_NODE"] = "执行工作流节点",
        ["PARSE_SOURCE"] = "解析原作",
        ["GENERATE_PAGE"] = "生成漫画页面",
        ["GENERATE_CHARACTER"] = "生成人物参考",
        ["INSPECT_PAGE"] = "检查页面",
    };

    public static readonly IReadOnlyDictionary<string, string> JobStatus = new Dictionary<string, string>
    {
        ["WAITING"] = "等待中",
        ["QUEUED"] = "排队中",
        ["PREPARING"] = "准备中",
        ["UPLOADING_REFERENCES"] = "上传参考图",
        ["GENERATING"] = "生成中",
        ["OCR_CHECKING"] = "文字检查",
        ["CONSISTENCY_CHECKING"] = "连续性检查",
        ["REPAIRING"] = "修复中",
        ["RUNNING"] = "进行中",
        ["COMPLETED"] = "已完成",
        ["FAILED"] = "已失败",
        ["CANCELLED"] = "已取消",
        ["NEEDS_REVIEW"] = "待复核",
    };

    public static readonly IReadOnlyDictionary<string, string> GenerationKind = new Dictionary<string, string>
    {
        ["PAGE"] = "页面抽卡",
        ["REPAIR"] = "页面修复",
        ["CHARACTER"] = "角色形象补全",
        ["OUTFIT"] = "角色服装形象",
        ["STYLE_TEST"] = "漫画风格测试",
        ["UPSCALE"] = "保持结构升清",
        ["REGION_REGENERATED"] = "局部重绘",
    };

    public static readonly IReadOnlyDictionary<string, string> InspectionCategory = new Dictionary<string, string>
    {
        ["TEXT"] = "文字",
        ["SPEAKER"] = "说话人",
        ["CHARACTER"] = "角色",
        ["OUTFIT"] = "服装",
        ["PROP"] = "道具",
        ["CONTINUITY"] = "连续性",
    };

    public static readonly IReadOnlyDictionary<string, string> RepairType = new Dictionary<string, string>
    {
        ["BUBBLE_REGION"] = "气泡区域",
        ["PANEL"] = "单格",
        ["PAGE"] = "整页",
    };

    public static readonly IReadOnlyDictionary<string, string> CandidateStatus = new Dictionary<string, string>
    {
        ["QUEUED"] = "排队中",
        ["GENERATING"] = "生成中",
        ["READY"] = "已就绪",
        ["STALE"] = "已过期",
        ["INSPECTED"] = "已检查",
        ["NEEDS_REVIEW"] = "待复核",
        ["FAILED"] = "已失败",
        ["CANCELLED"] = "已取消",
    };

    public static readonly IReadOnlyDictionary<string, string> CandidateVersionState = new Dictionary<string, string>
    {
        ["CURRENT"] = "当前版本",
        ["STALE"] = "分镜已更新",
        ["STALE_ACCEPTED"] = "已采用但分镜过期",
        ["LEGACY_UNKNOWN"] = "版本未知",
    };

    public static readonly IReadOnlyDictionary<string, string> WorkflowRunStatus = new Dictionary<string, string>
    {
        ["WAITING"] = "等待中",
        ["RUNNING"] = "运行中",
        ["COMPLETED"] = "已完成",
        ["WAITING_APPROVAL"] = "等待确认",
        ["FAILED"] = "已失败",
        ["SKIPPED"] = "已跳过",
        ["CANCELLED"] = "已取消",
    };

    public static readonly IReadOnlyDictionary<string, string> ChapterStatus = new Dictionary<string, string>
    {
        ["IMPORTED"] = "已导入",
        ["SCRIPT_READY"] = "剧本已生成",
        ["SCRIPT_INCOMPLETE"] = "剧本不完整",
        ["PAGES_PLANNED"] = "已分页",
    };

    public static readonly IReadOnlyDictionary<string, string> AssetStatus = new Dictionary<string, string>
    {
        ["UPLOADED"] = "已上传",
        ["ANALYZED"] = "已分析",
        ["GENERATED"] = "已生成",
        ["NEEDS_CONFIRMATION"] = "待确认",
        ["CANONICAL"] = "已定稿",
        ["ARCHIVED"] = "已归档",
    };

    public static readonly IReadOnlyDictionary<string, string> StyleStatus = new Dictionary<string, string>
    {
        ["ANALYZING"] = "分析中",
        ["DRAFT"] = "草稿",
        ["TEST_GENERATED"] = "测试图已生成",
        ["CONFIRMED"] = "已确认",
        ["ACTIVE"] = "使用中",
    };

    public static readonly IReadOnlyDictionary<string, string> ScriptStatus = new Dictionary<string, string>
    {
        ["NOT_CREATED"] = "未创建",
        ["DRAFT"] = "生成中",
        ["READY"] = "已生成",
        ["INCOMPLETE"] = "不完整",
    };

    public static readonly IReadOnlyDictionary<string, string> InspectionOutcome = new Dictionary<string, string>
    {
        ["PASS"] = "通过",
        ["ACCEPTABLE"] = "可接受",
        ["MATCH"] = "匹配",
        ["MISMATCH"] = "不匹配",
        ["MISSING"] = "画面缺失",
        ["EXTRA"] = "画面多余",
    };

    public static readonly IReadOnlyDictionary<string, string> ProductionState = new Dictionary<string, string>
    {
        ["READY"] = "已通过",
        ["NEEDS_REPAIR"] = "待修复",
        ["STALE"] = "分镜已更新",
        ["AWAITING_INSPECTION"] = "待视觉检查",
        ["AWAITING_SELECTION"] = "待暂选",
    };

    public static readonly IReadOnlyDictionary<string, string> SceneAssetStatus = new Dictionary<string, string>
    {
        ["UPLOADED"] = "已上传",
        ["ANALYZED"] = "已分析",
        ["GENERATED"] = "已生成",
        ["NEEDS_CONFIRMATION"] = "待确认 · 尚未设置规范参考图",
        ["CANONICAL"] = "已就绪 · 可直接用于剧本与分镜",
        ["ARCHIVED"] = "已归档",
    };

    public static readonly IReadOnlyDictionary<string, string> PackageVersionStatus = new Dictionary<string, string>
    {
        ["DRAFT"] = "草稿",
        ["READY"] = "已发布",
        ["IN_PRODUCTION"] = "生产使用中",
        ["ARCHIVED"] = "已归档",
    };

    public static readonly IReadOnlyDictionary<string, string> PackageRole = new Dictionary<string, string>
    {
        ["cover"] = "封面",
        ["front"] = "正面（主视）",
        ["side"] = "右侧面",
        ["back"] = "背面",
        ["three_quarter"] = "3/4 侧面",
        ["expression"] = "表情",
        ["pose"] = "姿态",
        ["extra"] = "补充",
    };

    public static readonly IReadOnlyDictionary<string, string> ErrorCode = new Dictionary<string, string>
    {
        ["MODEL_ROUTE_UNAVAILABLE"] = "没有已验证且满足任务能力的模型",
        ["PROVIDER_ERROR"] = "供应商返回错误，请稍后重试",
        ["RATE_LIMIT"] = "供应商限流，请稍后重试",
        ["CONTENT_POLICY"] = "内容被供应商安全策略拦截",
        ["INSUFFICIENT_QUOTA"] = "供应商余额不足",
        ["INVALID_API_KEY"] = "密钥无效或已失效",
        ["NETWORK_ERROR"] = "网络连接失败",
        ["TIMEOUT"] = "请求超时",
        ["CAPABILITY_MISMATCH"] = "模型能力不满足该任务",
        ["ASSET_MISSING"] = "参考素材缺失",
        ["STORYBOARD_VERSION_CONFLICT"] = "分镜版本已变化，请刷新后重试",
        ["CANDIDATE_NOT_READY"] = "候选尚未生成完成",
        ["QUALITY_INSPECTION_REQUIRED"] = "需要先完成视觉检查",
        ["QUALITY_REVIEW_REQUIRED"] = "存在未通过的视觉检查",
        ["PAGE_NOT_PRODUCTION_READY"] = "页面尚未生产通过",
        ["CHAPTER_NOT_READY"] = "章节尚未满足导出条件",
        ["WORKFLOW_INVALID"] = "工作流校验未通过",
        ["WORKFLOW_VERSION_CONFLICT"] = "工作流版本已变化，请刷新后重试",
        ["CONCURRENCY_LIMIT"] = "并发名额已满，任务等待中",
        ["JOB_CANCELLED"] = "任务已取消",
        ["JOB_LEASE_LOST"] = "任务租约丢失",
        ["RETRY_EXHAUSTED"] = "重试次数已用尽",
        ["UPSCALE_NOT_SUPPORTED"] = "该模型不支持此清晰度升清",
        ["MASK_REQUIRED"] = "缺少选区蒙版",
        ["UNSUPPORTED_CAPABILITY"] = "所选模型不支持该操作面",
        ["DEPENDENCY_MISSING"] = "上游依赖缺失",
        ["TEXT_LENGTH_EXCEEDED"] = "文字超出单页上限",
        ["NO_DIALOGUE_TARGET"] = "找不到目标气泡",
        ["PANEL_NOT_FOUND"] = "找不到目标分镜格",
        ["SCENE_NOT_FOUND"] = "找不到目标场景",
        ["VERSION_CONFLICT"] = "数据版本冲突，请刷新后重试",
    };

    public static readonly IReadOnlyDictionary<string, string> DirectorOperation = new Dictionary<string, string>
    {
        ["update_page_layout"] = "整页布局",
        ["update_panel_layout"] = "格布局",
        ["update_panel_shot"] = "镜头景别",
        ["update_panel_cast"] = "角色出场 / 表情",
        ["update_scene_context"] = "场景上下文",
        ["update_dialogue"] = "气泡台词",
        ["move_dialogue"] = "气泡位置",
        ["regenerate_region"] = "局部重绘",
    };

    public static readonly IReadOnlyDictionary<string, string> DirectorCommandStatus = new Dictionary<string, string>
    {
        ["PROPOSED"] = "待解析",
        ["PREVIEWED"] = "待确认",
        ["ACCEPTED"] = "已接受",
        ["REJECTED"] = "已拒绝",
        ["EXECUTED"] = "已执行",
        ["SUPERSEDED"] = "已被更新取代",
        ["DISCARDED"] = "已丢弃",
        ["FAILED"] = "执行失败",
    };

    public static readonly IReadOnlyDictionary<string, string> DirectorDiffField = new Dictionary<string, string>
    {
        ["shot_type"] = "景别",
        ["camera_angle"] = "镜头角度",
        ["target_text"] = "台词",
        ["weather"] = "天气",
        ["time_label"] = "时间",
        ["panel_count"] = "格数",
        ["layout_mode"] = "布局模式",
        ["characters"] = "入镜角色",
        ["character_presence"] = "出场状态",
        ["expressions"] = "表情",
    };

    public static readonly IReadOnlyDictionary<string, string> ShotType = new Dictionary<string, string>
    {
        ["establishing"] = "远景建立",
        ["wide_action"] = "全景动作",
        ["medium_close_up"] = "中近景",
        ["close_up"] = "近景",
        ["extreme_close_up"] = "大特写",
    };

    public static readonly IReadOnlyDictionary<string, string> CameraAngle = new Dictionary<string, string>
    {
        ["eye_level"] = "平视",
        ["low_angle"] = "仰拍",
        ["high_angle"] = "俯拍",
        ["dutch_angle"] = "倾斜镜头",
        ["over_shoulder"] = "越肩",
    };

    public static readonly IReadOnlyDictionary<string, string> CameraHeight = new Dictionary<string, string>
    {
        ["eye_level"] = "视线高度",
        ["ground_level"] = "贴地机位",
        ["waist_level"] = "腰部机位",
        ["top_down"] = "顶视机位",
    };

    public static readonly IReadOnlyDictionary<string, string> CharacterPresence = new Dictionary<string, string>
    {
        ["VISIBLE"] = "实际出镜",
        ["OFFSCREEN"] = "画外人物",
        ["MENTIONED"] = "仅被提及",
        ["NONE"] = "不在本格",
    };

    public static readonly IReadOnlyDictionary<string, string> TextDirection = new Dictionary<string, string>
    {
        ["VERTICAL"] = "竖排",
        ["HORIZONTAL"] = "横排",
    };

    public static readonly IReadOnlyDictionary<string, string> ProviderHealth = new Dictionary<string, string>
    {
        ["HEALTHY"] = "健康",
        ["DEGRADED"] = "降级",
        ["UNKNOWN"] = "未知",
        ["UNCONFIGURED"] = "未配置",
        ["CHECKING"] = "检查中",
        ["OFFLINE"] = "离线",
        ["PROBING"] = "探测中",
        ["AVAILABLE"] = "就绪",
        ["UNAVAILABLE"] = "未安装",
        ["UNAUTHENTICATED"] = "未登录 CLI",
        ["UNSUPPORTED"] = "不支持图片生成",
    };

    public static readonly IReadOnlyDictionary<string, string> ProviderCategory = new Dictionary<string, string>
    {
        ["OFFICIAL"] = "官方",
        ["GATEWAY"] = "网关",
        ["THIRD_PARTY"] = "第三方",
        ["CUSTOM"] = "自定义",
    };

    public static readonly IReadOnlyDictionary<string, string> ProviderRisk = new Dictionary<string, string>
    {
        ["LOW"] = "低风险",
        ["OFFICIAL"] = "官方",
        ["GATEWAY"] = "网关",
        ["THIRD_PARTY"] = "第三方",
        ["CUSTOM"] = "自定义",
        ["HIGH"] = "高风险",
    };

    public static readonly IReadOnlyDictionary<string, string> ModelOperation = new Dictionary<string, string>
    {
        ["structured_text"] = "结构化文本",
        ["multimodal_analysis"] = "视觉理解",
        ["image_generate"] = "图片生成",
        ["image_edit"] = "图片编辑",
    };

    public static readonly IReadOnlyDictionary<string, string> ModelConfidence = new Dictionary<string, string>
    {
        ["MANUAL"] = "待验证",
        ["DECLARED"] = "待验证",
        ["INFERRED"] = "推断/待验证",
        ["PARTIAL"] = "部分验证",
        ["VERIFIED"] = "已验证",
    };

    public static readonly IReadOnlyDictionary<string, string> UsageStatus = new Dictionary<string, string>
    {
        ["COMPLETE"] = "完整计量",
        ["PARTIAL"] = "部分计量",
        ["UNKNOWN"] = "计量未知",
    };

    public static readonly IReadOnlyDictionary<string, string> CostMode = new Dictionary<string, string>
    {
        ["BILLED"] = "账单",
        ["ESTIMATED"] = "预估",
        ["USAGE_ONLY"] = "仅计量",
        ["UNKNOWN"] = "成本未知",
        ["UNAVAILABLE"] = "无用量返回",
    };

    public static readonly IReadOnlyDictionary<string, string> CostModeHint = new Dictionary<string, string>
    {
        ["BILLED"] = "来自运营对账导入的账单事实",
        ["ESTIMATED"] = "按本地价格表推算，估算值不等于供应商账单",
        ["USAGE_ONLY"] = "已统计到用量；单次金额需以汇总层估算为准（该范围可能未配置单价）",
        ["UNKNOWN"] = "缺少计量或价格数据，未知不等于 0，也不等于免费",
        ["UNAVAILABLE"] = "供应商调用成功但未返回 usage 字段；早期版本的文本调用在用量回填前也记为无用量",
    };

    public static readonly IReadOnlyDictionary<string, string> AttemptOutcome = new Dictionary<string, string>
    {
        ["SUCCEEDED"] = "成功",
        ["FAILED"] = "失败",
        ["PENDING"] = "未决",
    };

    public static string JobStatusText(string? status) => Map(JobStatus, status);
    public static string JobTypeText(string? type) => Map(Jobs, type);
}
