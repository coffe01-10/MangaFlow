using System.ComponentModel;
using System.Runtime.CompilerServices;
using System.Text.Json;

namespace MangaFlow.Native;

public static class JsonFields
{
    public static string Text(this JsonElement json, string name, string fallback = "") =>
        json.TryGetProperty(name, out var value) && value.ValueKind != JsonValueKind.Null
            ? value.ToString() : fallback;
    public static int Number(this JsonElement json, string name) =>
        json.TryGetProperty(name, out var value) && value.TryGetInt32(out var number) ? number : 0;
}

public record ProjectItem(string Id, string Name, string Summary, int Pending, int Failed)
{
    public int PageCount { get; init; }
    public int SelectedPages { get; init; }
    public string ModeLabel { get; init; } = "";
    public string Resolution { get; init; } = "";
    public int Progress => PageCount <= 0 ? 0 : Math.Clamp((int)Math.Round(100d * SelectedPages / PageCount), 0, 100);
    public string CoverTitle
    {
        get
        {
            var letters = System.Globalization.StringInfo.GetTextElementEnumerator(Name);
            var visible = new List<string>();
            while (visible.Count < 8 && letters.MoveNext()) visible.Add(letters.GetTextElement());
            return string.Join("\n", visible);
        }
    }
    public string Initial => string.IsNullOrEmpty(Name) ? "漫" : Name[..1];
    public static ProjectItem From(JsonElement row)
    {
        var p = row.GetProperty("project");
        return new(p.Text("id"), p.Text("name"),
            $"{row.Number("chapter_count")} 章 · {row.Number("page_count")} 页 · {row.Number("selected_page_count")} 已采用",
            row.Number("pending_job_count"), row.Number("failed_job_count"))
        {
            PageCount = row.Number("page_count"), SelectedPages = row.Number("selected_page_count"),
            Resolution = p.Text("default_resolution"),
            ModeLabel = p.Text("workflow_mode") switch
            {
                "SEMI_AUTO" => "半自动", "AUTO" => "自动", "MANUAL" => "手动", var mode => mode,
            },
        };
    }
}

public record ChapterItem(string Id, string Title, string Summary)
{
    public static ChapterItem From(JsonElement c) => new(c.Text("id"), c.Text("title"),
        $"{c.Number("source_character_count"):N0} 字 · {c.Number("page_count")} 页");
}

public record JobItem(string Id, string Name, string State, string StatusLabel, int Progress,
    string Detail, bool CanCancel, bool CanRetry)
{
    public static JobItem From(JsonElement j)
    {
        var state = j.Text("status");
        var label = state switch
        {
            "WAITING" => "等待中", "QUEUED" => "排队中", "RUNNING" => "进行中",
            "COMPLETED" => "已完成", "FAILED" => "失败", "CANCELLED" => "已取消",
            "NEEDS_REVIEW" => "待复核", _ => state,
        };
        var name = j.Text("job_type") switch
        {
            "PARSE_SOURCE" => "解析原作", "GENERATE_PAGE" => "生成漫画页面",
            "INSPECT_PAGE" => "检查页面", "GENERATE_CHARACTER" => "生成人物参考",
            var type => type,
        };
        return new(j.Text("id"), name, state, label, Math.Clamp(j.Number("progress"), 0, 100),
            j.Text("error_message", "任务由本地服务执行"),
            state is "WAITING" or "QUEUED" or "RUNNING",
            (state is "FAILED" or "NEEDS_REVIEW" or "WAITING") && j.Number("attempt_count") < j.Number("max_attempts"));
    }
}

public class Observable : INotifyPropertyChanged
{
    public event PropertyChangedEventHandler? PropertyChanged;
    protected void Changed([CallerMemberName] string? name = null) =>
        PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(name));
    protected void Set<T>(ref T field, T value, [CallerMemberName] string? name = null)
    {
        if (EqualityComparer<T>.Default.Equals(field, value)) return;
        field = value;
        Changed(name);
    }
}
