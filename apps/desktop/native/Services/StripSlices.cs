namespace MangaFlow.Native.Services;

/// <summary>
/// PUB-01A 手机预览的条带切片几何——apps/web/lib/mobile-preview.ts computeStrip
/// 与 apps/api services/manga_export.py compute_slices 的等价移植（契约
/// docs/pub-01b-webtoon-export-contract.md §3）。坐标一律在目标宽度图像像素空间：
/// 每页按原始宽高比缩放到目标宽度后的高度、页间距、最大片高都是图像像素；
/// 渲染时再乘 viewport/target 换算成屏幕像素。页间优先收刀，超限单页等距硬切，
/// 三侧边界必须一致，改动须同步。
/// </summary>
public static class StripSlices
{
    public sealed record Item(string Id, double Top, double Height);
    public sealed record Slice(int Index, double Start, double End, bool Oversized, IReadOnlyList<string> ItemIds);
    public sealed record Strip(double TotalHeight, IReadOnlyList<Item> Items, IReadOnlyList<Slice> Slices);

    /// <summary>源图按目标宽度缩放后的高度；缺尺寸时回退 4:3 竖版占位高。</summary>
    public static int ScaledPageHeight(int? width, int? height, int targetWidth)
    {
        if (width is > 0 && height is > 0)
            return (int)Math.Round(height.Value * (double)targetWidth / width.Value, MidpointRounding.AwayFromZero);
        return (int)Math.Round(targetWidth * 4.0 / 3.0, MidpointRounding.AwayFromZero);
    }

    /// <summary>
    /// 页间优先贪心：当下一个条目的底边会使当前片超过 maxSlicePx 时，在该条目
    /// 顶边的页间缝处收刀；单个条目自身超过 maxSlicePx 时等距硬切成独立片。
    /// 间距计入条带高度且挂在前一个条目之后（切片边界=下一条目顶边）。
    /// </summary>
    public static Strip ComputeStrip(IReadOnlyList<(string Id, double HeightPx)> entries, double gapPx, double maxSlicePx)
    {
        var items = new List<Item>();
        var cursor = 0.0;
        for (var index = 0; index < entries.Count; index++)
        {
            var height = Math.Max(0, entries[index].HeightPx);
            items.Add(new Item(entries[index].Id, cursor, height));
            cursor += height + (index < entries.Count - 1 ? gapPx : 0);
        }
        var totalHeight = cursor;
        var slices = new List<Slice>();
        if (items.Count == 0 || maxSlicePx <= 0) return new Strip(totalHeight, items, slices);

        var sliceStart = 0.0;
        var sliceItems = new List<string>();
        foreach (var entry in items)
        {
            var bottom = entry.Top + entry.Height;
            if (entry.Top > sliceStart && bottom - sliceStart > maxSlicePx)
            {
                slices.Add(new Slice(slices.Count + 1, sliceStart, entry.Top, false, [.. sliceItems]));
                sliceStart = entry.Top;
                sliceItems = [];
            }
            if (entry.Height > maxSlicePx)
            {
                var offset = 0.0;
                while (offset < entry.Height)
                {
                    var segment = Math.Min(maxSlicePx, entry.Height - offset);
                    slices.Add(new Slice(slices.Count + 1, sliceStart + offset, sliceStart + offset + segment, true, [entry.Id]));
                    offset += segment;
                }
                sliceStart = bottom;
                sliceItems = [];
                continue;
            }
            sliceItems.Add(entry.Id);
        }
        if (sliceStart < totalHeight || slices.Count == 0)
            slices.Add(new Slice(slices.Count + 1, sliceStart, totalHeight, false, [.. sliceItems]));
        return new Strip(totalHeight, items, slices);
    }
}
