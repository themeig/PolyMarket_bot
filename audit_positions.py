import os
import sys
import json
import requests
from dotenv import load_dotenv
from py_clob_client_v2.client import ClobClient
from tabulate import tabulate

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

load_dotenv("polymarket_bot/.env")
key = os.getenv("PRIVATE_KEY", "").strip().strip('"').strip("'")
if not key.startswith("0x") and len(key) == 64:
    key = "0x" + key

client = ClobClient(
    host="https://clob.polymarket.com",
    key=key,
    chain_id=137,
    signature_type=3,
    funder="0x117da19a541ba6d89ae52043a67dbc22572d1de8"
)
client.set_api_creds(client.create_or_derive_api_key())

open_orders = client.get_open_orders()
status_res = requests.get("http://localhost:8080/api/status").json()

print("=" * 110)
print("🔍 REPORT COMPLETO DI AUDIT: STATO POSIZIONI, ESECUZIONI & SALDO REALE")
print("=" * 110)

print(f"💰 Saldo Iniziale Depositato:     14.69 USDC")
print(f"💵 Saldo Net Worth Attuale:       {status_res.get('net_worth')} USDC")
print(f"🟢 Saldo Libero nel Wallet:       {status_res.get('free_cash')} USDC")
print(f"📌 Capitale Impegnato in Ordini:  {status_res.get('in_orders')} USDC")
print(f"⛽ Gas POL Rimanente su Polygon:  {status_res.get('pol_gas')} POL")
print(f"🏆 PnL Netto Complessivo:         {status_res.get('pnl_val')} USDC ({status_res.get('pnl_pct')} %)")
print(f"⚡ Esecuzioni Chiuse Completate:  {status_res.get('total_trades')}")
print("=" * 110)

print("\n📋 1. POSIZIONI ATTUALMENTE APERTE SUL MERCATO (CLOB):")
active_orders = status_res.get("active_orders", [])
if active_orders:
    table_data = []
    for idx, o in enumerate(active_orders, 1):
        strat = o.get("strategy", "N/D")
        m_title = o.get("market", "")
        buy_p = o.get("buy_price", 0)
        sell_p = o.get("sell_price", 0)
        shares = o.get("shares", 0)
        cost = round(buy_p * shares, 2)
        rev = round(sell_p * shares, 2)
        profit = round(rev - cost, 2)
        pct = round((profit / cost) * 100, 1) if cost > 0 else 0
        status = o.get("status")

        table_data.append({
            "#": idx,
            "Strategia": "⚡ HFT" if strat == "HFT" else "💎 WIDE",
            "Mercato": m_title[:38] + "...",
            "Stato": "IN ATTESA VENDITORE" if status == "PLACED" else "IN VENDITA",
            "Tuo BID": f"{buy_p:.3f} $",
            "Target ASK": f"{sell_p:.3f} $",
            "Quote": f"{shares} q",
            "Capitale ($)": f"{cost:.2f} $",
            "Profitto Target": f"+{profit:.2f} $ (+{pct}%)"
        })
    print(tabulate(table_data, headers="keys", tablefmt="fancy_grid"))
else:
    print("Nessun ordine aperto al momento.")

print("\n📜 2. STORICO ESECUZIONI CHIUSE & RECENTI TRADE:")
trades = status_res.get("trades", [])
if trades:
    print(tabulate(trades, headers="keys", tablefmt="fancy_grid"))
else:
    print("Nessuna esecuzione ancora registrata nella sessione corrente (gli ordini sono tutti in offerta attiva al BID).")

print("=" * 110)
