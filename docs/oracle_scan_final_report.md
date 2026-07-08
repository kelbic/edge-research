# Oracle/DeFi config scanner — final project report

**Status: concluded by human direction, 2026-07-08. 34 batches across 4
phases, 53 commits. Full history in `git log` and `data/scan_0*.json`.**

## Overview

This project scanned live DeFi lending/money-market protocols, read-only,
for configuration misconfigurations across four sequential classes, each
pre-registered in a `CRITERIA*.md` file committed before its scan began:

| Phase | Class | Batches | Findings (investigated) |
|---|---|---|---|
| 1 | Oracle/decimals/hardcoded-feed/init-holes | 001-020 | 3 real hits, all resolved false-positive |
| 2 | Privileged-function access control | 021-026 | 2 real gaps, both `no-program-logged` |
| 3 | Non-standard token handling (fee-on-transfer/rebasing) | 027-030 | 0 live anomalies; 3 historical incidents documented |
| 4 | Interest-rate-model parameter sanity | 031-034 | 0 anomalies, 7/7 clean |

Zero mainnet state-changing transactions across the entire project —
every check was `eth_call`/`eth_getStorageAt`/`eth_getLogs`, or (Phase 3
only) a transfer simulated on a disposable local `anvil` fork.

## Coverage

- **15 chains**: Ethereum mainnet, Base, Arbitrum, Monad, MegaETH,
  Optimism, Sonic, Injective EVM, Robinhood Chain, Avalanche, Polygon,
  BNB Chain, Scroll, Gnosis, Linea.
- **~25 protocols/products**: Aave v3 (6+ chains) and V4, Aave Horizon
  (RWA), Morpho Blue (5 chains), Compound III (2 chains), Moonwell, GMX,
  Balancer V2, SparkLend (2 chains), Euler v2, Ethena, Flux Finance/Ondo,
  Fluid, Silo v2/v3, Notional Exponent, Dolomite Margin, plus direct
  checks on EtherFi/Kelp/Renzo/Puffer's LRT rate providers.

## Real findings, by phase

**Phase 1** — `findings/sparklend_hardcoded-feed_stablecoin-basket.md`,
`findings/morpho-base_hardcoded-feed_hermes-usdc.md`, and the
Compound-III deUSD/sdeUSD case (documented in batch data). All three
investigated to false-positive with fork/log evidence, not asserted.

**Phase 2** — `findings/euler_access-control_single-eoa-governor.md`
(Euler vault governed by a 1-of-1 EOA-controlled Safe, ~$1.75M TVL,
oracle-redirect capability ruled out via source, explicitly excluded from
Euler's own bounty scope) and
`findings/dolomite_access-control_short-timelock-with-bypass.md`
(5-minute timelock with role-based bypass, no bounty program exists).
Both real, both documented, neither actionable for disclosure.

**Phase 3** — `findings/morpho_known-incidents_bad-debt-markets.md`:
discovered 3 large "bad_debt_unrealized" Morpho markets ($1.5B+, $55M,
$39.5M), investigated thoroughly rather than alarm-escalated or ignored,
resolved to 3 already-public historical incidents (Kinto proxy exploit
July 2025, Stream Finance collapse November 2025, Resolv key compromise
March 2026). Notably, the Stream Finance case involved lending protocols
hardcoding xUSD's oracle to $1.00 during the crisis — the exact Class-3
pattern from Phase 1, observed here as a real contributing factor in a
$93M+ incident.

## Process/tooling findings

`findings/tooling_stale-address-book-entry_aave-polygon.md` and a
parallel Flux Finance case (Phase 1 batch-009): two stale third-party
address-book entries caught via independent on-chain verification before
they could mislead further work — direct validation of the project's
"never trust an address without confirming it yourself" discipline.

## Methodology established

- `scripts/oracle_sweep.sh`, `scripts/decimals_check.py`: reusable
  per-protocol price/decimals-sanity helpers (Phase 1).
- `scripts/fork_transfer_test.md`: validated Gate 1 fork methodology
  (`anvil` + `anvil_impersonateAccount` + real simulated transfer),
  exercised twice end-to-end (USDT, BONDUSD) in Phase 3 — the first
  actual use of fork verification this project's design always called
  for but hadn't needed until Phase 3's class required it.
- Depth-check pattern (Phase 2): codesize-only checks miss disguised
  single-signer "multisigs" that share a byte-identical proxy signature
  with genuinely robust ones — `getThreshold()`/`getOwners()` is required
  to actually distinguish them (this is exactly how the Euler finding was
  caught, after Morpho/GMX/Notional's superficially-identical Safes
  turned out to be genuinely robust).

## Cross-validation network (Phase 1, holds throughout)

WETH price agreed within ~0.5% across 8+ independent protocol
integrations; wstETH's ~23.6-23.9% premium agreed within ~0.3% across 8+
sources; BTC (WBTC/cbBTC/BTCB/etc.) clustered within ~0.5% across 8+
reads; every major LRT (weETH, rsETH, ezETH, wrsETH) cross-validated
across 3-9 unrelated integrations; 3 Pendle PT markets matched Pendle's
own live market API within 0.03-0.24%.

## Self-audit (final)

- Every real finding traces to a source+date and, where a severity claim
  was made, a fork/log verification — not an inferred guess.
- Every "this looks alarming" moment (HERMES/USDC, the K/xUSD/wstUSR bad
  debt markets) was investigated to a specific, evidenced conclusion
  before being reported, consistent with "honest NO cheaper than false
  GO" throughout.
- Zero mainnet transactions broadcast across 34 batches.
- Scope/bounty-eligibility was actively checked for every real finding
  before any disclosure framing was suggested (Euler's exclusion,
  Dolomite's absent program) — nothing was assumed in-scope.
- Both stale-address catches are documented precisely as tooling issues,
  not inflated into protocol vulnerabilities.

## What's left, if resumed later

Chains never reached: Celo, zkSync Era, Soneium, Metis. Classes
suggested but not attempted: cross-chain bridge/messaging trust
configuration, reward/incentive-distribution config, liquidation-incentive
sufficiency, collateral-correlation risk. Any of these would be a
reasonable Phase 5 starting point, following the same
pre-register-criteria-before-scanning discipline established here.
