using System.Net;
using System.Net.Http;
using System.Text.Json;
using System.Web;
using MangaFlow.Native;
using MangaFlow.Native.Services;

internal static class NativeBehaviorChecks
{
    public static async Task Run(Action<bool, string> check)
    {
        static List<JsonElement> Rows(string json) => JsonSerializer.Deserialize<List<JsonElement>>(json)!;
        var rectangle = LocalEditRules.Rectangle(new System.Windows.Point(10, 10), new System.Windows.Point(20, 20), new System.Windows.Size(100, 100));
        check(LocalEditRules.TouchedByStroke(rectangle, [new(0, 15), new(30, 15)]), "fast eraser strokes remove crossed regions even when samples lie outside");
        check(!LocalEditRules.TouchedByStroke(rectangle, [new(0, 5), new(30, 5)]), "eraser strokes preserve neighboring regions");
        check(LocalEditRules.TouchedByStroke(rectangle, [new(10, 15)]), "eraser taps on polygon boundaries are recognized");
        var panels = Rows("""[{"characters":["alice","bob"],"outfits":{"alice":"coat"}},{"characters":["alice"]}]""");
        var characters = Rows("""[{"id":"alice","references":[{"asset_id":"first"},{"asset_id":"canonical","is_canonical":true}]},{"id":"bob","references":[{"asset_id":"bob-ref"}]}]""");
        var outfits = Rows("""[{"id":"coat","reference_asset_ids":["coat-front","coat-back"]}]""");
        var packages = Rows("""[{"character_id":"alice","status":"ACTIVE","published_version_id":"v2"}]""");
        var legacy = GenerationReferences.Defaults(panels, characters, outfits, []);
        check(legacy.Count == 2 && legacy["alice"].CharacterAssetId == "canonical" && legacy["alice"].OutfitAssetId == "coat-front",
            "generation selects canonical references once per visible character");
        var inherited = GenerationReferences.Defaults(panels, characters, outfits, packages);
        check(inherited["alice"].CharacterAssetId == null && inherited["alice"].OutfitId == "coat" && GenerationReferences.Ready(inherited, outfits, packages),
            "published packages inherit server defaults while retaining storyboard outfits");
        var emptyOutfit = Rows("""[{"id":"coat","reference_asset_ids":[]}]""");
        check(!GenerationReferences.Ready(inherited, emptyOutfit, packages), "published packages cannot hide missing outfit references");
        legacy["bob"] = legacy["bob"] with { CharacterAssetId = null };
        check(!GenerationReferences.Ready(legacy, outfits, []), "missing legacy references block generation");
        inherited["alice"] = inherited["alice"] with { PackageVersionId = "v1", OutfitAssetId = "coat-back" };
        var payload = JsonSerializer.SerializeToElement(new { reference_selections = inherited });
        check(payload.Element("reference_selections").Element("alice").Text("package_version_id") == "v1"
            && payload.Element("reference_selections").Element("alice").Text("outfit_asset_id") == "coat-back",
            "per-run package and outfit overrides serialize with API field names");
        check(GenerationReferences.Defaults(panels, characters, outfits, Rows("""[{"character_id":"alice","status":"ARCHIVED","published_version_id":"v2"}]"""))["alice"].CharacterAssetId == "canonical",
            "archived packages do not override legacy defaults");

        var filter = new UsageFilter(DateTimeOffset.Parse("2026-09-01T00:00:00+08:00"), DateTimeOffset.Parse("2026-09-08T00:00:00+08:00"), "p", "provider & co", "model/a", "HTTP_API");
        var firstQuery = HttpUtility.ParseQueryString(new Uri("http://localhost/" + filter.AttemptsPath()).Query);
        var nextQuery = HttpUtility.ParseQueryString(new Uri("http://localhost/" + filter.AttemptsPath("cursor+value")).Query);
        check(new[] { "since", "until", "project_id", "provider", "model_id", "channel", "limit" }.All(k => firstQuery[k] == nextQuery[k])
            && nextQuery["cursor"] == "cursor+value" && nextQuery["channel"] == "HTTP_API", "usage pagination retains every filter and exact time bounds");
        check(DateTimeOffset.Parse(firstQuery["since"]!).Hour == 16 && firstQuery["provider"] == "provider & co", "usage timestamps and escaped filter values round trip");
        try { (filter with { Until = filter.Since }).Validate(); throw new Exception("invalid range accepted"); }
        catch (ArgumentException) { check(true, "invalid usage ranges fail before HTTP requests"); }
        var summaryQuery = HttpUtility.ParseQueryString(new Uri("http://localhost/" + filter.SummaryPath()).Query);
        check(summaryQuery["from"] == firstQuery["since"] && summaryQuery["to"] == firstQuery["until"] && summaryQuery["channel"] == null,
            "usage summary respects its separate API filter contract");

        static HttpResponseMessage Response(string body) => new(HttpStatusCode.OK) { Content = new StringContent(body) };
        var oldPage = new TaskCompletionSource<HttpResponseMessage>(TaskCreationOptions.RunContinuationsAsynchronously);
        var requestCount = 0;
        using var api = new ApiClient("http://127.0.0.1:12345", new Handler((request, _) =>
        {
            requestCount++;
            var query = HttpUtility.ParseQueryString(request.RequestUri!.Query);
            if (query["cursor"] == "older") return oldPage.Task;
            return Task.FromResult(query["project_id"] == "p"
                ? Response("""{"items":[{"id":"old"}],"next_cursor":"older"}""")
                : Response("""{"items":[{"id":"new"}],"next_cursor":null}"""));
        }));
        var feed = new UsageAttemptFeed();
        await feed.LoadAsync(api, filter, CancellationToken.None);
        var more = feed.LoadMoreAsync(api, CancellationToken.None);
        check(!await feed.LoadMoreAsync(api, CancellationToken.None) && requestCount == 2, "repeated load-more clicks dispatch only one request");
        await feed.LoadAsync(api, filter with { ProjectId = "new-project" }, CancellationToken.None);
        oldPage.SetResult(Response("""{"items":[{"id":"stale"}],"next_cursor":"stale-cursor"}"""));
        check(!await more && feed.Items.Single().Text("id") == "new" && feed.NextCursor == null,
            "late pagination responses cannot contaminate a new usage filter");
        var delayed = new TaskCompletionSource<HttpResponseMessage>(TaskCreationOptions.RunContinuationsAsynchronously);
        using var delayedApi = new ApiClient("http://127.0.0.1:12345", new Handler((request, _) =>
            HttpUtility.ParseQueryString(request.RequestUri!.Query)["project_id"] == "p" ? delayed.Task
                : Task.FromResult(Response("""{"items":[{"id":"latest"}],"next_cursor":null}"""))));
        var early = feed.LoadAsync(delayedApi, filter, CancellationToken.None);
        await feed.LoadAsync(delayedApi, filter with { ProjectId = "latest" }, CancellationToken.None);
        delayed.SetResult(Response("""{"items":[{"id":"late-first-page"}],"next_cursor":null}"""));
        check(!await early && feed.Items.Single().Text("id") == "latest", "late first pages cannot overwrite newer usage results");
    }

    private sealed class Handler(Func<HttpRequestMessage, CancellationToken, Task<HttpResponseMessage>> respond) : HttpMessageHandler
    {
        protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken cancellationToken) => respond(request, cancellationToken);
    }
}
