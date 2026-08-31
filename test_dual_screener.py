import asyncio
from step2_smart_screener import get_all_active_markets, filter_dual_engine_markets
from tabulate import tabulate

async def test():
    markets, _ = await get_all_active_markets(total_to_fetch=1200)
    hft, wide = filter_dual_engine_markets(markets)
    print("=" * 90)
    print(f"⚡ HFT RAPIDO DISPONIBILI: {len(hft)}")
    print(tabulate(hft[:3], headers="keys", tablefmt="fancy_grid"))
    print("\n💎 SPREAD LARGO DISPONIBILI:", len(wide))
    print(tabulate(wide[:4], headers="keys", tablefmt="fancy_grid"))
    print("=" * 90)

asyncio.run(test())
