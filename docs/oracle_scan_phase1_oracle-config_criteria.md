# Pre-registered verdict criteria (committed BEFORE any scan)

Committed: 2026-07-07, before batch-001 discovery. These thresholds are fixed
in advance and MUST NOT be loosened post-hoc to make a finding qualify.

## Scanner classes and anomaly thresholds

1. **Oracle mismatch**: on-chain `getAssetPrice()` / `latestAnswer()` (or
   equivalent read function) vs a UniswapV3 1h TWAP for the same asset pair,
   computed independently from pool slot0/observations. Anomaly iff
   `abs(feed_price - twap_price) / twap_price > 0.05` (>5%).
   - Excluded by design (not an anomaly) if: the feed is a documented
     rate-oracle (e.g. stETH/ETH exchange-rate style), the asset is in a
     known depeg event (cross-check CoinGecko/DefiLlama price history for the
     same UTC window), or the feed is a stated constant peg (e.g. USDe-style
     1:1) — these get status `false-positive`, not `finding`.

2. **Decimals mismatch**: token `decimals()` vs the scale the consuming
   contract assumes for that feed (inferred from how the raw answer is used
   in the contract's own math, not from docs). Anomaly iff the assumed scale
   and actual feed scale differ by a power of 10 that the code does not
   itself correct for.

3. **Hardcoded/zero feed**: `priceFeed` storage slot resolves to
   `0x000...000`, or the feed address's bytecode is short/constant-return
   (e.g. `price()` always returns a fixed value regardless of block/input,
   feed-update functions revert or are absent). Anomaly iff true AND not
   accompanied by an explicit, verifiable constant-peg design doc/comment in
   verified source.

4. **Init holes**: proxy `implementation()` slot (EIP-1967
   `0x360894...bbb` slot) set but `initialized`/version storage slot at 0,
   OR `initialize()` selector present and callable (no `initializer`
   modifier effect observed via bytecode) on a proxy whose implementation
   has >3 distinct historical `Upgraded` log entries (storage-collision risk
   window).

## Severity bands (post fork verification only)

- **Critical**: impact ≥ 25% of protocol's on-chain TVL for the affected
  market, exploitable by any address with no special permissions.
- **High**: impact ≥ 5% of affected-market TVL, or affects borrow/liquidation
  solvency of the whole pool (not just one isolated market).
- **Medium**: impact confined to a single isolated market/asset, < 5% of
  that market's TVL, or requires a precondition (e.g. specific collateral
  combination) to realize.
- **Low / informational**: anomaly confirmed but no realizable fund impact
  found on fork within this scan's time budget.

Severity numbers MUST come from a fork measurement + a saved recompute
script (`scripts/`), not estimation. impact ≤ exposure is a hard sanity
check — if computed impact exceeds protocol TVL/exposure, treat as a
computation bug, not a finding, until resolved.

## Status taxonomy (every findings/*.md must declare one)

- `draft-report`: anomaly confirmed on fork, severity computed, protocol has
  an active bounty program, asset/contract is in the program's stated scope,
  safe harbor applies. Ready for human disclosure decision.
- `no-program-logged`: anomaly confirmed on fork, but protocol has no
  bounty program (or contract out of scope) — logged for record, not
  actionable for disclosure.
- `false-positive`: anomaly detected by scanner but explained by legitimate
  by-design behavior (see exclusions above), disproven on fork.
- `suspicion`: anomaly detected, fork verification incomplete or severity
  depends on an unverified assumption (e.g. "no fallback oracle exists") —
  NOT a finding until the assumption is checked on fork.

## Zero-mainnet-interaction rule

All scanning uses `eth_call` / `eth_getStorageAt` / `eth_getLogs` against
mainnet (read-only, no signed tx). All PoC / impact demonstration happens on
an `anvil --fork-url <rpc> --fork-block-number <N>` local fork. No mainnet
transaction is ever broadcast by this project, including "harmless"
claim/deposit calls.

## Stop conditions (session-loop level)

- Critical/High anomaly confirmed on a protocol with an active bounty
  program → stop, do not disclose, ask human.
- Curated queue exhausted (all priority programs processed at least once).
- ≥20 consecutive batches with zero anomalies of the 4 classes above →
  report "field dry for this class", ask human whether to change class/queue.
