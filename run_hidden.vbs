' Runs one scan with no console window flashing on screen.
' Task Scheduler calls this; it calls run_scan.bat invisibly.
CreateObject("WScript.Shell").Run """C:\Users\ADMIN\meme-scanner\run_scan.bat""", 0, False
