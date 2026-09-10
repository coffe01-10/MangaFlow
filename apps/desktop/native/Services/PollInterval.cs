using System.Text.Json;

namespace MangaFlow.Native.Services;

/// <summary>
/// Global UI poll period sourced from the runtime setting "ui_poll_interval_seconds"
/// (GET /settings/runtime). Despite the field name the value is milliseconds, matching
/// the web settings form（“界面轮询周期（毫秒）”，ClampedNumberInput 1000–60000）and the
/// API default of 3000 (apps/api settings_schemas.py / services/runtime_settings.py).
/// The web hardcodes 2000/3000 ms per query; the desktop routes its global timers
/// (MainWindow poll/dock poll, LocalEditWindow poll) through this value so the settings
/// page actually drives them. A changed value takes effect on the NEXT timer tick:
/// running ticks are never re-armed mid-flight, each view keeps its own in-flight
/// guard, and windows opened later read the fresh value.
/// </summary>
public static class PollInterval
{
    public const int DefaultMs = 3000;
    public const int MinMs = 1000;
    public const int MaxMs = 60000;

    private static int currentMs = DefaultMs;

    public static int CurrentMs => currentMs;
    public static TimeSpan Interval => TimeSpan.FromMilliseconds(currentMs);

    /// <summary>Same clamp the web ClampedNumberInput applies before PATCHing.</summary>
    public static int Clamp(int milliseconds) => Math.Clamp(milliseconds, MinMs, MaxMs);

    /// <summary>
    /// Apply a raw setting value. Null/invalid keeps the current period: a broken
    /// settings row must never zero out polling.
    /// </summary>
    public static void Apply(int? milliseconds)
    {
        if (milliseconds is > 0) currentMs = Clamp(milliseconds.Value);
    }

    /// <summary>Parse "ui_poll_interval_seconds" from a runtime settings payload.</summary>
    public static int? Parse(JsonElement settings)
    {
        if (settings.ValueKind != JsonValueKind.Object) return null;
        var value = settings.Element("ui_poll_interval_seconds");
        return value.ValueKind == JsonValueKind.Number && value.TryGetInt32(out var ms) && ms > 0 ? ms : null;
    }

    /// <summary>
    /// Best-effort refresh from the API. Failures (offline, malformed payload)
    /// keep the current period; callers decide whether to surface them.
    /// </summary>
    public static async Task LoadAsync(ApiClient api, CancellationToken cancellation = default)
    {
        var settings = await api.SendAsync("settings/runtime", cancellation: cancellation).ConfigureAwait(false);
        Apply(Parse(settings));
    }

    /// <summary>Test seam: restore the shipped default between checks.</summary>
    public static void ResetForTests() => currentMs = DefaultMs;
}
