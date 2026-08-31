import os
import sys
import requests
import json
from dotenv import load_dotenv
from py_clob_client_v2.client import ClobClient
from py_clob_client_v2.clob_types import OrderArgs, OrderType, BalanceAllowanceParams, AssetType
from py_clob_client_v2.order_builder.constants import BUY

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
collateral = float(bal.get("balance", 0)) / 1e6
print(f"Collaterale disponibile: {collateral:.2f} $")

# Cerchiamo un mercato attivo
token_id = "5615282760875985231868508008056959876238536896643315063916840237042205273721" # Fed Interest Rates
buy_price = 0.471
shares = 5 # 5 * 0.471 = 2.35$

print(f"Invio ordine da {shares} quote a {buy_price:.3f}$ (Costo: {shares*buy_price:.2f}$)...")
args = OrderArgs(price=buy_price, size=shares, side=BUY, token_id=token_id)
res = client.post_order(client.create_order(args), OrderType.GTC)
print("Risposta piazzamento:", res)
