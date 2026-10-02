@echo off
setlocal
set "DIR=%USERPROFILE%\QuestionTemplateHelper"
if not exist "%DIR%" mkdir "%DIR%"
echo Updating shared helper code. Existing .venv and Torch are preserved.
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; $dest=Join-Path $env:USERPROFILE 'QuestionTemplateHelper'; $script=Join-Path $dest 'update-helper.ps1'; Invoke-WebRequest -UseBasicParsing 'https://raw.githubusercontent.com/EvanR12345/questiontemplate.com/main/nvidia-helper/update-helper.ps1' -OutFile $script; & $script"
if errorlevel 1 (
  echo Update failed. Installed code and environment are preserved.
  pause
  exit /b 1
)
call "%DIR%\start-windows.bat"
