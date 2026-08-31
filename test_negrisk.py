import requests

r = requests.get('https://gamma-api.polymarket.com/events?active=true&closed=false&limit=40')
events = r.json()

print(f"Scaricati {len(events)} eventi complessi.")
for e in events:
    title = e.get("title", "N/A")
    markets = e.get("markets", [])
    if len(markets) >= 3 and e.get("negRisk"):
        bids = [float(m.get("bestBid") or 0) for m in markets]
        asks = [float(m.get("bestAsk") or 0) for m in markets]
        sum_bid = sum(bids)
        sum_ask = sum(asks)
        diff_bid = round((sum_bid - 1.0) * 100, 2)
        diff_ask = round((1.0 - sum_ask) * 100, 2)
        print(f"[*] Evento: {title}")
        print(f"    - Esiti: {len(markets)} candidati")
        print(f"    - Somma Migliori Offerte Acquisto (Bid): {sum_bid:.3f} $ (Overprice: {diff_bid}%)")
        print(f"    - Somma Migliori Offerte Vendita  (Ask): {sum_ask:.3f} $ (Underprice: {diff_ask}%)")
        print("-" * 65)
