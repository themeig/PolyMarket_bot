import os
import sys
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
print("Balance raw:", bal.get("balance"))
raw_val = float(bal.get("balance", 0)) / 1e6
print(f"Available Collateral on CLOB: {raw_val:.2f} $")

orders = client.get_open_orders()
print(f"Open Orders on CLOB: {len(orders)}")
for o in orders:
    print(" - Order:", o.get("id"), o.get("asset_id"), o.get("price"), o.get("size"), o.get("side"))
