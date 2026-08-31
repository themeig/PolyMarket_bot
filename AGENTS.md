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
