"""Telegram Bot & Push Notifier for Polymarket Quant Engine.

Supports push notifications (fills, merges, rewards, risk alerts, hourly status)
and interactive commands (/status, /rewards, /stop, /resume, /ping).
"""

from __future__ import annotations

import asyncio
import os
import time
from typing import Any, Callable, Coroutine
import httpx
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()


class TelegramNotifier:
    """Async Telegram notification client."""

    def __init__(self, token: str | None = None, chat_id: str | None = None):
        self.token = token or TELEGRAM_BOT_TOKEN
        self.chat_id = chat_id or TELEGRAM_CHAT_ID
        self.base_url = f"https://api.telegram.org/bot{self.token}" if self.token else ""
        self._last_offset = 0

    @property
    def is_configured(self) -> bool:
        return bool(self.token and self.chat_id)

    async def send_message(self, text: str, parse_mode: str = "HTML") -> bool:
        """Send a push message to the configured Telegram chat."""
        if not self.is_configured:
            return False
        url = f"{self.base_url}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": True,
        }
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                r = await client.post(url, json=payload)
                return r.status_code == 200
        except Exception as e:
            print(f"[Telegram] Errore invio messaggio: {e}")
            return False

    def send_message_sync(self, text: str) -> None:
        """Sync wrapper for sending telegram message from sync functions."""
        if not self.is_configured:
            return
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self.send_message(text))
        except RuntimeError:
            asyncio.run(self.send_message(text))

    async def notify_fill(self, token_name: str, side: str, price: float, size: float, title: str) -> None:
        """Alert on trade fill."""
        msg = (
            f"🎯 <b>ORDINE ESEGUITO (FILL)!</b>\n\n"
            f"• <b>Mercato:</b> {title}\n"
            f"• <b>Lato:</b> {side} {token_name}\n"
            f"• <b>Prezzo:</b> <code>{price:.3f}$</code>\n"
            f"• <b>Dimensione:</b> <code>{size:.2f} quote</code> ({price * size:.2f}$ USDC)\n"
            f"• <b>Orario:</b> <code>{time.strftime('%H:%M:%S')}</code>"
        )
        await self.send_message(msg)

    async def notify_merge(self, amount_shares: float, payout_usdc: float, title: str = "") -> None:
        """Alert on complete set merge."""
        msg = (
            f"🎉 <b>COMPLETE SET MERGE ESEGUITO!</b>\n\n"
            f"• <b>Quote Fuse:</b> <code>{amount_shares:.2f} coppie YES+NO</code>\n"
            f"• <b>Collaterale Riscattato:</b> <code>+{payout_usdc:.2f}$ USDC</code>\n"
            f"• <b>Stato:</b> 🟢 Collaterale liberato e pronto per nuove quotazioni!"
        )
        await self.send_message(msg)

    async def notify_reward(self, amount_usdc: float, new_balance: float) -> None:
        """Alert on daily reward distribution."""
        msg = (
            f"🎁 <b>ACCREDITO RICOMPENSE POLYMARKET!</b>\n\n"
            f"• <b>Importo Ricevuto:</b> <code>+{amount_usdc:.4f}$ USDC</code>\n"
            f"• <b>Nuovo Saldo Totale:</b> <code>{new_balance:.2f}$ USDC</code>\n"
            f"• <b>Data:</b> <code>{time.strftime('%d/%m/%Y %H:%M:%S')}</code>"
        )
        await self.send_message(msg)

    async def notify_alert(self, title: str, details: str, is_critical: bool = False) -> None:
        """Alert on system errors, maintenance or circuit breaker."""
        emoji = "🚨" if is_critical else "⚠️"
        msg = (
            f"{emoji} <b>ALLERTA BOT POLYMARKET</b>\n\n"
            f"• <b>Evento:</b> {title}\n"
            f"• <b>Dettagli:</b> {details}\n"
            f"• <b>Orario:</b> <code>{time.strftime('%H:%M:%S')}</code>"
        )
        await self.send_message(msg)

    async def poll_commands(self, on_status_request: Callable[[], Coroutine[Any, Any, str]],
                            on_rewards_request: Callable[[], Coroutine[Any, Any, str]],
                            on_stop_request: Callable[[], Coroutine[Any, Any, str]],
                            on_resume_request: Callable[[], Coroutine[Any, Any, str]]) -> None:
        """Background listener for interactive Telegram commands (/status, /rewards, /stop, /resume)."""
        if not self.is_configured:
            return

        print("[Telegram Bot] In ascolto per comandi interattivi (/status, /rewards, /stop, /resume)...")
        while True:
            try:
                url = f"{self.base_url}/getUpdates"
                params = {"offset": self._last_offset + 1, "timeout": 20}
                async with httpx.AsyncClient(timeout=25.0) as client:
                    r = await client.get(url, params=params)
                    if r.status_code == 200:
                        data = r.json()
                        for update in data.get("result", []):
                            self._last_offset = update.get("update_id", self._last_offset)
                            msg = update.get("message", {})
                            chat_id = str(msg.get("chat", {}).get("id", ""))
                            text = msg.get("text", "").strip().lower()

                            if self.chat_id and chat_id != str(self.chat_id):
                                continue

                            if text in ("/status", "/stats", "status"):
                                reply = await on_status_request()
                                await self.send_message(reply)
                            elif text in ("/rewards", "/ricompense", "/yield", "/guadagni", "rewards", "ricompense"):
                                reply = await on_rewards_request()
                                await self.send_message(reply)
                            elif text in ("/stop", "/halt", "stop"):
                                reply = await on_stop_request()
                                await self.send_message(reply)
                            elif text in ("/resume", "/start", "start"):
                                reply = await on_resume_request()
                                await self.send_message(reply)
                            elif text in ("/ping", "ping"):
                                await self.send_message("🏓 <b>Pong!</b> Il bot è attivo e connesso.")
                            elif text.startswith("/"):
                                help_msg = (
                                    "🤖 <b>Comandi Disponibili:</b>\n\n"
                                    "• <code>/status</code> - Saldo, ordini e mercati attivi\n"
                                    "• <code>/rewards</code> - Calcolo ricompense in tempo reale ($/ora e $/giorno)\n"
                                    "• <code>/stop</code> - Cancellazione ordini ed arresto emergenza\n"
                                    "• <code>/resume</code> - Riattiva la quotazione\n"
                                    "• <code>/ping</code> - Verifica se il bot risponde"
                                )
                                await self.send_message(help_msg)

            except Exception:
                pass

            await asyncio.sleep(2.0)


telegram = TelegramNotifier()
