using System.Reflection;
using System.Text.Json;
using MangaFlow.Native;
using MangaFlow.Native.Views;

// P2-1 回归：WorkflowView 的连线四契约此前只活在 TryConnect 的 UI 事件链里，
// 零测试。校验已提取为纯函数 WorkflowView.CanConnect + WorkflowView.EdgeId
// （对齐 web workflow-studio.tsx 的 validConnection+connect 与后端
// catalog.py 的 _edge id 契约）。本文件对每条规则构造正反用例，并用真实
// WorkflowView 实例（不 Activate、零网络）验证 TryConnect/BuildGraph 走同一契约。
//
// 注册说明：本仓库的发现机制是 NativeInteractionChecks.RunIsolated 里的手动
// 调用列表（--render STA 链，Application 已带 Theme 资源）。因文件级互斥本文件
// 未自行接线——需在该列表加入 NativeWorkflowConnectionChecks.Run()。
// 直接 new WorkflowView() 依赖 Application.Current 的 Theme 资源（Kit.Act），
// 所以必须运行在 --render 的 STA 线程上，而不是默认 STA 主线程。
internal static class NativeWorkflowConnectionChecks
{
    private const BindingFlags All = BindingFlags.Static | BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic;

    public static void Run()
    {
        PureContractChecks();
        EdgeIdChecks();
        TryConnectWiringChecks();
        DuplicateConfigAliasingChecks();
        Console.WriteLine("PASS: workflow connection rules (data_type match / no self-loop / duplicate port pair / deterministic edge id) enforced end to end");
    }

    // ── 四条契约的纯函数正反用例 ──
    private static void PureContractChecks()
    {
        var canConnect = typeof(WorkflowView).GetMethod("CanConnect", All) ?? throw Missing("CanConnect");
        bool Connect(string sn, string sp, string st, string tn, string tp, string tt,
            params (string, string, string, string)[] existing) =>
            (bool)canConnect.Invoke(null, [sn, sp, st, tn, tp, tt, existing])!;

        // ① data_type 相同才允许：正例（同 text）通过；反例（text→json）拒绝。
        Require(Connect("parse", "script", "text", "draw", "script_in", "text"),
            "same data_type connections must be accepted");
        Require(!Connect("parse", "script", "text", "draw", "style_in", "json"),
            "mixed data_type connections must be rejected");

        // ② 禁自连：同节点 source==target 拒绝；不同节点（①正例）通过。
        Require(!Connect("parse", "script", "text", "parse", "chapter", "text"),
            "self connections must be rejected");

        // ③ 四元组判重：完全相同的端口对拒绝；同源不同目标端口、反方向不误伤。
        Require(!Connect("parse", "script", "text", "draw", "script_in", "text", ("parse", "script", "draw", "script_in")),
            "duplicate port pair must be rejected");
        Require(Connect("parse", "script", "text", "draw", "other_in", "text", ("parse", "script", "draw", "script_in")),
            "a different target port is not a duplicate");
        Require(Connect("draw", "page", "image", "parse", "chapter", "image", ("parse", "script", "draw", "script_in")),
            "the reversed direction is not a duplicate");

        // ④ 四元组必须完整（对应 web connect 对 sourceHandle/targetHandle 的真值检查）。
        Require(!Connect("", "script", "text", "draw", "script_in", "text"), "empty source node id must be rejected");
        Require(!Connect("parse", "", "text", "draw", "script_in", "text"), "empty source port id must be rejected");
        Require(!Connect("parse", "script", "text", "draw", "", "text"), "empty target port id must be rejected");
    }

    // ── 边 id 契约：与后端 catalog.py `${source}:${source_port}-${target}:${target_port}` 逐字一致 ──
    private static void EdgeIdChecks()
    {
        var edgeId = typeof(WorkflowView).GetMethod("EdgeId", All) ?? throw Missing("EdgeId");
        var expected = "parse-1:script-draw-1:script_in";
        Require((string)edgeId.Invoke(null, ["parse-1", "script", "draw-1", "script_in"])! == expected,
            "EdgeId must match the backend catalog edge id format");

        var edgeKey = typeof(WorkflowView).GetMethod("EdgeKey", All) ?? throw Missing("EdgeKey");
        var edge = Activator.CreateInstance(EdgeType(), ["parse-1", "script", "draw-1", "script_in"])!;
        Require((string)edgeKey.Invoke(null, [edge])! == expected,
            "EdgeKey must produce the same deterministic id as EdgeId");
    }

    // ── TryConnect/BuildGraph 走同一契约（真实 WorkflowView，无网络、不 Activate）──
    private static void TryConnectWiringChecks()
    {
        var view = new WorkflowView();
        var viewType = typeof(WorkflowView);
        try
        {
            // 节点类型定义须与后端 catalog 形状一致（inputs/outputs 带 data_type）。
            // JsonDocument 刻意不释放：节点持有的 JsonElement 是它的视图。
            var parseType = JsonDocument.Parse(
                """{"type":"agent.parse","inputs":[{"id":"chapter","label":"章节","data_type":"text"}],"outputs":[{"id":"script","label":"剧本","data_type":"text"}]}""");
            var drawType = JsonDocument.Parse(
                """{"type":"generator.page","inputs":[{"id":"script_in","label":"剧本","data_type":"text"},{"id":"style_in","label":"风格","data_type":"json"}],"outputs":[{"id":"page","label":"页面","data_type":"image"}]}""");
            var create = NodeType().GetMethod("Create", All) ?? throw Missing("WorkflowNode.Create");
            var parse = create.Invoke(null, ["parse-1", "agent.parse", "剧情解析", (10.0, 20.0), parseType.RootElement])!;
            var draw = create.Invoke(null, ["draw-1", "generator.page", "成页生成", (340.0, 20.0), drawType.RootElement])!;
            var nodes = (System.Collections.IList)viewType.GetField("nodes", All)!.GetValue(view)!;
            nodes.Add(parse);
            nodes.Add(draw);
            var edges = (System.Collections.IList)viewType.GetField("edges", All)!.GetValue(view)!;
            var tryConnect = viewType.GetMethod("TryConnect", All) ?? throw Missing("TryConnect");
            bool Try(object source, object target) => (bool)tryConnect.Invoke(view, [source, target])!;

            // ① 反例：text 输出连 json 输入必须拒绝且不留半成品边。
            Require(!Try(Port(parse, "script"), Port(draw, "style_in")), "TryConnect accepted mismatched data_type");
            Require(edges.Count == 0, "rejected connection still created an edge");

            // ② 反例：同节点自连必须拒绝。
            Require(!Try(Port(parse, "script"), Port(parse, "chapter")), "TryConnect accepted a self connection");
            Require(edges.Count == 0, "self connection still created an edge");

            // ① 正例：text → text 建边成功。
            Require(Try(Port(parse, "script"), Port(draw, "script_in")), "TryConnect rejected a valid same-type connection");
            Require(edges.Count == 1, "valid connection did not create exactly one edge");

            // ③ 反例：同一端口对再次连接必须拒绝（确定性 id 下重复即后端 422）。
            Require(!Try(Port(parse, "script"), Port(draw, "script_in")), "TryConnect accepted a duplicate port pair");
            Require(edges.Count == 1, "duplicate connection added a second edge");

            // ② 边 id 契约：BuildGraph 的 edges[].id 与四元组字段逐字一致。
            var graph = (Dictionary<string, object?>)viewType.GetMethod("BuildGraph", All)!.Invoke(view, null)!;
            var rows = (List<Dictionary<string, object?>>)graph["edges"]!;
            Require(rows.Count == 1, "BuildGraph lost the connected edge");
            Require((string)rows[0]["id"]! == "parse-1:script-draw-1:script_in",
                "BuildGraph edge id diverged from the backend catalog contract");
            Require((string)rows[0]["source_node"]! == "parse-1" && (string)rows[0]["source_port"]! == "script"
                && (string)rows[0]["target_node"]! == "draw-1" && (string)rows[0]["target_port"]! == "script_in",
                "BuildGraph edge endpoints diverged from the connection that created it");
        }
        finally
        {
            // TryConnect 成功路径武装了 800ms 防抖计时器：停掉，避免泄漏到后续检查。
            (viewType.GetField("autosave", All)!.GetValue(view) as System.Timers.Timer)?.Stop();
        }
    }

    // ── #340: DuplicateSelected 的 config 不得与原节点共享（检查器编辑就地写
    // Config[key]，引用共享会让克隆编辑静默改写原节点并被自动保存双写）──
    // 零网络：不 Activate，节点经 WorkflowNode.Create 直接入列（与上方检查同模式）。
    private static void DuplicateConfigAliasingChecks()
    {
        var view = new WorkflowView();
        var viewType = typeof(WorkflowView);
        try
        {
            var agentType = JsonDocument.Parse(
                """{"type":"agent.parse","display_name":"解析","category":"AGENT","inputs":[],"outputs":[]}""");
            // Restore/DuplicateSelected 都按 nodeTypes 找类型定义补端口，喂入同形状数据。
            viewType.GetField("nodeTypes", All)!.SetValue(view, new List<JsonElement> { agentType.RootElement });
            var nodes = (System.Collections.IList)viewType.GetField("nodes", All)!.GetValue(view)!;
            var original = CreateNode(view, "orig-1", "agent.parse");
            SetConfig(original, "temperature", 0.4);
            SetConfig(original, "locked", true);

            Select(view, original);
            Duplicate(view);
            Require(nodes.Count == 2, "复制未生成克隆节点");
            var clone = nodes.Cast<object>().Single(node => !ReferenceEquals(node, original));
            // 失败构造①：字典引用共享（原缺陷的直接形态）。
            Require(!ReferenceEquals(Config(original), Config(clone)),
                "克隆的 config 字典与原节点共享引用：编辑克隆会改写原节点");
            Require(JsonSerializer.Serialize(Config(original)) == JsonSerializer.Serialize(Config(clone)),
                "深拷后的克隆 config 内容应与原节点一致（只隔离引用，不改值）");

            // 失败构造②：克隆上改温度/锁定/备注（检查器同款写回键），原节点必须纹丝不动。
            SetConfig(clone, "temperature", 1.5);
            SetConfig(clone, "locked", false);
            SetConfig(clone, "notes", "克隆备注");
            Require(AsDouble(Config(original), "temperature") == 0.4, "编辑克隆的温度改写了原节点 config");
            Require(Config(original)["locked"] is true, "编辑克隆的锁定开关改写了原节点 config");
            Require(Config(original).GetValueOrDefault("notes") is "" or null, "编辑克隆的备注改写了原节点 config");
            Require(AsDouble(Config(clone), "temperature") == 1.5 && Config(clone)["locked"] is false,
                "克隆自身的编辑未生效");
        }
        finally
        {
            StopAutosave(view);
        }
    }

    private static object CreateNode(WorkflowView view, string id, string type)
    {
        // JsonDocument 刻意不释放：节点持有的 JsonElement 是它的视图。
        var definition = JsonDocument.Parse("{}").RootElement;
        var create = NodeType().GetMethod("Create", All) ?? throw Missing("WorkflowNode.Create");
        var node = create.Invoke(null, [id, type, type, (10.0, 20.0), definition])!;
        var nodes = (System.Collections.IList)typeof(WorkflowView).GetField("nodes", All)!.GetValue(view)!;
        nodes.Add(node);
        return node;
    }

    private static void Select(WorkflowView view, object node) =>
        typeof(WorkflowView).GetMethod("Select", All)!.Invoke(view, [node]);

    private static void Duplicate(WorkflowView view) =>
        typeof(WorkflowView).GetMethod("DuplicateSelected", All)!.Invoke(view, null);

    private static Dictionary<string, object?> Config(object node) =>
        (Dictionary<string, object?>)node.GetType().GetProperty("Config")!.GetValue(node)!;

    private static void SetConfig(object node, string key, object? value) =>
        node.GetType().GetMethod("SetConfig", All)!.Invoke(node, [key, value]);

    private static JsonElement ConfigElement(object node) =>
        (JsonElement)node.GetType().GetProperty("ConfigElement")!.GetValue(node)!;

    private static double AsDouble(Dictionary<string, object?> config, string key) =>
        config.TryGetValue(key, out var value) && value is JsonElement { ValueKind: JsonValueKind.Number } number
            ? number.GetDouble() : value is double parsed ? parsed : double.NaN;

    private static void StopAutosave(WorkflowView view) =>
        (typeof(WorkflowView).GetField("autosave", All)!.GetValue(view) as System.Timers.Timer)?.Stop();

    private static object Port(object node, string id)
    {
        var ports = (System.Collections.IEnumerable)node.GetType().GetField("Ports", All)!.GetValue(node)!;
        foreach (var port in ports)
            if ((string)port.GetType().GetProperty("Id")!.GetValue(port)! == id)
                return port;
        throw new Exception("fixture port missing on node: " + id);
    }

    private static Type NodeType() => typeof(WorkflowView).GetNestedType("WorkflowNode", BindingFlags.NonPublic) ?? throw Missing("WorkflowNode");
    private static Type EdgeType() => typeof(WorkflowView).GetNestedType("WorkflowEdge", BindingFlags.NonPublic) ?? throw Missing("WorkflowEdge");
    private static Exception Missing(string member) => new("反射入口缺失：WorkflowView." + member);
    private static void Require(bool condition, string message) { if (!condition) throw new Exception(message); }
}
