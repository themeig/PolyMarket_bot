@echo off
chcp 65001 > nul
title Installazione Dipendenze Polymarket Dual-Engine Bot
color 0A

echo =====================================================================
echo    🚀 INSTALLAZIONE DIPENDENZE POLYMARKET QUANT BOT
echo =====================================================================
echo.
echo Controllo installazione Python in corso...
python --version > nul 2>&1
if errorlevel 1 (
    color 0C
    echo [X] ERRORE: Python non sembra essere installato o non e' presente nel PATH.
    echo Scarica e installa Python da https://www.python.org/
    echo RICORDATI di spuntare la casella "Add Python to PATH" durante l'installazione!
    echo.
    pause
    exit /b
)

echo [+] Python rilevato con successo!
echo.
echo Aggiornamento pip e installazione librerie necessarie...
echo.

python -m pip install --upgrade pip
python -m pip install -r requirements.txt


if errorlevel 1 (
    color 0C
    echo.
    echo [X] Si e' verificato un errore durante l'installazione delle librerie.
    echo.
    pause
    exit /b
)

echo.
echo =====================================================================
echo    🎉 TUTTE LE DIPENDENZE SONO STATE INSTALLATE CON SUCCESSO!
echo =====================================================================
echo Ora puoi fare doppio clic su "2_AVVIA_BOT.bat" per avviare il bot.
echo =====================================================================
echo.
pause
