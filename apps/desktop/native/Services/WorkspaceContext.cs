using System.Text.Json;
using System.Windows;

namespace MangaFlow.Native.Services;

/// <summary>
/// Shared per-window context handed to every workspace view on activation.
/// Carries the API client, current project identity and navigation hooks.
/// #441: the inert request-cache layer (dedup + prefix invalidation that nothing
/// ever fed) was removed — freshness is owned by each mutator's explicit reload.
/// </summary>
public sealed class WorkspaceContext
{
    public required ApiClient Api { get; init; }
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
