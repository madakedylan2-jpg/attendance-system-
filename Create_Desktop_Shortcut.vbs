' Creates a Desktop shortcut for the TCFL Attendance System launcher,
' using the barcode icon.ico in this same folder. Double-click this
' file once - you only need to run it a single time.

Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")

scriptFolder = fso.GetParentFolderName(WScript.ScriptFullName)
desktopFolder = shell.SpecialFolders("Desktop")

Set shortcut = shell.CreateShortcut(desktopFolder & "\TCFL Attendance System.lnk")
shortcut.TargetPath = scriptFolder & "\Start_Attendance_System.bat"
shortcut.WorkingDirectory = scriptFolder
shortcut.IconLocation = scriptFolder & "\icon.ico"
shortcut.WindowStyle = 1
shortcut.Description = "Launch the TCFL Attendance System"
shortcut.Save

MsgBox "Desktop shortcut created!" & vbCrLf & vbCrLf & _
       "Look for 'TCFL Attendance System' on your Desktop." & vbCrLf & _
       "Double-click it any time to start the system.", 64, "TCFL Attendance System"
