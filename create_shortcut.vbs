Set objShell = CreateObject("WScript.Shell")
Set objFSO = CreateObject("Scripting.FileSystemObject")
strDesktop = objShell.SpecialFolders("Desktop")
strCurrentDir = objFSO.GetParentFolderName(WScript.ScriptFullName)

strVbsPath = strCurrentDir & "\TurboCopy.vbs"
strShortcutPath = strDesktop & "\TurboCopy Pro.lnk"

Set objShortcut = objShell.CreateShortcut(strShortcutPath)
objShortcut.TargetPath = "wscript.exe"
objShortcut.Arguments = """" & strVbsPath & """"
objShortcut.WorkingDirectory = strCurrentDir
objShortcut.IconLocation = strCurrentDir & "\icon.ico,0"
objShortcut.Description = "TurboCopy Pro - Yüksek Hızlı Disk ve Dosya Kopyalayıcı"
objShortcut.Save

WScript.Echo "Masaustu kisayolu guncellendi: " & strShortcutPath
