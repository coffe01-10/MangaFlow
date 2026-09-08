using System.Net.Http;
using System.Text.Json;

namespace MangaFlow.Native.Services;

/// <summary>
/// Global queue dock domain (web QueueDock + useJobsWorkspace dock query). Reads the
/// active-jobs list (archived=false) for every workspace section and owns cancel/retry
/// for the latest job. Every response is pinned to the project identity captured before
/// the await: a late reply for another project or page is dropped, never painted.
/// Mutations are deduplicated per job id and never retried automatically.
/// </summary>
public sealed class DockQueue
{
    private readonly ApiClient api;
    private readonly WorkspaceState state;
    private readonly HashSet<string> pending = [];
    // Only one read per project may be in flight; a read for another project runs alongside
    // and its reply is dropped by the identity check (project switch must not wait 3s).
    private string? readingProject;
    private bool rereadRequested;
    private bool abandoned;

    public DockQueue(ApiClient api, WorkspaceState state)
    {
        this.api = api;
        this.state = state;
    }

    /// <summary>Latest job of the current project's active-jobs list (dock headline).</summary>
    public JobItem? Latest => state.DockJob;

    /// <summary>
    /// Detach on reconnect: the instance's in-flight writes must stop touching the shared
    /// state that a replacement DockQueue now owns (pending flags, notices, counters).
    /// </summary>
    public void Abandon() => abandoned = true;

    /// <summary>Drop dock data immediately (project switch / disconnect).</summary>
    public void Reset()
    {
        state.DockJob = null;
        state.WaitingJobs = 0;
        state.RunningJobs = 0;
        state.FailedJobs = 0;
        state.CompletedJobs = 0;
        state.DockTotal = 0;
        state.DockNotice = "";
        // Reconnect abandons in-flight actions whose cleanup will not run; without this
        // the shared pending flag would stay true and hide the dock actions forever.
        state.DockActionPending = false;
    }

    /// <summary>
    /// Refresh the dock from the active-jobs list. Responses arriving after a project
    /// switch are discarded by comparing the live project id with the captured one.
    /// A request that collides with an in-flight read of the same project schedules a
    /// follow-up read instead of being dropped (post-action/post-switch freshness).
    /// </summary>
    public async Task RefreshAsync(string projectId, CancellationToken cancellation)
    {
        if (abandoned || projectId.Length == 0) return;
        if (readingProject == projectId)
        {
            rereadRequested = true;
            return;
        }
        readingProject = projectId;
        try
        {
            var rows = await api.SendAsync(
                QueryBuilder.Build($"projects/{projectId}/jobs", ("archived", "false")),
                cancellation: cancellation);
            if (cancellation.IsCancellationRequested) return;
            // A late reply from the previous project must not repaint the dock of the
            // project the user switched to while the request was in flight.
            if (state.CurrentProject?.Id != projectId) return;
            Apply(rows);
        }
        catch (Exception) when (cancellation.IsCancellationRequested)
        {
            // Shutdown or reconnect: any exception surfaced by the aborted request is void.
        }
        catch (Exception)
        {
            // Reads must not spam error UI on transient poll failures; the next tick retries.
        }
        finally
        {
            if (readingProject == projectId) readingProject = null;
            // Only re-read for the project that is still current: a stale re-read would
            // cover the reading slot of the project the user switched to.
            if (rereadRequested && !abandoned && !cancellation.IsCancellationRequested &&
                state.CurrentProject?.Id == projectId)
            {
                rereadRequested = false;
                _ = RefreshAsync(projectId, cancellation);
            }
        }
    }

    private void Apply(JsonElement rows)
    {
        var jobs = rows.EnumerateArray().Select(JobItem.From).ToList();
        state.DockJob = jobs.FirstOrDefault();
        state.WaitingJobs = jobs.Count(j => j.State is "WAITING" or "QUEUED");
        state.RunningJobs = jobs.Count(j => j.Active && j.State is not ("WAITING" or "QUEUED"));
        state.FailedJobs = jobs.Count(j => j.State == "FAILED");
        state.CompletedJobs = jobs.Count(j => j.State == "COMPLETED");
        state.DockTotal = jobs.Count;
        // Success notices describe the pre-action state; once fresh data lands they are
        // stale. Failure notices stay until the user's next action.
        if (state.DockNotice is "任务已请求取消" or "任务已重新排队") state.DockNotice = "";
    }

    /// <summary>
    /// Cancel or retry the latest dock job. One request per job id at a time; the failure
    /// message surfaces in the dock and the caller's UI re-enables afterwards.
    /// </summary>
    public async Task ActAsync(JobItem job, string action, string projectId, CancellationToken cancellation)
    {
        if (abandoned || job.Id.Length == 0 || pending.Contains(job.Id)) return;
        var label = action == "cancel" ? "取消任务" : "重试任务";
        pending.Add(job.Id);
        state.DockActionPending = true;
        state.DockNotice = "";
        try
        {
            await api.SendAsync($"jobs/{job.Id}/{action}?project_id={projectId}", HttpMethod.Post,
                cancellation: cancellation);
            if (!Stale(projectId))
            {
                state.DockNotice = action == "cancel" ? "任务已请求取消" : "任务已重新排队";
                await RefreshAsync(projectId, cancellation);
            }
        }
        catch (Exception) when (cancellation.IsCancellationRequested)
        {
            // App shutdown or reconnect: leave the notice empty rather than a fake success.
        }
        catch (Exception error)
        {
            if (!Stale(projectId)) state.DockNotice = ErrorText(error, label);
        }
        finally
        {
            pending.Remove(job.Id);
            if (!abandoned) state.DockActionPending = false;
        }
    }

    /// <summary>An action result is stale once its project was switched away or this queue was replaced.</summary>
    private bool Stale(string projectId) => abandoned || state.CurrentProject?.Id != projectId;

    private static string ErrorText(Exception error) => error switch
    {
        OperationCanceledException or TimeoutException => "请求超时，提交操作可能已被服务接收，请先刷新确认。",
        HttpRequestException => "无法连接本地服务。",
        _ => error.Message,
    };

    private static string ErrorText(Exception error, string label) => $"{label}失败：{ErrorText(error)}";
}
