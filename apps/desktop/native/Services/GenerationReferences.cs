using System.Text.Json;
using System.Text.Json.Serialization;

namespace MangaFlow.Native.Services;

public sealed record ReferenceChoice(
    [property: JsonPropertyName("character_asset_id")] string? CharacterAssetId,
    [property: JsonPropertyName("outfit_id")] string? OutfitId,
    [property: JsonPropertyName("outfit_asset_id")] string? OutfitAssetId,
    [property: JsonPropertyName("package_version_id"), JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)] string? PackageVersionId = null);

public static class GenerationReferences
{
    public static Dictionary<string, ReferenceChoice> Defaults(IEnumerable<JsonElement> panels,
        IReadOnlyList<JsonElement> characters, IReadOnlyList<JsonElement> outfits, IReadOnlyList<JsonElement> packages)
    {
        var panelList = panels.ToList();
        var result = new Dictionary<string, ReferenceChoice>();
        foreach (var id in panelList.SelectMany(p => p.Strings("characters")).Distinct())
        {
            var character = characters.FirstOrDefault(c => c.Text("id") == id);
            var outfitId = panelList.Select(p => p.Element("outfits").TextOrNull(id)).FirstOrDefault(x => x != null);
            var outfit = outfits.FirstOrDefault(o => o.Text("id") == outfitId);
            var references = character.Array("references");
            var canonical = references.FirstOrDefault(r => r.Flag("is_canonical"));
            var package = HasPublishedPackage(id, packages);
            result[id] = new ReferenceChoice(
                package ? null : (canonical.ValueKind == JsonValueKind.Object ? canonical : references.FirstOrDefault()).TextOrNull("asset_id"),
                outfitId, package ? null : outfit.Strings("reference_asset_ids").FirstOrDefault());
        }
        return result;
    }

    public static bool HasPublishedPackage(string characterId, IReadOnlyList<JsonElement> packages) =>
        packages.Any(p => p.Text("character_id") == characterId && p.Text("status") == "ACTIVE" && p.Text("published_version_id").Length > 0);

    public static bool Ready(IReadOnlyDictionary<string, ReferenceChoice> choices, IReadOnlyList<JsonElement> outfits,
        IReadOnlyList<JsonElement> packages) => choices.All(pair =>
        {
            var choice = pair.Value;
            var outfit = outfits.FirstOrDefault(o => o.Text("id") == choice.OutfitId);
            var package = choice.PackageVersionId != null || HasPublishedPackage(pair.Key, packages);
            if (package) return outfit.ValueKind != JsonValueKind.Object || outfit.Strings("reference_asset_ids").Count > 0;
            return choice.CharacterAssetId != null && (outfit.ValueKind != JsonValueKind.Object ||
                (outfit.Strings("reference_asset_ids").Count > 0 && choice.OutfitAssetId != null));
        });
}
