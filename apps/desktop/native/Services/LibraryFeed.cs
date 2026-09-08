using System.Text.Json;

namespace MangaFlow.Native.Services;

public sealed record LibraryFilter(string ProjectId, string? Chapter = null, string? Character = null,
    string? Kind = null, string? Model = null, string? Resolution = null, bool Favorite = false,
    DateTime? From = null, DateTime? To = null)
{
    public string Path(string? cursor)
    {
        if (From?.Date > To?.Date) throw new ArgumentException("素材结束日期不能早于开始日期。");
        return QueryBuilder.Build($"projects/{ProjectId}/library", ("group_by", "batch"), ("limit", 30),
            ("chapter_id", Chapter), ("character_id", Character), ("generation_kind", Kind),
            ("model_alias", Model), ("resolution", Resolution), ("favorite", Favorite ? "true" : null),
            ("date_from", From?.ToString("yyyy-MM-dd") + (From == null ? "" : "T00:00:00Z")),
            ("date_to", To?.ToString("yyyy-MM-dd") + (To == null ? "" : "T23:59:59Z")), ("cursor", cursor));
    }
}

/// <summary>The current page cursor is distinct from the server's next cursor. Navigation commits only after success.</summary>
public sealed class LibraryFeed
{
    private int revision;
    private readonly List<string?> history = [];
    public LibraryFilter? Filter { get; private set; }
    public JsonElement Data { get; private set; }
    public string? Cursor { get; private set; }
    public string? NextCursor => Data.TextOrNull("next_cursor");
    public bool Loading { get; private set; }
    public bool CanPrevious => !Loading && history.Count > 0;
    public bool CanNext => !Loading && NextCursor != null;
    public int PageNumber => history.Count + 1;

    public void Reset()
    {
        revision++; Loading = false; Filter = null; Cursor = null; Data = default; history.Clear();
    }

    public Task<bool> LoadAsync(ApiClient api, LibraryFilter filter, CancellationToken token)
    {
        if (Filter != filter) { Reset(); Filter = filter; }
        return Read(api, Cursor, 0, token);
    }
    public Task<bool> NextAsync(ApiClient api, CancellationToken token) =>
        CanNext ? Read(api, NextCursor, 1, token) : Task.FromResult(false);
    public Task<bool> PreviousAsync(ApiClient api, CancellationToken token) =>
        CanPrevious ? Read(api, history[^1], -1, token) : Task.FromResult(false);
    public Task<bool> RefreshAsync(ApiClient api, CancellationToken token)
    {
        revision++; Loading = false;
        return Read(api, Cursor, 0, token);
    }

    private async Task<bool> Read(ApiClient api, string? cursor, int direction, CancellationToken token)
    {
        if (Loading || Filter == null) return false;
        var requested = ++revision;
        var path = Filter.Path(cursor);
        Loading = true;
        try
        {
            var data = await api.SendAsync(path, cancellation: token);
            if (token.IsCancellationRequested || revision != requested) return false;
            if (direction > 0) history.Add(Cursor);
            else if (direction < 0) history.RemoveAt(history.Count - 1);
            Data = data; Cursor = cursor;
            return true;
        }
        catch (OperationCanceledException) when (token.IsCancellationRequested) { return false; }
        catch (Exception) when (revision != requested || token.IsCancellationRequested) { return false; }
        finally { if (requested == revision) Loading = false; }
    }
}
