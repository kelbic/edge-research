# Euler v2 EVK Vault ewstETH-2 — single-EOA governor (1-of-1 Safe)

**Status: no-program-logged.** Real, verified fact; explicitly excluded from
Euler's own Cantina bounty scope by their published policy. Not a protocol
vulnerability in Euler's core (EVK/EVC/EPO) contracts.

## Summary

The vault `EVK Vault ewstETH-2` (`0xbc4b4ac47582c3e38ce5940b80da65401f4628f1`,
Ethereum mainnet, first scanned in Phase 1 batch-003) has `governorAdmin()`
= `0x060DB084bF41872861f175d83f3cb1B5566dfEA3`, a Gnosis Safe proxy
(codesize 171 bytes). That Safe's `getThreshold()` = 1, `getOwners()` =
`[0xaA10A4d3fC764F8d0A7cE9E2Adf54aA4D36251c7]` — a single signer. That
signer address has **zero bytecode** (a bare EOA, not a contract) —
confirmed via `eth_getCode`.

In effect: one private key has unilateral, no-delay control over this
vault's governance functions.

## What the governor can and cannot do (verified via source, not assumed)

Fetched `euler-xyz/euler-vault-kit`'s `Governance.sol` (current main
branch) directly. `governorOnly`-gated functions: `setGovernorAdmin`,
`setFeeReceiver`, `setLTV`, `setMaxLiquidationDiscount`,
`setLiquidationCoolOffTime`, `setInterestRateModel`, `setHookConfig`,
`setConfigFlags`, `setCaps`, `setInterestFee`.

**Critically, there is no governor-controlled function to change the
vault's price oracle** — `oracle()` reads from immutable proxy metadata
set at deployment, not a governance setter. This caps the severity below
"redirect the oracle, drain instantly" (which would be Critical per
CRITERIA_v2) — the actual capability is real but narrower: a compromised
governor could set LTV/caps to unsafe values, change the interest rate
model, or configure malicious hooks, which could produce bad debt or
degrade the vault over time rather than an instant single-transaction
drain.

## Current exposure

- `totalAssets()` = 799.999 wstETH (2026-07-08 read)
- `totalSupply()` = 795.819 shares
- At the wstETH price established throughout this project (~$2190,
  cross-validated across 8+ independent sources in Phase 1), this is
  approximately **$1.75M** in vault assets — real, non-trivial, exceeding
  this project's own $100k materiality threshold from CRITERIA_v2.
- Depositor concentration (self-supplied vs. genuine third-party funds)
  was **not** independently traced via event logs this batch, unlike the
  HERMES/USDC precedent in Phase 1 — noted as an open gap below.

## Why this is NOT treated as a stop-condition trigger

Fetched Euler's Cantina bounty page
(`cantina.xyz/bounties/4d285eee-602e-440a-845e-25e155cec26a`) directly.
Its own published scope states, verbatim in substance:

> "Vault creators/governors: Anyone can create a vault and optionally
> retain governance control over it. Governors are responsible for
> securely configuring their own vaults, and for selecting suitable
> vaults to use as collateral."

And explicitly excludes:

> "Issues related to mistakes made by governors/deployers when
> configuring vaults or price oracles: The issue will be considered out
> of scope if it involves a user or vault actively opting to use
> something created or controlled by the untrusted actor."

And scopes the bounty only to vaults Euler's own production app considers
"known" for a given network — this specific vault's "known" status was not
independently confirmed, which would matter for scope even if the governor
question weren't already dispositive.

This is a direct, on-point match: Euler v2 (EVK) is explicitly a
permissionless vault-creation framework, and this vault's creator chose a
single-EOA governor. That is the creator's own risk decision within
Euler's documented design, not a bug in Euler's core protocol code — the
same class of situation as the Morpho Base HERMES/USDC market from Phase
1 (permissionless-market self-risk, not a protocol misconfiguration).

## Open gap / honest limitation

Unlike the HERMES precedent, this batch did NOT trace `Deposit`/`Withdraw`
event logs to determine whether the ~$1.75M in this vault came from the
governor's own address (fully self-contained, no third party at risk) or
from independent depositors (real third-party exposure to the governor's
key-compromise risk, even though not Euler's fault). That trace was not
completed this batch due to time budget — if this vault's depositor
identity matters for a future decision (e.g. whether to attempt contacting
depositors or Euler's team informally, outside the bounty process, given
real money could be at stake even if not bounty-eligible), it should be
done before any such outreach, not assumed either way.

## Self-audit

- Every claim traces to a direct `eth_call`/`eth_getCode`/source-fetch
  this session, dated 2026-07-08.
- Governor capability was verified via Euler's own current source, not
  assumed from the function name alone.
- The strongest counterargument (this is by-design permissionless
  behavior, not a protocol bug) is stated and supported with Euler's own
  published policy, not just asserted.
- Zero mainnet interaction: all reads were `eth_call`/`eth_getCode`/
  `WebFetch`, no transaction was broadcast.
- Sensitivity flagged plainly: severity assessment assumes the governor
  cannot redirect the oracle (confirmed via source) and that the TVL
  figure is accurate as of the read timestamp; depositor-concentration is
  an explicit unresolved gap, not silently assumed safe.
