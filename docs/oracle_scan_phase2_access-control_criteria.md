# Pre-registered verdict criteria — Phase 2: privileged-function access control

Committed 2026-07-08, before batch-021, per the same pre-registration
discipline as CRITERIA.md (Phase 1: oracle/decimals/hardcoded-feed/
init-holes, closed dry after 20 batches — see
`findings/SESSION_REPORT_batches_001-020.md`). These thresholds are fixed
in advance and MUST NOT be loosened post-hoc to make a finding qualify.

## Scope of this class

Privileged administrative functions in DeFi lending/money-market protocols
that can change economically-critical parameters — price oracle addresses,
interest rate strategies, reserve caps, pause/freeze state, upgrade
targets — and whether the address empowered to call them is adequately
protected (multisig + timelock, or equivalent) versus dangerously exposed
(a single EOA, a contract with no delay, or literally unprotected).

This is explicitly NOT a re-run of Phase 1. A legitimately-configured
multisig/timelock-controlled owner calling a privileged function as
designed is NOT an anomaly, even though the function itself is
"privileged" — centralization that is disclosed, expected, and consistent
with the protocol's own governance documentation is by-design, not a
misconfiguration. The target is unprotected or under-protected privileged
access: single-EOA admin roles on live, non-trivial-TVL contracts;
zero-delay timelocks; missing access-control modifiers entirely; or a
privileged role quietly held by an address inconsistent with the
protocol's documented governance model.

## Anomaly definition (any of)

1. **EOA-controlled admin/owner on a live, non-trivial-TVL contract**: the
   address returned by `owner()`, `admin()`, the EIP-1967 admin slot, or a
   role-based `hasRole(DEFAULT_ADMIN_ROLE, ...)` check resolves to an
   address with zero bytecode (an EOA), not a Safe/multisig or Timelock
   contract, on a protocol with meaningful TVL (>$100k, matching this
   project's existing candidate set).
2. **Missing/trivial timelock on upgrade or critical-parameter functions**:
   the address controlling `upgradeTo`/`setPriceOracle`/
   `setReserveInterestRateStrategyAddress`/`setFallbackOracle`-class
   functions is a contract, but inspection shows no timelock (a
   Safe alone, with no `TimelockController` or equivalent delay
   mechanism, calling a critical function directly) where the protocol's
   own documentation claims one exists.
3. **Unprotected privileged function**: a function that should require
   `onlyOwner`/`onlyPoolAdmin`/`onlyRiskOrPoolAdmins`-style access control
   (per the contract's own naming/intent, inferred from ABI/source) is
   callable by any address (missing modifier, verified via source or a
   safe simulated call showing no revert for a non-privileged caller).
4. **Governance/admin address inconsistent with documented model**: the
   actual on-chain admin differs from what the protocol's own
   docs/governance forum/Etherscan labels claim it should be (e.g. docs
   say "controlled by X DAO Safe" but the real admin resolves to a
   different, unlabeled address).

## Explicitly NOT an anomaly (exclusions)

- A real Gnosis Safe (even a small n-of-m) or Aave-style governance
  executor holding admin rights, functioning as documented — centralization
  is a disclosed risk, not a misconfiguration, unless a specific documented
  claim (e.g. "requires timelock") is contradicted by on-chain fact.
- Immutable contracts with no admin at all (Morpho Blue's core, Silo's
  per-market deployments) — no admin key exists to compromise, which is
  the strongest possible answer to this class, not something to flag.
- A protocol's own foundation/team multisig acting exactly as its own
  published documentation says it will, even if some might consider that
  level of centralization high — record the fact plainly if notable, but
  do not call it a "finding" absent a concrete discrepancy or unprotected
  access path.

## Severity bands (post fork verification only, same fork-before-finding
## discipline as Phase 1 — if practical to demonstrate, show what an
## unprotected admin/EOA COULD do on a fork; if not practically
## demonstrable without actually controlling the key, severity is capped
## at "high" based on documented capability, not proven exploited)

- **Critical**: an admin function that can drain/redirect user funds
  directly (e.g. change price oracle to a self-controlled contract, then
  the same or a colluding address borrows against manipulated collateral)
  is callable by a bare EOA with no delay, on a protocol with significant
  TVL.
- **High**: admin function can materially harm users (freeze funds,
  change risk parameters to enable bad debt) callable by an EOA or an
  under-timelocked contract.
- **Medium**: admin function has real but bounded blast radius (e.g.
  affects one isolated market/reserve, not protocol-wide) under
  insufficient protection.
- **Low/informational**: technically-unprotected function with no
  meaningful exploitable capability (e.g. a cosmetic setter), or a
  disclosed-but-notable centralization fact worth recording without
  calling it a vulnerability.

## Status taxonomy (same as Phase 1, findings/*.md must declare one)

`draft-report` (real gap, in-scope program, ready for human disclosure
decision) / `no-program-logged` (real gap, no bounty coverage) /
`false-positive` (looks concerning, explained by documented/legitimate
governance) / `suspicion` (evidence incomplete, not yet a finding).

## Zero-mainnet-interaction rule (unchanged from Phase 1)

All checks via `eth_call`/`eth_getStorageAt`/`eth_getLogs` only. No signed
transaction is ever broadcast, including a "harmless" call to test whether
an unprotected function actually executes — a safe `eth_call` simulation
(which reverts state after execution) is the correct read-only technique
to test "would this function accept my call", never a real broadcast tx.

## Stop conditions (unchanged in spirit from Phase 1)

- Critical/High gap confirmed on a protocol with an active bounty program
  → stop, do not disclose, ask human.
- Candidate queue (the existing set of ~20 protocols/15 chains already
  mapped in Phase 1) exhausted for this class.
- 20 consecutive dry batches → report field dry, ask human.
