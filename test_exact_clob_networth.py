import os
import requests
import json
from dotenv import load_dotenv
from py_clob_client_v2.client import ClobClient
from py_clob_client_v2.clob_types import BalanceAllowanceParams, AssetType

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

# Interroga balance allowance collaterale (USDC)
p = BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
clob_bal = client.get_balance_allowance(p)
print("CLOB Balance Allowance (Collateral):", clob_bal)

cash_val = float(clob_bal.get("balance", 0) or 0) / 1e6

# Interroga posizioni Data API
url = f"https://data-api.polymarket.com/positions?user=0x117da19a541ba6d89ae52043a67dbc22572d1de8"
pos = requests.get(url).json()
total_pos_val = sum(float(p.get("currentValue", 0) or 0) for p in pos)

print(f"\n=======================================================")
print(f"💵 Cash USDC Disponibile su Polymarket:    {cash_val:.2f} $")
print(f"📦 Valore Attuale di Tutte le Posizioni:  {total_pos_val:.2f} $")
print(f"💎 TOTALE PORTAFOGLIO POLYMARKET:         {cash_val + total_pos_val:.2f} $")
print(f"=======================================================")
