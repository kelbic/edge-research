# Morpho Blue (Base) — HERMES/USDC market, hardcoded/absurd oracle

**Status: false-positive (resolved 2026-07-07 via on-chain event-log trace — see "Resolution" section at end). Originally logged as `suspicion` and used to trigger a stop/human-check; downgraded after read-only follow-up confirmed no third party is exposed.**

## Summary

A Morpho Blue market on Base pairs USDC (loan) against an "unrecognized",
"not_whitelisted" collateral token called HERMES, using a custom oracle
contract with only 202 bytes of bytecode that returns a constant price
implying ~1e21 USDC per HERMES token. The market is ~100% utilized with
~$10.6M USDC borrowed (on-chain ground truth; Morpho's own API reports
$38.5M for the same market, a discrepancy that is itself unexplained and
noted honestly below, not resolved by assumption).

## Evidence (read-only, source + date for every number)

- Market discovery: `blue-api.morpho.org` GraphQL, queried 2026-07-07,
  `markets(first: 20, orderBy: SizeUsd, orderDirection: Desc, where: {
  chainId_in: [8453] })`.
- Ground truth market params, confirmed via direct `eth_call` to Morpho Blue
  Base singleton `0xBBBBBbbBBb9cC5e90e3b3Af64bdAF62C37EEFFCb`,
  `idToMarketParams(0xe1986e80099257c65dd18091ec7e34752ae2336870a5649f20c450c9c4931fb8)`
  via `https://mainnet.base.org`, 2026-07-07:
  - loanToken = `0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913` (USDC, 6 decimals)
  - collateralToken = `0x4bcaf180df5b13c0441FE41A66e9638A2a410C6D` (HERMES, 18 decimals, symbol confirmed via `symbol()`/`decimals()` on-chain)
  - oracle = `0x83Ea4F286A8Eb97DD122c399Ca0CBE219Fffe81e`
  - irm = `0x46415998764C29aB2a25CbeA6254146D50D22687`
  - lltv = 980000000000000000 (98%)
- Oracle bytecode size: `cast codesize 0x83Ea4F286A8Eb97DD122c399Ca0CBE219Fffe81e` = **202 bytes** (2026-07-07, `mainnet.base.org`).
- Oracle `price()` = `1000000000000000000000000000000000000000000700` (≈1e45) (2026-07-07, same RPC).
  Per Morpho's documented oracle scale (`10^(36 + loanDecimals - collateralDecimals)`
  = `10^(36+6-18)` = `1e24` here), implied real price = 1e45 / 1e24 = **1e21
  USDC per HERMES** — not a plausible real-world price for any token.
- On-chain aggregate market state, `market(bytes32)` call to the same Morpho
  Blue singleton, 2026-07-07:
  - totalSupplyAssets = 10,601,580,152,383 raw (÷1e6 USDC decimals) = **$10,601,580.15**
  - totalBorrowAssets = 10,601,526,126,113 raw = **$10,601,526.13**
  - totalSupplyShares ≈ totalBorrowShares ≈ 5e18 (roughly matching Morpho's
    1e6-virtual-shares-per-asset initial ratio — consistent with a market
    that saw one large initial supply+borrow rather than many organic
    participants)
- Morpho API's own reported `state.supplyAssetsUsd` for the SAME marketId =
  **$38,529,703.09** (queried 2026-07-07) — **this does not match the
  on-chain read above and is not explained by accrued interest alone (3.6x
  gap). Flagged honestly as an unresolved discrepancy; the on-chain
  `eth_call` is treated as ground truth per project discipline, not the API.**
- `supplyingVaults` for this market (via `marketById` query) = **empty array**
  — no MetaMorpho vault (i.e., no pooled/curated third-party depositor
  capital) currently allocates to this market.
- API-native risk flags on this market: `not_whitelisted` (YELLOW),
  `unrecognized_collateral_asset` (YELLOW).
- `badDebt`/`realizedBadDebt` per API = 0/0 (2026-07-07) — no realized bad
  debt recognized yet, for whatever that API field is worth given the
  supplyAssetsUsd discrepancy above.

## Why this might NOT be a protocol misconfiguration (strongest counterargument)

Morpho Blue is explicitly permissionless: any address can create a market
with any (loanToken, collateralToken, oracle, irm, lltv) tuple, including a
custom oracle contract of arbitrary logic. Morpho's own indexer already
flags this exact market as `not_whitelisted` / `unrecognized_collateral_asset`
— i.e., the risk is visible to anyone querying the API or a front-end before
supplying. No MetaMorpho vault (curated, pooled depositor capital) allocates
here. This pattern is consistent with a self-created market where a single
actor supplied USDC directly and also acts as (or colludes with) the
borrower, extracting real USDC against fabricated collateral value — which
would be a loss to whoever supplied that specific USDC (if that party did so
unaware), not a bug in Morpho Blue's core contracts. This is analogous to
Euler's bounty scope explicitly excluding vault/market-specific pricing
decisions as "the responsibility of the party who chose it" — permissionless
oracle choice is a documented feature, not an audit miss.

## Why it's not yet dismissed as false-positive

- The $10.6M (on-chain) to $38.5M (API) discrepancy is unresolved — until
  explained, treating the position as fully self-contained/harmless would be
  an assumption, not a verified fact.
- Supplier identity is NOT yet established — I have not identified whether
  the USDC supplied to this market came from the same address that borrowed
  it (self-contained, no victim) or a distinct address (possible real
  victim). This requires reading `Supply`/`Borrow` event logs or
  `position()` calls per-user, which was not completed before hitting the
  stop condition.
- $10.6M real, currently-borrowed USDC against what appears to be a
  functionally worthless collateral asset is a large enough number that I
  am not comfortable unilaterally downgrading this to false-positive
  without a second opinion, per project discipline on honest NO vs false GO.

## Scope / disclosure assessment (preliminary, NOT final)

- Morpho's bug bounty is on Cantina: https://cantina.xyz/bounties/35a5f0a1-2ffd-432c-8f3b-77d169add8c3
  ($2.5M core / $1.5M periphery).
- Likely OUT OF SCOPE as a "Morpho Blue bug" given the permissionless-market
  design argument above — but this assessment is preliminary and explicitly
  flagged for human review, not acted on.
- Zero mainnet/Base state-changing interaction was made. All reads were
  `eth_call` (via `mainnet.base.org`) and a GraphQL read against Morpho's
  public API. No disclosure has been made to any party.

## Resolution (2026-07-07, read-only follow-up)

Traced via direct `eth_getLogs` against the Morpho Blue Base singleton
(`0xBBBBBbbBBb9cC5e90e3b3Af64bdAF62C37EEFFCb`), filtered by topic0 (event
signature) and topic1 (this exact marketId), over
`mainnet.base.org`, blocks 43,388,553-43,438,552 (creation block +50,000):

- Exactly one matching transaction:
  `0x9fa49c215a9d57fc537d84fca5f3bfca2e2fa66c7500670a064a537e6dd9039b`,
  block 43,388,553 (2026-03-15), containing all three events:
  - `Supply`: caller = onBehalf = `0x484662e06454eda4626bde25ebe21ebfb6aa3d72`, assets = 5,000,000,000,000 raw = **5,000,000.00 USDC**, shares = 5e18.
  - `SupplyCollateral`: same address, assets = 1,000,000,000,000,000,000 raw = **exactly 1.0 HERMES**.
  - `Borrow`: caller = onBehalf = receiver = same address, assets = 5,000,000,000,000 raw = **5,000,000.00 USDC**, shares = 5e18.
- No further `Supply`/`Borrow`/`SupplyCollateral` event for this marketId
  appears in the following 50,000 blocks (four more 10k-block chunks
  queried, all empty).
- Current on-chain state: `totalBorrowShares` = 5,000,000,000,000,000,000
  (exactly 5e18) — identical to the original borrow's share count, meaning
  no second borrow or repay has ever occurred. `totalSupplyShares` =
  5,000,028,378,911,230,201 (5e18 + ~0.00057%), consistent with a
  rounding/virtual-shares artifact, not a second depositor.
- The growth from $5.00M (2026-03-15) to ~$10.60M (2026-07-07, 113 days
  later) is fully explained by compounding interest: implied APY ≈ 1033%,
  consistent with a Morpho adaptive-curve IRM pinned near 100% utilization
  for months (recomputed via `(10.601580/5.0)^(365/113)-1`, script-checked).

**Conclusion:** single self-referential actor, closed loop, zero third-party
funds involved. Not a Morpho Blue protocol bug (permissionless-market
design working as intended), not bounty-eligible, not a live threat to any
depositor. Downgraded from `suspicion` to `false-positive`. No disclosure
made or warranted; no further action taken beyond this record.
