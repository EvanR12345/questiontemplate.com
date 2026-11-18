@echo off
setlocal
set "DIR=%USERPROFILE%\QuestionTemplateHelper"
echo Updating shared helper code. Existing .venv and Torch are preserved.
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; $script=Join-Path ([IO.Path]::GetTempPath()) ('qt-update-' + [Guid]::NewGuid().ToString('N') + '.ps1'); try { Invoke-WebRequest -UseBasicParsing 'https://raw.githubusercontent.com/EvanR12345/questiontemplate.com/main/nvidia-helper/update-helper.ps1' -OutFile $script; & $script } finally { Remove-Item -LiteralPath $script -Force -ErrorAction SilentlyContinue }"
if errorlevel 1 (
  echo Update failed. Review the message above.
  pause
  exit /b 1
)
call "%DIR%\start-windows.bat"
