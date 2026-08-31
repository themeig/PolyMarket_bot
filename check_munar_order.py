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

open_orders = client.get_open_orders()
print("=" * 75)
print("ORDINI APERTI SUL TUO CONTO:")
print("=" * 75)
for o in open_orders:
    print(f"- ID: {o.get('id') or o.get('orderID')}")
    print(f"  Asset ID: {o.get('asset_id')}")
    print(f"  Side: {o.get('side')} | Price: {o.get('price')} $ | Size: {o.get('size')} quote")

# Controlliamo la posizione specifica di Jaume Munar
url = f"https://data-api.polymarket.com/positions?user=0x117da19a541ba6d89ae52043a67dbc22572d1de8"
r = requests.get(url)
print("\n" + "=" * 75)
print("POSIZIONI ATTUALI NEL WALLET:")
print("=" * 75)
if r.status_code == 200:
    for p in r.json():
        print(json.dumps(p, indent=2))
