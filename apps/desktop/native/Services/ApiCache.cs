using System.Collections.Concurrent;
using System.Net.Http;
using System.Text.Json;
using System.Windows;

namespace MangaFlow.Native.Services;

/// <summary>
/// Minimal React-Query-style cache: keyed entries with dedup, refresh and prefix
/// invalidation. Views subscribe to entry changes and re-render; invalidation of a
/// prefix triggers active views to refetch their subscriptions.
/// </summary>
public sealed class QueryEntry
{
    private JsonElement data;
    public Exception? Error { get; private set; }
    public bool Loading { get; private set; }
    public DateTime FetchedAt { get; private set; }
    public bool Stale { get; private set; }
    public event Action? Changed;

    public JsonElement Data => data;

    public bool HasData => data.ValueKind != JsonValueKind.Undefined;

    public QueryEntry Snapshot(JsonElement value)
    {
        data = value;
        Error = null;
        Stale = false;
        FetchedAt = DateTime.Now;
        Changed?.Invoke();
        return this;
    }

    public void Begin() { Loading = true; Changed?.Invoke(); }
    public void Fail(Exception error) { Loading = false; Error = error; Changed?.Invoke(); }
    public void Complete(JsonElement value)
    {
        Loading = false;
        data = value;
        Error = null;
        Stale = false;
        FetchedAt = DateTime.Now;
        Changed?.Invoke();
    }
    public void MarkStale() { Stale = true; Changed?.Invoke(); }
    internal void ClearLoading() { Loading = false; Changed?.Invoke(); }
}

public sealed class ApiCache
{
    private readonly ConcurrentDictionary<string, QueryEntry> entries = new();
    private readonly ConcurrentDictionary<string, Task> inflight = new();
    public event Action<string>? Invalidated;

    public QueryEntry Entry(string key) => entries.GetOrAdd(key, _ => new QueryEntry());

    /// <summary>Fetch into the entry; concurrent callers share one in-flight request.</summary>
    public async Task<QueryEntry> FetchAsync(ApiClient api, string key, string path,
        HttpMethod? method = null, object? body = null, CancellationToken cancellation = default)
    {
        var entry = Entry(key);
        if (inflight.TryGetValue(key, out var running))
        {
            await running.ConfigureAwait(false);
            return entry;
        }
        var task = FetchCore(api, entry, path, method, body, cancellation);
        if (!inflight.TryAdd(key, task))
        {
            // Another caller won the race; share its request instead of duplicating.
            await inflight[key].ConfigureAwait(false);
            return entry;
        }
        try
        {
            entry.Begin();
            await task.ConfigureAwait(false);
        }
        finally
        {
            // Remove only our own task: a later caller may have replaced the slot.
            ((ICollection<KeyValuePair<string, Task>>)inflight).Remove(new KeyValuePair<string, Task>(key, task));
        }
        return entry;
    }

    private static async Task FetchCore(ApiClient api, QueryEntry entry, string path,
        HttpMethod? method, object? body, CancellationToken cancellation)
    {
        try
        {
            var result = await api.SendOptionalAsync(path, method, body, cancellation).ConfigureAwait(false);
            entry.Complete(result ?? default);
        }
        catch (OperationCanceledException)
        {
            entry.ClearLoading();
        }
        catch (Exception error)
        {
            entry.Fail(error);
        }
    }

    /// <summary>Invalidate every key starting with the prefix; active views refetch.</summary>
    public void Invalidate(string prefix)
    {
        foreach (var (key, entry) in entries)
            if (key.StartsWith(prefix, StringComparison.Ordinal)) entry.MarkStale();
        Invalidated?.Invoke(prefix);
    }

    public void Invalidate(params string[] prefixes)
    {
        foreach (var prefix in prefixes) Invalidate(prefix);
    }
}

/// <summary>
/// Shared per-window context handed to every workspace view on activation.
/// Carries the API client, cache, current project identity and navigation hooks.
/// </summary>
public sealed class WorkspaceContext
{
    public required ApiClient Api { get; init; }
    public required ApiCache Cache { get; init; }
    public required WorkspaceState State { get; init; }
    public required Window Window { get; init; }
    public required Func<string, string, Task> NavigateSection;  // (section, query)
    public required Func<Task> OpenDashboard;
    public ProjectItem? Project;
    public string ProjectId => Project?.Id ?? "";

    public Task<JsonElement> GetAsync(string path, CancellationToken cancellation = default) =>
        Api.SendAsync(path, cancellation: cancellation);

    public async Task<JsonElement?> TryGetAsync(string path, CancellationToken cancellation = default)
    {
        try { return await Api.SendOptionalAsync(path, cancellation: cancellation); }
        catch (Exception) when (!cancellation.IsCancellationRequested) { return null; }
    }

    public void ShowError(Exception error, string fallback = "操作未完成")
    {
        var message = error is OperationCanceledException or TimeoutException
            ? "请求超时，请检查本地服务后重试。"
            : error.Message;
        if (message.Length == 0) message = fallback;
        State.Error = message;
        _ = FlashStatusAsync($"操作失败：{message.Split('\n')[0]}");
    }

    private async Task FlashStatusAsync(string message)
    {
        State.Status = message;
        await Task.Delay(60);
    }
}
