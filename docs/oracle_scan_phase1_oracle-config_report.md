# Oracle-scanner session report — batches 001–020

**Status: 20-dry-batch stop condition reached, 2026-07-07/08. Reporting to
human per pre-registered CRITERIA.md stop conditions.**

## Bottom line

Zero critical/high findings survived verification across 20 batches. This
is a valid, documented negative result: the oracle/decimals/hardcoded-feed/
init-hole misconfiguration class (per CRITERIA.md's 4 scanner classes) is
currently **dry** across everything scanned this session. Three real
scanner hits were investigated to false-positive with full evidentiary
writeups; two tooling/process issues (stale third-party address-book
entries) were caught and corrected before they could mislead further work.

## Coverage

- **20 batches**, ~55-60 distinct on-chain candidate checks
- **15 chains**: Ethereum mainnet, Base, Arbitrum, Monad, MegaETH, Optimism,
  Sonic, Injective EVM, Robinhood Chain, Avalanche, Polygon, BNB Chain,
  Scroll, Gnosis, Linea
- **~20 protocols/products**: Aave v3 (6+ chains), Aave V4 (Global Dollar
  Hub), Aave Horizon (permissioned RWA instance), Morpho Blue (5 chains),
  Compound III/Comet (2 chains), Moonwell, GMX, Balancer V2, SparkLend
  (2 chains), Euler v2, Ethena, Fluid, Silo Finance v2/v3, Notional
  Exponent, Flux Finance/Ondo, Dolomite Margin, plus direct checks on
  EtherFi/Kelp DAO/Renzo/Puffer's own LRT rate-provider contracts
- **Categories covered**: mainstream lending-fork oracle wiring,
  RWA/NAV-based off-chain-attested feeds, LRT custom exchange-rate oracles
  (including multi-hop composition), Pendle PT/YT discount pricing,
  fallback-oracle presence/behavior, liquidation-threshold sanity,
  storage-collision risk on long-lived upgraded proxies

## Real findings (investigated, all resolved to false-positive)

1. **Compound III mainnet — deUSD/sdeUSD "Constant price feed"**
   (`findings/` — not a separate file, documented in batch-002/008 data).
   A feed literally named "Constant price feed" returning ~$1e-8, paired
   with `borrowCollateralFactor=0`, `supplyCap=0`. Deliberate post-incident
   de-risking (under-prices collateral, protective not exploitable), not a
   misconfiguration.

2. **`findings/sparklend_hardcoded-feed_stablecoin-basket.md`** — a shared
   constant-$1 oracle across multiple stablecoins, confirmed as documented
   Aave/Spark ecosystem practice, independently re-confirmed **4 times**
   across the session (SparkLend Ethereum, Aave-Monad GHO/mUSD, Aave-Avalanche
   USDe/USDT, SparkLend Gnosis) — strong evidence this is genuine standard
   practice, not per-deployment coincidence.

3. **`findings/morpho-base_hardcoded-feed_hermes-usdc.md`** — the session's
   only stop-condition trigger (batch-001). A Morpho Blue market on Base
   with an absurdly-priced custom oracle and ~$10.6M apparent exposure.
   Traced via raw `eth_getLogs` event history to a single self-referential
   closed loop (one address supplying, collateralizing, and borrowing
   itself) — zero third-party funds exposed, not a protocol bug, not
   bounty-eligible. Downgraded from `suspicion` to `false-positive` with
   full on-chain evidence before continuing the loop.

## Tooling/process notes (not protocol vulnerabilities)

1. **`findings/tooling_stale-address-book-entry_aave-polygon.md`** — a
   third-party GitHub convenience library (`aave-address-book`) had a stale
   `AAVE_ORACLE` constant for Polygon. Caught via independent
   `ADDRESSES_PROVIDER` cross-check before it could produce a false
   conclusion. Aave's actual deployed contracts were unaffected throughout.

2. **Flux Finance/Ondo** (batch-009) — same pattern, same catch method,
   different chain/protocol. The discipline of never trusting a research
   agent's or a static file's address without independent `eth_call`
   verification caught real discrepancies twice.

## Cross-validation network (independent evidence the ecosystem is healthy)

- **WETH price**: 8+ independent sources agree within ~0.5%
- **wstETH premium** (~23.6-23.9%): 8+ independent sources agree within ~0.3%
- **BTC** (WBTC/cbBTC/BTCB/WBTC.e/BTC.b): 8+ independent reads cluster
  within ~0.5%
- **LRT premiums** — weETH (~9.6-9.9%), rsETH (~7.5-7.6%), ezETH
  (~8.1-8.85%), wrsETH (~7.25-7.6%): each confirmed across 3-9 independent
  protocol integrations
- **Pendle PT pricing**: 3 PT markets checked against Pendle's own live
  market-maker API as ground truth (not just internal plausibility) — all
  within 0.03-0.24%, the most rigorous verification of the session

## Self-audit (per CRITERIA.md mandate)

- Every number in this report traces to a source+date in the batch
  `data/scan_0NN.json` files and `git log`.
- The one real anomaly (HERMES/USDC) was fork/log-verified before being
  downgraded — not assumed safe.
- Misconfiguration vs. by-design was argued with evidence in all 3 real
  findings (not asserted).
- Zero mainnet state-changing transactions were made this entire session —
  confirmed: every RPC call across 20 batches was `eth_call`,
  `eth_getStorageAt`, or `eth_getLogs`.
- Sensitivities flagged, not hidden: RWA NAV-feed staleness rests on an
  unstress-tested assumption (off-chain pusher keeps running); Aave-Avalanche's
  USDe/USDT shared feed rests on an assumption those two stablecoins move
  together; both documented in their respective batch data rather than
  silently assumed safe.

## Recommendation to human

The class is dry as scanned. Options, not a decision made on your behalf:

1. **Change class** — e.g. privileged-function access-control audit
   (`setReserveInterestRateStrategyAddress`, `setPriceOracle`-style admin
   functions without adequate timelock/multisig protection), a class never
   attempted this session.
2. **Expand chain/protocol coverage** — Celo, zkSync Era, Soneium, Metis
   were identified as having Aave v3 deployments but never reached;
   Centrifuge's own tokenized-credit product (outside Morpho markets) was
   never directly examined.
3. **Conclude the project here** — 20 batches is substantial, systematic
   coverage; further scanning of this class faces genuine diminishing
   returns as documented across batches 013-019.

Stopping the loop now per the pre-registered stop condition. Awaiting your
direction.
