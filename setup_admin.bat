@echo off
REM ── Kiwi Sorter — ONE-TIME admin setup ──────────────────────
REM Fixes the two things that need administrator rights:
REM   1. Windows Firewall rule so the phone + AR glasses can reach
REM      ports 5000 / 5001 / 5443 (without it they are silently blocked).
REM   2. Starts the PostgreSQL service (DocMindAI says "DB offline"
REM      whenever this service is stopped).
REM Run me ONCE. I self-elevate, so just double-click.

REM ── Self-elevate to administrator ──
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo Requesting administrator rights...
    powershell -Command "Start-Process '%~f0' -Verb RunAs"
    exit /b
)

echo.
echo [1/3] Adding firewall rules (TCP 5000,5001,5443 + UDP 5006 discovery)...
powershell -Command "if (-not (Get-NetFirewallRule -DisplayName 'Kiwi Sorter Servers' -ErrorAction SilentlyContinue)) { New-NetFirewallRule -DisplayName 'Kiwi Sorter Servers' -Direction Inbound -Action Allow -Protocol TCP -LocalPort 5000,5001,5443 -Profile Any | Out-Null; 'TCP rule added.' } else { 'TCP rule already exists.' }"
powershell -Command "if (-not (Get-NetFirewallRule -DisplayName 'Kiwi Sorter AR Discovery' -ErrorAction SilentlyContinue)) { New-NetFirewallRule -DisplayName 'Kiwi Sorter AR Discovery' -Direction Inbound -Action Allow -Protocol UDP -LocalPort 5006 -Profile Any | Out-Null; 'UDP discovery rule added.' } else { 'UDP rule already exists.' }"

echo.
echo [2/3] Starting PostgreSQL service...
powershell -Command "Set-Service postgresql-x64-18 -StartupType Automatic; Start-Service postgresql-x64-18 -ErrorAction SilentlyContinue; (Get-Service postgresql-x64-18).Status"

echo.
echo [3/3] Done. PostgreSQL will now also start automatically with Windows.
echo You can close this window and run start_all.bat normally.
echo.
pause
