param(
    [int]$Samples = 3
)
$ErrorActionPreference = 'Stop'
$repo = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../../..'))
$exe = Join-Path $repo 'apps/desktop/native/bin/Release/net8.0-windows/MangaFlow.Native.exe'
$dataRoot = Join-Path $env:TEMP ("mangaflow-perf-" + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Force -Path $dataRoot | Out-Null
$results = @()
try {
    for ($i = 1; $i -le $Samples; $i++) {
        $psi = New-Object System.Diagnostics.ProcessStartInfo
        $psi.FileName = $exe
        $psi.WorkingDirectory = $repo
        $psi.UseShellExecute = $false
        $psi.EnvironmentVariables['MANGAFLOW_NATIVE_REPO'] = $repo
        $psi.EnvironmentVariables['MANGAFLOW_DESKTOP_USER_DATA'] = $dataRoot
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
        $children = @(Get-CimInstance Win32_Process -Filter "ParentProcessId = $($proc.Id)" -ErrorAction SilentlyContinue)
        $cleanClose = $proc.CloseMainWindow()
        $proc.WaitForExit(45000) | Out-Null
        $exited = $proc.HasExited
        # Observe exit cleanup: children spawned by this run must not outlive it by 5s.
        Start-Sleep -Seconds 5
        $leftover = @()
        foreach ($child in $children) {
            if (Get-Process -Id $child.ProcessId -ErrorAction SilentlyContinue) { $leftover += "$($child.Name):$($child.ProcessId)" }
        }
        $results[-1] | Add-Member -NotePropertyName CleanClose -NotePropertyValue $cleanClose
        $results[-1] | Add-Member -NotePropertyName Exited -NotePropertyValue $exited
        $results[-1] | Add-Member -NotePropertyName LeftoverChildren -NotePropertyValue ($leftover -join ',')
        if (-not $exited) { Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue }
    }
}
finally {
    $results | Format-Table -AutoSize | Out-String | Write-Output
    try { Remove-Item -Recurse -Force $dataRoot -ErrorAction SilentlyContinue } catch {}
}
