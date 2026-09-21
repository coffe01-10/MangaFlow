<#
    NUI-8 P4-2：安装/升级/卸载独立验收（沿用旧壳 W-12 配方）。

    红线：安装目录与用户数据目录都取临时路径，绝不触碰
    %LOCALAPPDATA%\com.mangaflow.desktop（旧 Tauri 壳）与
    %LOCALAPPDATA%\MangaFlow\Native（真机客户端默认数据）。
    静默安装不建快捷方式（-NoShortcuts 构建），因此不污染真实桌面。

    口径：本脚本证明的是「安装器语义」——静默装/覆盖升级/静默卸的文件与
    用户数据后果，以及注册表版本项；它不证明客户端能启动。启动可用性由
    -Launch 单独探测，未探测时台账必须记 NOT RUN，不得借本脚本勾选。
#>
param(
    [Parameter(Mandatory = $true)][string]$Installer,
    [string]$Version = "1.0.0-rc2",
    [string]$Root = "",
    [switch]$Launch
)

$ErrorActionPreference = "Stop"
$repo = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot "..\..\.."))
if (-not $Root) { $Root = Join-Path $env:TEMP ("mangaflow-installer-verify-" + [Guid]::NewGuid().ToString("N")) }
$install = Join-Path $Root "install"
$userData = Join-Path $Root "user-data"
if ($Root -match " ") { throw "验收根目录不能含空格：NSIS /D= 参数不接收引号" }
New-Item -ItemType Directory -Force -Path $install, $userData | Out-Null

function Get-Snapshot($directory) {
    if (-not (Test-Path -LiteralPath $directory)) { return @{} }
    $map = [ordered]@{}
    foreach ($file in (Get-ChildItem -LiteralPath $directory -Recurse -File | Sort-Object FullName)) {
        # 客户端刚被 Kill 时，sidecar 子进程可能还持着 SQLite 句柄；这里把读不到
        # 记成 LOCKED 而不是抛异常（ErrorActionPreference=Stop 会直接终止整轮验收）。
        try {
            $map[$file.FullName.Substring($directory.Length).TrimStart('\')] =
                (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash
        } catch { $map[$file.FullName.Substring($directory.Length).TrimStart('\')] = "LOCKED" }
    }
    return $map
}
function Compare-Snapshot($before, $after) {
    $drift = @()
    foreach ($key in $before.Keys) {
        if (-not $after.Contains($key)) { $drift += "missing:$key" }
        elseif ($after[$key] -ne $before[$key]) { $drift += "changed:$key" }
    }
    foreach ($key in $after.Keys) { if (-not $before.Contains($key)) { $drift += "added:$key" } }
    return $drift
}
function Assert-Exit($exitCode, $label) {
    # WScript.Shell.Run 以 bWaitOnReturn=$true 调用时直接返回子进程退出码（Int32），
    # 不是 Process 对象；上一版按 Process 写 WaitForExit，首轮即在 [1/4] 崩。
    if ($exitCode -ne 0) { throw "$label 退出码 $exitCode" }
}

# 播种用户数据：升级与卸载都必须逐字节留下它。
Set-Content -LiteralPath (Join-Path $userData "window.json") -Value '{"width":1320}' -Encoding UTF8
New-Item -ItemType Directory -Force -Path (Join-Path $userData "storage") | Out-Null
[IO.File]::WriteAllBytes((Join-Path $userData "storage\seed.bin"), (1..64 | ForEach-Object { [byte]$_ }))
$snapshot0 = Get-Snapshot $userData
$shell = New-Object -ComObject WScript.Shell

Write-Host "[1/4] 静默全新安装"
Assert-Exit $shell.Run("""$Installer"" /S /D=$install", 0, $true) "安装"
$layout = @(
    "client\MangaFlow.Native.exe", "client\native-host.exe",
    "apps\api\alembic.ini", "apps\desktop\sidecar\mangaflow_desktop_helper.py",
    ".venv-desktop\Scripts\python.exe", "unins000.exe")
$layoutMissing = @($layout | Where-Object { -not (Test-Path -LiteralPath (Join-Path $install $_)) })
$registered = (Get-ItemProperty -Path "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\MangaFlow.Native" `
    -ErrorAction SilentlyContinue).DisplayVersion
$driftAfterInstall = Compare-Snapshot $snapshot0 (Get-Snapshot $userData)

Write-Host "[2/4] 覆盖升级（同目录再装一次）"
Assert-Exit $shell.Run("""$Installer"" /S /D=$install", 0, $true) "升级"
$driftAfterUpgrade = Compare-Snapshot $snapshot0 (Get-Snapshot $userData)
$layoutMissingUpgrade = @($layout | Where-Object { -not (Test-Path -LiteralPath (Join-Path $install $_)) })

$launchProbe = "NOT RUN —— 未加 -Launch"
$driftAfterLaunch = @()
if ($Launch) {
    # 硬规则：探测绝不写真实 %LOCALAPPDATA%\MangaFlow\Native，一律指向隔离目录。
    $env:MANGAFLOW_DESKTOP_USER_DATA = $userData
    try {
        $process = Start-Process -FilePath (Join-Path $install "client\MangaFlow.Native.exe") -PassThru
        Start-Sleep -Seconds 40
        if ($process.HasExited) { $launchProbe = "FAILED exit=$($process.ExitCode)" }
        else {
            $launchProbe = "RUNNING（40s 未退出）"
            $process.Kill(); $process.WaitForExit(30000) | Out-Null
        }
        # Kill 之后 native-host 靠 stdin 关闭自检并写 stopped，但它自己的 exe 句柄
        # 还要一瞬间才放；NSIS 的 RMDir /r 不重试，早一步卸载就会把 client\ 里
        # 这个文件留成「卸载残留」。等到真能独占打开再继续。
        $hostExe = Join-Path $install "client\native-host.exe"
        $releaseDeadline = (Get-Date).AddSeconds(30)
        while ((Get-Date) -lt $releaseDeadline) {
            try { $handle = [IO.File]::Open($hostExe, 'Open', 'ReadWrite', 'None'); $handle.Close(); break }
            catch { Start-Sleep -Milliseconds 500 }
        }
        # 判据不只看进程没退：安装目录里的客户端必须真的把 sidecar 拉到 health，
        # 否则「窗口没关」也可能只是卡在启动失败弹窗上。
        $shellLog = Get-ChildItem -LiteralPath (Join-Path $userData "logs") -Filter "shell-*.log" `
            -File -ErrorAction SilentlyContinue | Sort-Object LastWriteTime | Select-Object -Last 1
        if (-not $shellLog) { $launchProbe += "；无 shell 日志 = sidecar 从未启动" }
        else {
            $events = Get-Content -LiteralPath $shellLog.FullName -Raw
            $missing = @("ready_verified", "healthy", "stopped") | Where-Object { $events -notmatch "`"$_`"" }
            if ($missing.Count) { $launchProbe += "；sidecar 缺事件 $($missing -join ',')" }
            else { $launchProbe += "；sidecar ready_verified→healthy→stopped 全齐（安装目录可直接起服务）" }
        }
        # 客户端真的跑起来就会合法写用户数据（窗口状态、storage），因此这一轮的
        # 零漂移断言必须让位给启动探测，否则把「启动成功」报成失败。
        $driftAfterLaunch = Compare-Snapshot $snapshot0 (Get-Snapshot $userData)
    } finally { Remove-Item Env:MANGAFLOW_DESKTOP_USER_DATA -ErrorAction SilentlyContinue }
}

Write-Host "[3/4] 静默卸载"
Assert-Exit $shell.Run("""$(Join-Path $install 'unins000.exe')"" /S", 0, $true) "卸载"
# unins000.exe 会把自身复制到 %TEMP% 后立刻退出，Run 的退出码只覆盖那第一跳；
# 真正删文件与写注册表的是复制出来的那个进程。首轮按固定 2s 等待误判成
# 「卸载残留 .venv-desktop/unins000.exe + 注册表未删」，故改为轮询到实际结束。
$uninstallKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\MangaFlow.Native"
$deadline = (Get-Date).AddSeconds(300)
do {
    Start-Sleep -Milliseconds 500
    $dirGone = -not (Test-Path -LiteralPath $install)
    $registryGone = -not (Test-Path $uninstallKey)
} until (($dirGone -and $registryGone) -or (Get-Date) -gt $deadline)
if (-not $dirGone -or -not $registryGone) { Write-Host "WARN 卸载在 300s 内未结束（目录消失=$dirGone 注册表移除=$registryGone）" }
$leftover = @()
foreach ($entry in @("client", "apps", ".venv-desktop", "unins000.exe")) {
    if (Test-Path -LiteralPath (Join-Path $install $entry)) { $leftover += $entry }
}
$driftAfterUninstall = Compare-Snapshot $snapshot0 (Get-Snapshot $userData)

Write-Host "[4/4] 证据落盘"
$evidence = [ordered]@{
    at = (Get-Date).ToString("yyyy-MM-dd HH:mm:ss")
    installer = (Get-Item -LiteralPath $Installer).Length
    install_dir = $install
    user_data_dir = $userData
    install_layout_missing = $layoutMissing
    upgrade_layout_missing = $layoutMissingUpgrade
    registered_display_version = $registered
    expected_version = $Version
    user_data_drift_after_install = $driftAfterInstall
    user_data_drift_after_upgrade = $driftAfterUpgrade
    user_data_drift_after_uninstall = $driftAfterUninstall
    uninstall_leftover = $leftover
    uninstall_registry_removed = $registryGone
    launch_probe = $launchProbe
    user_data_drift_after_launch = $driftAfterLaunch
}
$path = Join-Path $Root "installer-verify.json"
$evidence | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $path -Encoding UTF8
Write-Host "evidence -> $path"

$failed = @()
if ($layoutMissing.Count) { $failed += "全新安装布局缺项: $($layoutMissing -join ',')" }
if ($layoutMissingUpgrade.Count) { $failed += "升级后布局缺项: $($layoutMissingUpgrade -join ',')" }
if ($registered -ne $Version) { $failed += "注册表版本项不符: '$registered' != '$Version'" }
$driftPhases = @("user_data_drift_after_install", "user_data_drift_after_upgrade")
if (-not $Launch) { $driftPhases += "user_data_drift_after_uninstall" }
foreach ($phase in $driftPhases) {
    if ($evidence[$phase] -and $evidence[$phase].Count) { $failed += "$phase 用户数据被改动: $($evidence[$phase] -join ',')" }
}
if ($leftover.Count) { $failed += "卸载后安装目录残留: $($leftover -join ',')" }
if (-not $registryGone) { $failed += "卸载后 HKCU 卸载项仍在" }
if ($failed.Count) { $failed | ForEach-Object { Write-Host "FAIL $_" }; throw "安装验收失败（$($failed.Count) 项）" }
Write-Host "PASS 静默安装/覆盖升级/静默卸载与用户数据保留全部通过；启动探测：$launchProbe"
