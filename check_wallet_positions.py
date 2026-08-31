import os
import sys
import requests
from dotenv import load_dotenv
from py_clob_client_v2.client import ClobClient

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

PROXY_WALLET = "0x117da19a541ba6d89ae52043a67dbc22572d1de8"

# Interroghiamo Data API di Polymarket per le posizioni del wallet
url = f"https://data-api.polymarket.com/positions?user={PROXY_WALLET}"
resp = requests.get(url)
print("=" * 85)
print("📦 POSIZIONI E TOKEN EFFETTIVAMENTE POSSEDUTI NEL TUO WALLET:")
print("=" * 85)
if resp.status_code == 200:
    positions = resp.json()
    for p in positions:
        title = p.get("title") or p.get("market", {}).get("question") or p.get("asset")
        size = p.get("size")
        cur_p = p.get("curPrice")
        avg_p = p.get("avgPrice")
        val = p.get("currentValue")
        pnl = p.get("cashPnl") or p.get("unrealizedPnl")
        print(f"🔹 Mercato: {title}")
        print(f"   - Quote Possedute:  {size} quote")
        print(f"   - Prezzo di Carico: {avg_p} $")
        print(f"   - Prezzo Attuale:   {cur_p} $")
        print(f"   - Valore Attuale:   {val} $")
        print(f"   - PnL Posizione:    {pnl} $")
        print("-" * 50)
else:
    print("Status:", resp.status_code)
print("=" * 85)
