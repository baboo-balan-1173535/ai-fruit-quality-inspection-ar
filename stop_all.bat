@echo off
REM ── Kiwi Sorter — stop both servers cleanly ─────────────────
REM Closes the Kiwi Sorter (5000/5443) and DocMindAI (5001) servers
REM and their console windows. PostgreSQL stays running (it is a
REM Windows service and other apps may use it).

echo Stopping Kiwi Sorter and DocMindAI...

REM Kill by window title (the windows start_all.bat opened)
taskkill /fi "WINDOWTITLE eq Kiwi Sorter :5000*" /t /f >nul 2>&1
taskkill /fi "WINDOWTITLE eq DocMindAI :5001*"   /t /f >nul 2>&1

REM Fallback: kill whatever still listens on the ports
powershell -Command "Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | Where-Object { $_.LocalPort -in 5000,5001,5443 } | Select-Object -ExpandProperty OwningProcess -Unique | ForEach-Object { Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue }"

echo All servers stopped.
timeout /t 3
