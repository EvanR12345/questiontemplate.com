@echo off
setlocal
set "DIR=%USERPROFILE%\QuestionTemplateHelper"
if not exist "%DIR%" mkdir "%DIR%"
echo Updating the QuestionTemplate NVIDIA helper...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; $base='https://raw.githubusercontent.com/EvanR12345/questiontemplate.com/main/nvidia-helper/'; Invoke-WebRequest -UseBasicParsing ($base+'server.py') -OutFile \"%DIR%\server.py\"; Invoke-WebRequest -UseBasicParsing ($base+'requirements.txt') -OutFile \"%DIR%\requirements.txt\"; Invoke-WebRequest -UseBasicParsing ($base+'start-windows.bat') -OutFile \"%DIR%\start-windows.bat\""
if errorlevel 1 (
  echo Update failed. Check your internet connection and try again.
  pause
  exit /b 1
)
call "%DIR%\start-windows.bat"
