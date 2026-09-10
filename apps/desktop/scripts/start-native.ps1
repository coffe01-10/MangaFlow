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
    # Release host (#315): the copied host used to be the DEBUG build, so
    # startup measurements against this output directory timed a debug binary.
    # Debuggability is unaffected — NativeBackend falls back to the debug path
    # when present.
    & cargo build --release --manifest-path apps/desktop/shell-core/Cargo.toml --bin native-host
    if ($LASTEXITCODE -ne 0) { throw 'Native host build failed.' }
    & dotnet build $nativeProject -c Release --nologo
    if ($LASTEXITCODE -ne 0) { throw 'Native UI build failed.' }
    $nativeOutput = Join-Path $nativeRepo 'apps/desktop/native/bin/Release/net8.0-windows'
    # The WPF client loads the host from ITS output directory first (NativeBackend),
    # so a failed or partial copy here would silently run a stale host. Build and
    # copy are already fail-closed; the hash comparison additionally rejects a
    # truncated/locked copy that Copy-Item reported as success.
    $hostSource = Join-Path $nativeRepo 'apps/desktop/shell-core/target/release/native-host.exe'
    $hostTarget = Join-Path $nativeOutput 'native-host.exe'
    Copy-Item -LiteralPath $hostSource -Destination $hostTarget -Force
    if ((Get-FileHash -LiteralPath $hostSource -Algorithm SHA256).Hash -ne
        (Get-FileHash -LiteralPath $hostTarget -Algorithm SHA256).Hash) {
        throw 'native-host.exe copy verification failed: output copy differs from the freshly built host.'
    }
    Write-Host ("Host deployed: {0} (SHA256 {1})" -f $hostTarget, (Get-FileHash -LiteralPath $hostTarget -Algorithm SHA256).Hash)
    if (!$BuildOnly) {
        $env:MANGAFLOW_NATIVE_REPO = $nativeRepo
        if ($UserData) { $env:MANGAFLOW_DESKTOP_USER_DATA = [IO.Path]::GetFullPath($UserData) }
        # This is the interactive application requested by the user; show its native window.
        Start-Process -FilePath (Join-Path $nativeOutput 'MangaFlow.Native.exe') -WorkingDirectory $nativeRepo
    }
}
finally { Pop-Location }
