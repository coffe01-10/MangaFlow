param([int]$Candidates = 300)
$ErrorActionPreference = 'Stop'
$repo = (Get-Location).Path
$py = "$repo\.venv\Scripts\python.exe"
$run = "$repo\output\nui9-g2-tier"

# 干净起点
python scripts/nui67_side_by_side.py stop 2>&1 | Out-Null
if (Test-Path $run) { Remove-Item -Recurse -Force $run }

# 1. 全新种子启动（web+native 基线数据）
python scripts/nui67_side_by_side.py start --run-dir $run 2>&1 | Out-Null
Write-Output ("G2[$Candidates]: baseline stack up")
python scripts/nui67_side_by_side.py stop 2>&1 | Select-Object -Last 1 | Out-Null

# 2. 客户端未运行时注入长列表数据到 native 库
$nativeDb = "$run/native-data/data/mangaflow.db"
$nativeStorage = "$run/native-data/storage"
$r = & $py scripts/nui67_seed_long_list.py "sqlite:///$($nativeDb -replace '\\','/')" $nativeStorage --candidates $Candidates 2>&1 | Select-Object -Last 1
Write-Output ("G2[$Candidates]: native seeded -> " + $r)

# 3. 无种子重启（保留长列表数据）
python scripts/nui67_side_by_side.py start --run-dir $run --no-seed 2>&1 | Out-Null
Start-Sleep -Seconds 6

# 4. 测量
New-Item -ItemType Directory -Force -Path "$repo\output\nui9-rc\p5-g2" | Out-Null
powershell -NoProfile -ExecutionPolicy Bypass -File apps/desktop/scripts/measure_library_memory.ps1 -Samples 20 -OutCsv "$repo\output\nui9-rc\p5-g2\g2-memory-$Candidates.csv" 2>&1 | Select-Object -Last 4

# 5. 停栈
python scripts/nui67_side_by_side.py stop 2>&1 | Select-Object -Last 1
Write-Output "G2[$Candidates]: done"
