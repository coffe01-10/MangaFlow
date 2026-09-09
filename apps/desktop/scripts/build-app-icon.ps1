param([Parameter(Mandatory=$true)][string]$Source)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Drawing
# Fit the existing artwork into an icon viewport; do not redraw the supplied design.
Add-Type -ReferencedAssemblies System.Drawing -TypeDefinition @'
using System;
using System.Drawing;
public static class IconViewport {
    public static Rectangle Bounds(Bitmap bitmap) {
        int left=bitmap.Width, top=bitmap.Height, right=-1, bottom=-1;
        for(int y=0;y<bitmap.Height;y++) for(int x=0;x<bitmap.Width;x++) {
            if(bitmap.GetPixel(x,y).A <= 8) continue;
            left=Math.Min(left,x); top=Math.Min(top,y); right=Math.Max(right,x); bottom=Math.Max(bottom,y);
        }
        if(right<left) throw new ArgumentException("Image contains no visible artwork");
        return Rectangle.FromLTRB(left,top,right+1,bottom+1);
    }
}
'@
$repo = Split-Path (Split-Path (Split-Path $PSScriptRoot -Parent) -Parent) -Parent
$icons = Join-Path $repo 'apps\desktop\src-tauri\icons'
$sourceImage = [System.Drawing.Bitmap]::new((Resolve-Path -LiteralPath $Source).Path)
$frames = @()
try {
    $bounds = [IconViewport]::Bounds($sourceImage)
    $side = [Math]::Ceiling([Math]::Max($bounds.Width,$bounds.Height) / 0.94)
    $viewport = [System.Drawing.RectangleF]::new(($bounds.Left+$bounds.Width/2-$side/2),($bounds.Top+$bounds.Height/2-$side/2),$side,$side)
    foreach($size in @(16,20,24,32,40,48,64,128,256)) {
        $bitmap = [System.Drawing.Bitmap]::new($size,$size,[System.Drawing.Imaging.PixelFormat]::Format32bppArgb)
        $graphics = [System.Drawing.Graphics]::FromImage($bitmap)
        $stream = [System.IO.MemoryStream]::new()
        try {
            $graphics.Clear([System.Drawing.Color]::Transparent)
            $graphics.CompositingMode = [System.Drawing.Drawing2D.CompositingMode]::SourceCopy
            $graphics.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
            $graphics.PixelOffsetMode = [System.Drawing.Drawing2D.PixelOffsetMode]::HighQuality
            $graphics.DrawImage($sourceImage,[System.Drawing.RectangleF]::new(0,0,$size,$size),$viewport,[System.Drawing.GraphicsUnit]::Pixel)
            $bitmap.Save($stream,[System.Drawing.Imaging.ImageFormat]::Png)
            $frames += @{Size=$size; Data=$stream.ToArray()}
            if($size -eq 32 -or $size -eq 128) { [System.IO.File]::WriteAllBytes((Join-Path $icons "${size}x${size}.png"),$stream.ToArray()) }
        } finally { $stream.Dispose(); $graphics.Dispose(); $bitmap.Dispose() }
    }
    Write-Output "Artwork bounds: $bounds; icon viewport: $viewport; target coverage: 94%"
} finally { $sourceImage.Dispose() }
$writer = [System.IO.BinaryWriter]::new([System.IO.File]::Create((Join-Path $icons 'icon.ico')))
try {
    $writer.Write([uint16]0); $writer.Write([uint16]1); $writer.Write([uint16]$frames.Count)
    $offset = 6 + 16 * $frames.Count
    foreach($frame in $frames) {
        $dimension = if($frame.Size -eq 256) { 0 } else { $frame.Size }
        $writer.Write([byte]$dimension); $writer.Write([byte]$dimension); $writer.Write([byte]0); $writer.Write([byte]0)
        $writer.Write([uint16]1); $writer.Write([uint16]32); $writer.Write([uint32]$frame.Data.Length); $writer.Write([uint32]$offset)
        $offset += $frame.Data.Length
    }
    foreach($frame in $frames) { $writer.Write([byte[]]$frame.Data) }
} finally { $writer.Dispose() }
