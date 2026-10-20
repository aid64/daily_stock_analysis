' ============================================================
' run_hidden_sync.vbs - start a console command hidden and WAIT
'
' run_hidden.vbs uses sh.Run(cmd, 0, False): the launcher exits
' immediately, so Task Scheduler stops tracking the child and may
' clean it up before the run finishes (the exit line can go missing).
' This variant waits and returns the child exit code, so the task
' stays Running for the whole run and LastTaskResult is meaningful.
'
' Usage (task action):
'   program: wscript.exe
'   args:    //B //Nologo "D:\...\scripts\run_hidden_sync.vbs" "powershell.exe ... -File D:\...\xxx.ps1"
' ============================================================
Option Explicit

Dim sh, cmd, rc
If WScript.Arguments.Count = 0 Then
    WScript.Quit 2
End If

cmd = WScript.Arguments(0)
Set sh = CreateObject("WScript.Shell")
rc = sh.Run(cmd, 0, True)
WScript.Quit rc
