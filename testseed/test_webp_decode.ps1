Add-Type -AssemblyName PresentationCore
$path = "C:\Windows\Temp\nui67test.webp"
try {
    $img = New-Object System.Windows.Media.Imaging.BitmapImage
    $img.BeginInit()
    $img.CacheOption = [System.Windows.Media.Imaging.BitmapCacheOption]::OnLoad
    $img.StreamSource = New-Object IO.MemoryStream (,[IO.File]::ReadAllBytes($path))
    $img.CreateOptions = [System.Windows.Media.Imaging.BitmapCreateOptions]::IgnoreColorProfile
    $img.EndInit()
    Write-Output ("decoded: " + $img.PixelWidth + "x" + $img.PixelHeight)
} catch {
    Write-Output ("decode FAILED: " + $_.Exception.Message)
}
