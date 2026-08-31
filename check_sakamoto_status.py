import os
import sys
import requests
import json
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
print("=" * 80)
print(f"ORDINI ATTUALMENTE APERTI SU POLYMARKET ({len(orders)}):")
print("=" * 80)
for o in orders:
    print(f"- ID: {o.get('id') or o.get('orderID')}")
    print(f"  Asset: {o.get('asset_id')}")
    print(f"  Side:  {o.get('side')} | Price: {o.get('price')} $ | Size: {o.get('size')} quote")

# Posizioni
pos_url = f"https://data-api.polymarket.com/positions?user=0x117da19a541ba6d89ae52043a67dbc22572d1de8"
pos = requests.get(pos_url).json()
print("\n" + "=" * 80)
print(f"POSIZIONI TOKEN NEL TUO WALLET ({len(pos)}):")
print("=" * 80)
for p in pos:
    print(f"- {p.get('title')}")
    print(f"  Asset: {p.get('asset')} | Quote: {p.get('size')} | Carico: {p.get('avgPrice')} $ | Prezzo Attuale: {p.get('curPrice')} $")
