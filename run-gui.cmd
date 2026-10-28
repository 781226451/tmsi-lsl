@echo off
rem ---------------------------------------------------------------------------
rem  TMSi SAGA -> LSL : double-click launcher for the Qt Quick interface.
rem  Extra arguments are forwarded, e.g.  run-gui.cmd --settings lab.toml
rem ---------------------------------------------------------------------------
setlocal
cd /d "%~dp0"

where uv >nul 2>&1
if errorlevel 1 goto no_uv

echo Starting the TMSi SAGA -^> LSL interface ...
echo (the first start also syncs the dependencies, please wait)
echo.
uv run tmsi-lsl-gui %*
if errorlevel 1 goto failed
exit /b 0

:no_uv
echo [ERROR] uv was not found in PATH.
echo         Install it with:  winget install --id astral-sh.uv
pause
exit /b 1

:failed
echo.
echo [ERROR] The interface exited with an error (see the messages above).
pause
exit /b 1
