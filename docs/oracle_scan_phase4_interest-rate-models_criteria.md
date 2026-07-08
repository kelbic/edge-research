# Pre-registered verdict criteria — Phase 4: interest-rate-model parameter sanity

Committed 2026-07-08, before batch-031. Phases 1-3 closed/paused by human
direction (see `findings/SESSION_REPORT_batches_001-020.md`,
`findings/PHASE2_INTERIM_REPORT_batches_021-024.md`, and batch-027/030
Phase 3 data). Same discipline: thresholds fixed in advance.

## Scope

Interest-rate-model (IRM) configuration across the lending markets already
mapped this session (Aave-family per-reserve strategies, Compound III
per-asset curves, Morpho Blue's pluggable IRMs — predominantly
AdaptiveCurveIRM). The question: do the configured parameters (base rate,
slope(s), kink/target utilization, adjustment speed) produce sane borrow/
supply rates at the market's CURRENT observed utilization, or do they
produce a config that's degenerate (flat/meaningless curve), broken
(negative or absurd rate), or misaligned with the market's actual observed
behavior (e.g. a market pinned at ~100% utilization for a long time
without the IRM pushing rates high enough to matter)?

## Anomaly definition (any of)

1. **Degenerate curve parameters**: kink/target utilization set to 0% or
   100% (removing any meaningful two-slope behavior), or slope1 == slope2
   (flat curve, kink has no effect), verified via direct contract reads
   of the IRM/strategy contract's configured constants.
2. **Rate non-response at extreme utilization**: a market observed at
   >95% utilization (via already-collected `market()`/reserve data) for
   a meaningfully long period (verified via a stability/history check,
   not a single snapshot) where the CURRENT computed borrow rate is not
   materially elevated above its base/below-kink rate — i.e. the IRM is
   not doing its job of pricing scarcity.
3. **Overflow/negative-rate risk**: parameters that could mathematically
   produce a rate calculation overflow or negative value under a
   reachable utilization value (verified via direct computation using the
   contract's own formula, not assumed).
4. **Copy-paste mismatch**: identical IRM parameters applied to markets
   with clearly different risk/volatility profiles (e.g. a stablecoin
   pair and a volatile-asset pair sharing numerically identical curve
   constants) where that would be inappropriate — flagged as a
   sensitivity/suspicion, not asserted as a bug without confirming intent.

## Explicitly NOT an anomaly (exclusions)

- A market with a genuinely appropriate flat/simple rate model by design
  for a specific low-risk asset class (e.g. some RWA/NAV-feed-backed
  markets intentionally use simpler, more predictable rate curves) — not
  every market needs the same two-slope kinked model.
- A market observed near-100% utilization for only a SHORT window
  (verified via `lastUpdate` timestamp and cross-batch history) — brief
  spikes are normal and not evidence of misconfiguration.
- Reasonable, intentional differences in kink/slope across markets that
  reflect real differences in the underlying asset's volatility/liquidity
  profile — the burden is on demonstrating the parameters are
  *inappropriate*, not merely *different* from another market's.

## Severity bands (same discipline as prior phases — computed, not eyeballed)

- **Critical**: a live market where the IRM is verifiably producing a
  rate that actively harms the protocol RIGHT NOW (e.g. persistent
  under-pricing at max utilization causing an observable, ongoing
  liquidity crunch with no incentive to resolve it), on a protocol with
  meaningful TVL and a bounty program.
- **High**: a confirmed degenerate/broken parameter set on a live market,
  not yet observably causing harm but structurally capable of it.
- **Medium/Low**: a copy-paste sensitivity or suboptimal-but-not-broken
  parameter choice, recorded for completeness.

## Status taxonomy (unchanged): draft-report / no-program-logged /
## false-positive / suspicion

## Zero-mainnet-interaction rule (unchanged)

All reads via `eth_call`. Rate computation is done by replicating the
protocol's own documented/sourced formula against on-chain-read
parameters and utilization — not by calling any state-changing function.

## Stop conditions (unchanged in spirit)

Critical/High gap on a protocol with a program → stop, ask human.
Candidate list exhausted → report, ask human. 20 consecutive dry batches
→ report field dry, ask human.
