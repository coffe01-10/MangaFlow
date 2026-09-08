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
            var detail = $"请求失败（{(int)response.StatusCode}）";
            try
            {
                using var error = JsonDocument.Parse(text);
                if (error.RootElement.ValueKind == JsonValueKind.Object)
                {
                    if (error.RootElement.TryGetProperty("detail", out var value))
                    {
                        detail = value.ValueKind switch
                        {
                            JsonValueKind.String => value.GetString() ?? detail,
                            JsonValueKind.Array => string.Join("\n", value.EnumerateArray()
                                .Select(item => item.TryGetProperty("msg", out var msg) && item.TryGetProperty("loc", out var loc)
                                    ? $"{string.Join(".", loc.EnumerateArray().Skip(1).Select(l => l.ToString()))}：{msg}"
                                    : item.ToString())),
                            JsonValueKind.Object => value.TryGetProperty("message", out var message)
                                ? message.GetString() ?? detail
                                : value.ToString(),
                            _ => value.ToString(),
                        };
                    }
                }
                else if (error.RootElement.ValueKind == JsonValueKind.String)
                    detail = error.RootElement.GetString() ?? detail;
            }
            catch (JsonException) { }
            if (response.StatusCode == HttpStatusCode.Conflict)
                detail = "数据已变化或操作条件不满足。请刷新后重试。\n" + detail;
            throw new InvalidOperationException(detail.Length > 2000 ? detail[..2000] : detail);
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
            var detail = $"上传失败（{(int)response.StatusCode}）";
            try
            {
                using var error = JsonDocument.Parse(text);
                if (error.RootElement.ValueKind == JsonValueKind.Object &&
                    error.RootElement.TryGetProperty("detail", out var value)) detail = value.ToString();
                else if (error.RootElement.ValueKind == JsonValueKind.String)
                    detail = error.RootElement.GetString() ?? detail;
            }
            catch (JsonException) { }
            throw new InvalidOperationException(detail);
        }
        using var document = JsonDocument.Parse(text);
        return document.RootElement.Clone();
    }

    public async Task<byte[]> DownloadAsync(string path, CancellationToken cancellation = default)
    {
        Validate(path);
        using var response = await client.GetAsync(path, cancellation).ConfigureAwait(false);
        response.EnsureSuccessStatusCode();
        return await response.Content.ReadAsByteArrayAsync(cancellation).ConfigureAwait(false);
    }

    // Stream to an owned sibling file, then replace the user's destination only after completion.
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
            response.EnsureSuccessStatusCode();
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
