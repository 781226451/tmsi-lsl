' ---------------------------------------------------------------------------
'  TMSi SAGA -> LSL : silent launcher (no console window at all).
'
'  Double-click this file to open the interface.  Arguments are forwarded:
'      run-gui.vbs --settings lab.toml
'
'  It starts pythonw.exe from the project's .venv, so nothing but the Qt
'  window appears.  Run "uv sync" (or run-gui-console.cmd) once if the
'  dependencies are missing.
' ---------------------------------------------------------------------------
Option Explicit

Dim shell, fso, repo, pythonw, code, commandLine, args, i
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

repo = fso.GetParentFolderName(WScript.ScriptFullName)
pythonw = repo & "\.venv\Scripts\pythonw.exe"

If Not fso.FileExists(pythonw) Then
    MsgBox "pythonw.exe was not found:" & vbCrLf & vbCrLf & pythonw & vbCrLf & vbCrLf & _
           "Run 'uv sync' (or run-gui-console.cmd) once and try again.", _
           vbExclamation, "TMSi SAGA - LSL"
    WScript.Quit 1
End If

code = "import sys; from tmsi_lsl.gui.app import main; raise SystemExit(main())"
commandLine = """" & pythonw & """ -c """ & code & """"

Set args = WScript.Arguments
For i = 0 To args.Count - 1
    commandLine = commandLine & " """ & args(i) & """"
Next

shell.CurrentDirectory = repo
shell.Run commandLine, 0, False
