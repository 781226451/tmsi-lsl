# Create a "TMSi SAGA - LSL" shortcut on the desktop (double-click, no console).
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\create_gui_shortcut.ps1
#
# The shortcut calls pythonw.exe from the project's .venv, so no console window
# appears. After dependencies change run "uv sync" (or run-gui.cmd) once; the
# shortcut itself does not need to be recreated.
#
# This file is ASCII on purpose: Windows PowerShell 5.1 reads .ps1 files as ANSI
# unless they carry a BOM, which would garble non-ASCII shortcut names.

[CmdletBinding()]
param(
    [string]$Name = "TMSi SAGA - LSL",
    [string]$Location = [Environment]::GetFolderPath("Desktop")
)

$ErrorActionPreference = "Stop"

$repo = Split-Path -Parent $PSScriptRoot
$pythonw = Join-Path $repo ".venv\Scripts\pythonw.exe"

if (-not (Test-Path $pythonw)) {
    Write-Host "pythonw.exe not found at $pythonw"
    Write-Host "Run 'uv sync' (or run-gui.cmd once) and try again."
    exit 1
}

$code = 'import sys; from tmsi_lsl.gui.app import main; raise SystemExit(main())'
$shortcutPath = Join-Path $Location "$Name.lnk"

$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = $pythonw
$shortcut.Arguments = '-c "' + $code + '"'
$shortcut.WorkingDirectory = $repo
$shortcut.Description = "TMSi SAGA to LSL: channel gating, sample rate, LSL stream"
$shortcut.WindowStyle = 1
$shortcut.Save()

Write-Host "Shortcut created:"
Write-Host "  path      : $shortcutPath"
Write-Host "  target    : $pythonw"
Write-Host "  arguments : $($shortcut.Arguments)"
Write-Host "  workdir   : $repo"
