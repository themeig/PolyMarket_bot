import requests
import json

PROXY = "0x117da19a541ba6d89ae52043a67dbc22572d1de8"

# 1. Polymarket Data API
url_pm = f"https://data-api.polymarket.com/positions?user={PROXY}"
res_pm = requests.get(url_pm)
print("=" * 80)
print(f"1. RISPOSTA DIRETTA DA DATA-API POLYMARKET (Status: {res_pm.status_code}):")
print("=" * 80)
if res_pm.status_code == 200:
    pm_data = res_pm.json()
    print(f"Numero di posizioni restituite da Polymarket: {len(pm_data)}")
    for idx, p in enumerate(pm_data, 1):
        print(f"[{idx}] Titolo: {p.get('title')}")
        print(f"    Size: {p.get('size')} | AvgPrice: {p.get('avgPrice')} | CurPrice: {p.get('curPrice')} | CurrentVal: {p.get('currentValue')}")
else:
    print("Errore:", res_pm.text)

# 2. Nostro endpoint locale
url_local = "http://localhost:8080/api/status"
res_local = requests.get(url_local)
print("\n" + "=" * 80)
print(f"2. RISPOSTA DAL NOSTRO SERVER /api/status (Status: {res_local.status_code}):")
print("=" * 80)
if res_local.status_code == 200:
    local_data = res_local.json()
    print(f"Buy Orders ({len(local_data.get('buy_orders', []))}):", local_data.get('buy_orders'))
    print(f"Sell Orders ({len(local_data.get('sell_orders', []))}):", local_data.get('sell_orders'))
    print(f"Positions ({len(local_data.get('positions', []))}):", local_data.get('positions'))
