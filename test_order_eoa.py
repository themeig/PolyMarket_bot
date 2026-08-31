import os
import sys
import json
from dotenv import load_dotenv
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import OrderArgs, OrderType
from py_clob_client.order_builder.constants import BUY

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))
private_key = os.getenv("PRIVATE_KEY", "").strip().strip('"').strip("'")
if not private_key.startswith("0x") and len(private_key) == 64:
    private_key = "0x" + private_key

# signature_type 0 = EOA (Standard Ethereum/MetaMask wallet)
# signature_type 1 = PolyProxy
# signature_type 2 = PolyGnosisSafe
client = ClobClient(
    host="https://clob.polymarket.com", 
    key=private_key, 
    chain_id=137, 
    signature_type=0
)
api_creds = client.create_or_derive_api_creds()
client.set_api_creds(api_creds)

print("Test ordine con signature_type=0 (EOA)...")
token_id = "101513571766435454355723114188696307527864518980689079866102837918467349506537"
order_args = OrderArgs(
    price=0.11,
    size=15.0,
    side=BUY,
    token_id=token_id
)

try:
    signed_order = client.create_order(order_args)
    resp = client.post_order(signed_order, OrderType.GTC)
    print("Risposta Ordine Postato con SUCCESSO:", resp)
except Exception as e:
    print("Errore Post Order:", e)
