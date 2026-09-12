param(
    [int]$Samples = 3
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
    param([int]$SamplePid)
    # The client spawns a job-less child tree (native-host → python sidecar);
    # Stop-Process would kill only the WPF root and orphan the children
    # holding the API port. taskkill /T walks the whole descendant tree.
    # Identity guard (round-6 review): Windows reuses pids aggressively - a
    # Ctrl+C after the sample already exited can land this taskkill on an
    # unrelated new process. Verify the pid still names MangaFlow.Native
    # before killing; a recycled pid is skipped (its own owner reaps it).
    $proc = Get-Process -Id $SamplePid -ErrorAction SilentlyContinue
    if (-not $proc) { return }
    if ($proc.ProcessName -ne 'MangaFlow.Native') {
        Write-Warning ("Stop-SampleTree: pid {0} is now '{1}' (pid reuse) - skip" -f $SamplePid, $proc.ProcessName)
        return
    }
    & taskkill /PID $SamplePid /T /F | Out-Null
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
        # Fresh data dir per sample so each run includes full backend provisioning.
        $runData = Join-Path $dataRoot "run-$i"
        $psi.EnvironmentVariables['MANGAFLOW_DESKTOP_USER_DATA'] = $runData
        $sw = [System.Diagnostics.Stopwatch]::StartNew()
        $proc = [System.Diagnostics.Process]::Start($psi)
        $windowMs = -1
        $idleMs = -1
        while ($sw.ElapsedMilliseconds -lt 120000) {
            $proc.Refresh()
            if ($windowMs -lt 0 -and $proc.MainWindowHandle -ne 0) { $windowMs = $sw.ElapsedMilliseconds }
            if ($windowMs -ge 0) {
                if ($proc.WaitForInputIdle(50)) { $idleMs = $sw.ElapsedMilliseconds; break }
            } else { Start-Sleep -Milliseconds 20 }
        }
        $results += [pscustomobject]@{ Sample = $i; WindowHandleMs = $windowMs; InputIdleMs = $idleMs }
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
        if (-not $exited) { Stop-SampleTree $proc.Id }
        $proc = $null
    }
}
finally {
    # Belt-and-braces: a Ctrl+C or a mid-sample throw during the 120 s sample
    # window would otherwise leak the whole tree (root + native-host + sidecar)
    # holding its API port for the rest of the session.
    if ($proc -and (Get-Process -Id $proc.Id -ErrorAction SilentlyContinue)) {
        Stop-SampleTree $proc.Id
    }
    $results | Format-Table -AutoSize | Out-String | Write-Output
    try { Remove-Item -Recurse -Force $dataRoot -ErrorAction SilentlyContinue } catch {}
}
