@echo off
REM One scan, appended to scanner.log. Called by run_hidden.vbs from Task Scheduler.
cd /d "C:\Users\ADMIN\meme-scanner"

REM keep the log from growing forever - roll it over past 5 MB
for %%F in (scanner.log) do if %%~zF GTR 5000000 move /y scanner.log scanner.log.old >nul 2>&1

python scanner.py >> scanner.log 2>&1
