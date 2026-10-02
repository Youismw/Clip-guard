@echo off
cd /d "%~dp0"
set "PYTHONPATH=%~dp0src;%PYTHONPATH%"

set "PY_EXE="
if exist "C:\Python314\python.exe" set "PY_EXE=C:\Python314\python.exe"
if not defined PY_EXE set "PY_EXE=python"

"%PY_EXE%" -m dedupe.cli %*
