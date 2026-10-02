' ============================================================
' run_hidden.vbs —— 以"创建即隐藏"的方式启动控制台程序
' 窗口样式 0 = SW_HIDE，在 CreateProcess 阶段就带隐藏标志，
' 因此控制台不会被 Windows Terminal 接管成可见窗口。
'
' 背景（2026-10-02 实测结论）：
'   默认终端应用 = Windows Terminal 时，powershell.exe -WindowStyle Hidden
'   无效 —— 控制台先以可见方式创建、被 WindowsTerminal.exe 接管，之后
'   PowerShell 再隐藏已经来不及，于是每次都有大窗口弹出来。
'   实测：VBS 的 Run(cmd, 0, False) 只创建 cmd+conhost，无 WindowsTerminal，
'   无可见窗口句柄。
'
' 用法（计划任务的动作里）：
'   程序:   wscript.exe
'   参数:   //B //Nologo "D:\...\scripts\run_hidden.vbs" "powershell.exe -NoProfile -ExecutionPolicy Bypass -File D:\...\xxx.ps1"
' ============================================================
Option Explicit

Dim sh, cmd
If WScript.Arguments.Count = 0 Then
    WScript.Quit 2
End If

cmd = WScript.Arguments(0)
Set sh = CreateObject("WScript.Shell")
sh.Run cmd, 0, False
