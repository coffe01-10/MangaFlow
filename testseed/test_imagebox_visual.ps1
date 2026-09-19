$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName PresentationFramework, PresentationCore, WindowsBase
$dll = "D:\自媒体\漫画工作流\apps\desktop\native\bin\Release\net8.0-windows\MangaFlow.Native.dll"
$asm = [Reflection.Assembly]::LoadFrom($dll)
$app = New-Object System.Windows.Application
$app.Resources.MergedDictionaries.Add((New-Object System.Windows.ResourceDictionary -Property @{ Source = (New-Object Uri("/MangaFlow.Native;component/Theme.xaml", [UriKind]::Relative)) }))
$type = $asm.GetType("MangaFlow.Native.Controls.ImageBox")
$box = New-Object $type
$text = New-Object System.Windows.Controls.TextBlock
$text.Text = "HELLO"
$box.Content = $text
$host1 = New-Object System.Windows.Controls.Border
$host1.Child = $box
$host1.Measure((New-Object System.Windows.Size(200, 200)))
$host1.Arrange((New-Object System.Windows.Rect(0, 0, 200, 200)))
$host1.UpdateLayout()
function Count-Visuals($v) {
    $n = 1
    foreach ($c in [System.Windows.Media.VisualTreeHelper]::GetChildren($v)) { $n += Count-Visuals $c }
    return $n
}
Write-Output ("ImageBox visual tree node count: " + (Count-Visuals $box))
$plain = New-Object System.Windows.Controls.ContentControl
$plain.Content = $text
$host2 = New-Object System.Windows.Controls.Border
$host2.Child = $plain
$host2.Measure((New-Object System.Windows.Size(200, 200)))
$host2.Arrange((New-Object System.Windows.Rect(0, 0, 200, 200)))
$host2.UpdateLayout()
Write-Output ("plain ContentControl (no style) node count: " + (Count-Visuals $plain))
