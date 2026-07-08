# Morpho Blue — 3 large "bad_debt_unrealized" markets, all already-public historical incidents

**Status: false-positive (not a new finding) — retrospective validation of
scanning methodology, documented for completeness, not for disclosure.**

## Summary

While broadening the Phase 3 (non-standard token) search to Morpho's
permissionless markets, discovered 3 markets flagged `bad_debt_unrealized`
by Morpho's own API, with enormous absolute figures:

| Collateral | Chain | Market TVL (on-chain) | Unrealized bad debt |
|---|---|---|---|
| K (Kinto) | Arbitrum | ~$1.547B (verified via `market()` call) | $1.49B (API) |
| xUSD (Stream Finance) | — | $55.0M | $53.8M |
| wstUSR (Resolv) | — | $39.5M | $32.2M |

Initial reaction was that this could be a live, ongoing incident given the
scale. Investigation (on-chain event-log check + live web search) shows
all three are already fully public, independently reported, ~4-15-month-old
incidents — not new discoveries, and not actively unfolding (zero
Supply/Borrow/Repay/collateral events on the K/USDC market in the last 60
days, confirmed via `eth_getLogs`).

## What actually happened (per public reporting, not this project's discovery)

1. **K (Kinto)**, July 2025: an ERC-1967 proxy storage-slot zero-day let an
   attacker mint 110,000 counterfeit K tokens, deposit them as Morpho
   collateral while the price was still inflated, borrow USDC against
   them, then dump the tokens (crashing the real price >99%). Kinto
   attempted a recovery ("Phoenix"), partially succeeded, then shut down
   operations by 2025-09-30. Some compensation to affected Morpho users
   was already funded by the founder personally.
2. **xUSD (Stream Finance)**, November 2025: an external fund manager
   reportedly mismanaged ~$93M, xUSD depegged from $1 to ~$0.26 in 24
   hours (later $0.07-0.14). Widely reported as one of the largest DeFi
   contagion events of 2025 (~$756M in ecosystem-wide bad debt, $1B+
   outflows). Notably and directly relevant to this project's own
   specialization: **Morpho, Euler, and Elixir reportedly hardcoded
   xUSD's oracle price to $1.00 during the crisis** to halt cascading
   liquidations — which also meant the bad debt wasn't recognized/priced
   correctly going forward. This is the exact "hardcoded/constant feed"
   pattern (Class 3) this project scanned for throughout Phase 1, observed
   here in a real, already-materialized, extensively-reported incident
   rather than caught proactively.
3. **wstUSR (Resolv)**, March 2026: a single compromised private key in
   Resolv's minting infrastructure let an attacker print 80M unbacked USR
   from a $200k deposit, crashing USR 97.5% in ~17 minutes. Reported that
   Morpho's oracle for this asset did not update fast enough during the
   window, allowing under-priced collateral to be borrowed against at the
   stale $1.00 valuation before the crash was reflected.

## Why this is false-positive / not a new finding

- All three incidents are extensively covered by independent crypto media
  (CoinJournal, The Block, The Defiant, Cointegrity, BlockEden, Tiger
  Research, and others), predate this scan by 4-15 months, and in two
  cases already have documented remediation (Kinto compensation fund,
  Resolv's on-chain burn of hacker-controlled tokens).
- The root causes were NOT Morpho Blue protocol misconfigurations:
  Kinto's was a bug in Kinto's own token/proxy contract; Stream
  Finance's was reported fund mismanagement outside any single lending
  protocol; Resolv's was a compromised private key in Resolv's own
  minting system. Morpho Blue's permissionless design (anyone can create
  a market with any oracle/collateral) meant it was *exposed to* the
  fallout, consistent with the already-established HERMES/USDC pattern
  from Phase 1 (batch-001) — permissionless-market risk, not a Morpho
  code bug.
- Confirmed dormant via on-chain event logs (zero activity in 60+ days on
  the K/USDC market) — not an active, ongoing exploit requiring urgent
  action.

## Self-audit

- The scale claim ($1.547B TVL on the K/USDC market) was independently
  verified via direct `market()` call to Morpho Blue on Arbitrum
  (0x6c247b1F6182318877311737BaC0844bAa518F5e), not taken from the API
  alone — the API's $2.001B figure for the same market did not match the
  on-chain read and is flagged as an unexplained discrepancy, consistent
  with this project's standing practice of trusting on-chain reads over
  API display values.
- The "already publicly known" claim is sourced to multiple independent
  news outlets via live web search, not assumed from memory (training
  cutoff predates all three incidents).
- Zero mainnet interaction: all reads were `eth_call`/`eth_getLogs`/
  `WebSearch`.
- Correctly distinguished "large and alarming-looking" from "actionable
  new finding" — the instinct to verify further (event-log dormancy
  check, public-record search) before escalating is the same discipline
  applied to the HERMES/USDC case in Phase 1, now validated against a
  much larger and more consequential set of numbers.
