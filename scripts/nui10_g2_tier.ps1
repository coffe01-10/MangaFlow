param([int]$Candidates = 300, [string]$OutName = "g2-memory-$Candidates.csv")
# NUI-10 net10 G2 tier driver (recipe = nui9_g2_tier.ps1, net10 env).
# PS 5.1 pitfalls designed around (all hit 2026-09-20):
#  1) EAP='Stop' turns any native stderr line into a terminating error → 'Continue'.
#  2) Piping/redirecting a native command waits for pipe EOF; the orchestrator's
#     uvicorn/sidecar grandchildren inherit the write end and never close it →
#     `| Out-Null`, `2>&1`, and `Start-Process -RedirectStandardOutput -Wait`
#     ALL hang forever. Invoke natively with NO redirection instead; the two
#     short "session started" lines just land in this script's own output.
#  3) With redirection, Process.ExitCode reads back NULL (verified by
#     tmp-exitcode-test.ps1) → use $LASTEXITCODE after plain invocations.
$ErrorActionPreference = 'Continue'
$repo = (Get-Location).Path
$py = "$repo\.venv\Scripts\python.exe"
$run = "$repo\output\nui10-g2-tier"
$stepLog = "$repo\output\nui10-rc1\p2-g2\driver-steps.log"
$env:DOTNET_ROOT = "$env:LOCALAPPDATA\Microsoft\dotnet10"

function Mark([string]$message) {
    Add-Content -Path $stepLog -Value ("[{0}] {1}" -f (Get-Date -Format 'HH:mm:ss'), $message)
}

function Invoke-Orchestrator([string]$orchArguments, [switch]$TolerateNoSession) {
    Mark "orch-start: $orchArguments"
    $parts = $orchArguments.Split(' ')
    & python @parts
    $code = $LASTEXITCODE
    Mark "orch-done: exit=$code"
    if ($code -ne 0 -and -not ($TolerateNoSession -and $code -eq 1)) {
        throw "orchestrator '$orchArguments' exit=$code"
    }
}

# 干净起点（stop 无会话时 orchestrator 退出码 1 = 幂等成功）
New-Item -ItemType Directory -Force -Path "$repo\output\nui10-rc1\p2-g2" | Out-Null
Remove-Item $stepLog -ErrorAction SilentlyContinue
Mark "driver begin, candidates=$Candidates"
Invoke-Orchestrator "scripts/nui67_side_by_side.py stop" -TolerateNoSession
if (Test-Path $run) { Remove-Item -Recurse -Force $run }
Mark "run-dir cleaned"

# 1. 全新种子启动（web+native 基线数据）
Invoke-Orchestrator "scripts/nui67_side_by_side.py start --run-dir $run"
if (-not (Test-Path "$run\native-data\data\mangaflow.db")) {
    throw "G2[$Candidates]: baseline start produced no native DB"
}
Mark "baseline stack up"
Invoke-Orchestrator "scripts/nui67_side_by_side.py stop" -TolerateNoSession

# 2. 客户端未运行时注入长列表数据到 native 库
$nativeDb = "$run/native-data/data/mangaflow.db"
$nativeStorage = "$run/native-data/storage"
Mark "long-list seed begin"
& $py scripts/nui67_seed_long_list.py "sqlite:///$($nativeDb -replace '\\','/')" $nativeStorage --candidates $Candidates
$seedCode = $LASTEXITCODE
Mark "long-list seed done, exit=$seedCode"
if ($seedCode -ne 0) { throw "G2[$Candidates]: long-list seed failed (exit=$seedCode)" }
Write-Output ("G2[$Candidates]: native seeded (candidates=$Candidates)")

# 3. 无种子重启（保留长列表数据）
Invoke-Orchestrator "scripts/nui67_side_by_side.py start --run-dir $run --no-seed"
Start-Sleep -Seconds 6
$proc = @(Get-Process MangaFlow.Native -ErrorAction SilentlyContinue)
if ($proc.Count -eq 0) { throw "G2[$Candidates]: MangaFlow.Native not running after start" }
Mark ("stack up, wpf pid=" + $proc[0].Id)

# 4. 测量（measure 自身不长驻子进程，可直接调用并取 $LASTEXITCODE）
Mark "measure begin"
& powershell -NoProfile -ExecutionPolicy Bypass -File apps/desktop/scripts/measure_library_memory.ps1 -Samples 20 -OutCsv "$repo\output\nui10-rc1\p2-g2\$OutName"
$mCode = $LASTEXITCODE
Mark "measure done, exit=$mCode"
if (-not (Test-Path "$repo\output\nui10-rc1\p2-g2\$OutName")) {
    throw "G2[$Candidates]: measure produced no CSV (exit=$mCode)"
}

# 5. 停栈
Invoke-Orchestrator "scripts/nui67_side_by_side.py stop" -TolerateNoSession
Mark "done"
Write-Output "G2[$Candidates]: done"
