@echo off
REM ── Kiwi Sorter — START everything ──────────────────────────
REM Double-click to start. Run stop_all.bat to stop everything.
REM Opens two console windows (their titles say which server is which):
REM   "Kiwi Sorter :5000"  dashboard  http://localhost:5000  (+ HTTPS :5443)
REM   "DocMindAI :5001"    RAG/chat   http://localhost:5001  (~30s first load)
REM Each `start` inherits the current directory, so no nested quoting
REM is needed - the old quoted form silently failed for DocMindAI.

cd /d "%~dp0"

REM PostgreSQL check - DocMindAI shows "DB offline" when it is stopped.
powershell -Command "if ((Get-Service postgresql-x64-18 -ErrorAction SilentlyContinue).Status -ne 'Running') { Write-Host ''; Write-Host '  WARNING: PostgreSQL is NOT running - DocMindAI will say DB offline.' -ForegroundColor Yellow; Write-Host '  Fix: double-click setup_admin.bat once (it self-elevates).' -ForegroundColor Yellow; Write-Host '' }"

echo Starting Kiwi Sorter (ports 5000 + 5443)...
start "Kiwi Sorter :5000" cmd /k "venv\Scripts\python.exe app.py"

cd /d "%~dp0DocMindAi Dai"
echo Starting DocMindAI RAG (port 5001)... first load takes ~30s
start "DocMindAI :5001" cmd /k ".venv\Scripts\python.exe app.py"
cd /d "%~dp0"

echo.
echo Both servers launching in separate windows.
echo   Dashboard   : http://localhost:5000
echo   RAG / Chat  : http://localhost:5001
echo.
echo To STOP everything: run stop_all.bat (or close the two server windows).
timeout /t 8
