import sys
import asyncio
import aiohttp
import json
from tabulate import tabulate
from datetime import datetime, timezone

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

GAMMA_API_URL = "https://gamma-api.polymarket.com/markets"

async def scan_hft_markets():
    params = {
        "active": "true",
        "closed": "false",
        "limit": 100,
        "order": "volume24hr",
        "ascending": "false"
    }
    async with aiohttp.ClientSession() as session:
        async with session.get(GAMMA_API_URL, params=params) as resp:
            if resp.status == 200:
                markets = await resp.json()
            else:
                markets = []

    hft_candidates = []
    for m in markets:
        vol_24h = float(m.get("volume24hr", 0) or 0)
        liq = float(m.get("liquidity", 0) or 0)
        best_bid = m.get("bestBid")
        best_ask = m.get("bestAsk")
        q = m.get("question", "")
        m_id = m.get("id")
        tokens_raw = m.get("clobTokenIds", "[]")
        tokens = json.loads(tokens_raw) if isinstance(tokens_raw, str) else tokens_raw
        token_id = tokens[0] if tokens else ""

        if best_bid is None or best_ask is None or best_bid <= 0 or best_ask <= 0:
            continue
            
        spread = best_ask - best_bid
        mid = (best_ask + best_bid) / 2.0
        spread_pct = (spread / mid) * 100.0

        # Vogliamo mercati con ALTO volume (almeno 3.000$ nelle 24h) e spread sfruttabile
        if vol_24h >= 3000 and 0.5 <= spread_pct <= 25.0 and 0.05 <= best_bid <= 0.95:
            my_bid = round(best_bid + 0.001, 3)
            my_ask = round(best_ask - 0.001, 3)
            net_spread = my_ask - my_bid
            net_pct = (net_spread / mid) * 100.0

            if net_spread >= 0.003:
                hft_candidates.append({
                    "id": m_id,
                    "token_id": token_id,
                    "Mercato": q[:40] + ("..." if len(q) > 40 else ""),
                    "Vol 24h": f"{vol_24h:,.0f} $",
                    "Liquidita": f"{liq:,.0f} $",
                    "Best Bid": f"{best_bid:.3f} $",
                    "Mio BID": f"{my_bid:.3f} $",
                    "Mio ASK": f"{my_ask:.3f} $",
                    "Best Ask": f"{best_ask:.3f} $",
                    "Margine Netto": f"+{net_spread:.3f}$ ({net_pct:.1f}%)"
                })

    print("=" * 110)
    print("TOP MERCATI AD ALTA FREQUENZA DISPONIBILI (Volume 24h Elevato + Spread Eseguibile):")
    print("=" * 110)
    if hft_candidates:
        print(tabulate(hft_candidates[:10], headers="keys", tablefmt="fancy_grid"))
    else:
        print("Nessun candidato al momento.")

if __name__ == "__main__":
    asyncio.run(scan_hft_markets())
