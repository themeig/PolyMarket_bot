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

    async def send_message(self, text: str, parse_mode: str = "HTML", reply_markup: dict | None = None) -> bool:
        """Send a push message to the configured Telegram chat."""
        if not self.is_configured:
            return False
        url = f"{self.base_url}/sendMessage"
        payload: dict[str, Any] = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": True,
        }
        if reply_markup:
            payload["reply_markup"] = reply_markup
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                r = await client.post(url, json=payload)
                return r.status_code == 200
        except Exception as e:
            print(f"[Telegram] Errore invio messaggio: {e}")
            return False

    async def answer_callback_query(self, callback_query_id: str, text: str | None = None) -> bool:
        """Acknowledge inline keyboard button click to dismiss loading spinner."""
        if not self.is_configured or not callback_query_id:
            return False
        url = f"{self.base_url}/answerCallbackQuery"
        payload: dict[str, Any] = {"callback_query_id": callback_query_id}
        if text:
            payload["text"] = text
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                await client.post(url, json=payload)
                return True
        except Exception:
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

    async def register_bot_commands(self) -> bool:
        """Register command autocomplete menu (/status, /rewards, etc.) with Telegram."""
        if not self.is_configured:
            return False
        url = f"{self.base_url}/setMyCommands"
        commands = [
            {"command": "status", "description": "Saldo, ordini aperti, scoring e stato bot"},
            {"command": "pnl", "description": "Rendimento & PnL (Oggi, 7G, 30G, Sempre)"},
            {"command": "rewards", "description": "Guadagni live oggi, resa e soglia $1.00"},
            {"command": "accumulated", "description": "Storico totale ricompense on-chain"},
            {"command": "maxorders", "description": "Mostra o imposta tetto massimo ordini"},
            {"command": "target", "description": "Mostra o imposta target e profilo di rischio"},
            {"command": "info", "description": "Guida completa a tutti i comandi"},
            {"command": "ping", "description": "Test connettività e reattività bot"},
            {"command": "stop", "description": "Revoca ordini e arresto immediato"},
            {"command": "resume", "description": "Riattiva quotazione automatica"}
        ]
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                r = await client.post(url, json={"commands": commands})
                return r.status_code == 200
        except Exception as e:
            print(f"[Telegram] Errore registrazione comandi: {e}")
            return False

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
                            on_resume_request: Callable[[], Coroutine[Any, Any, str]],
                            on_pnl_request: Callable[[], Coroutine[Any, Any, str]] | None = None,
                            on_target_request: Callable[[str], Coroutine[Any, Any, str]] | None = None,
                            on_info_request: Callable[[], Coroutine[Any, Any, str]] | None = None,
                            on_accumulated_request: Callable[[], Coroutine[Any, Any, str]] | None = None,
                            on_max_orders_request: Callable[[str], Coroutine[Any, Any, str]] | None = None) -> None:
        """Background listener for interactive Telegram commands."""

        if not self.is_configured:
            return

        try:
            await self.register_bot_commands()
        except Exception:
            pass

        print("[Telegram Bot] In ascolto per comandi interattivi (/status, /rewards, /accumulated, /info, /target, /rischio, /stop, /resume)...")
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
                            
                            is_callback = "callback_query" in update
                            if is_callback:
                                cb = update["callback_query"]
                                cb_id = cb.get("id", "")
                                msg = cb.get("message", {})
                                chat_id = str(cb.get("from", {}).get("id", "") or msg.get("chat", {}).get("id", ""))
                                raw_text = cb.get("data", "").strip()
                                asyncio.create_task(self.answer_callback_query(cb_id))
                            else:
                                msg = update.get("message", {})
                                chat_id = str(msg.get("chat", {}).get("id", ""))
                                raw_text = msg.get("text", "").strip()

                            text = raw_text.lower()
                            parts = text.split()
                            cmd = parts[0].split("@")[0] if parts else ""

                            if not raw_text:
                                continue

                            print(f"[Telegram Bot] Ricevuto messaggio: '{raw_text}' (cmd: '{cmd}') da chat {chat_id}")

                            if self.chat_id and chat_id != str(self.chat_id):
                                print(f"[Telegram Bot] Ignorato messaggio da chat {chat_id} (chat_id autorizzato: {self.chat_id})")
                                continue

                            async def send_reply(res: Any) -> None:
                                if isinstance(res, tuple):
                                    txt, markup = res
                                    await self.send_message(txt, reply_markup=markup)
                                elif isinstance(res, str):
                                    await self.send_message(res)

                            try:
                                if cmd in ("/info", "/help", "info", "help", "/comandi", "comandi"):
                                    if on_info_request:
                                        reply = await on_info_request()
                                    else:
                                        reply = "🤖 Digita /status, /rewards, /accumulated, /target"
                                    await send_reply(reply)
                                elif cmd in ("/accumulated", "/storico", "/accumulate", "/totale", "/payouts", "accumulated", "storico", "totale"):
                                    if on_accumulated_request:
                                        reply = await on_accumulated_request()
                                        await send_reply(reply)
                                elif cmd in ("/status", "/stats", "status"):
                                    reply = await on_status_request()
                                    await send_reply(reply)
                                elif cmd in ("/pnl", "/profit", "/rendimento", "/gain", "pnl", "profit", "rendimento", "gain"):
                                    if on_pnl_request:
                                        reply = await on_pnl_request()
                                        await send_reply(reply)
                                elif cmd in ("/rewards", "/ricompense", "/yield", "/guadagni", "rewards", "ricompense"):
                                    reply = await on_rewards_request()
                                    await send_reply(reply)
                                elif cmd in ("/target", "/rischio", "target", "rischio") and on_target_request:
                                    reply = await on_target_request(raw_text)
                                    await send_reply(reply)
                                elif cmd in ("/maxorders", "/maxordini", "/ordini", "/coppie", "maxorders", "maxordini", "coppie") and on_max_orders_request:
                                    reply = await on_max_orders_request(raw_text)
                                    await send_reply(reply)
                                elif cmd in ("/stop", "/halt", "stop"):
                                    reply = await on_stop_request()
                                    await send_reply(reply)
                                elif cmd in ("/resume", "/start", "start"):
                                    reply = await on_resume_request()
                                    await send_reply(reply)
                                elif cmd in ("/ping", "ping"):
                                    await self.send_message("🏓 <b>Pong!</b> Il bot è attivo e connesso.")
                                elif cmd.startswith("/"):
                                    if on_info_request:
                                        reply = await on_info_request()
                                    else:
                                        reply = "🤖 Comando non riconosciuto. Digita /info per la lista completa."
                                    await send_reply(reply)
                            except Exception as cmd_err:
                                print(f"[Telegram Bot] Errore esecuzione '{cmd}': {cmd_err}")
                                await self.send_message(f"⚠️ Errore esecuzione comando: {cmd_err}")

            except Exception as loop_err:
                print(f"[Telegram Bot] Polling loop exception: {loop_err}")
                await asyncio.sleep(2)

            await asyncio.sleep(2.0)


telegram = TelegramNotifier()


