using System.Text.Json;

namespace MangaFlow.Native.Services;

public sealed record UsageFilter(DateTimeOffset Since, DateTimeOffset Until,
    string? ProjectId = null, string? Provider = null, string? ModelId = null, string? Channel = null)
{
    public void Validate()
    {
        if (Until <= Since) throw new ArgumentException("结束日期不能早于开始日期。");
        if (Channel is not (null or "" or "HTTP_API" or "CLI")) throw new ArgumentException("不支持的调用通道。");
    }

    public string SummaryPath()
    {
        Validate();
        // A10：channel 与明细同一契约——后端 get_usage_summary 已支持，汇总口径
        // 必须跟随所选通道，否则统计卡/趋势/预算与分页明细描述不同的数据集
        // （web usageSummary 同样透传 channel）。空/未选时省略参数。
        return QueryBuilder.Build("usage/summary", ("from", Since.ToUniversalTime().ToString("O")),
            ("to", Until.ToUniversalTime().ToString("O")), ("project_id", ProjectId), ("provider", Provider),
            ("model_id", ModelId), ("channel", Channel));
    }

    /// <summary>A12：维度（供应商/模型）选项的独立来源——不带任何筛选的汇总，
    /// 对应 web usage-facets 查询。选项不随当前筛选增减，供应商联动模型。</summary>
    public static string FacetsPath() => "usage/summary";

    public string AttemptsPath(string? cursor = null)
    {
        Validate();
        return QueryBuilder.Build("usage/attempts", ("since", Since.ToUniversalTime().ToString("O")),
            ("until", Until.ToUniversalTime().ToString("O")), ("project_id", ProjectId), ("provider", Provider),
            ("model_id", ModelId), ("channel", Channel), ("cursor", cursor), ("limit", 50));
    }
}

/// <summary>One immutable filter snapshot for a complete keyset traversal.</summary>
public sealed class UsageAttemptFeed
{
    private int revision;
    private bool loadingMore;
    public UsageFilter? Filter { get; private set; }
    public List<JsonElement> Items { get; private set; } = [];
    public string? NextCursor { get; private set; }

    public void Reset()
    {
        revision++;
        Filter = null;
        Items = [];
        NextCursor = null;
        loadingMore = false;
    }

    public async Task<bool> LoadAsync(ApiClient api, UsageFilter filter, CancellationToken token)
    {
        filter.Validate();
        Reset();
        Filter = filter;
        var requested = revision;
        var page = await api.SendAsync(filter.AttemptsPath(), cancellation: token);
        if (token.IsCancellationRequested || requested != revision) return false;
        Items = page.Array("items").ToList();
        NextCursor = page.TextOrNull("next_cursor");
        return true;
    }

    public async Task<bool> LoadMoreAsync(ApiClient api, CancellationToken token)
    {
        if (loadingMore || Filter == null || NextCursor == null) return false;
        var requested = revision;
        var path = Filter.AttemptsPath(NextCursor);
        loadingMore = true;
        try
        {
            var page = await api.SendAsync(path, cancellation: token);
            if (token.IsCancellationRequested || requested != revision) return false;
            var ids = Items.Select(i => i.Text("id")).ToHashSet();
            Items.AddRange(page.Array("items").Where(i => ids.Add(i.Text("id"))));
            NextCursor = page.TextOrNull("next_cursor");
            return true;
        }
        finally { if (requested == revision) loadingMore = false; }
    }
}
