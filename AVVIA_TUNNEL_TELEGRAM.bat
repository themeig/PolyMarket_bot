@echo off
chcp 65001 > nul
title Cloudflare Tunnel HTTPS per Telegram Mini App
color 0A

echo =====================================================================
echo    CLOUDFLARE HTTPS TUNNEL PER TELEGRAM MINI APP
echo =====================================================================
echo.
echo [+] Connessione tunnel HTTPS verso http://localhost:8080...
echo.

cloudflared.exe tunnel --url http://localhost:8080

pause
