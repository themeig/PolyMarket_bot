@echo off
chcp 65001 > nul
title Polymarket Institutional MM and Dashboard
color 0B

echo =====================================================================
echo    POLYMARKET SUITE COMPLETA (Dashboard + Telegram + Bot)
echo =====================================================================
echo.

if not exist ".env" (
    color 0C
    echo [X] ATTENZIONE: Il file .env non e' stato trovato!
    echo Assicurati di copiare il file .env con la tua chiave privata.
    pause
    exit /b
)

echo [+] Avvio Server Web e Bot Telegram su http://localhost:8080/polymaker...
start "Polymarket Server" cmd /k "python -u server_real.py"

timeout /t 2 > nul
start "" "http://localhost:8080/polymaker"

echo [+] Avvio Motore poly-maker Market Maker...
cd external_repos\poly-maker
set PYTHONUTF8=1
python -u -m polymaker.cli run

pause
