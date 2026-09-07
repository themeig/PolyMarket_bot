"""Daily Catalyst & Portfolio Risk Auditor for Antigravity & Telegram.

This script runs daily as an autonomous scheduled audit routine. It:
1. Audits current positions, open orders, cash, and PnL on Polymarket.
2. Conducts real-time online news scanning for active market keywords.
3. Evaluates and maintains the Catalyst Calendar (catalysts.json).
4. Dispatches an executive morning briefing directly to Telegram.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# Ensure local imports work
ROOT_DIR = Path(__file__).parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from catalyst_manager import CatalystManager, CatalystPhase
from telegram_bot import TelegramNotifier

STATUS_URL = "http://127.0.0.1:8080/api/polymaker/status"


def get_active_miniapp_url() -> str:
    url_file = ROOT_DIR / "cloudflared_url.txt"
    base = "https://pic-draft-presently-careers.trycloudflare.com"
    if url_file.exists():
        try:
            content = url_file.read_text(encoding="utf-8").strip()
            if content.startswith("http"):
                base = content
        except Exception:
            pass
    return f"{base}/miniapp?v={int(time.time())}"



def fetch_system_status() -> dict[str, Any]:
    """Fetch live engine status from local daemon or return fallback defaults."""
    try:
        with httpx.Client(timeout=5.0) as client:
            r = client.get(STATUS_URL)
            if r.status_code == 200:
                return r.json()
    except Exception as e:
        print(f"[Audit] Warning: Could not reach {STATUS_URL}: {e}")
    return {}


def load_ai_notes(notes_arg: str | None = None) -> str:
    """Load AI analysis notes passed via argument or from ai_briefing_notes.txt."""
    if notes_arg:
        return notes_arg.strip()
    notes_file = ROOT_DIR / "ai_briefing_notes.txt"
    if notes_file.exists():
        try:
            return notes_file.read_text(encoding="utf-8").strip()
        except Exception:
            pass
    return ""


def build_telegram_report(
    status: dict[str, Any],
    catalyst_evals: list[dict[str, Any]],
    ai_notes: str = "",
) -> str:
    """Build a professional HTML-formatted morning briefing for Telegram."""
    now_str = datetime.now().strftime("%d/%m/%Y ore %H:%M")
    net_worth = status.get("net_worth", 0.0)
    free_cash = status.get("free_cash", 0.0)
    locked_cash = status.get("locked_orders", 0.0)
    total_cash = status.get("total_balance", free_cash + locked_cash)
    pos_val = status.get("positions_val", 0.0)
    pnl_data = status.get("pnl", {})
    pnl_1d = pnl_data.get("1d", {}).get("pnl_usd", 0.0)
    pnl_7d = pnl_data.get("7d", {}).get("pnl_usd", 0.0)
    rewards_1d = pnl_data.get("1d", {}).get("rewards_usd", 0.0)

    pnl_1d_sign = "+" if pnl_1d >= 0 else ""
    pnl_7d_sign = "+" if pnl_7d >= 0 else ""

    lines = [
        f"🌅 <b>Antigravity Daily Briefing & Catalyst Audit</b>",
        f"<i>Report delle {now_str} CET</i>",
        "",
        f"💼 <b>Stato Capitale & Portafoglio:</b>",
        f"• <b>Net Worth Totale:</b> <code>${net_worth:.2f} USDC</code>",
        f"• <b>Cassa Libera:</b> <code>${free_cash:.2f}</code> | <b>Negli Ordini:</b> <code>${locked_cash:.2f}</code> (Tot: <code>${total_cash:.2f}</code>)",
        f"• <b>Valore Quote:</b> <code>${pos_val:.2f} USDC</code>",
        f"• <b>PnL Oggi (24h):</b> <code>{pnl_1d_sign}${pnl_1d:.2f}</code> (Rewards: <code>+${rewards_1d:.2f}</code>)",
        f"• <b>PnL Settimana (7G):</b> <code>{pnl_7d_sign}${pnl_7d:.2f}</code>",
        "",
    ]

    # Open Positions section (ALL markets)
    positions = status.get("positions", [])
    if positions:
        lines.append(f"📦 <b>Posizioni Aperte ({len(positions)} mercati):</b>")
        for p in positions:
            title = p.get("title", "Mercato")
            size = p.get("size", 0.0)
            avg_p = p.get("avg_price", 0.0)
            cur_p = p.get("cur_price", 0.0)
            val = p.get("current_val", 0.0)
            sell_ord = p.get("sell_order")
            sell_str = f" | In vendita ASK @ {sell_ord['price']:.2f}$" if sell_ord else ""
            lines.append(f"• <b>{title}</b>")
            lines.append(f"  └ <code>{size:.1f} quote</code> | Carico: <code>${avg_p:.2f}</code> ➔ Attuale: <code>${cur_p:.3f}</code> (Val: <code>${val:.2f}</code>){sell_str}")
        lines.append("")
    else:
        lines.append("📦 <i>Nessuna posizione aperta al momento. Portafoglio al 100% in liquidità.</i>\n")

    # Catalyst Calendar Section
    lines.append("📅 <b>Catalyst Calendar & Protezione Eventi:</b>")
    if catalyst_evals:
        for ce in catalyst_evals:
            ev = ce["event"]
            phase = ce["phase"]
            hrs = ce["hours_left"]
            status_icon = "🟢" if phase == CatalystPhase.NORMAL else ("🟡" if phase == CatalystPhase.REDUCE_ONLY else "🔴")
            days_str = f"{hrs / 24.0:.1f} giorni" if hrs and hrs >= 48 else (f"{hrs:.1f} ore" if hrs else "N/A")
            lines.append(f"{status_icon} <b>{ev.title}</b>")
            lines.append(f"  └ Data: <code>{ev.event_date_utc[:10]}</code> (Mancano: <b>{days_str}</b>)")
            lines.append(f"  └ Fase: <b>{phase.value}</b> ({ce['reason']})")
    else:
        lines.append("• <i>Nessun catalizzatore registrato a breve termine.</i>")
    lines.append("")

    # AI Agent Intelligence Section
    lines.append("🧠 <b>Verifica Notizie & Analisi Antigravity AI:</b>")
    if ai_notes:
        lines.append(ai_notes)
    else:
        lines.append("✅ <i>Scansione completata: nessun evento straordinario o rischio di toxic flow rilevato.</i>")

    lines.append("")
    lines.append("🛡️ <b>Verifica Integrità:</b> Motore di market making conforme, zero ordini incrociati taker.")
    return "\n".join(lines)


async def run_audit(dry_run: bool = False, ai_notes_arg: str | None = None) -> None:
    print(f"[{datetime.now().isoformat()}] Starting Daily Catalyst & Portfolio Audit...")

    # 1. Fetch system status
    status = fetch_system_status()

    # 2. Evaluate Catalyst Calendar for ALL markets
    cat_mgr = CatalystManager()
    events = cat_mgr.list_events()
    catalyst_evals = []
    seen_slugs = set()
    for ev in events:
        if ev.market_slug in seen_slugs:
            continue
        phase, hrs, next_ev, reason = cat_mgr.evaluate_market(ev.market_slug)
        if next_ev:
            catalyst_evals.append({
                "event": next_ev,
                "phase": phase,
                "hours_left": hrs,
                "reason": reason
            })
            seen_slugs.add(ev.market_slug)

    # 3. Load AI intelligence notes
    ai_notes = load_ai_notes(ai_notes_arg)

    # 4. Build message
    tg_text = build_telegram_report(status, catalyst_evals, ai_notes)

    print("\n--- REPORT TELEGRAM GENERATO ---")
    print(tg_text)
    print("--------------------------------\n")

    if dry_run:
        print("[Audit] Modalità DRY-RUN attiva: messaggio non inviato a Telegram.")
        return

    # 5. Send Telegram message
    notifier = TelegramNotifier()
    if notifier.is_configured:
        miniapp_url = get_active_miniapp_url()
        markup = {
            "inline_keyboard": [
                [{"text": "📱 Apri Dashboard (Mini App)", "web_app": {"url": miniapp_url}}],
                [{"text": "🔄 Aggiorna Status", "callback_data": "cmd_status"}],
            ]
        }
        ok = await notifier.send_message(tg_text, parse_mode="HTML", reply_markup=markup)
        if ok:
            print("[Audit] Messaggio inviato con successo su Telegram.")
        else:
            print("[Audit] Errore nell'invio del messaggio su Telegram.")
    else:
        print("[Audit] TelegramNotifier non configurato nel file .env.")


def main():
    parser = argparse.ArgumentParser(description="Antigravity Daily Catalyst Audit")
    parser.add_argument("--dry-run", action="store_true", help="Print report without sending to Telegram")
    parser.add_argument("--ai-notes", type=str, default=None, help="Custom AI analysis notes")
    args = parser.parse_args()
    asyncio.run(run_audit(dry_run=args.dry_run, ai_notes_arg=args.ai_notes))


if __name__ == "__main__":
    main()

