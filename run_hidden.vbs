' Launches a single .bat file with its console window hidden.
' Used by Task Scheduler actions so the 2-minute live-bot polls
' (SR+Bias, XAUUSD ORB) don't flash a visible cmd window while
' still writing to logs/*.log exactly as before.
Set WshShell = CreateObject("WScript.Shell")
WshShell.Run """" & WScript.Arguments(0) & """", 0, True
