import os
import sys
import requests
import json
from dotenv import load_dotenv
from py_clob_client_v2.client import ClobClient
from py_clob_client_v2.clob_types import BalanceAllowanceParams, AssetType

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

p = BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
bal = client.get_balance_allowance(p)
cash = float(bal.get("balance", 0)) / 1e6
print(f"CLOB Collateral Cash: {cash:.2f} $")

orders = client.get_open_orders()
print(f"\n=======================================================")
print(f"ORDINI ATTUALMENTE APERTI SU POLYMARKET ({len(orders)}):")
print(f"=======================================================")
for o in orders:
    print(f"Order ID: {o.get('id') or o.get('orderID')}")
    print(f" - Asset: {o.get('asset_id')}")
    print(f" - Side:  {o.get('side')} | Price: {o.get('price')} $ | Size: {o.get('size')} quote | Amount: {float(o.get('price', 0) or 0) * float(o.get('size', 0) or 0):.2f} $")

# Posizioni nel wallet
pos_url = f"https://data-api.polymarket.com/positions?user=0x117da19a541ba6d89ae52043a67dbc22572d1de8"
pos = requests.get(pos_url).json()
print(f"\n=======================================================")
print(f"POSIZIONI TOKEN NEL TUO WALLET ({len(pos)}):")
print(f"=======================================================")
for p_item in pos:
    print(f" - {p_item.get('title')}")
    print(f"   Quote: {p_item.get('size')} azioni | Carico: {p_item.get('avgPrice')} $ | Spesa: {p_item.get('initialValue')} $ | Valore: {p_item.get('currentValue')} $")
