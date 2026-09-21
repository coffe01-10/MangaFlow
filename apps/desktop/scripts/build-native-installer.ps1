<#
    NUI-8 P4-1：组装 staging 并调用 makensis 产出 WPF 客户端安装器。

    布局按 App.xaml.cs:41-47 的向上查找契约摆：client\ 放客户端输出，
    apps\api / apps\desktop\sidecar / .venv-desktop 与 client\ 同级，
    这样安装后无需设 MANGAFLOW_NATIVE_REPO 也能定位后端。
#>
param(
    [string]$Version = "0.9.0",
    [string]$Staging = "",
    [string]$OutDir = "",
    [ValidateSet("zlib", "lzma", "bzip2")][string]$Compressor = "zlib",
    [string]$MakeNSIS = "",
    [string]$SignThumbprint = "",
    [string]$SignTool = "",
    [switch]$NoShortcuts
)

$ErrorActionPreference = "Stop"
$repo = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot "..\..\.."))
if (-not $OutDir) { $OutDir = Join-Path $repo "output\desktop-installer" }
# staging 默认留在仓库内（已 gitignore）：payload 200 MB 级，放工作区里才能被
# 后续校验直接检视，也不在系统临时目录里留下无人认领的副本。
if (-not $Staging) { $Staging = Join-Path $OutDir "staging" }

function Copy-Tree($source, $target, $excludeDirs) {
    if (-not (Test-Path -LiteralPath $source)) { throw "缺少 payload：$source" }
    $arguments = @($source, $target, "/E", "/NFL", "/NDL", "/NJH", "/NJS", "/NP", "/XD") + $excludeDirs
    & robocopy.exe @arguments | Out-Null
    $code = $LASTEXITCODE
    if ($code -ge 8) { throw "robocopy 失败（$code）：$source -> $target" }
}

$nativeOut = Join-Path $repo "apps\desktop\native\bin\Release\net10.0-windows"
if (-not (Test-Path -LiteralPath (Join-Path $nativeOut "MangaFlow.Native.exe"))) {
    throw "客户端未构建：先 dotnet build apps/desktop/native -c Release"
}
if (-not (Test-Path -LiteralPath (Join-Path $nativeOut "native-host.exe"))) {
    throw "安装 payload 缺 native-host.exe：客户端 StartAsync 需要它，Release 输出里必须已就位"
}

if (Test-Path -LiteralPath $Staging) { Remove-Item -LiteralPath $Staging -Recurse -Force }
New-Item -ItemType Directory -Force -Path $Staging | Out-Null

Copy-Tree $nativeOut (Join-Path $Staging "client") @("__pycache__")
Copy-Tree (Join-Path $repo "apps\api") (Join-Path $Staging "apps\api") @(".venv", "__pycache__", ".pytest_cache")
Copy-Tree (Join-Path $repo "apps\desktop\sidecar") (Join-Path $Staging "apps\desktop\sidecar") @("__pycache__")
Copy-Tree (Join-Path $repo ".venv-desktop") (Join-Path $Staging ".venv-desktop") @("__pycache__")

foreach ($probe in @(
    "client\MangaFlow.Native.exe",
    "client\native-host.exe",
    "apps\api\alembic.ini",
    "apps\desktop\sidecar\mangaflow_desktop_helper.py",
    ".venv-desktop\Scripts\python.exe")) {
    if (-not (Test-Path -LiteralPath (Join-Path $Staging $probe))) { throw "staging 校验失败：$probe" }
}

if (-not $MakeNSIS) {
    foreach ($candidate in @((Join-Path $repo "tools\nsis-3.10\makensis.exe"),
                             "C:\Program Files (x86)\NSIS\makensis.exe",
                             "C:\Program Files\NSIS\makensis.exe")) {
        if (Test-Path -LiteralPath $candidate) { $MakeNSIS = $candidate; break }
    }
}
if (-not $MakeNSIS -or -not (Test-Path -LiteralPath $MakeNSIS)) {
    throw "找不到 makensis：仓库内 tools\nsis-3.10 与系统 NSIS 均无，或传 -MakeNSIS"
}

New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
$script = Join-Path $repo "apps\desktop\installer\mangaflow-native.nsi"
# NSIS 3 把无 BOM 的脚本按系统 ACP 解码，本脚本的中文注释会直接触发
# "Bad text encoding ... on line 1" 并中止打包，所以在这里提前拦下。
$nsiHead = [IO.File]::ReadAllBytes($script)[0..2]
if (-not ($nsiHead[0] -eq 0xEF -and $nsiHead[1] -eq 0xBB -and $nsiHead[2] -eq 0xBF)) {
    throw "mangaflow-native.nsi 缺 UTF-8 BOM：makensis 会按 ACP 解码并报 Bad text encoding"
}
$outFile = Join-Path $OutDir "MangaFlow-Native-$Version-x64.exe"
$defines = @("/DVERSION=$Version", "/DSTAGING=$Staging", "/DOUTFILE=$outFile", "/DCOMPRESSOR=$Compressor")
if ($NoShortcuts) { $defines += "/DNO_SHORTCUTS" }
& $MakeNSIS @defines $script
if ($LASTEXITCODE -ne 0) { throw "makensis 退出码 $LASTEXITCODE" }
if (-not (Test-Path -LiteralPath $outFile)) { throw "安装器未产出：$outFile" }

$bytes = (Get-Item -LiteralPath $outFile).Length
Write-Host ("installer: {0}`nsize-mb: {1:N1}`nsha256: {2}" -f $outFile, ($bytes / 1MB),
    (Get-FileHash -LiteralPath $outFile -Algorithm SHA256).Hash)

if ($SignThumbprint) {
    if (-not $SignTool) {
        foreach ($candidate in @("C:\Program Files (x86)\Windows Kits\10\bin\10.0.26100.0\x64\signtool.exe",
                                 "C:\Program Files\Windows Kits\10\bin\10.0.26100.0\x64\signtool.exe")) {
            if (Test-Path -LiteralPath $candidate) { $SignTool = $candidate; break }
        }
    }
    if (-not $SignTool -or -not (Test-Path -LiteralPath $SignTool)) { throw "找不到 signtool.exe，传 -SignTool" }
    # 签名会改写 PE 尾部，产物 sha256 随之变化，所以打完必须重新报摘要。
    # 不加 /tr：时间戳要出网打第三方 RFC3161，本轮按「不产生对外请求」的口径跳过。
    & $SignTool sign /fd SHA256 /sha1 $SignThumbprint /d "MangaFlow Desktop Client" $outFile
    if ($LASTEXITCODE -ne 0) { throw "signtool 签名退出码 $LASTEXITCODE" }
    & $SignTool verify /pa $outFile
    Write-Host ("signed-sha256: {0}" -f (Get-FileHash -LiteralPath $outFile -Algorithm SHA256).Hash)
}
