@echo off
setlocal
set "DIR=%USERPROFILE%\QuestionTemplateImageHelper"
if not exist "%DIR%" mkdir "%DIR%"
echo Downloading QuestionTemplate Image Helper...
curl.exe -L --fail "https://questiontemplate.com/image-helper/server.py" -o "%DIR%\server.py" || goto :fail
curl.exe -L --fail "https://questiontemplate.com/image-helper/requirements.txt" -o "%DIR%\requirements.txt" || goto :fail
curl.exe -L --fail "https://questiontemplate.com/image-helper/start-windows.bat" -o "%DIR%\start-windows.bat" || goto :fail
curl.exe -L --fail "https://questiontemplate.com/image-helper/README.md" -o "%DIR%\README.md" || goto :fail
call "%DIR%\start-windows.bat"
exit /b
:fail
echo Download failed. Check your internet connection and try again.
pause
