Set objShell = CreateObject("WScript.Shell")
Set objFSO = CreateObject("Scripting.FileSystemObject")
strCurrentDir = objFSO.GetParentFolderName(WScript.ScriptFullName)
strExe = strCurrentDir & "\dist\TurboCopy\TurboCopy.exe"

objShell.CurrentDirectory = strCurrentDir

If objFSO.FileExists(strExe) Then
    objShell.Run """" & strExe & """", 1, False
Else
    objShell.Run "pythonw.exe app.py", 0, False
End If
