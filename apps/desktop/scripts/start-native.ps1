param(
    [switch]$BuildOnly,
    [string]$UserData
)
$ErrorActionPreference = 'Stop'
$nativeRepo = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../../..'))
$nativeProject = Join-Path $nativeRepo 'apps/desktop/native/MangaFlow.Native.csproj'
Push-Location $nativeRepo
try {
    $env:DOTNET_CLI_TELEMETRY_OPTOUT = '1'
    $env:MSBuildEnableWorkloadResolver = 'false'
    & cargo build --manifest-path apps/desktop/shell-core/Cargo.toml --bin native-host
    if ($LASTEXITCODE -ne 0) { throw 'Native host build failed.' }
    & dotnet build $nativeProject -c Release --nologo
    if ($LASTEXITCODE -ne 0) { throw 'Native UI build failed.' }
    $nativeOutput = Join-Path $nativeRepo 'apps/desktop/native/bin/Release/net8.0-windows'
    Copy-Item -LiteralPath (Join-Path $nativeRepo 'apps/desktop/shell-core/target/debug/native-host.exe') -Destination $nativeOutput -Force
    if (!$BuildOnly) {
        $env:MANGAFLOW_NATIVE_REPO = $nativeRepo
        if ($UserData) { $env:MANGAFLOW_DESKTOP_USER_DATA = [IO.Path]::GetFullPath($UserData) }
        # This is the interactive application requested by the user; show its native window.
        Start-Process -FilePath (Join-Path $nativeOutput 'MangaFlow.Native.exe') -WorkingDirectory $nativeRepo
    }
}
finally { Pop-Location }
