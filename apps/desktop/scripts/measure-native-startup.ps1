param(
    [int]$Samples = 3,
    # G1 cold/hot: cold (default) provisions a fresh user-data dir per sample,
    # hot reuses one dir so the backend is already migrated on every launch.
    # Neither is a disk-cold run (that needs the system file cache dropped),
    # so label the column, do not call either one "cold start" in a report.
    [switch]$HotReuse,
    [string]$OutCsv = ''
)
$ErrorActionPreference = 'Stop'
$repo = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../../..'))
$exe = Join-Path $repo 'apps/desktop/native/bin/Release/net8.0-windows/MangaFlow.Native.exe'
$dataRoot = Join-Path $env:TEMP ("mangaflow-perf-" + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Force -Path $dataRoot | Out-Null
$results = @()
# Current sample's process, so the finally block can tree-kill it after a
# Ctrl+C or a mid-sample throw. Cleared once a sample has been accounted for.
$proc = $null
function Stop-SampleTree {
    # #896: kill through the HELD [System.Diagnostics.Process] object — its
    # identity is pinned by handle from Start() to Kill(). The old shape
    # (Get-Process name check, then `taskkill /PID … /T /F`) re-resolved the
    # pid a second time: a pid recycled in that window was tree-killed with
    # no name check left. The client spawns a job-less child tree
    # (native-host → python sidecar); killing only the WPF root would orphan
    # the children holding the API port, so the kill must still cover the
    # whole tree.
    param([System.Diagnostics.Process]$Sample)
    if ($null -eq $Sample) { return }
    try {
        $Sample.Refresh()
        if (-not $Sample.HasExited) {
            if ($PSVersionTable.PSVersion.Major -ge 7) {
                # .NET Core: Kill(entireProcessTree) — handle-pinned tree kill,
                # no pid re-resolution anywhere.
                $Sample.Kill($true)
            }
            else {
                # Windows PowerShell 5.1 (.NET Framework) has no Kill(bool):
                # snapshot the descendants (pid → image name) while the root
                # handle is still verifiably ours, kill the ROOT handle-pinned,
                # then kill each child only while its live image still matches
                # the snapshot — a mismatched image is a pid reuse, skipped
                # (its own owner reaps it). Residual window (snapshot → child
                # kill) is image-verified, unlike the old unchecked /T walk.
                $kids = Get-DescendantPids -RootPid $Sample.Id
                $Sample.Kill()
                foreach ($childPid in $kids.Keys) {
                    $still = Get-Process -Id $childPid -ErrorAction SilentlyContinue
                    if ($still -and (($still.ProcessName + '.exe') -eq $kids[$childPid])) {
                        & taskkill /PID $childPid /F | Out-Null
                    }
                }
            }
        }
    }
    catch [InvalidOperationException] { }   # exited between HasExited and Kill — already gone
    catch [System.ComponentModel.Win32Exception] {
        Write-Warning ("Stop-SampleTree: root kill failed: {0}" -f $_.Exception.Message)
    }
}

function Get-DescendantPids {
    # #444: the python sidecar is a GRANDCHILD (WPF → native-host → python),
    # but Win32_Process ParentProcessId matches DIRECT children only — the
    # historical single-level snapshot could not see a leaked sidecar still
    # holding the API port, so `LeftoverChildren` reported a false (empty).
    # Walk the whole descendant tree, deduped (a pid reattached to an
    # earlier ancestor must not loop the walk), bounded by the dedup set.
    param([int]$RootPid)
    $seen = @{}
    $frontier = @($RootPid)
    while ($frontier.Count -gt 0) {
        $next = @()
        foreach ($parentPid in $frontier) {
            $children = @(Get-CimInstance Win32_Process -Filter "ParentProcessId = $parentPid" -ErrorAction SilentlyContinue)
            foreach ($child in $children) {
                if (-not $seen.ContainsKey($child.ProcessId)) {
                    $seen[$child.ProcessId] = $child.Name
                    $next += $child.ProcessId
                }
            }
        }
        $frontier = $next
    }
    return $seen
}
try {
    for ($i = 1; $i -le $Samples; $i++) {
        $psi = New-Object System.Diagnostics.ProcessStartInfo
        $psi.FileName = $exe
        $psi.WorkingDirectory = $repo
        $psi.UseShellExecute = $false
        $psi.EnvironmentVariables['MANGAFLOW_NATIVE_REPO'] = $repo
        # Cold: fresh data dir per sample so each run includes full backend
        # provisioning. Hot (-HotReuse): one shared dir, already migrated.
        $runData = if ($HotReuse) { Join-Path $dataRoot 'shared' } else { Join-Path $dataRoot "run-$i" }
        $psi.EnvironmentVariables['MANGAFLOW_DESKTOP_USER_DATA'] = $runData
        # G1 strict first frame: the client writes this only when the env var is
        # set, and only once the compositor has actually pumped a frame. A sample
        # with no file stays -1 in the CSV instead of being dropped.
        $frameFile = Join-Path $dataRoot "first-frame-$i.txt"
        $psi.EnvironmentVariables['MANGAFLOW_FIRSTFRAME_OUT'] = $frameFile
        $sw = [System.Diagnostics.Stopwatch]::StartNew()
        $proc = [System.Diagnostics.Process]::Start($psi)
        $windowMs = -1
        $idleMs = -1
        while ($sw.ElapsedMilliseconds -lt 120000) {
            $proc.Refresh()
            if ($windowMs -lt 0 -and $proc.MainWindowHandle -ne 0) { $windowMs = $sw.ElapsedMilliseconds }
            # A root that died at spawn must not burn the rest of the sample
            # window (#575): exit the poll and record the honest -1 row; the
            # leftover sweep below still reports any sidecar it leaked.
            if ($proc.HasExited) { break }
            if ($windowMs -ge 0) {
                if ($proc.WaitForInputIdle(50)) { $idleMs = $sw.ElapsedMilliseconds; break }
            } else { Start-Sleep -Milliseconds 20 }
        }
        # The first-frame file is written by the render callback itself, which can
        # land a beat after WaitForInputIdle returns. Reading once at that instant
        # silently lost frames (the first hot run only got 3/20), so wait it out.
        $frameWait = [System.Diagnostics.Stopwatch]::StartNew()
        while (-not (Test-Path $frameFile) -and $frameWait.ElapsedMilliseconds -lt 20000 -and -not $proc.HasExited) {
            Start-Sleep -Milliseconds 50
        }
        $frameMs = -1
        if (Test-Path $frameFile) {
            $line = @(Get-Content $frameFile | Where-Object { $_ -like 'first_frame_ms=*' }) | Select-Object -First 1
            if ($line) { $frameMs = [double]($line.Split('=')[1]) }
        }
        $results += [pscustomobject]@{
            Sample = $i; Mode = $(if ($HotReuse) { 'hot' } else { 'cold' })
            FirstFrameMs = $frameMs; WindowHandleMs = $windowMs; InputIdleMs = $idleMs
        }
        # Give the window a moment to settle its first dashboard load, then close it.
        Start-Sleep -Seconds 3
        # Descendant snapshot BEFORE the close (#444): recursive, so a
        # grandchild sidecar counts as this run's child. Taken right before
        # CloseMainWindow; late spawns after this point are invisible to the
        # leftover check by construction — a known limit, noted because the
        # docs'「5 秒内零残留」claims cite this script (NUI-7 rows in
        # docs/native-ui-migration.md and docs/roadmap.md were collected with
        # the historical SINGLE-LEVEL sweep and need re-measurement under the
        # recursive one before being cited again).
        $children = Get-DescendantPids -RootPid $proc.Id
        $cleanClose = $proc.CloseMainWindow()
        $proc.WaitForExit(45000) | Out-Null
        $exited = $proc.HasExited
        # Observe exit cleanup: descendants spawned by this run must not
        # outlive it by 5s. A pid alive again with a DIFFERENT image is a
        # reuse, not a leftover (Win32_Process names carry the .exe suffix;
        # Get-Process ProcessName does not).
        Start-Sleep -Seconds 5
        $leftover = @()
        foreach ($childPid in $children.Keys) {
            $still = Get-Process -Id $childPid -ErrorAction SilentlyContinue
            if ($still -and (($still.ProcessName + '.exe') -eq $children[$childPid])) { $leftover += "$($children[$childPid]):$childPid" }
        }
        $results[-1] | Add-Member -NotePropertyName CleanClose -NotePropertyValue $cleanClose
        $results[-1] | Add-Member -NotePropertyName Exited -NotePropertyValue $exited
        $results[-1] | Add-Member -NotePropertyName LeftoverChildren -NotePropertyValue ($leftover -join ',')
        if (-not $exited) { Stop-SampleTree $proc }
        $proc = $null
    }
}
finally {
    # Belt-and-braces: a Ctrl+C or a mid-sample throw during the 120 s sample
    # window would otherwise leak the whole tree (root + native-host + sidecar)
    # holding its API port for the rest of the session. The held object pins
    # the identity (#896) — no pid re-lookup here (HasExited inside covers the
    # already-exited case).
    if ($proc) { Stop-SampleTree $proc }
    $results | Format-Table -AutoSize | Out-String | Write-Output
    if ($OutCsv) {
        New-Item -ItemType Directory -Force -Path (Split-Path $OutCsv) | Out-Null
        $results | Export-Csv -Path $OutCsv -NoTypeInformation -Encoding UTF8
        Write-Output ("csv=" + $OutCsv)
    }
    try { Remove-Item -Recurse -Force $dataRoot -ErrorAction SilentlyContinue } catch {}
}
