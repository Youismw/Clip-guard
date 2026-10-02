@echo off
cd /d "%~dp0"
set "PYTHONPATH=%~dp0src;%PYTHONPATH%"

:: Prefer direct Python installation to bypass WindowsApps store alias
set "PY_EXE="
if exist "C:\Python314\python.exe" set "PY_EXE=C:\Python314\python.exe"
if not defined PY_EXE (
    where python >nul 2>nul
    if %ERRORLEVEL% EQU 0 set "PY_EXE=python"
)

if not defined PY_EXE (
    echo [ERROR] Python executable was not found.
    echo Please verify Python is installed.
    pause
    exit /b 1
)

echo Starting ClipGuard GUI...
"%PY_EXE%" -m dedupe.gui

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo ---------------------------------------------------------------
    echo [ERROR] ClipGuard GUI exited with error code %ERRORLEVEL%.
    echo ---------------------------------------------------------------
    pause
)
