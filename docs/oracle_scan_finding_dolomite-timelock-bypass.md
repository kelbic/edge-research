# Dolomite Margin — 5-minute timelock with role-based bypass

**Status: no-program-logged.** Real, verified fact (via Etherscan's
analysis of verified source); no active bug bounty program found for
Dolomite on Immunefi or Cantina as of 2026-07-08, so not actionable for
disclosure regardless of severity. Documented for the record and for
comparison against the rest of this session's governance findings.

## Summary

Dolomite Margin's `owner()` on Arbitrum (`0x6bd780e7fdf01d77e4d475c821f1e7ae05409072`)
resolves to `0xC2B66E247daE5Ee749Ae1d827190115F3653dE06`, a verified
contract named **"DolomiteOwnerV2"** (13,011 bytes — a real, substantial
contract, not an EOA, confirmed via `eth_getCode`). Per Etherscan's
analysis of its verified source, this contract implements role-based
access control (`DEFAULT_ADMIN_ROLE`, `EXECUTOR_ROLE`,
`SECURITY_COUNCIL_ROLE`, `LISTING_COMMITTEE_ROLE`) with a timelock
mechanism enforcing a **300-second (5-minute) delay** before execution —
**with an explicit bypass option for specific roles**.

## Why this is worth recording

Every other timelock/delay mechanism independently confirmed this session
is 1-2+ orders of magnitude longer: GMX's Timelock buffer = 24 hours
(86,400s, confirmed via direct `buffer()` call, batch-022); Compound III's
Timelock = 2 days (172,800s, confirmed via Etherscan analysis of verified
source, Phase 1 batch-016). A 5-minute delay provides negligible practical
protection — it is not enough time for affected users to notice a queued
malicious/erroneous change and react (withdraw, exit positions) before it
executes, which is the entire security rationale for having a timelock at
all. Combined with an explicit bypass path for certain roles, the
practical protection could be even lower than 5 minutes for whichever
roles qualify.

## What was NOT resolved this batch (honest limitation)

- Did not identify which specific addresses hold `EXECUTOR_ROLE` or
  `SECURITY_COUNCIL_ROLE` (attempted via `getRoleMemberCount` with computed
  role hashes; reverted, likely because this contract does not implement
  OpenZeppelin's `AccessControlEnumerable` extension, or the exact role
  constant differs from the naive `keccak256("ROLE_NAME")` computation).
  Without this, it's unconfirmed whether the bypass-capable roles are
  themselves EOAs or further-protected multisigs -- this materially
  affects real severity and was not chased further given no bounty program
  makes the marginal effort lower-value than it would otherwise be.
- Did not fetch Dolomite's own source directly (their GitHub repo
  structure for this specific contract was not quickly locatable) --
  relied on Etherscan's analysis of the verified bytecode/source rather
  than reading the .sol file directly, a weaker form of verification than
  used for Euler's finding in the same batch.

## Self-audit

- The core fact (5-minute delay, bypass option, role names) is
  Etherscan-sourced from verified contract analysis, not assumed or
  guessed.
- Actively checked for bounty coverage before over-investing further
  effort or treating this as urgent -- none found, consistent with
  CRITERIA_v2's economic framing (don't grind indefinitely on
  non-actionable findings).
- This is explicitly weaker evidence than the Euler finding in the same
  batch (which had full source confirmation of governor capabilities and
  an explicit, on-point bounty-scope exclusion) -- flagged as such rather
  than presented with equal confidence.
- Zero mainnet interaction: all reads were `eth_call`/`eth_getCode`/
  `WebFetch`.
