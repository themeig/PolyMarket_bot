import os
import sys
from dotenv import load_dotenv
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import BalanceAllowanceParams, AssetType

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))
private_key = os.getenv("PRIVATE_KEY", "").strip().strip('"').strip("'")
if not private_key.startswith("0x") and len(private_key) == 64:
    private_key = "0x" + private_key

client = ClobClient(host="https://clob.polymarket.com", key=private_key, chain_id=137)
api_creds = client.create_or_derive_api_creds()
client.set_api_creds(api_creds)

params = BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
res = client.update_balance_allowance(params)
print("Balance allowance updated successfully:", res)
