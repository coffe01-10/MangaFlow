using System.Text.Json;
using System.Windows;

namespace MangaFlow.Native.Services;

public static class LocalEditRules
{
    public const int MaxRegions = 8;
    public static bool SupportsMask(JsonElement model) => model.Flag("enabled") && model.Text("model_type") == "IMAGE"
        && model.Flag("accepts_explicit_mask") && model.Array("operations").Any(x => x.ToString() == "image_edit");

    public static Point Clamp(Point point, Size size) => new(Math.Clamp(point.X, 0, size.Width), Math.Clamp(point.Y, 0, size.Height));
    public static Point[] Rectangle(Point start, Point end, Size size)
    {
        var a = Clamp(new Point(Math.Min(start.X, end.X), Math.Min(start.Y, end.Y)), size);
        var b = Clamp(new Point(Math.Max(start.X, end.X), Math.Max(start.Y, end.Y)), size);
        return [a, new Point(b.X, a.Y), b, new Point(a.X, b.Y)];
    }
    public static Point[] Brush(IReadOnlyList<Point> stroke, double diameter, Size size)
    {
        if (stroke.Count == 0) return [];
        var radius = Math.Max(4, diameter) / 2;
        if (stroke.Count < 3) return Rectangle(stroke[0] - new Vector(radius, radius), stroke[0] + new Vector(radius, radius), size);
        var left = new List<Point>();
        var right = new List<Point>();
        for (var i = 0; i < stroke.Count; i++)
        {
            var direction = stroke[Math.Min(stroke.Count - 1, i + 1)] - stroke[Math.Max(0, i - 1)];
            var length = Math.Max(1, direction.Length);
            var offset = new Vector(-direction.Y / length * radius, direction.X / length * radius);
            left.Add(Clamp(stroke[i] + offset, size));
            right.Add(Clamp(stroke[i] - offset, size));
        }
        right.Reverse();
        var all = left.Concat(right).ToArray();
        return all.Length <= 64 ? all : Enumerable.Range(0, 64).Select(i => all[(int)Math.Round(i * (all.Length - 1d) / 63)]).ToArray();
    }
    public static double Area(Point[] points)
    {
        double area = 0;
        for (var i = 0; i < points.Length; i++)
        {
            var next = points[(i + 1) % points.Length];
            area += points[i].X * next.Y - next.X * points[i].Y;
        }
        return Math.Abs(area) / 2;
    }
    public static bool TouchedByStroke(Point[] polygon, IReadOnlyList<Point> stroke)
    {
        static bool On(Point a, Point p, Point b) => p.X >= Math.Min(a.X, b.X) && p.X <= Math.Max(a.X, b.X)
            && p.Y >= Math.Min(a.Y, b.Y) && p.Y <= Math.Max(a.Y, b.Y);
        static int Turn(Point a, Point b, Point c)
        {
            var cross = Vector.CrossProduct(b - a, c - a);
            return Math.Abs(cross) < 1e-9 ? 0 : cross > 0 ? 1 : -1;
        }
        static bool Crosses(Point a, Point b, Point c, Point d)
        {
            var abc = Turn(a, b, c); var abd = Turn(a, b, d);
            var cda = Turn(c, d, a); var cdb = Turn(c, d, b);
            return (abc != abd && cda != cdb) || (abc == 0 && On(a, c, b)) || (abd == 0 && On(a, d, b))
                || (cda == 0 && On(c, a, d)) || (cdb == 0 && On(c, b, d));
        }
        foreach (var point in stroke)
        {
            var inside = false;
            for (int i = 0, j = polygon.Length - 1; i < polygon.Length; j = i++)
            {
                var a = polygon[i]; var b = polygon[j];
                if (Turn(a, b, point) == 0 && On(a, point, b)) return true;
                if ((a.Y > point.Y) != (b.Y > point.Y) && point.X < (b.X - a.X) * (point.Y - a.Y) / (b.Y - a.Y) + a.X) inside = !inside;
            }
            if (inside) return true;
        }
        for (var s = 0; s + 1 < stroke.Count; s++)
            for (var i = 0; i < polygon.Length; i++)
                if (Crosses(stroke[s], stroke[s + 1], polygon[i], polygon[(i + 1) % polygon.Length])) return true;
        return false;
    }
    public static object Envelope(string projectId, JsonElement page, string instruction, string model,
        string resolution, IReadOnlyList<Point[]> regions, string commandId, string groupId)
    {
        if (string.IsNullOrWhiteSpace(instruction) || regions.Count is 0 or > MaxRegions || regions.Any(r => r.Length is < 3 or > 64 || Area(r) < 1
            || r.Any(p => !double.IsFinite(p.X) || !double.IsFinite(p.Y))))
            throw new ArgumentException("请填写修改指令并绘制 1–8 个有效选区。");
        return new
        {
            schema_version = 1, command_id = commandId, command_group_id = groupId,
            created_at = DateTimeOffset.UtcNow.ToString("O"),
            target = new { project_id = projectId, page_id = page.Text("id") },
            expected_version = new { scope = "page", value = page.Number("version") },
            retry_of_command_id = (string?)null, operation = "regenerate_region",
            payload = new
            {
                instruction = instruction.Trim(), model_alias = model, resolution,
                mask = regions.Select(r => new { points = r.Select(p => new[] { Math.Round(p.X, 2), Math.Round(p.Y, 2) }).ToArray() }).ToArray(),
            },
            source = new { user_prompt = instruction.Trim(), reference_asset_ids = Array.Empty<string>(), model = (string?)null, raw_output_id = "local_edit_v1" },
        };
    }
}
