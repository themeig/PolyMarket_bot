import os
import sys
import json
from dotenv import load_dotenv
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import OrderArgs
from step2_smart_screener import get_all_active_markets, filter_and_rank_markets

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))
private_key = os.getenv("PRIVATE_KEY", "").strip().strip('"').strip("'")
if not private_key.startswith("0x") and len(private_key) == 64:
    private_key = "0x" + private_key

client = ClobClient(host="https://clob.polymarket.com", key=private_key, chain_id=137)
api_creds = client.create_or_derive_api_creds()
client.set_api_creds(api_creds)

print("Test piazzamento micro-ordine reale...")
# Test order on top market
import asyncio
async def test_order():
    markets, _ = await get_all_active_markets(total_to_fetch=500)
    screened = filter_and_rank_markets(markets)
    print(f"Mercati trovati: {len(screened)}")
    for m in screened:
        buy_p = float(m["Mio BID (Compra)"].replace(" $", ""))
        # Scegli un mercato con buy_p basso (es. 0.10 - 0.20) per avere almeno 5 quote con 1.50$
        if 0.05 <= buy_p <= 0.25:
            tokens_raw = [mk for mk in markets if mk.get("id") == m.get("id")][0].get("clobTokenIds", "[]")
            tokens = json.loads(tokens_raw) if isinstance(tokens_raw, str) else tokens_raw
            token_id = tokens[0] if tokens else ""
            shares = max(5, int(1.50 / buy_p))
            cost = shares * buy_p
            print(f"Tentativo ordine su: {m['Mercato']}")
            print(f"Token ID: {token_id}")
            print(f"Prezzo BID: {buy_p:.3f} $ | Quote: {shares} | Costo totale: {cost:.2f} $")
            try:
                args = OrderArgs(price=buy_p, size=shares, side="BUY", token_id=token_id)
                res = client.create_and_post_order(args)
                print("Risposta CLOB:", res)
                return
            except Exception as e:
                print("Errore CLOB:", e)
                return

asyncio.run(test_order())
