$ErrorActionPreference = 'Stop'
$taskHelperRoot = [IO.Path]::GetFullPath($PSScriptRoot)
$taskPython = Join-Path $taskHelperRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $taskPython)) { throw 'Keep the existing shared helper environment; start this installer from QuestionTemplateHelper.' }
$taskPlatform = & $taskPython -B -c 'import sys,platform;print(str(sys.version_info[:2])+"/"+platform.system()+"/"+platform.machine())'
if ($taskPlatform -ne '(3, 11)/Windows/AMD64') { throw 'This pinned optional codec package supports the current Windows x64 Python 3.11 helper only. CPU rendering remains available.' }
$taskLibrary = [IO.Path]::GetFullPath((Join-Path $taskHelperRoot 'render-libs'))
if (-not $taskLibrary.StartsWith($taskHelperRoot + '\', [StringComparison]::OrdinalIgnoreCase)) { throw 'Unsafe isolated codec target.' }
$taskFingerprint = (Get-FileHash -LiteralPath $taskPython).Hash
$taskRequirements = Join-Path $taskHelperRoot 'gpu-render-requirements.txt'
& $taskPython -B -m pip install --no-deps --no-cache-dir --only-binary=:all: --require-hashes --target $taskLibrary -r $taskRequirements
if ($LASTEXITCODE -ne 0) { throw 'The optional codec install failed; the existing environment and CPU renderer remain available.' }
if ((Get-FileHash -LiteralPath $taskPython).Hash -ne $taskFingerprint) { throw 'Existing Python fingerprint changed unexpectedly.' }
Write-Host 'Optional NVIDIA codec installed separately in render-libs. No Torch/CUDA or voice dependencies were installed. Select NVIDIA GPU under Studio Video output after the idle helper restart.'
