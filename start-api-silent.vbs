' Start the AI Listing System API on port 8788 with NO console window at all.
' Double-click this file (or make a shortcut to it) instead of start-api.bat.
'
' It calls pythonw.exe, a GUI-subsystem executable, so Windows never allocates a
' console. That matters on Windows 11: a console created by a .bat or by cmd.exe
' gets handed to Windows Terminal and shows up as a visible window even when the
' launcher asks for a hidden window (that is why start-api.bat flashes a window).
'
' Server output still goes to data\api-runtime\api.out.log / api.err.log.

Option Explicit

Dim shell, fso, root, pyw, pythonw, cmd
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

root = fso.GetParentFolderName(WScript.ScriptFullName)
pyw = fso.BuildPath(root, "run_api_silent.pyw")

pythonw = "pythonw.exe"
If Not fso.FileExists(pythonw) Then
  If fso.FileExists("C:\Users\Administrator\AppData\Local\Programs\Python\Python314\pythonw.exe") Then
    pythonw = "C:\Users\Administrator\AppData\Local\Programs\Python\Python314\pythonw.exe"
  End If
End If

shell.CurrentDirectory = root
cmd = """" & pythonw & """ """ & pyw & """"
' Window style 0 = hidden. pythonw has no console anyway; this covers the rest.
shell.Run cmd, 0, False
