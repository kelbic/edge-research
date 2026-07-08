# Tooling note: stale `AAVE_ORACLE` constant in aave-address-book (Polygon)

**Status: false-positive / not a protocol vulnerability -- documented as a tooling/data-quality note, not a security finding.**

## What happened

`bgd-labs/aave-address-book`'s `AaveV3Polygon.sol` (fetched 2026-07-07) lists
`AAVE_ORACLE = 0x72484B12719E23115761D5DA1646945632979bB6`. That address has
real, substantial bytecode (9,571 bytes) but every function call to it --
including the argument-free `BASE_CURRENCY_UNIT()` -- reverts. It is not
Polygon's live AaveOracle.

The real, working oracle (confirmed via `Pool.ADDRESSES_PROVIDER()` ->
`Provider.getPriceOracle()`, live on-chain, 2026-07-07) is
`0xb023e699F5a33916Ea823A16485e259257cA8Bd1` (3,405 bytes, all standard
AaveOracle functions respond correctly).

## Why this is NOT a protocol misconfiguration

Aave's own deployed contracts (Pool, PoolAddressesProvider, the real
AaveOracle) are wired correctly and were unaffected throughout -- confirmed
by a full 21-asset reserve sweep against the real oracle address, all
sane. The stale value lives entirely in a third-party, community-maintained
GitHub convenience library that off-chain tooling (including this scanner,
initially) reads for convenience. This is a documentation/tooling
data-quality issue, not an on-chain vulnerability, and carries no exploit
path -- no user, contract, or protocol interaction is affected by a wrong
value in someone else's reference file.

## Why worth recording anyway

Any tooling, monitoring dashboard, or *other* scanner that blindly trusts
this specific address-book entry for Polygon without independently
resolving it via `ADDRESSES_PROVIDER` would read from a non-functional
contract and either see reverts or (worse, if it silently swallowed errors)
report incorrect/missing price data for Polygon's Aave market -- a
believability risk for anyone consuming that specific file, not a risk to
Aave depositors/borrowers. Reported here for the record and as a reminder
of why this project independently re-verifies every address regardless of
source (this is the second such catch this session, after Flux Finance's
oracle address in batch-009).

## Disclosure

Not applicable -- no protocol bug exists to disclose. If useful, a PR
correcting the constant in `bgd-labs/aave-address-book` would be a
reasonable community contribution, but that repo has no bug bounty and
this is out of scope for this project's mandate (DeFi protocol oracle
misconfigurations, not third-party tooling repos).
