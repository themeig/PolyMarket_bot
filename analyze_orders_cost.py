import os
import sys
from dotenv import load_dotenv
from py_clob_client_v2.client import ClobClient

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
orders = client.get_open_orders()

print("=" * 75)
print("ANALISI DETTAGLIATA DEGLI ORDINI APERTI SUL TUO CONTO:")
print("=" * 75)

total_cost = 0.0
total_payout_potential = 0.0

for idx, o in enumerate(orders, 1):
    price = float(o.get("price", 0))
    size = float(o.get("original_size") or o.get("size") or 0)
    actual_cost = price * size
    potential_payout = size * 1.0
    total_cost += actual_cost
    total_payout_potential += potential_payout
    
    print(f"[{idx}] Prezzo: {price:.3f} $ | Quote: {size:,.0f} quote")
    print(f"    - COSTO REALE SPESO:     {actual_cost:.2f} $")
    print(f"    - POTENZIALE VINCITA:    {potential_payout:.2f} $ (se l'evento scade a 1.00$)")
    print("-" * 50)

print(f"TOTALE SOLDI REALI IMPEGNATI SU TUTTI GLI ORDINI: {total_cost:.2f} $")
print(f"TOTALE VALORE DI LIQUIDAZIONE MASSIMA:           {total_payout_potential:.2f} $")
print("=" * 75)
