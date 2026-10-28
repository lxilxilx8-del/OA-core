Option Explicit
Dim shell, fso, folder, command
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
folder = fso.GetParentFolderName(WScript.ScriptFullName)
command = "powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -File """ & folder & "\manage.ps1"" -Action Start"
If shell.Run(command, 0, True) <> 0 Then
    MsgBox "XiaoA could not start. Run the installer or check the data folder log.", 48, "XiaoA Core"
End If
