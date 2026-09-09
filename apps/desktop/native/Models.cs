using System.ComponentModel;
using System.Runtime.CompilerServices;
using System.Text.Json;

namespace MangaFlow.Native;

public static class JsonFields
{
    public static string Text(this JsonElement json, string name, string fallback = "") =>
        json.ValueKind == JsonValueKind.Object &&
        json.TryGetProperty(name, out var value) && value.ValueKind is not JsonValueKind.Null and not JsonValueKind.Undefined
            ? value.ToString() : fallback;
    public static string?TextOrNull(this JsonElement json, string name) =>
        json.ValueKind == JsonValueKind.Object &&
        json.TryGetProperty(name, out var value) && value.ValueKind is JsonValueKind.String
            ? value.GetString() : null;
    public static int Number(this JsonElement json, string name) =>
        json.ValueKind == JsonValueKind.Object &&
        json.TryGetProperty(name, out var value) && value.TryGetInt32(out var number) ? number : 0;
    public static double Decimal(this JsonElement json, string name, double fallback = 0) =>
        json.ValueKind == JsonValueKind.Object &&
        json.TryGetProperty(name, out var value) && value.ValueKind is JsonValueKind.Number && value.TryGetDouble(out var number)
            ? number : fallback;
    public static bool Flag(this JsonElement json, string name) =>
        json.ValueKind == JsonValueKind.Object &&
        json.TryGetProperty(name, out var value) && value.ValueKind is JsonValueKind.True;
    public static JsonElement Element(this JsonElement json, string name)
    {
        if (json.ValueKind == JsonValueKind.Object &&
            json.TryGetProperty(name, out var value) && value.ValueKind is not JsonValueKind.Null and not JsonValueKind.Undefined)
            return value;
        return default;
    }
    public static List<JsonElement> Array(this JsonElement json, string name)
    {
        var element = json.Element(name);
        return element.ValueKind == JsonValueKind.Array
            ? element.EnumerateArray().ToList()
            : [];
    }
    public static List<string> Strings(this JsonElement json, string name) =>
        json.Array(name).Select(item => item.ToString()).ToList();
    public static string MapText(this JsonElement json, string name, IReadOnlyDictionary<string, string> table) =>
        Labels.Map(table, json.TextOrNull(name));
}

public record ProjectItem(string Id, string Name, string Summary, int Pending, int Failed)
{
    public int PageCount { get; init; }
    public int SelectedPages { get; init; }
    public string ModeLabel { get; init; } = "";
    public string Resolution { get; init; } = "";
    public string NextSection { get; init; } = "source";
    public string NextLabel { get; init; } = "";
    public string ModeAndResolution => $"{ModeLabel} · {Resolution}";
    public int Progress => PageCount <= 0 ? 0 : Math.Clamp((int)Math.Round(100d * SelectedPages / PageCount), 0, 100);
    public string CoverTitle
    {
        get
        {
            var letters = System.Globalization.StringInfo.GetTextElementEnumerator(Name);
            var visible = new List<string>();
            while (visible.Count < 8 && letters.MoveNext()) visible.Add(letters.GetTextElement());
            return string.Join("\n", visible);
        }
    }
    public static ProjectItem From(JsonElement row)
    {
        var p = row.Element("project");
        if (p.ValueKind == JsonValueKind.Object)
            return new(p.Text("id"), p.Text("name"),
                $"{row.Number("chapter_count")} 章 · {row.Number("page_count")} 页 · {row.Number("selected_page_count")} 已采用",
                row.Number("pending_job_count"), row.Number("failed_job_count"))
            {
                PageCount = row.Number("page_count"),
                SelectedPages = row.Number("selected_page_count"),
                Resolution = p.Text("default_resolution"),
                NextSection = row.Element("next_action").Text("section", "source"),
                NextLabel = row.Element("next_action").Text("label"),
                ModeLabel = p.TextOrNull("workflow_mode") switch
                {
                    "SEMI_AUTO" => "半自动", "AUTO" => "自动", "MANUAL" => "手动", "DIRECTOR" => "导演", var mode => mode ?? "",
                },
            };
        return new(p.Text("id"), p.Text("name"), "", row.Number("pending_job_count"), row.Number("failed_job_count"))
        {
            Resolution = p.Text("default_resolution"),
            ModeLabel = p.TextOrNull("workflow_mode") switch
            {
                "SEMI_AUTO" => "半自动", "AUTO" => "自动", "DIRECTOR" => "导演", var mode => mode ?? "",
            },
        };
    }
}

public record ChapterItem(string Id, string Title, string Summary)
{
    public string Status { get; init; } = "";
    public int Pages { get; init; }
    public int Ordinal { get; init; }
    public int Characters { get; init; }
    public int Segments { get; init; }
    public int Coverage { get; init; }
    public string StatusLabel => Labels.Map(Labels.ChapterStatus, Status);
    public static ChapterItem From(JsonElement c) => new(c.Text("id"), c.Text("title"),
        $"{c.Number("source_character_count"):N0} 字 · {c.Number("page_count")} 页")
    {
        Status = c.Text("status"),
        Pages = c.Number("page_count"),
        Ordinal = c.Number("ordinal"),
        Characters = c.Number("source_character_count"),
        Segments = c.Number("segment_count"),
        Coverage = (int)Math.Round(c.Decimal("coverage_ratio", 0) * 100),
    };
}

public record CostEstimate(string Amount, string Currency, string Status, string Note)
{
    public static readonly CostEstimate None = new("", "", "", "");
    public string Label
    {
        get
        {
            if (Amount.Length == 0) return Status == "PARTIAL" ? "部分费用暂不可估算" : "费用暂不可估算";
            var symbol = Currency switch { "CNY" => "¥", "USD" => "$", "EUR" => "€", "GBP" => "£", _ => $"{Currency} " };
            return $"{(Status == "PARTIAL" ? "（部分）估算 " : "估算 ")}{symbol}{Amount}";
        }
    }
    public static CostEstimate From(JsonElement json)
    {
        if (json.ValueKind != JsonValueKind.Object) return None;
        return new(json.Text("amount"), json.Text("currency"), json.Text("status"), json.Text("note"));
    }

    public static CostEstimate FromJob(JsonElement job) => new(
        job.Text("estimated_cost"), job.Text("estimated_cost_currency"),
        job.Text("estimated_cost_status", job.Element("estimated_cost").ValueKind == JsonValueKind.Number ? "AVAILABLE" : "UNAVAILABLE"),
        job.Text("estimated_cost_note"));
}

public record JobItem(string Id, string Name, string State, string StatusLabel, int Progress,
    string Detail, bool CanCancel, bool CanRetry)
{
    public string Type { get; init; } = "";
    public string NodeName { get; init; } = "";
    public string ModelName { get; init; } = "";
    public string ErrorCode { get; init; } = "";
    public double Duration { get; init; }
    public bool HasDuration { get; init; }
    public string CreatedAt { get; init; } = "";
    public List<string> PricingVersions { get; init; } = [];
    public int Attempt { get; init; }
    public int MaxAttempts { get; init; }
    public CostEstimate Cost { get; init; } = CostEstimate.None;
    public string ResultImageUrl { get; init; } = "";
    public string ErrorLabel =>
        ErrorCode.Length > 0 ? $"{ErrorCode} · {(Detail.Length > 0 ? Detail : Labels.Map(Labels.ErrorCode, ErrorCode))}" : Detail;
    public bool Terminal => State is "COMPLETED" or "FAILED" or "CANCELLED" or "NEEDS_REVIEW";
    public bool Active => !Terminal;
    public string CostLabel => Cost.Label.Length > 0
        ? Cost.Label + " · " + (Cost.Note.Length > 0 ? Cost.Note : "估算值不等于供应商账单")
        : "";
    public static JobItem From(JsonElement j)
    {
        var state = j.Text("status");
        var label = Labels.Map(Labels.JobStatus, state);
        var name = Labels.Map(Labels.Jobs, j.Text("job_type"));
        return new(j.Text("id"), name, state, label, Math.Clamp(j.Number("progress"), 0, 100),
            j.Text("error_message"),
            state is "WAITING" or "QUEUED" or "RUNNING" or "PREPARING" or "GENERATING"
                or "UPLOADING_REFERENCES" or "CONSISTENCY_CHECKING" or "REPAIRING" or "OCR_CHECKING",
            (state is "FAILED" or "NEEDS_REVIEW" or "WAITING") && j.Number("attempt_count") < j.Number("max_attempts"))
        {
            Type = j.Text("job_type"),
            NodeName = j.Text("workflow_node_id") is { Length: > 0 } node ? $"节点 {node}" : "",
            ModelName = j.Text("model_alias"),
            ErrorCode = j.Text("error_code"),
            Duration = j.Decimal("duration_ms") / 1000,
            HasDuration = j.Element("duration_ms").ValueKind == JsonValueKind.Number,
            CreatedAt = j.Text("created_at"),
            PricingVersions = j.Strings("estimated_cost_pricing_versions"),
            Attempt = j.Number("attempt_count"),
            MaxAttempts = j.Number("max_attempts"),
            Cost = CostEstimate.FromJob(j),
            ResultImageUrl = j.Element("result").Text("content_url"),
        };
    }
}

public record CharacterItem(string Id, string PrimaryName)
{
    public List<string> Aliases { get; init; } = [];
    public string LockedFeatures { get; init; } = "";
    public string ForbiddenChanges { get; init; } = "";
    public int Version { get; init; }
    public int ReferenceCount { get; init; }
    public List<JsonElement> References { get; init; } = [];
    public int LockedReferences { get; init; }
    public bool AliasConflict { get; init; }
    public string AliasLabel => Aliases.Count == 0 ? "无绰号" : "又名 " + string.Join(" / ", Aliases);
    public static CharacterItem From(JsonElement c) => new(c.Text("id"), c.Text("primary_name"))
    {
        Aliases = c.Strings("aliases"),
        LockedFeatures = string.Join("，", c.Strings("locked_features")),
        ForbiddenChanges = string.Join("，", c.Strings("forbidden_changes")),
        Version = c.Number("version"),
        ReferenceCount = c.Array("references").Count,
        References = c.Array("references"),
        LockedReferences = c.Strings("locked_features").Count,
        AliasConflict = c.Flag("alias_conflict"),
    };
}

public record OutfitItem(string Id, string CharacterId, string Name)
{
    public List<string> ReferenceAssetIds { get; init; } = [];
    public string LockedFields { get; init; } = "";
    public int Version { get; init; }
    public int ReferenceCount => ReferenceAssetIds.Count;
    public static OutfitItem From(JsonElement o) => new(o.Text("id"), o.Text("character_id"), o.Text("name"))
    {
        ReferenceAssetIds = o.Strings("reference_asset_ids"),
        LockedFields = o.Text("locked_fields"),
        Version = o.Number("version"),
    };
}

public record StyleItem(string Id, string Name, string Status, string ColorMode)
{
    public int Version { get; init; }
    public int ReferenceCount { get; init; }
    public List<string>? Palette { get; init; }
    public List<string>? PaletteDraft { get; init; }
    public bool PaletteConfirmed { get; init; }
    public string LockedFields { get; init; } = "";
    public bool Analyzing => Status == "ANALYZING";
    public string StatusLabel => Labels.Map(Labels.StyleStatus, Status);
    public static StyleItem From(JsonElement s) => new(s.Text("id"), s.Text("name"), s.Text("status", "DRAFT"), s.Text("color_mode", "monochrome"))
    {
        Version = s.Number("version"),
        ReferenceCount = s.Array("reference_asset_ids").Count,
        Palette = s.Element("palette").ValueKind == JsonValueKind.Array ? s.Strings("palette") : null,
        PaletteDraft = s.Element("palette_draft").ValueKind == JsonValueKind.Array ? s.Strings("palette_draft") : null,
        PaletteConfirmed = s.Flag("palette_confirmed"),
        LockedFields = s.Text("locked_fields"),
    };
}

public record AssetItem(string Id, string Kind)
{
    public string DisplayName { get; init; } = "";
    public string OriginalName { get; init; } = "";
    public long FileSize { get; init; }
    public string Status { get; init; } = "";
    public string ContentUrl { get; init; } = "";
    public string BoundCharacterName { get; init; } = "";
    public string BoundOutfitName { get; init; } = "";
    public string BoundStyleName { get; init; } = "";
    public bool Canonical { get; init; }
    public string Name => DisplayName.Length > 0 ? DisplayName : OriginalName.Length > 0 ? OriginalName : "未命名素材";
    public string KindLabel => Labels.Map(Labels.AssetKinds, Kind);
    public string StatusLabel => Labels.Map(Labels.AssetStatus, Status);
    public string SizeLabel
    {
        get
        {
            if (FileSize <= 0) return "";
            if (FileSize < 1024 * 1024) return $"{Math.Ceiling(FileSize / 1024.0):N0} KB";
            return $"{FileSize / 1024.0 / 1024.0:0.#} MB";
        }
    }
    public static AssetItem From(JsonElement a) => new(a.Text("id"), a.Text("kind"))
    {
        DisplayName = a.Text("display_name"),
        OriginalName = a.Text("original_name"),
        FileSize = a.Element("byte_size").ValueKind == JsonValueKind.Number ? a.Number("byte_size") : a.Number("file_size"),
        Status = a.Text("status", "UPLOADED"),
        ContentUrl = a.Text("content_url"),
        Canonical = a.Flag("is_canonical"),
        BoundCharacterName = a.Text("character_name"),
        BoundOutfitName = a.Text("outfit_name"),
        BoundStyleName = a.Text("style_name"),
    };
}

public record SceneAssetItem(string Id, string Name)
{
    public string Description { get; init; } = "";
    public string LocationHint { get; init; } = "";
    public string Status { get; init; } = "UPLOADED";
    public bool? Interior { get; init; }
    public int Version { get; init; }
    public bool Deleted { get; init; }
    public List<JsonElement> References { get; init; } = [];
    public List<JsonElement> Variants { get; init; } = [];
    public string StatusLabel => Labels.Map(Labels.SceneAssetStatus, Deleted ? "ARCHIVED" : Status);
    public string InteriorLabel => Interior == true ? "室内" : Interior == false ? "室外" : "空间未指定";
    public static SceneAssetItem From(JsonElement s) => new(s.Text("id"), s.Text("name"))
    {
        Description = s.Text("description"),
        LocationHint = s.Text("location_hint"),
        Status = s.Text("status", "UPLOADED"),
        Interior = s.TryGetProperty("interior", out var interior) && interior.ValueKind is JsonValueKind.True or JsonValueKind.False
            ? interior.ValueKind == JsonValueKind.True
            : null,
        Version = s.Number("version"),
        Deleted = s.Flag("deleted_at"),
        References = s.Array("references"),
        Variants = s.Array("variants"),
    };
}

public record PageItem(string Id, int PageNumber)
{
    public int PanelCount { get; init; }
    public int StoryboardVersion { get; init; }
    public string SelectedCandidateId { get; init; } = "";
    public string ContinuityStatus { get; init; } = "";
    public string Status { get; init; } = "";
    public int CharacterCount { get; init; }
    public int BubbleCount { get; init; }
    public bool NeedsReview => Status == "NEEDS_REVIEW";
    public bool Adopted => SelectedCandidateId.Length > 0;
    public string StateLabel => NeedsReview ? "待复查" : Adopted ? "已采用" : "已规划";
    public static PageItem From(JsonElement p) => new(p.Text("id"), p.Number("page_number"))
    {
        PanelCount = p.Number("panel_count"),
        StoryboardVersion = p.Number("storyboard_version"),
        SelectedCandidateId = p.Text("selected_candidate_id"),
        ContinuityStatus = p.Text("continuity_status"),
        Status = p.Text("status"),
        CharacterCount = p.Number("character_count"),
        BubbleCount = p.Number("bubble_count"),
    };
}

public record CandidateItem(string Id, int Ordinal, string Status)
{
    public string ModelAlias { get; init; } = "";
    public string Provider { get; init; } = "";
    public string Resolution { get; init; } = "";
    public string AssetId { get; init; } = "";
    public string ContentUrl { get; init; } = "";
    public bool Favorite { get; init; }
    public bool IsSelected { get; init; }
    public string VersionState { get; init; } = "CURRENT";
    public string StoryboardVersion { get; init; } = "";
    public string BatchId { get; init; } = "";
    public string PageId { get; init; } = "";
    public string CreatedAt { get; init; } = "";
    public CostEstimate Cost { get; init; } = CostEstimate.None;
    public string LineageCommandId { get; init; } = "";
    public string StatusLabel => Labels.Map(Labels.CandidateStatus, Status);
    public string VersionLabel => Labels.Map(Labels.CandidateVersionState, VersionState);
    public bool HasImage => ContentUrl.Length > 0 || AssetId.Length > 0;
    public static CandidateItem From(JsonElement c) => new(c.Text("id"), c.Number("ordinal"), c.Text("status", "QUEUED"))
    {
        ModelAlias = c.Text("model_alias"),
        Provider = c.Text("provider"),
        Resolution = c.Text("resolution", "1K"),
        AssetId = c.Text("asset_id"),
        ContentUrl = c.Text("content_url"),
        Favorite = c.Flag("is_favorite"),
        IsSelected = c.Flag("is_selected"),
        VersionState = c.Text("version_state", "CURRENT"),
        StoryboardVersion = c.Text("storyboard_version"),
        BatchId = c.Text("batch_id"),
        PageId = c.Text("page_id"),
        CreatedAt = c.Text("created_at"),
        Cost = CostEstimate.From(c.Element("estimated_cost")),
        LineageCommandId = c.Element("prompt_snapshot").Element("lineage").Text("source_command_id"),
    };
}

public record BatchItem(string Id, int Ordinal, string GenerationKind)
{
    public string CreatedAt { get; init; } = "";
    public string KindLabel => Labels.Map(Labels.GenerationKind, GenerationKind);
    public static BatchItem From(JsonElement b) => new(b.Text("id"), b.Number("ordinal"), b.Text("generation_kind", "PAGE"))
    {
        CreatedAt = b.Text("created_at"),
    };
}

public record ExportItem(string Id, string ExportType)
{
    public string FileName { get; init; } = "";
    public int PageCount { get; init; }
    public long FileSize { get; init; }
    public string Url { get; init; } = "";
    public string CreatedAt { get; init; } = "";
    public string SizeLabel => FileSize <= 0 ? "" :
        FileSize < 1024 * 1024 ? $"{Math.Ceiling(FileSize / 1024.0):N0} KB" : $"{FileSize / 1024.0 / 1024.0:0.#} MB";
    public static ExportItem From(JsonElement e) => new(e.Text("id"), e.Text("export_type"))
    {
        FileName = e.Text("file_name"),
        PageCount = e.Number("page_count"),
        FileSize = e.Element("byte_size").ValueKind == JsonValueKind.Number && e.Element("byte_size").TryGetInt64(out var bytes) ? bytes : 0,
        Url = e.Text("download_url"),
        CreatedAt = e.Text("created_at"),
    };
}

public record ModelOption(string Value, string Label, string Provider, string ModelId, bool Hidden, bool Image)
{
    public static ModelOption From(JsonElement m) => new(
        m.Text("logical_alias"),
        $"{m.Text("provider")} · {m.Text("display_name")}",
        m.Text("provider"), m.Text("model_id"),
        m.ValueKind == JsonValueKind.Object && m.TryGetProperty("display_enabled", out var display) && display.ValueKind == JsonValueKind.False,
        m.Text("model_type") == "IMAGE");
}

public class Observable : INotifyPropertyChanged
{
    public event PropertyChangedEventHandler? PropertyChanged;
    protected void Changed([CallerMemberName] string? name = null) =>
        PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(name));
    protected void ChangedAll(params string[] names)
    {
        foreach (var name in names) PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(name));
    }
    protected void Set<T>(ref T field, T value, [CallerMemberName] string? name = null)
    {
        if (EqualityComparer<T>.Default.Equals(field, value)) return;
        field = value;
        Changed(name);
    }
    protected void Set<T>(ref T field, T value, params string[] names)
    {
        if (EqualityComparer<T>.Default.Equals(field, value)) return;
        field = value;
        ChangedAll(names);
    }
}
