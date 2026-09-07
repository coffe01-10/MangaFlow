using System.IO;
using System.Text.Json;

namespace MangaFlow.Native.Services;

/// <summary>
/// Persistent key-value store for per-project UI choices (image model, style mode, …),
/// the native counterpart of the web's localStorage keys.
/// </summary>
public static class KeyValueStore
{
    private static readonly object gate = new();
    private static Dictionary<string, string> values = new();
    private static bool loaded;
    private static string path = "";

    private static string Location => path.Length > 0 ? path : Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "MangaFlow", "Native", "prefs.json");

    public static string Get(string key)
    {
        lock (gate)
        {
            Load();
            return values.GetValueOrDefault(key, "");
        }
    }

    public static void Set(string key, string value)
    {
        lock (gate)
        {
            Load();
            values[key] = value;
            Save();
        }
    }

    public static void Remove(string key)
    {
        lock (gate)
        {
            Load();
            values.Remove(key);
            Save();
        }
    }

    public static void UseLocation(string file)
    {
        lock (gate)
        {
            path = file;
            loaded = false;
        }
    }

    private static void Load()
    {
        if (loaded) return;
        loaded = true;
        try
        {
            if (File.Exists(Location))
                values = JsonSerializer.Deserialize<Dictionary<string, string>>(File.ReadAllText(Location)) ?? [];
        }
        catch (Exception e) when (e is IOException or JsonException or UnauthorizedAccessException)
        {
            values = new Dictionary<string, string>();
        }
    }

    private static void Save()
    {
        try
        {
            Directory.CreateDirectory(Path.GetDirectoryName(Location)!);
            var temporary = Location + ".tmp";
            File.WriteAllText(temporary, JsonSerializer.Serialize(values));
            File.Move(temporary, Location, true);
        }
        catch (Exception e) when (e is IOException or UnauthorizedAccessException)
        {
            // Preferences are cosmetic; never crash the app over them.
        }
    }
}
