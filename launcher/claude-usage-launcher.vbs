Set oShell = CreateObject("WScript.Shell")
Dim exePath
exePath = oShell.ExpandEnvironmentStrings("%USERPROFILE%") & "\.local\bin\claude-usage.exe"
oShell.Run """" & exePath & """", 0, False
