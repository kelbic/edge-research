# Phase 2 interim report — batches 021–024 (access control)

**Status: candidate queue exhausted for the basic depth level. Reporting
to human per CRITERIA_v2's queue-exhaustion stop condition.**

## Coverage

13 protocols' privileged-role structures checked across 4 batches:
Aave-family (10 deployments' `PoolAddressesProvider.owner()`), Morpho
Blue, Compound III, GMX, Balancer V2, Euler v2, Aave V4 (Hub/Spoke),
Fluid, Silo v2/v3, Notional Exponent, Dolomite Margin, SparkLend
(Ethereum + Gnosis, separately).

Two check depths applied:
1. **Basic**: is the admin/owner/governor address an EOA (zero bytecode)
   or a contract?
2. **Threshold depth** (added after batch-022 flagged the gap): for
   Safe-shaped (170-171 byte) admin addresses specifically, what is the
   actual signer threshold (n-of-m)?

## Findings

1. **`findings/euler_access-control_single-eoa-governor.md`** (high
   confidence) — Euler v2's `EVK Vault ewstETH-2` (~$1.75M TVL) is
   governed by a Safe with `threshold=1` wrapping a single EOA. Verified
   via Euler's own source that the governor cannot redirect the vault's
   oracle (capping severity below "instant drain"), and verified via
   Euler's own Cantina bounty scope that vault-governor misconfiguration
   on permissionless vaults is explicitly excluded from bounty coverage.
   Classified `no-program-logged`. Open gap: depositor concentration
   (self-supplied vs. third-party) not traced.

2. **`findings/dolomite_access-control_short-timelock-with-bypass.md`**
   (lower confidence) — Dolomite Margin's owner contract enforces only a
   5-minute timelock with a role-based bypass option, 1-2 orders of
   magnitude shorter than every other timelock confirmed this session.
   No active bounty program found for Dolomite (checked Immunefi,
   Cantina), so `no-program-logged` regardless of severity. Role-holder
   identities unresolved.

## Everything else: clean

11 of 13 protocols' privileged roles resolved to genuinely robust
governance: real DAO Safes with meaningful thresholds (Morpho 5-of-9,
GMX 4-of-6, Notional 3-of-7), known-good Timelocks with real delays
(GMX 24h, Compound 2 days), or legitimate documented infrastructure
confirmed via Etherscan/source (Aave's "ACL Admin V3 2" executor pattern
appearing consistently across 10 chains and Aave V4's Hub/Spoke via a
traced ProxyAdmin chain; Balancer's Authorizer; MakerDAO/Spark's SubProxy
"wards" pattern; Silo's genuinely immutable per-market config with no
admin key at all).

## Self-audit

- Both findings trace every claim to a source+date (on-chain read or
  WebFetch) this session.
- The Euler finding's severity ceiling (no oracle-redirect capability) was
  verified via direct source inspection, not assumed from the function
  name.
- Both findings' bounty-scope status was actively checked, not assumed --
  Euler's exclusion is explicit and on-point; Dolomite's is absence of any
  program at all.
- The two findings are presented with different, honestly-stated
  confidence levels (Euler: full chain of evidence; Dolomite: Etherscan
  analysis only, role-holders unresolved) rather than uniform certainty.
- Zero mainnet interaction across all 4 batches.

## Recommendation to human

The basic + threshold-depth check queue is exhausted across every
protocol reachable this session. Options:

1. **Go deeper still**: verify actual Safe signer *identity/reputation*
   (are the 5-9 signers on Morpho/GMX/Notional's multisigs independently
   known/reputable entities, or could they be sockpuppets of a single
   actor?), or scan for missing access-control modifiers via systematic
   source-diffing rather than spot-checks.
2. **Conclude Phase 2**: two real, documented, no-program-logged findings
   plus a clean sweep of 11 other protocols is a reasonable, substantial
   result for this class.
3. **Change class again** or **expand chain/protocol coverage** (same
   options as the Phase 1 wrap-up).
