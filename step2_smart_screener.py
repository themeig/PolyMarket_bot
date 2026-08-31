"""
=============================================================================
POLYMARKET STEP 2: DUAL-ENGINE SMART SCREENER (WITH SPORTS FILTER)
=============================================================================
"""

import sys
import asyncio
import aiohttp
import time
import math
import json
from datetime import datetime, timezone

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

GAMMA_API_URL = "https://gamma-api.polymarket.com/markets"

SPORTS_KEYWORDS = [
    "vs.", "vs ", " v ", "atp", "wta", "us open", "french open", "wimbledon", "australian open",
    " fc", "fc ", "cf ", "sc ", "inter", "milan", "juventus", "real madrid", "barcelona", 
    "arsenal", "chelsea", "liverpool", "manchester", "bayern", "psg", "tottenham", "newcastle",
    "nba", "wnba", "mlb", "nfl", "nhl", "premier league", "serie a", "la liga", "bundesliga",
    "ligue 1", "champions league", "europa league", "tennis", "soccer", "football", "basketball",
    "baseball", "hockey", "ufc", "boxing", "f1", "formula 1", "grand prix", "o/u ", "over/under",
    "spread", "handicap", "goal", "points", "winner", "match", "dodgers", "yankees", "red sox",
    "celtics", "lakers", "warriors", "bulls", "braves", "mets", "padres", "tigers", "athletics",
    "astros", "guardians", "blue jays", "orioles", "rays", "rangers", "mariners", "twins",
    "royals", "white sox", "phillies", "marlins", "nationals", "cubs", "brewers", "cardinals",
    "pirates", "reds", "diamondbacks", "rockies", "giants", "set 1", "set 2", "set 3", "half",
    "quarter", "innings", "round 1", "round 2", "round 3", "atmane", "munar", "vukic", "sakamoto",
    "alcaraz", "sinner", "djokovic", "sabalenka", "swiatek", "gauff", "medvedev", "zverev"
]

def is_sports_market(question_text):
    q_lower = question_text.lower()
    return any(k in q_lower for k in SPORTS_KEYWORDS)

def format_iso_time_ago(iso_str):
    if not iso_str:
        return "N/D"
    try:
        iso_clean = iso_str.replace("Z", "+00:00")
        dt = datetime.fromisoformat(iso_clean)
        now = datetime.now(timezone.utc)
        diff = (now - dt).total_seconds()
        if diff < 0:
            return "Adesso"
        if diff < 60:
            return f"{int(diff)}s fa"
        elif diff < 3600:
            mins = int(diff // 60)
            return f"{mins}m fa"
        elif diff < 86400:
            hours = int(diff // 3600)
            return f"{hours}h fa"
        else:
            days = int(diff // 86400)
            return f"{days}g fa"
    except Exception:
        return "N/D"

async def fetch_markets_batch(session, offset=0, limit=100):
    params = {
        "active": "true",
        "closed": "false",
        "limit": limit,
        "offset": offset,
        "order": "volume24hr",
        "ascending": "false"
    }
    try:
        async with session.get(GAMMA_API_URL, params=params, timeout=10) as response:
            if response.status == 200:
                return await response.json()
            return []
    except Exception:
        return []

async def get_all_active_markets(total_to_fetch=1500):
    batch_size = 100
    offsets = range(0, total_to_fetch, batch_size)
    t_start = time.time()
    async with aiohttp.ClientSession() as session:
        tasks = [fetch_markets_batch(session, offset=off, limit=batch_size) for off in offsets]
        results = await asyncio.gather(*tasks)
    all_markets = []
    for batch in results:
        all_markets.extend(batch)
    elapsed = time.time() - t_start
    return all_markets, elapsed

def filter_dual_engine_markets(markets, exclude_sports=True):
    hft_markets = []
    wide_spread_markets = []

    for m in markets:
        question = m.get("question", "N/A")
        market_id = m.get("id")
        best_bid = m.get("bestBid")
        best_ask = m.get("bestAsk")
        volume_24h = float(m.get("volume24hr", 0) or 0)
        liquidity = float(m.get("liquidity", 0) or 0)
        updated_at = m.get("updatedAt", "")
        last_trade_p = m.get("lastTradePrice")
        
        # FILTRO SPORT: Esclude qualsiasi evento sportivo se richiesto
        if exclude_sports and is_sports_market(question):
            continue

        tokens_raw = m.get("clobTokenIds", "[]")
        tokens = json.loads(tokens_raw) if isinstance(tokens_raw, str) else tokens_raw
        token_id = tokens[0] if tokens else ""

        if best_bid is None or best_ask is None or best_bid <= 0 or best_ask <= 0:
            continue
            
        spread = best_ask - best_bid
        mid_price = (best_ask + best_bid) / 2.0
        spread_pct = (spread / mid_price) * 100.0

        my_bid = round(best_bid + 0.001, 3)
        my_ask = round(best_ask - 0.001, 3)
        net_spread_capture = round(my_ask - my_bid, 3)
        net_spread_pct = round((net_spread_capture / mid_price) * 100.0, 1)

        if my_ask <= my_bid or net_spread_capture < 0.003:
            continue

        time_ago_str = format_iso_time_ago(updated_at)
        last_p_str = f"{last_trade_p:.3f} $" if last_trade_p is not None else "-"

        # CATEGORIA 1: HFT (Volume > $2.000, Spread Stretto 0.5% - 20%, Esecuzione Rapida)
        if volume_24h >= 2000 and 0.5 <= spread_pct <= 25.0 and 0.06 <= best_bid <= 0.94:
            vol_score = min(100.0, (math.log10(volume_24h) / 5.5) * 100.0)
            score = round(vol_score * 0.70 + min(100.0, liquidity / 500.0) * 0.30, 1)
            hft_markets.append({
                "id": market_id,
                "token_id": token_id,
                "clob_token_ids": tokens,
                "raw_best_bid": best_bid,
                "raw_best_ask": best_ask,
                "strategy": "HFT",
                "badge": "⚡ HFT RAPIDO",
                "Score": score,
                "Mercato": question[:42] + ("..." if len(question) > 42 else ""),
                "Mid Price": f"{mid_price:.3f} $",
                "Best Bid": f"{best_bid:.3f} $",
                "Mio BID (Compra)": f"{my_bid:.3f} $",
                "Mio ASK (Vendi)": f"{my_ask:.3f} $",
                "Best Ask": f"{best_ask:.3f} $",
                "Margine Netto": f"+{net_spread_capture:.3f} $ ({net_spread_pct}%)",
                "Ultimo Trade": f"{last_p_str} ({time_ago_str})",
                "Vol 24h": f"{volume_24h:,.0f} $",
                "Liquidita": f"{liquidity:,.0f} $"
            })

        # CATEGORIA 2: WIDE SPREAD (Spread Largo > 15%, Margine Alto +30% / +80%)
        if spread >= 0.02 and spread_pct >= 15.0 and volume_24h >= 30 and 0.04 <= best_bid <= 0.85:
            spread_score = min(100.0, (spread_pct / 50.0) * 100.0)
            score = round(spread_score * 0.60 + min(100.0, math.log10(max(10, volume_24h)) * 25) * 0.40, 1)
            wide_spread_markets.append({
                "id": market_id,
                "token_id": token_id,
                "clob_token_ids": tokens,
                "raw_best_bid": best_bid,
                "raw_best_ask": best_ask,
                "strategy": "WIDE",
                "badge": "💎 SPREAD LARGO",
                "Score": score,
                "Mercato": question[:42] + ("..." if len(question) > 42 else ""),
                "Mid Price": f"{mid_price:.3f} $",
                "Best Bid": f"{best_bid:.3f} $",
                "Mio BID (Compra)": f"{my_bid:.3f} $",
                "Mio ASK (Vendi)": f"{my_ask:.3f} $",
                "Best Ask": f"{best_ask:.3f} $",
                "Margine Netto": f"+{net_spread_capture:.3f} $ ({net_spread_pct}%)",
                "Ultimo Trade": f"{last_p_str} ({time_ago_str})",
                "Vol 24h": f"{volume_24h:,.0f} $",
                "Liquidita": f"{liquidity:,.0f} $"
            })

    hft_markets = sorted(hft_markets, key=lambda x: x["Score"], reverse=True)
    wide_spread_markets = sorted(wide_spread_markets, key=lambda x: x["Score"], reverse=True)
    
    return hft_markets, wide_spread_markets
