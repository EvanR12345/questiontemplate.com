$ErrorActionPreference = 'Stop'
$destination = Join-Path $env:USERPROFILE 'QuestionTemplateHelper'
if (-not (Test-Path -LiteralPath (Join-Path $destination '.venv\Scripts\python.exe'))) {
    throw 'The existing shared .venv is missing. This update does not create or replace it.'
}
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$staging = Join-Path $destination ('updates\' + $stamp)
New-Item -ItemType Directory -Path $staging -Force | Out-Null
$archive = Join-Path $staging 'helper.zip'
Invoke-WebRequest -UseBasicParsing 'https://raw.githubusercontent.com/EvanR12345/questiontemplate.com/main/nvidia-helper.zip' -OutFile $archive
Add-Type -AssemblyName System.IO.Compression.FileSystem
$zip = [IO.Compression.ZipFile]::OpenRead($archive)
try {
    foreach ($entry in $zip.Entries) {
        if ($entry.FullName -notmatch '^nvidia-helper/' -or $entry.FullName -match '(^|/)\.\.(/|$)|:|^/') { throw 'Unsafe helper archive path.' }
        if ($entry.FullName -match '^nvidia-helper/(\.venv|python|models|studio-runtime|outputs|studio-config\.json|\.pairing-key)(/|$)') { throw 'Archive attempted to replace protected helper data.' }
    }
} finally { $zip.Dispose() }
Expand-Archive -LiteralPath $archive -DestinationPath $staging -Force
$source = [IO.Path]::GetFullPath((Join-Path $staging 'nvidia-helper'))
$destRoot = [IO.Path]::GetFullPath($destination)
if (-not $source.StartsWith($destRoot + '\', [StringComparison]::OrdinalIgnoreCase)) { throw 'Update staging must remain inside the named helper directory.' }
$updateFiles = @(Get-ChildItem -LiteralPath $source -Recurse -File)
foreach ($file in $updateFiles) {
    $relative = $file.FullName.Substring($source.Length + 1)
    $target = [IO.Path]::GetFullPath((Join-Path $destination $relative))
    if (-not $target.StartsWith($destRoot + '\', [StringComparison]::OrdinalIgnoreCase)) { throw 'Unsafe update destination.' }
}
$venvExe = Join-Path $destination '.venv\Scripts\python.exe'
$processes = @(Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'python.exe' -and $_.ExecutablePath -eq $venvExe -and $_.CommandLine -match 'server.py' })
$keyPath = Join-Path $destination '.pairing-key'
if (Test-Path -LiteralPath $keyPath) {
    $headers = @{ Authorization = 'Bearer ' + [IO.File]::ReadAllText($keyPath).Trim() }
    foreach ($endpoint in @('/image/queue', '/studio/queue')) {
        try { $queue = Invoke-RestMethod -Uri ('http://127.0.0.1:8765' + $endpoint) -Headers $headers -TimeoutSec 3 }
        catch {
            if ($processes.Count -gt 0 -and $_.Exception.Response.StatusCode.value__ -ne 404) { throw 'The running helper could not confirm that its queue is idle. The update was stopped before replacing code.' }
            $queue = $null
        }
        if ($queue -and ($queue.current -or $queue.counts.queued -gt 0 -or $queue.counts.QUEUED -gt 0)) { throw 'Finish or cancel queued work before updating. Saved assets and installed code were retained.' }
    }
}
foreach ($process in $processes) { & "$env:SystemRoot\System32\taskkill.exe" /PID $process.ProcessId /T /F | Out-Null }
$backup = Join-Path $destination ('backups\update-' + $stamp)
foreach ($file in $updateFiles) {
    $relative = $file.FullName.Substring($source.Length + 1)
    if ($relative -match '^(\.venv|python|models|studio-runtime|outputs|studio-config\.json|\.pairing-key)(\\|$)') { throw 'Archive attempted to replace protected helper data.' }
    $target = [IO.Path]::GetFullPath((Join-Path $destination $relative))
    if (-not $target.StartsWith($destRoot + '\', [StringComparison]::OrdinalIgnoreCase)) { throw 'Unsafe update destination.' }
    if (Test-Path -LiteralPath $target) {
        $previous = Join-Path $backup $relative
        New-Item -ItemType Directory -Path ([IO.Path]::GetDirectoryName($previous)) -Force | Out-Null
        Copy-Item -LiteralPath $target -Destination $previous -Force
    }
    New-Item -ItemType Directory -Path ([IO.Path]::GetDirectoryName($target)) -Force | Out-Null
    Copy-Item -LiteralPath $file.FullName -Destination $target -Force
}
Write-Host 'Helper code and website updated. Existing .venv, Torch, configuration, models and generated assets were preserved.'
