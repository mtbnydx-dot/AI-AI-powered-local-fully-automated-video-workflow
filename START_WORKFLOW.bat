@echo off
setlocal

cd /d "%~dp0"

if /i "%~1"=="--check" (
    echo START_WORKFLOW.bat OK
    exit /b 0
)
if /i "%~1"=="--help" goto :help
if /i "%~1"=="-h" goto :help
if /i "%~1"=="/?" goto :help

where powershell.exe >nul 2>nul
if errorlevel 1 (
    echo Windows PowerShell was not found.
    echo Please install Windows PowerShell, or install Python 3.12 and run:
    echo python START_WORKFLOW.py
    echo Python download page: https://www.python.org/downloads/windows/
    pause
    exit /b 1
)

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0START_WORKFLOW.ps1" %*
set "EXIT_CODE=%ERRORLEVEL%"

if not "%EXIT_CODE%"=="0" (
    echo.
    echo START_WORKFLOW failed with exit code %EXIT_CODE%.
    echo If Python is missing, install Python 3.12 or run:
    echo winget install -e --id Python.Python.3.12 --accept-package-agreements --accept-source-agreements
    echo Python download page: https://www.python.org/downloads/windows/
    echo Then double-click START_WORKFLOW.bat again.
    pause
)

exit /b %EXIT_CODE%

:help
echo Wan2.2 local video workflow
echo.
echo Usage:
echo   START_WORKFLOW.bat
echo   START_WORKFLOW.bat --check
echo   START_WORKFLOW.bat --help
echo   START_WORKFLOW.bat --no-browser
echo   START_WORKFLOW.bat --show-download-settings
echo   START_WORKFLOW.bat --set-hf-endpoint https://huggingface.co
echo   START_WORKFLOW.bat --set-pip-index https://pypi.org/simple
echo   START_WORKFLOW.bat --set-proxy http://127.0.0.1:7890
echo   START_WORKFLOW.bat --clear-download-settings
echo.
echo Beginner path: start the frontend, open Environment detection, click one-click setup, then run the generation smoke test.
exit /b 0
