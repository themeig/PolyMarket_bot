# Polymarket Quant Bot Operational & Microstructure Rules

## 1. Wallet & Contract Architecture
- **Custody Verification:** When interacting on-chain, always verify if the account is an EOA or a Polymarket Deposit Wallet (Proxy). For Deposit Wallets, ERC-1155 tokens and collateral are held on the proxy address (`proxy_wallet`), requiring Relayer EIP-712 batching rather than direct EOA transactions.
- **Contract Routing:** Check `negRisk` metadata on every market:
  - Standard binary markets route to CTF (`0x4D97DCd97eC945f40cF65F87097ACe5EA0476045`).
  - Multi-outcome / mutually exclusive events route to NegRisk Adapter (`0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296`).
  - When placing orders on CLOB for NegRisk markets, include `neg_risk = True` in order creation options.

## 2. Market Making & Inventory Rules
- **Complete Set Merging:** Any condition holding both YES and NO tokens must prioritize immediate complete set merging ($1.0\text{ YES} + 1.0\text{ NO} = 1.000\$$ USDC) to recycle collateral, restore capital velocity, and eliminate 100% of directional market risk.
- **Unpaired Residuals:** Unpaired single-leg inventory must be placed as passive Maker Take-Profit ASK orders (`_maybe_exit`) with an edge above the weighted average entry price.
- **Accounting & Equity Invariants:** Always compute Net Worth as $\text{Free Cash} + \sum(\text{Positions Value})$. Merging converts illiquid token equity into liquid cash without altering total equity.

## 3. Rewards Optimization
- For liquidity rewards farming, target markets with `rewardsDailyRate > 0`, verifying that order sizes meet `rewardsMinSize` and spreads are strictly within `rewardsMaxSpread`.
- Prioritize dual-bidding (BUY YES + BUY NO) to avoid the $3\times$ penalty applied to single-sided quoting.
- Note that rewards are distributed automatically daily at 00:00 UTC directly in USDC to maker addresses (minimum $1.00 payout threshold).

## 4. Empirical Data Verification & API Parsing Invariants
- **Mandatory Live Data Verification:** Never assert market conditions, rewards availability, order book states, or account balances based on assumptions or partial fields. Always execute a live script to query the raw endpoint / contract before formulating conclusions.
- **Nested Rewards Parsing Invariant:** In the Polymarket Gamma API, top-level `rewardsDailyRate` or `rewardDailyRate` can be `null`/`None` even when active rewards exist. Always inspect the nested `clobRewards` list (`clobRewards[].rewardsDailyRate`) and check `is_order_scoring` / `are_orders_scoring` on the CLOB API.
- **Proxy Balance Allowance Invariant:** When querying CLOB collateral balances, always pass `signature_type=2` in `BalanceAllowanceParams` to prevent silent exceptions on Polymarket proxy wallets.
- **Atomic Paired Quoting:** Dual-bidding maker orders (`BUY YES` + `BUY NO`) must be posted with atomic rollback: if one leg fails or is rejected, the sibling leg must be cancelled immediately to prevent orphaned one-sided inventory.

## 5. Market Making Exit & Risk Invariants (Poly-Maker Standard)
- **Zero Taker Market-Dump Invariant:** Never execute taker market orders or naive percentage-based stop-loss dumps on illiquid prediction market books. Crossing the spread destroys maker edge.
- **Merge-First Priority Invariant:** The dominant, zero-risk exit mechanism is Complete Set Merging ($1.0\text{ YES} + 1.0\text{ NO} = 1.000\$$ USDC). Prioritize pairing and merging before attempting inventory sales.
- **Dynamic Maker Exit with Urgency Walk-Down (`_maybe_exit`):** Unpaired inventory must be managed via passive SELL limit orders:
  - Interpolate target between passive profit ($FV + \delta$) and floor (`best_bid + 0.001$`) based on hold duration urgency $u \in [0, 1]$.
  - **Inviolable constraint:** `target = max(target, best_bid + 0.001$)` (Never cross down through the bid).
- **Daily Loss Circuit Breaker (`daily_loss_kill_usdc`):** If cumulative realized daily loss breaches the threshold, transition the bot to `HALTED` / `REDUCE_ONLY`—halt all new BUY bids immediately and maintain only passive maker exits and on-chain merges.


