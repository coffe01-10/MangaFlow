using System.IO;
using System.Net;
using System.Net.Http;
using System.Net.Http.Headers;
using System.Text.Json;
using MangaFlow.Native.Services;

internal static class NativeOpenIssueChecks
{
    internal static async Task Run()
    {
        foreach (var count in new[] { 201, 401 })
        {
            var offsets = new List<int>();
            using var api = new ApiClient("http://127.0.0.1:12345", new Handler(request =>
            {
                var query = new Uri("http://127.0.0.1:12345/" + request.RequestUri!.PathAndQuery.TrimStart('/')).Query;
                var offset = int.Parse(System.Web.HttpUtility.ParseQueryString(query)["offset"]!);
                offsets.Add(offset);
                var rows = Enumerable.Range(offset, Math.Min(200, count - offset))
                    .Select(index => new { id = $"asset-{index}", kind = "OUTFIT_REFERENCE" });
                return new HttpResponseMessage(HttpStatusCode.OK)
                {
                    Content = new StringContent(JsonSerializer.Serialize(rows)),
                };
            }));
            var assets = await api.ListAssetsAsync("project-1");
            Require(assets.Count == count && assets[0].GetProperty("id").GetString() == "asset-0", "all assets remain visible");
            Require(offsets.SequenceEqual(count == 201 ? new[] { 0, 200 } : new[] { 0, 200, 400 }), "all pages are requested");
        }

        using (var api = new ApiClient("http://127.0.0.1:12345", new Handler(request =>
        {
            var offset = System.Web.HttpUtility.ParseQueryString(request.RequestUri!.Query)["offset"];
            return offset == "200"
                ? new HttpResponseMessage(HttpStatusCode.ServiceUnavailable)
                : new HttpResponseMessage(HttpStatusCode.OK)
                {
                    Content = new StringContent(JsonSerializer.Serialize(Enumerable.Range(0, 200).Select(i => new { id = i }))),
                };
        })))
        {
            try { await api.ListAssetsAsync("project-1"); throw new Exception("partial asset page was accepted"); }
            catch (ApiException error) when (error.Status == 503) { }
        }

        var folder = Path.Combine(Path.GetTempPath(), "mangaflow-format-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(folder);
        try
        {
            foreach (var (mime, extension, bytes) in new[]
            {
                ("image/png", ".png", new byte[] { 0x89, 0x50, 0x4e, 0x47 }),
                ("image/jpeg", ".jpg", new byte[] { 0xff, 0xd8, 0xff, 0xd9 }),
                ("image/webp", ".webp", new byte[] { 0x52, 0x49, 0x46, 0x46 }),
            })
            {
                using var api = new ApiClient("http://127.0.0.1:12345", new Handler(_ =>
                {
                    var response = new HttpResponseMessage(HttpStatusCode.OK) { Content = new ByteArrayContent(bytes) };
                    response.Content.Headers.ContentType = new MediaTypeHeaderValue(mime);
                    return response;
                }));
                var path = await api.SaveDownloadAsync("pages/page-1/export.png", Path.Combine(folder, mime.Split('/')[1] + ".png"), matchImageExtension: true);
                Require(path.EndsWith(extension) && File.ReadAllBytes(path).SequenceEqual(bytes), "desktop download uses response image type");
                if (extension != ".jpg") continue;
                try
                {
                    await api.SaveDownloadAsync("pages/page-1/export.png", Path.Combine(folder, "jpeg.png"), matchImageExtension: true);
                    throw new Exception("an unseen file was overwritten after changing its extension");
                }
                catch (IOException) { }
            }
        }
        finally { Directory.Delete(folder, recursive: true); }
    }

    private static void Require(bool condition, string message)
    {
        if (!condition) throw new Exception(message);
    }

    private sealed class Handler(Func<HttpRequestMessage, HttpResponseMessage> respond) : HttpMessageHandler
    {
        protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken cancellationToken) =>
            Task.FromResult(respond(request));
    }
}
