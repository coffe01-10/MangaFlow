using System.Net;
using System.Net.Http;
using System.Net.Http.Json;
using System.Text.Json;
using System.Web;
using System.IO;

namespace MangaFlow.Native.Services;

public sealed class ApiClient : IDisposable
{
    private readonly HttpClient client;
    public string Origin { get; }

    public ApiClient(string origin, HttpMessageHandler? handler = null)
    {
        var uri = new Uri(origin, UriKind.Absolute);
        if (uri.Scheme != "http" || uri.Host != "127.0.0.1" || uri.Port <= 0 ||
            uri.AbsolutePath != "/" || uri.Query.Length > 0 || uri.Fragment.Length > 0 || uri.UserInfo.Length > 0)
            throw new ArgumentException("本地服务地址未通过校验");
        Origin = origin.TrimEnd('/');
        client = handler == null
            ? new HttpClient(new HttpClientHandler { UseProxy = false, AllowAutoRedirect = false })
            : new HttpClient(handler);
        client.BaseAddress = new Uri(uri, "/api/v1/");
        client.Timeout = TimeSpan.FromSeconds(30);
    }

    public async Task<JsonElement> SendAsync(string path, HttpMethod? method = null, object? body = null,
        CancellationToken cancellation = default)
    {
        var root = await SendOptionalAsync(path, method, body, cancellation).ConfigureAwait(false);
        return root is null || root.Value.ValueKind == JsonValueKind.Undefined
            ? throw new InvalidOperationException("服务返回了空响应")
            : root.Value;
    }

    public async Task<JsonElement?> SendOptionalAsync(string path, HttpMethod? method = null, object? body = null,
        CancellationToken cancellation = default)
    {
        Validate(path);
        using var request = new HttpRequestMessage(method ?? HttpMethod.Get, path);
        if (body != null) request.Content = JsonContent.Create(body);
        HttpResponseMessage response;
        string text;
        try
        {
            response = await client.SendAsync(request, cancellation).ConfigureAwait(false);
            text = await response.Content.ReadAsStringAsync(cancellation).ConfigureAwait(false);
        }
        // HttpClient reports its 30s timeout as a cancellation with an uncancelled caller
        // token. Translate those to TimeoutException so error UI can show them; genuine
        // caller cancellations (page deactivation) stay OperationCanceledException for
        // the views' silent-swallow filters.
        catch (OperationCanceledException) when (!cancellation.IsCancellationRequested)
        {
            throw new TimeoutException("请求超时，请检查本地服务后重试。");
        }
        if (!response.IsSuccessStatusCode)
        {
            ThrowResponseError(response, text);
            throw new InvalidOperationException("unreachable");  // ThrowResponseError always throws
        }
        if (string.IsNullOrWhiteSpace(text)) return default;
        using var document = JsonDocument.Parse(text);
        return document.RootElement.Clone();
    }

    public async Task<JsonElement> UploadAsync(string path, IReadOnlyDictionary<string, string> fields,
        (string Name, string FileName, string ContentType, byte[] Content) file, CancellationToken cancellation = default)
    {
        Validate(path);
        using var form = new MultipartFormDataContent();
        foreach (var (name, value) in fields)
            form.Add(new StringContent(value), name);
        var content = new ByteArrayContent(file.Content);
        content.Headers.ContentType = new System.Net.Http.Headers.MediaTypeHeaderValue(file.ContentType);
        form.Add(content, file.Name, file.FileName);
        using var request = new HttpRequestMessage(HttpMethod.Post, path) { Content = form };
        using var response = await client.SendAsync(request, cancellation).ConfigureAwait(false);
        var text = await response.Content.ReadAsStringAsync(cancellation).ConfigureAwait(false);
        if (!response.IsSuccessStatusCode)
        {
            ThrowResponseError(response, text, "上传失败");
            throw new InvalidOperationException("unreachable");  // ThrowResponseError always throws
        }
        using var document = JsonDocument.Parse(text);
        return document.RootElement.Clone();
    }

    // Stream to an owned sibling file, then replace the user's destination only after completion.
    // Media downloads stay streaming (ResponseHeadersRead) so a large PNG/ZIP never buffers
    // in memory, and a failed transfer (non-2xx, network drop) leaves the user's previous
    // file untouched instead of a silent half-written copy.
    public async Task SaveDownloadAsync(string path, string destination, CancellationToken cancellation = default)
    {
        using var timeout = CancellationTokenSource.CreateLinkedTokenSource(cancellation);
        timeout.CancelAfter(TimeSpan.FromMinutes(5));
        cancellation = timeout.Token;
        var relative = MediaPath(path);
        var target = Path.GetFullPath(destination);
        var temporary = Path.Combine(Path.GetDirectoryName(target)!, ".mangaflow-" + Guid.NewGuid().ToString("N") + ".download");
        var created = false;
        try
        {
            using var response = await client.GetAsync(relative, HttpCompletionOption.ResponseHeadersRead, cancellation).ConfigureAwait(false);
            if (!response.IsSuccessStatusCode)
            {
                // Non-2xx bodies are small JSON errors (e.g. export.png answers 409 with
                // the production blockers); surface the server detail like SendAsync.
                var text = await response.Content.ReadAsStringAsync(cancellation).ConfigureAwait(false);
                ThrowResponseError(response, text, "下载失败");
                throw new InvalidOperationException("unreachable");  // ThrowResponseError always throws
            }
            await using (var input = await response.Content.ReadAsStreamAsync(cancellation).ConfigureAwait(false))
            await using (var output = new FileStream(temporary, FileMode.CreateNew, FileAccess.Write, FileShare.None, 81920, FileOptions.Asynchronous))
            {
                created = true;
                await input.CopyToAsync(output, cancellation).ConfigureAwait(false);
                await output.FlushAsync(cancellation).ConfigureAwait(false);
            }
            cancellation.ThrowIfCancellationRequested();
            File.Move(temporary, target, overwrite: true);
            created = false;
        }
        finally { if (created) File.Delete(temporary); }
    }

    /// <summary>
    /// Shared non-2xx translation for every request path (JSON, upload, media download).
    /// Mirrors lib/api.ts request(): detail string → as-is; detail array (FastAPI 422
    /// loc/msg) → field-prefixed lines; detail object → its message. On top of the web
    /// baseline, structured blocker arrays (web describeActionError in generate-section:
    /// 409 payloads such as select-candidate / export.png carry {code, message, blockers})
    /// are appended so per-blocker recovery guidance is never dropped.
    /// </summary>
    private static void ThrowResponseError(HttpResponseMessage response, string text, string fallbackLabel = "请求失败")
    {
        var detail = $"{fallbackLabel}（{(int)response.StatusCode}）";
        try
        {
            using var error = JsonDocument.Parse(text);
            if (error.RootElement.ValueKind == JsonValueKind.Object &&
                error.RootElement.TryGetProperty("detail", out var value))
                detail = DescribeDetail(value, detail);
            else if (error.RootElement.ValueKind == JsonValueKind.String)
                detail = error.RootElement.GetString() ?? detail;
        }
        catch (JsonException) { }
        if (response.StatusCode == HttpStatusCode.Conflict)
            detail = "数据已变化或操作条件不满足。请刷新后重试。\n" + detail;
        throw new InvalidOperationException(detail.Length > 2000 ? detail[..2000] : detail);
    }

    private static string DescribeDetail(JsonElement value, string fallback)
    {
        switch (value.ValueKind)
        {
            case JsonValueKind.String:
                return value.GetString() ?? fallback;
            case JsonValueKind.Array:
            {
                var lines = new List<string>();
                foreach (var item in value.EnumerateArray())
                {
                    if (item.ValueKind == JsonValueKind.Object &&
                        item.TryGetProperty("msg", out var msg) && msg.ValueKind == JsonValueKind.String &&
                        item.TryGetProperty("loc", out var loc) && loc.ValueKind == JsonValueKind.Array)
                    {
                        var path = string.Join(".", loc.EnumerateArray().Skip(1).Select(part => part.ToString()));
                        lines.Add(path.Length > 0 ? $"{path}：{msg.GetString()}" : msg.GetString() ?? "");
                    }
                    else lines.Add(item.ToString());
                }
                return lines.Count > 0 ? string.Join("\n", lines) : fallback;
            }
            case JsonValueKind.Object:
            {
                var message = value.TryGetProperty("message", out var header) && header.ValueKind == JsonValueKind.String
                    ? header.GetString() : null;
                var blockers = new List<string>();
                if (value.TryGetProperty("blockers", out var list) && list.ValueKind == JsonValueKind.Array)
                    foreach (var blocker in list.EnumerateArray())
                        if (blocker.ValueKind == JsonValueKind.Object &&
                            blocker.TryGetProperty("message", out var text) && text.ValueKind == JsonValueKind.String &&
                            text.GetString() is { Length: > 0 } blockerMessage)
                            blockers.Add(blockerMessage);
                if (message is { Length: > 0 } && blockers.Count > 0)
                    return $"{message}：{string.Join("；", blockers)}";
                if (blockers.Count > 0) return string.Join("；", blockers);
                return message is { Length: > 0 } ? message : value.ToString();
            }
            default:
                return value.ToString();
        }
    }

    // Web publicUrl(): grids use the 640px thumbnail; lightbox keeps the original.
    public string PublicUrl(string path)
    {
        var relative = MediaPath(path);
        if (relative.StartsWith("assets/", StringComparison.Ordinal) && relative.EndsWith("/content", StringComparison.Ordinal))
            relative = relative[..^"/content".Length] + "/thumbnail/640";
        return $"{Origin}/api/v1/{relative}";
    }
    public string OriginUrl(string path) => $"{Origin}/api/v1/{MediaPath(path)}";

    private static string MediaPath(string path)
    {
        // API responses already contain /api/v1; locally constructed paths do not.
        // Normalize once so every gallery, reference picker and lightbox resolves identically.
        var relative = path.StartsWith("/api/v1/", StringComparison.Ordinal) ? path[8..] : path.TrimStart('/');
        Validate(relative);
        return relative;
    }

    private static void Validate(string path)
    {
        if (path.StartsWith('/') || path.Contains("://", StringComparison.Ordinal) || path.Contains(".."))
            throw new ArgumentException("无效的 API 路径");
    }

    public void Dispose() => client.Dispose();
}

public static class QueryBuilder
{
    public static string Build(string path, params (string Key, object? Value)[] parameters)
    {
        var parts = parameters
            .Where(p => p.Value is not null and not "")
            .Select(p => $"{HttpUtility.UrlEncode(p.Key)}={HttpUtility.UrlEncode(p.Value!.ToString())}");
        var query = string.Join("&", parts);
        return query.Length == 0 ? path : $"{path}?{query}";
    }
}
