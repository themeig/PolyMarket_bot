@echo off
chcp 65001 > nul
title Polymarket Dual-Engine Production Server
color 0B

echo =====================================================================
echo    🚀 AVVIO POLYMARKET DUAL-ENGINE BOT (HFT + WIDE SPREAD)
echo =====================================================================
echo.

if not exist ".env" (
    color 0C
    echo [X] ATTENZIONE: Il file .env non e' stato trovato in questa cartella!
    echo Assicurati di copiare anche il file .env con le tue chiavi.
    echo.
    pause
    exit /b
)

echo [+] File di configurazione .env rilevato.
echo [+] Apertura Dashboard nel browser su http://localhost:8080...
start "" "http://localhost:8080"

echo [+] Avvio Motore di Trading in corso...
echo.
echo =====================================================================
echo  Per fermare il bot premi CTRL + C oppure chiudi questa finestra.
echo =====================================================================
echo.

python server_real.py

pause
