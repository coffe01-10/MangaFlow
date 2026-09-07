using System.IO;
using System.Text.Json;

namespace MangaFlow.Native.Services;

public sealed class Preferences
{
    public double Width { get; set; } = 1320;
    public double Height { get; set; } = 860;
    public bool Maximized { get; set; }
    public bool SidebarCollapsed { get; set; }
    public bool DockHidden { get; set; }
    public string? RecentProject { get; set; }

    public static Preferences Load(string root)
    {
        try { return JsonSerializer.Deserialize<Preferences>(File.ReadAllText(Path.Combine(root, "window.json"))) ?? new(); }
        catch (Exception e) when (e is IOException or JsonException or UnauthorizedAccessException) { return new(); }
    }

    public void Save(string root)
    {
        Directory.CreateDirectory(root);
        var destination = Path.Combine(root, "window.json");
        var temporary = Path.Combine(root, $"window-{Guid.NewGuid():N}.tmp");
        try
        {
            File.WriteAllText(temporary, JsonSerializer.Serialize(this));
            File.Move(temporary, destination, true);
        }
        finally { if (File.Exists(temporary)) File.Delete(temporary); }
    }
}
