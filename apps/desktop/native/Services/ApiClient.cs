using System.Net;
using System.Net.Http;
using System.Net.Http.Json;
using System.Text.Json;

namespace MangaFlow.Native.Services;

public sealed class ApiClient : IDisposable
{
    private readonly HttpClient client;
    public ApiClient(string origin, HttpMessageHandler? handler = null)
    {
        var uri = new Uri(origin, UriKind.Absolute);
        if (uri.Scheme != "http" || uri.Host != "127.0.0.1" || uri.Port <= 0 ||
            uri.AbsolutePath != "/" || uri.Query.Length > 0 || uri.Fragment.Length > 0 || uri.UserInfo.Length > 0)
            throw new ArgumentException("本地服务地址未通过校验");
        client = handler == null
            ? new HttpClient(new HttpClientHandler { UseProxy = false, AllowAutoRedirect = false })
            : new HttpClient(handler);
        client.BaseAddress = new Uri(uri, "/api/v1/");
        client.Timeout = TimeSpan.FromSeconds(30);
    }

    public async Task<JsonElement> SendAsync(string path, HttpMethod? method = null, object? body = null,
        CancellationToken cancellation = default)
    {
        if (path.StartsWith('/') || path.Contains(":") || path.Contains(".."))
            throw new ArgumentException("无效的 API 路径");
        using var request = new HttpRequestMessage(method ?? HttpMethod.Get, path);
        if (body != null) request.Content = JsonContent.Create(body);
        using var response = await client.SendAsync(request, cancellation).ConfigureAwait(false);
        var text = await response.Content.ReadAsStringAsync(cancellation).ConfigureAwait(false);
        if (!response.IsSuccessStatusCode)
        {
            var detail = $"请求失败（{(int)response.StatusCode}）";
            try
            {
                using var error = JsonDocument.Parse(text);
                if (error.RootElement.TryGetProperty("detail", out var value)) detail = value.ToString();
            }
            catch (JsonException) { }
            if (response.StatusCode == HttpStatusCode.Conflict)
                detail = "数据已变化或操作条件不满足。请刷新后重试。\n" + detail;
            throw new InvalidOperationException(detail.Length > 1500 ? detail[..1500] : detail);
        }
        if (string.IsNullOrWhiteSpace(text)) return default;
        using var document = JsonDocument.Parse(text);
        return document.RootElement.Clone();
    }

    public void Dispose() => client.Dispose();
}
