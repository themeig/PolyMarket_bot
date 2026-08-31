import requests
from web3 import Web3

PROXY_WALLET = "0x117da19a541ba6d89ae52043a67dbc22572d1de8"
w3 = Web3(Web3.HTTPProvider("https://polygon.drpc.org"))

# 1. Native USDC
usdc_contract = w3.eth.contract(
    address=Web3.to_checksum_address("0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359"),
    abi=[{"constant": True, "inputs": [{"name": "_owner", "type": "address"}], "name": "balanceOf", "outputs": [{"name": "balance", "type": "uint256"}], "type": "function"}]
)
cash_balance = usdc_contract.functions.balanceOf(Web3.to_checksum_address(PROXY_WALLET)).call() / 1e6

# 2. Open positions market value from Polymarket Data API
pos_url = f"https://data-api.polymarket.com/positions?user={PROXY_WALLET}"
resp = requests.get(pos_url)
positions_market_val = 0.0
if resp.status_code == 200:
    for p in resp.json():
        positions_market_val += float(p.get("currentValue", 0) or 0)

total_polymarket_net_worth = cash_balance + positions_market_val
print(f"Cash USDC nel Proxy:            {cash_balance:.2f} $")
print(f"Valore di Mercato delle Quote:  {positions_market_val:.2f} $")
print(f"TOTALE PORTAFOGLIO POLYMARKET:  {total_polymarket_net_worth:.2f} $")
