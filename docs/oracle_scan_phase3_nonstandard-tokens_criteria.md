# Pre-registered verdict criteria — Phase 3: non-standard token handling

Committed 2026-07-08, before batch-027. Phase 1 (oracle/decimals, 20
batches, see `findings/SESSION_REPORT_batches_001-020.md`) and Phase 2
(access control, 6 batches, see
`findings/PHASE2_INTERIM_REPORT_batches_021-024.md` + batches 025-026) are
both closed/paused by human direction. Same pre-registration discipline:
thresholds fixed in advance, not loosened post-hoc.

## Scope

Tokens listed as collateral or loan assets across the ~100+ tokens already
catalogued this session (see `data/scan_0*.json`) that exhibit
non-standard ERC20 behavior — fee-on-transfer (received amount < sent
amount) or rebasing (balance changes without a corresponding transfer) —
where the consuming protocol's accounting does not correctly handle that
behavior, creating an accounting-drift or share-price-manipulation
surface. This is a well-documented historical bug class (multiple real
incidents predate this session).

## Why this class needs Gate 1 (fork), not just Gate 0

Fee-on-transfer/rebasing behavior cannot be determined from static
`eth_call` reads alone — it requires observing an actual transfer's
before/after balance delta. Per the project's original Gate 0 → Gate 1
methodology (established at project start, first substantively used in
this phase): Gate 0 is identifying candidate tokens (via `eth_call`
inspection of decimals/symbol/name and, where source is available, source
inspection for `_fee`/`_taxRate`/rebase-related state); Gate 1 is
forking the relevant chain via `anvil --fork-url <rpc> --fork-block-number
<N>` and simulating a transfer (via `anvil_impersonateAccount` on a known
large holder + `cast send` against the LOCAL FORK only) to directly
observe whether sent amount == received amount, and whether balance
changes independent of any transfer occur across two block reads.

This fork-based simulation is READ-ONLY IN EFFECT on real state — the
fork is a local, disposable copy; nothing broadcasts to mainnet. This
satisfies the zero-mainnet-interaction rule exactly as intended by the
project's original design (Gate 1 was defined as fork verification from
the start; this class is simply the first to require exercising it, since
every Phase 1/2 finding resolved via static reads at Gate 0).

## Anomaly definition (any of)

1. **Fee-on-transfer token listed without an adapter**: a token whose
   simulated fork transfer shows received amount < sent amount, listed
   directly as collateral/loan asset in a protocol whose accounting
   (verified via source) tracks amounts via the transfer's specified
   parameter rather than a balance-before/balance-after delta check.
2. **Rebasing token listed directly (not via a non-rebasing wrapper)**:
   a token whose balance changes between two fork block reads with zero
   intervening transfer to/from the observed holder, listed directly
   (not as a `w`-prefixed/wrapped variant) as collateral in a protocol.
3. **Accounting drift observable on fork**: after a simulated
   supply/deposit of a non-standard token, the protocol's internal
   recorded balance for that position differs from the token's own
   `balanceOf()` for the protocol's holding address, beyond normal
   interest-accrual explanations.

## Explicitly NOT an anomaly (exclusions)

- Wrapped/non-rebasing variants (wstETH, wrsETH, sDAI, sUSDe, etc.) —
  these exist specifically to solve this problem and are the *correct*
  pattern, already confirmed extensively in Phase 1's cross-validation
  network. Their existence is evidence the ecosystem already understands
  this risk class, not a gap.
- A token with fee-on-transfer or rebase CAPABILITY in its contract that
  is currently disabled/zero (e.g. some tokens ship an unused fee
  mechanism at 0%) — the anomaly requires the behavior to be live and
  observable on fork, not merely theoretically possible in the bytecode.
- A protocol that correctly uses balance-before/balance-after accounting
  (verified via source) regardless of what token is listed — this is the
  correct defensive pattern and neutralizes the risk even for a
  genuinely non-standard token.

## Severity bands (post-fork-verification only, same discipline as Phase 1)

- **Critical**: live fee-on-transfer/rebasing token, protocol accounting
  provably wrong (verified via fork simulation + source), exploitable to
  extract value disproportionate to what was actually deposited, on a
  protocol with meaningful TVL.
- **High**: accounting drift confirmed on fork but requires specific
  preconditions or is bounded to a small/isolated market.
- **Medium/Low**: theoretical mismatch not yet confirmed exploitable on
  fork, or confirmed but immaterial (dust-level drift).

## Status taxonomy (unchanged): draft-report / no-program-logged /
## false-positive / suspicion

## Zero-mainnet-interaction rule (reaffirmed, this phase makes it concrete)

All simulation happens via `anvil --fork-url <rpc> --fork-block-number <N>`
against a LOCAL fork. `anvil_impersonateAccount` + `cast send` are used
ONLY against the local fork's RPC endpoint (typically `127.0.0.1:8545`),
NEVER against a real chain's public RPC. No real private key is ever used
to sign a real transaction. The fork is disposable and discarded after
each check; nothing persists to any real chain.

## Stop conditions (unchanged in spirit)

Critical/High gap on a protocol with a program → stop, ask human. Candidate
list exhausted → report, ask human. 20 consecutive dry batches → report
field dry, ask human.
