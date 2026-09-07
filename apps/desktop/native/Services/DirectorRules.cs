using System.Text.Json;

namespace MangaFlow.Native.Services;

/// <summary>
/// Rule-stub compiler ported from apps/web/lib/director-rules.ts (V02-40):
/// turns scope selection + a short utterance into one whitelisted command
/// envelope. Never calls a model; never does pixel-level edits.
/// </summary>
public sealed record DirectorScope(string Kind, string? PanelId, string? DialogueId, string? CharacterId)
{
    public static DirectorScope Page() => new("page", null, null, null);
    public static DirectorScope Panel(string id) => new("panel", id, null, null);
    public static DirectorScope Dialogue(string panelId, string dialogueId) => new("dialogue", dialogueId, panelId, null);
    public static DirectorScope Character(string id) => new("character", null, null, id);
}

public sealed record DirectorPlanResult(
    string Kind,  // command | clarify | blocked | unsupported
    object? Envelope, string IntentLabel, string ScopeLabel, string Summary, string Risk,
    string? Reason, List<(string Kind, string? Id, string Label)>? Options)
{
    public static DirectorPlanResult Command(object envelope, string intent, string scope, string summary, string risk) =>
        new("command", envelope, intent, scope, summary, risk, null, null);
    public static DirectorPlanResult Clarify(string reason, List<(string, string?, string)> options) =>
        new("clarify", null, "", "", "", "", reason, options);
    public static DirectorPlanResult Blocked(string reason) => new("blocked", null, "", "", "", "", reason, null);
    public static DirectorPlanResult Unsupported(string reason) => new("unsupported", null, "", "", "", "", reason, null);
}

public static class DirectorRules
{
    public const string ParserLabel = "规则解析，非模型";
    public const string RawOutputId = "rule_stub_v1";

    private static readonly string[] PixelIntentWords = ["重画", "重绘", "重新生成", "重新抽", "重抽", "局部", "选区", "蒙版", "mask", "涂"];

    public static DirectorPlanResult Compile(JsonElement page, JsonElement storyboard, List<JsonElement> characters,
        DirectorScope? selection, string utterance, string? retryOfCommandId, bool pageGenerationPending,
        int? sceneVersion = null)
    {
        var panels = storyboard.Array("panels");
        var text = utterance.Trim();
        if (text.Length == 0)
            return DirectorPlanResult.Clarify("请输入一句导演指令", [("page", null, "整页")]);

        if (PixelIntentWords.Any(word => text.Contains(word, StringComparison.OrdinalIgnoreCase)))
            return DirectorPlanResult.Unsupported("局部重绘需要在图上画选区，命令栏编不了 mask。请点击下方「在选区编辑（mask 局部重绘）」进入局部编辑器：画好选区并确认预览后，会按 regenerate_region 生成派生候选。导演台不会静默整页重绘。");

        var panelNumber = MatchPanelNumber(text);
        var targetPanel = ResolvePanel(panels, selection, panelNumber, text);

        // Whole-page panel count: 改成 N 格
        var countMatch = System.Text.RegularExpressions.Regex.Match(text,
            @"(?:改成|改为|变成|换成|调整为?|分成|划分为?)\s*([3-8]|[一二三四五六七八])\s*格");
        if (countMatch.Success)
        {
            if (pageGenerationPending)
                return DirectorPlanResult.Blocked("当前页已有生成任务进行中，请等当前图完成或取消后，再发整页命令");
            var count = countMatch.Groups[1].Value switch
            {
                "三" => 3, "四" => 4, "五" => 5, "六" => 6, "七" => 7, "八" => 8, var n => int.Parse(n),
            };
            var summary = $"把第 {page.Number("page_number")} 页改为 {count} 格（动态布局）。整页命令风险高：改动后该页候选将过期。";
            return DirectorPlanResult.Command(
                Envelope(page, null, null, null, "page", page.Number("version"), "update_page_layout",
                    new Dictionary<string, object?> { ["panel_count"] = count, ["layout_mode"] = "dynamic" }, text, retryOfCommandId),
                "整页布局", $"第 {page.Number("page_number")} 页 · 整页", summary, "high");
        }

        // Dialogue rewrite: 台词改成「…」
        var hasQuote = text.Contains('「') || text.Contains('『') || text.Contains('“') || text.Contains('"');
        var hasVerb = text.Contains("改成") || text.Contains("改为") || text.Contains("换成") || text.Contains("说");
        if (text.Contains("台词") || text.Contains("对白") || text.Contains("气泡") || (hasQuote && hasVerb))
        {
            if (targetPanel is { Kind: "clarify" or "blocked" or "unsupported" }) return targetPanel;
            var quoted = System.Text.RegularExpressions.Regex.Match(text, @"[「『“""]([^」』""]{1,200})[」』""]");
            string? newText = quoted.Success ? quoted.Groups[1].Value.Trim() : null;
            if (newText == null)
            {
                var bare = System.Text.RegularExpressions.Regex.Match(text, @"(?:台词|对白|气泡)(?:内容|文字)?(?:改成|改为|换成)\s*([^，。！？；\s]{1,200})");
                if (bare.Success) newText = bare.Groups[1].Value;
            }
            if (newText == null)
                return DirectorPlanResult.Clarify("请用引号写明新的台词内容，例如：台词改成「我没事」", []);
            var dialogue = ResolveDialogue((JsonElement)targetPanel!.Envelope!, selection, text, panels);
            if (dialogue.Kind != "command") return dialogue;
            var dialogueRow = (JsonElement)dialogue.Envelope!;
            if (dialogueRow.Flag("rewrite_forbidden"))
                return DirectorPlanResult.Unsupported($"格 {dialogueRow.Number("reading_order")} 的气泡被标记为禁止改写，请在分镜编辑器处理");
            var panelRow = (JsonElement)targetPanel.Envelope!;
            var summary = $"把格 {panelRow.Number("reading_order")} 气泡{dialogueRow.Number("reading_order")} 的台词改为「{newText}」。";
            return DirectorPlanResult.Command(
                Envelope(page, dialogueRow.Text("panel_id"), dialogueRow.Text("id"), null, "panel", panelRow.Number("version"),
                    "update_dialogue", new Dictionary<string, object?> { ["target_text"] = newText }, text, retryOfCommandId),
                "气泡台词", $"格 {panelRow.Number("reading_order")} · 气泡{dialogueRow.Number("reading_order")}", summary, "low");
        }

        // Scene weather / time — outcome values must mirror director-rules.ts exactly.
        var weather = MatchTable(text, [
            ("暴雨", "暴雨"), ("雷雨", "雷雨"), ("打雷", "雷雨"), ("雷电", "雷雨"),
            ("雨下大", "大雨"), ("雨大", "大雨"), ("大雨", "大雨"),
            ("雨小", "小雨"), ("小雨", "小雨"), ("毛毛雨", "小雨"),
            ("下雪", "雪"), ("降雪", "雪"), ("雪", "雪"),
            ("起雾", "雾"), ("大雾", "雾"), ("雾", "雾"),
            ("阴天", "阴"), ("转阴", "阴"),
            ("放晴", "晴"), ("晴天", "晴"), ("晴", "晴"),
            ("雨", "雨"),
        ], removeWords: ["去掉", "移除", "拿掉", "停", "不要"], removalValue: "无雨");
        var time = MatchTable(text, [
            ("深夜", "深夜"), ("半夜", "深夜"), ("夜晚", "夜晚"), ("夜里", "夜晚"), ("入夜", "夜晚"), ("晚上", "夜晚"),
            ("清晨", "清晨"), ("黎明", "清晨"), ("正午", "正午"), ("中午", "正午"),
            ("黄昏", "黄昏"), ("傍晚", "黄昏"), ("白天", "白天"), ("日间", "白天"),
        ]);
        if (weather != null || time != null)
        {
            var sceneId = page.Array("scene_ids").FirstOrDefault().ToString();
            if (sceneId.Length == 0)
                return DirectorPlanResult.Unsupported("本页没有关联剧本场景，无法修改天气或时间；请先在剧本中绑定场景");
            if (sceneVersion is not { } sceneVersionValue)
                return DirectorPlanResult.Unsupported("无法确认场景版本，请刷新剧本页后再试");
            var summary = $"把本页主场景的{(weather != null ? $"天气→{weather}" : "")}{(weather != null && time != null ? "、" : "")}{(time != null ? $"时间→{time}" : "")}。天气/时间是场景级字段，会影响本页后续所有抽卡。";
            return DirectorPlanResult.Command(
                Envelope(page, null, null, sceneId, "scene", sceneVersionValue,
                    "update_scene_context", new Dictionary<string, object?>
                    {
                        ["weather"] = weather, ["time_label"] = time,
                    }, text, retryOfCommandId),
                "场景上下文", "主场景", summary, "medium");
        }

        if (targetPanel is { Kind: "clarify" or "blocked" }) return targetPanel;
        if (targetPanel is not { Kind: "command" }) return targetPanel ?? DirectorPlanResult.Clarify("请先选择目标", []);
        var panel = (JsonElement)targetPanel.Envelope!;

        // Cast presence: 去掉 X / X 出现在
        var removeMatch = System.Text.RegularExpressions.Regex.Match(text, @"(?:去掉|移除|拿掉|删除|清空)\s*(.+)");
        var addMatch = System.Text.RegularExpressions.Regex.Match(text, @"(.+?)(?:出现在|登场|走进|加入|入镜)");
        if (removeMatch.Success || addMatch.Success)
        {
            var character = ResolveCharacter(characters, removeMatch.Success ? removeMatch.Groups[1].Value : addMatch.Groups[1].Value, selection, panels);
            if (character.Kind != "command") return character;
            var characterRow = (JsonElement)character.Envelope!;
            var characterId = characterRow.Text("id");
            var characterName = characterRow.Text("primary_name");
            var cast = panel.Strings("characters");
            var presence = new Dictionary<string, object?>();
            foreach (var pair in panel.Element("character_presence").EnumerateObject())
                presence[pair.Name] = pair.Value.ToString();
            if (removeMatch.Success)
            {
                if (!cast.Contains(characterId))
                    return DirectorPlanResult.Unsupported($"格 {panel.Number("reading_order")} 本来没有 {characterName}");
                cast = cast.Where(id => id != characterId).ToList();
                presence.Remove(characterId);
            }
            else
            {
                if (cast.Contains(characterId))
                    return DirectorPlanResult.Unsupported($"{characterName} 已经在格 {panel.Number("reading_order")} 的入镜角色里");
                cast.Add(characterId);
                presence[characterId] = "VISIBLE";
            }
            var intent = removeMatch.Success ? "移除入镜角色" : "加入入镜角色";
            return DirectorPlanResult.Command(
                Envelope(page, panel.Text("id"), null, null, "panel", panel.Number("version"), "update_panel_cast",
                    new Dictionary<string, object?>
                    {
                        ["characters"] = cast, ["character_presence"] = presence,
                    }, text, retryOfCommandId),
                intent, $"格 {panel.Number("reading_order")}",
                $"{intent} {characterName}（格 {panel.Number("reading_order")}）。", "medium");
        }

        // Expression: X 微笑/皱眉/…
        var expression = MatchTable(text, [
            ("微笑", "微笑"), ("笑了", "微笑"), ("露出笑容", "微笑"),
            ("皱眉", "皱眉"), ("皱起眉头", "皱眉"),
            ("惊讶", "惊讶"), ("吃惊", "惊讶"), ("震惊", "惊讶"),
            ("愤怒", "愤怒"), ("生气", "愤怒"), ("发怒", "愤怒"),
            ("哭泣", "哭泣"), ("哭了", "哭泣"), ("落泪", "哭泣"),
            ("害怕", "恐惧"), ("恐惧", "恐惧"), ("惊恐", "恐惧"),
            ("悲伤", "悲伤"), ("难过", "悲伤"),
            ("开心", "开心"), ("高兴", "开心"),
        ]);
        if (expression != null)
        {
            var nameMatch = System.Text.RegularExpressions.Regex.Match(text, @"^(.+?)(?:露出|变得|表现|出现|感到)?(微笑|皱眉|惊讶|愤怒|哭泣|害怕|悲伤|开心|高兴|笑了|哭了)");
            var character = ResolveCharacter(characters, nameMatch.Success ? nameMatch.Groups[1].Value : text, selection, panels);
            if (character.Kind != "command") return character;
            var characterRow = (JsonElement)character.Envelope!;
            if (!panel.Strings("characters").Contains(characterRow.Text("id")))
                return DirectorPlanResult.Unsupported($"格 {panel.Number("reading_order")} 里没有 {characterRow.Text("primary_name")}。可以先让 {characterRow.Text("primary_name")} 出场，再改表情。");
            var expressions = new Dictionary<string, object?>();
            foreach (var pair in panel.Element("expressions").EnumerateObject())
                expressions[pair.Name] = pair.Value.ToString();
            expressions[characterRow.Text("id")] = expression;
            return DirectorPlanResult.Command(
                Envelope(page, panel.Text("id"), null, null, "panel", panel.Number("version"), "update_panel_cast",
                    new Dictionary<string, object?> { ["expressions"] = expressions }, text, retryOfCommandId),
                "角色表情", $"格 {panel.Number("reading_order")} · {characterRow.Text("primary_name")}",
                $"把 {characterRow.Text("primary_name")} 的表情改为{expression}。", "low");
        }

        // Shot / camera.
        var shot = MatchTable(text, [
            ("大特写", "extreme_close_up"), ("中近景", "medium_close_up"), ("特写", "close_up"), ("近景", "close_up"),
            ("全景", "wide_action"), ("远景", "establishing"), ("建立镜头", "establishing"),
        ]);
        var camera = MatchTable(text, [
            ("俯拍", "high_angle"), ("俯视", "high_angle"), ("俯角", "high_angle"),
            ("仰拍", "low_angle"), ("仰视", "low_angle"), ("仰角", "low_angle"),
            ("倾斜", "dutch_angle"), ("斜角", "dutch_angle"),
            ("越肩", "over_shoulder"), ("平视", "eye_level"),
        ]);
        if (shot != null || camera != null)
        {
            var payload = new Dictionary<string, object?> { ["shot_type"] = shot, ["camera_angle"] = camera };
            var summary = $"把格 {panel.Number("reading_order")} 的{(shot != null ? $"景别→{shot}" : "")}{(shot != null && camera != null ? "、" : "")}{(camera != null ? $"镜头角度→{camera}" : "")}。";
            return DirectorPlanResult.Command(
                Envelope(page, panel.Text("id"), null, null, "panel", panel.Number("version"), "update_panel_shot", payload, text, retryOfCommandId),
                "镜头景别", $"格 {panel.Number("reading_order")}", summary, "medium");
        }

        if (selection == null)
            return DirectorPlanResult.Clarify("这条指令还没有明确目标。请点击下方作用域芯片，或换一种写法（如：第 3 格改成近景、改成 6 格、台词改成「……」）",
                PanelOptions(panels));
        return DirectorPlanResult.Unsupported("规则桩无法把这条指令编成白名单命令。当前支持：格的景别/镜头、气泡台词改写、入镜角色增删、角色表情、场景天气/时间、整页格数。分镜字段也可以在分镜编辑器直接修改。");
    }

    private static List<(string, string?, string)> PanelOptions(List<JsonElement> panels) =>
        [("page", null, "整页"), .. panels.OrderBy(p => p.Number("reading_order")).Select(p => ("panel", p.Text("id"), $"格 {p.Number("reading_order")}"))];

    private static int? MatchPanelNumber(string text)
    {
        var match = System.Text.RegularExpressions.Regex.Match(text, @"第\s*([1-8]|[一二三四五六七八])\s*格");
        if (!match.Success) return null;
        return match.Groups[1].Value switch
        {
            "一" => 1, "二" => 2, "三" => 3, "四" => 4, "五" => 5, "六" => 6, "七" => 7, "八" => 8, var n => int.Parse(n),
        };
    }

    private static DirectorPlanResult ResolvePanel(List<JsonElement> panels, DirectorScope? selection, int? number, string text)
    {
        if (number is { } index)
        {
            var panel = panels.FirstOrDefault(p => p.Number("reading_order") == index);
            if (panel.ValueKind == JsonValueKind.Object) return CommandPanel(panel);
            return DirectorPlanResult.Unsupported($"第 {index} 格不存在");
        }
        if (selection?.Kind == "panel" || selection?.Kind == "dialogue")
        {
            var panel = panels.FirstOrDefault(p => p.Text("id") == selection.PanelId);
            if (panel.ValueKind == JsonValueKind.Object) return CommandPanel(panel);
        }
        if (panels.Count == 0)
            return DirectorPlanResult.Blocked("当前页还没有分镜格，请先完成分镜");
        return DirectorPlanResult.Clarify("请先点击一个格芯片（或在指令里写明「第 N 格」）再下指令", PanelOptions(panels));
    }

    private static DirectorPlanResult CommandPanel(JsonElement panel) =>
        DirectorPlanResult.Command(panel, "", "", "", "");

    private static DirectorPlanResult ResolveDialogue(JsonElement panel, DirectorScope? selection, string text, List<JsonElement> panels)
    {
        var dialogues = panel.Array("dialogues");
        if (selection?.Kind == "dialogue")
        {
            var found = dialogues.FirstOrDefault(d => d.Text("id") == selection.DialogueId);
            if (found.ValueKind == JsonValueKind.Object) return CommandPanel(found);
        }
        var nth = System.Text.RegularExpressions.Regex.Match(text, @"第\s*([1-8])\s*(?:句|气泡|个气泡)");
        if (nth.Success)
        {
            var index = int.Parse(nth.Groups[1].Value) - 1;
            if (index < dialogues.Count) return CommandPanel(dialogues[index]);
        }
        if (dialogues.Count == 1) return CommandPanel(dialogues[0]);
        if (dialogues.Count == 0) return DirectorPlanResult.Unsupported($"格 {panel.Number("reading_order")} 没有气泡");
        return DirectorPlanResult.Clarify($"格 {panel.Number("reading_order")} 有 {dialogues.Count} 个气泡，请点击气泡芯片确认目标",
            dialogues.Select(d => ("dialogue", d.Text("id"), $"格{panel.Number("reading_order")}·气泡{d.Number("reading_order")}")).ToList());
    }

    private static DirectorPlanResult ResolveCharacter(List<JsonElement> characters, string rawName, DirectorScope? selection, List<JsonElement> panels)
    {
        var name = rawName.Trim('，', ' ', '。', '的');
        if (selection?.Kind == "character")
        {
            var bySelection = characters.FirstOrDefault(c => c.Text("id") == selection.CharacterId);
            if (bySelection.ValueKind == JsonValueKind.Object) return CommandPanel(bySelection);
        }
        var matches = characters.Where(c => name.Contains(c.Text("primary_name"), StringComparison.Ordinal)
            || c.Array("aliases").Any(a => name.Contains(a.ToString(), StringComparison.Ordinal))).ToList();
        if (matches.Count == 1) return CommandPanel(matches[0]);
        if (matches.Count > 1)
            return DirectorPlanResult.Clarify("指令中匹配到多名角色，必须点击角色芯片确认目标",
                characters.Select(c => ("character", c.Text("id"), c.Text("primary_name"))).ToList());
        if (characters.Count == 1) return CommandPanel(characters[0]);
        if (characters.Count == 0) return DirectorPlanResult.Blocked("当前页分镜没有入镜角色");
        return DirectorPlanResult.Clarify("页上有多名角色，请点击角色芯片确认目标",
            characters.Select(c => ("character", c.Text("id"), c.Text("primary_name"))).ToList());
    }

    private static string? MatchTable(string text, (string Keyword, string Value)[] table, string[]? removeWords = null, string? removalValue = null)
    {
        if (removeWords != null && removalValue != null && removeWords.Any(text.Contains))
            foreach (var (keyword, value) in table)
                if (text.Contains(keyword)) return removalValue;
        foreach (var (keyword, value) in table)
            if (text.Contains(keyword)) return value;
        return null;
    }

    private static object Envelope(JsonElement page, string? panelId, string? dialogueId, string? sceneId,
        string scope, int version, string operation, Dictionary<string, object?> payload,
        string utterance, string? retryOfCommandId)
    {
        var target = new Dictionary<string, object?>
        {
            ["project_id"] = page.Text("project_id"),
            ["page_id"] = page.Text("id"),
        };
        if (panelId != null) target["panel_id"] = panelId;
        if (dialogueId != null) target["dialogue_id"] = dialogueId;
        if (sceneId != null) target["scene_id"] = sceneId;
        return new Dictionary<string, object?>
        {
            ["schema_version"] = 1,
            ["command_id"] = $"cmd-{Guid.NewGuid():N}",
            ["command_group_id"] = $"grp-{Guid.NewGuid():N}",
            ["created_at"] = DateTimeOffset.UtcNow.ToString("yyyy-MM-ddTHH:mm:ss.fffzzz"),
            ["target"] = target,
            ["expected_version"] = new Dictionary<string, object?> { ["scope"] = scope, ["value"] = version },
            ["retry_of_command_id"] = retryOfCommandId,
            ["operation"] = operation,
            ["payload"] = payload,
            ["source"] = new Dictionary<string, object?>
            {
                ["user_prompt"] = utterance,
                ["reference_asset_ids"] = Array.Empty<string>(),
                ["model"] = null,
                ["raw_output_id"] = RawOutputId,
            },
        };
    }
}
