from web3 import Web3

w3 = Web3(Web3.HTTPProvider('https://polygon.drpc.org'))
addr = '0xb41EfF43D6cB0952313fE4d181453676f68a7CAf'

tokens = [
    ("Native USDC", "0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359"),
    ("USDC.e (Bridged)", "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"),
    ("USDT (Tether)", "0xc2132D05D31c914a87C6611C10748AEb04B58e8F"),
    ("DAI", "0x8f3Cf7ad23Cd3CaDbD9735AFf958023239c6A063"),
    ("Wrapped POL (WPOL)", "0x0d500B1d8E8eF31E21C99d1Db9A6444d3ADf1270")
]

abi = [
    {"constant": True, "inputs": [{"name": "_owner", "type": "address"}], "name": "balanceOf", "outputs": [{"name": "balance", "type": "uint256"}], "type": "function"},
    {"constant": True, "inputs": [], "name": "decimals", "outputs": [{"name": "", "type": "uint8"}], "type": "function"}
]

print(f"Controllo token su {addr}:")
for name, c_addr in tokens:
    try:
        c = w3.eth.contract(address=w3.to_checksum_address(c_addr), abi=abi)
        raw_bal = c.functions.balanceOf(addr).call()
        dec = c.functions.decimals().call()
        bal = raw_bal / (10 ** dec)
        print(f" - {name}: {bal}")
    except Exception as e:
        print(f" - {name}: Errore ({e})")
