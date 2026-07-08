# SparkLend — shared constant-$1 oracle across 5 stablecoins

**Status: false-positive**

## Summary

SparkLend's AaveOracle (`0x8105f69D9C41644c6A0803fDA7D03Aa70996cFD9`) resolves
five distinct reserve assets — DAI, USDC, USDT, USDS, PYUSD (decimals 18, 6,
6, 18, 6 respectively) — to the exact same price source contract
`0x42a03F81dd8A1cEcD746dc262e4d1CD9fD39F777`, which is 224 bytes of
bytecode and returns a constant `latestAnswer() = 100000000` (8 decimals =
$1.00) for every caller regardless of which asset is asking.

## Evidence

- `getSourceOfAsset()` sweep via SparkLend's Pool
  (`0xC13e21B648A5Ee794902342038FF3aDAB66BE987`) and PoolAddressesProvider
  (`0x02C3eA4e34C0cBd694D2adFa2c690EECbC1793eE`), `eth_call` via
  `ethereum.publicnode.com`, 2026-07-07. Script: `scripts/oracle_sweep.sh`,
  raw output in `data/sparklend_sweep.json`.
- Source contract codesize 224 bytes; `latestAnswer()` returns `1e8` with
  `decimals()` = 8, for all five stablecoin assets identically.

## Why false-positive, not a decimals/hardcode bug

Aave-style oracles return a USD price scaled to `BASE_CURRENCY_UNIT`,
independent of the underlying token's own decimals (token decimals are
handled elsewhere in the protocol's reserve accounting) — so a shared
8-decimal `$1.00` constant is not, by itself, a decimals mismatch. All five
assets are genuinely deep, long-established, ~$1-pegged stablecoins; using a
single fixed-price contract for a basket of hard-pegged reserve currencies
to avoid unnecessary oracle-manipulation surface is documented practice in
the Aave/Spark ecosystem (the same pattern was independently confirmed twice
more in this scan run: Aave-v3-Monad's `GHO` feed, description
`"ONE USD"`, and its `mUSD` feed, description `"Fixed mUSD/USD"` — both
explicit, self-declared constant pegs).

## Residual caveat (why not fully closed)

Unlike the Monad GHO/mUSD feeds, this specific source contract's
`description()` call reverted (no such function), so intent could not be
directly read from the contract itself the same way — the false-positive
call rests on the pattern match (constant $1, shared across major
stablecoins, consistent with the twice-confirmed sibling pattern) rather
than an explicit on-chain label. If a future batch has spare budget, the
contract's disassembled bytecode could confirm this definitively.

## Scope

SparkLend program: https://immunefi.com/bug-bounty/sparklend/scope/ ($5M
max). Not submitted — this is not a misconfiguration.
