"""Gate 1 (Направление 3, кандидат B1 HyperEVM): замер насыщенности ликвидаций на
HyperLend (Aave-v3 форк) — core Pool + isolated/paired markets. Read-only.

Вопрос Gate 1: существует ли на HyperEVM тейл-рынок с (а) реальным повторяющимся
потоком ликвидаций, (б) ≤3 конкурентами-ликвидаторами, (в) рабочей ликвидностью
выхода залога? Позитивный слепок — тейл изолированных Morpho-рынков Base.

Вывод замера 2026-07-05 (см. STATE.md): NO — где деньги, там рой (6–38 ликвидаторов);
где ≤3, там пыль. Единственный рынок с ≤3 и ≥$5k/90д — thBILL/USDH (4 события/90д).

Usage:
    python3 -m analysis.hyperlend_liq scan     # full-history LiquidationCall cache
    python3 -m analysis.hyperlend_liq report    # per-market $-volume × liquidator count
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.request
from collections import Counter, defaultdict

from analysis.keccak import keccak256

# Two contracts emit Aave-style LiquidationCall on HyperEVM (Felix/Keiko use Liquity
# stability-pool model — no LiquidationCall event, so they are out of scope here).
CORE_POOL = "0x00A89d7a5A02160f20150EbEA7a2b5E4879A1A8b"      # HyperLend core (Aave v3 fork)
ISOLATED = "0xcecce0eb9dd2ef7996e01e25dd70e461f918a14b"       # HyperLend paired/isolated markets
CONTRACTS = [CORE_POOL, ISOLATED]

RPC = "https://rpc.hyperlend.finance"  # only endpoint measured to serve wide getLogs windows
CHUNK = 5_000_000                       # 10M works, 40M times out; 5M is safe
BLOCKS_PER_DAY = 86_400                 # HyperEVM ~1s/block (measured 2026-07-05)

SIG_LIQ = "LiquidationCall(address,address,address,uint256,uint256,address,bool)"

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
CACHE = os.path.join(DATA_DIR, "hyperlend_liqcalls.json")

# token metadata verified on-chain 2026-07-05 (symbol()/decimals()); prices ~rough USD
# for order-of-magnitude sizing only — the verdict does not hinge on exact prices.
TOKENS = {
    "0x9fdbda0a5e284c32744d2f17ee5c74b284993463": ("UBTC", 8, 95000),
    "0xbe6727b535545c67d5caa73dea54865b92cf7907": ("UETH", 18, 3000),
    "0xb88339cb7199b77e23db6e890353e22632ba630f": ("USDC", 6, 1),
    "0x111111a1a0667d36bd57c0a9f569b98057111111": ("USDH", 6, 1),
    "0xb50a96253abdf803d85efcdce07ad8becbc52bd5": ("USDHL", 6, 1),
    "0xca79db4b49f608ef54a5cb813fbed3a6387bc645": ("USDXL", 18, 1),
    "0x5d3a1ff2b6bab83b63cd9ad0787074081a52ef34": ("USDe", 18, 1),
    "0xb8ce59fc3717ada4c02eadf9682a9e934f625ebb": ("USD₮0", 6, 1),
    "0x068f321fa8fb9f0d135f290ef6a3e2813e1c8a29": ("USOL", 9, 150),
    "0x5555555555555555555555555555555555555555": ("WHYPE", 18, 40),
    "0xf4d9235269a96aadafc9adae454a0618ebe37949": ("XAUt0", 6, 100),
    "0xd8fc8f0b03eba61f64d08b0bef69d80916e5dda9": ("beHYPE", 18, 40),
    "0x02c6a2fa58cc01a18b8d9e00ea48d65e4df26c70": ("feUSD", 18, 1),
    "0xfd739d4e423301ce9385c1fb8850539d657c296d": ("kHYPE", 18, 40),
    "0x211cc4dd073734da055fbf44a2b4667d5e5fe5d2": ("sUSDe", 18, 1.1),
    "0xfdd22ce6d1f66bc0ec89b20bf16ccb6670f55a5a": ("thBILL", 6, 1),
    "0x94e8396e0869c9f2200760af0621afd240e1cf38": ("wstHYPE", 18, 45),
}


def topic0(sig: str) -> str:
    return "0x" + keccak256(sig.encode()).hex()


TOPIC_LIQ = topic0(SIG_LIQ)


def _rpc(method, params, timeout=60):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    req = urllib.request.Request(RPC, data=body,
                                 headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"})
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read())


# ---------------------------------------------------------------- pure functions

def decode_liq(log: dict) -> dict:
    """Aave v3 LiquidationCall: topics=[t0, collateral, debt, user] (all indexed);
    data = debtToCover, liquidatedCollateralAmount, liquidator, receiveAToken."""
    d = log["data"][2:] if log["data"].startswith("0x") else log["data"]
    return {
        "bn": int(log["blockNumber"], 16),
        "contract": log["address"].lower(),
        "collateral": "0x" + log["topics"][1][26:].lower(),
        "debt": "0x" + log["topics"][2][26:].lower(),
        "user": "0x" + log["topics"][3][26:].lower(),
        "liqCollateral": int(d[64:128], 16),
        "liquidator": ("0x" + d[64 * 2 + 24:64 * 3]).lower(),
    }


def usd_value(asset: str, raw: int) -> float:
    meta = TOKENS.get(asset)
    if not meta:
        return 0.0
    _, dec, px = meta
    return raw / 10 ** dec * px


def sym(asset: str) -> str:
    meta = TOKENS.get(asset)
    return meta[0] if meta else asset[:8]


def market_stats(liqs: list[dict], head: int, recent_days: int = 90):
    """Aggregate per (contract, collateral, debt) market: count, unique liquidators, USD."""
    cut = head - recent_days * BLOCKS_PER_DAY
    mk = defaultdict(lambda: {"n": 0, "n90": 0, "usd": 0.0, "usd90": 0.0, "liqs": Counter()})
    for x in liqs:
        key = (x["contract"][:6], sym(x["collateral"]), sym(x["debt"]))
        g = mk[key]
        v = usd_value(x["collateral"], x["liqCollateral"])
        g["n"] += 1
        g["usd"] += v
        g["liqs"][x["liquidator"]] += 1
        if x["bn"] >= cut:
            g["n90"] += 1
            g["usd90"] += v
    return mk


# ---------------------------------------------------------------- scan / report

def scan() -> None:
    head = int(_rpc("eth_blockNumber", [])["result"], 16)
    all_logs = []
    for c in CONTRACTS:
        lo = 0
        while lo <= head:
            hi = min(lo + CHUNK - 1, head)
            for att in range(4):
                try:
                    r = _rpc("eth_getLogs", [{"address": c, "topics": [TOPIC_LIQ],
                                              "fromBlock": hex(lo), "toBlock": hex(hi)}])
                    if "error" in r:
                        raise RuntimeError(r["error"])
                    all_logs.extend(r["result"])
                    break
                except Exception as e:
                    time.sleep(2 * (att + 1))
                    if att == 3:
                        print(f"chunk fail {c} {lo}-{hi}: {str(e)[:50]}")
            time.sleep(0.3)
            lo = hi + 1
        print(f"{c}: cumulative logs {len(all_logs)}")
    os.makedirs(DATA_DIR, exist_ok=True)
    json.dump({"head": head, "logs": all_logs}, open(CACHE, "w"))
    print(f"cached {len(all_logs)} LiquidationCall logs up to block {head} -> {CACHE}")


def report() -> None:
    blob = json.load(open(CACHE))
    head, logs = blob["head"], blob["logs"]
    liqs = [decode_liq(lg) for lg in logs]
    print(f"=== HyperLend liquidations (both contracts), head {head} ===")
    print(f"total events: {len(liqs)}; unique liquidators overall: "
          f"{len(set(x['liquidator'] for x in liqs))}")
    mk = market_stats(liqs, head)
    rows = sorted(mk.items(), key=lambda kv: -kv[1]["usd90"])
    print(f"\n{'ctr':<7}{'market':<20}{'n':>5}{'n90':>5}{'uniqLiq':>8}{'USD_all':>13}{'USD_90d':>12}")
    for (c, col, debt), g in rows[:22]:
        print(f"{c:<7}{col[:9] + '/' + debt[:9]:<20}{g['n']:>5}{g['n90']:>5}"
              f"{len(g['liqs']):>8}{g['usd']:>13,.0f}{g['usd90']:>12,.0f}")
    print("\n=== archetype test: markets with <=3 uniq liquidators AND >=$5k liquidated in 90d ===")
    hits = [(k, g) for k, g in mk.items() if len(g["liqs"]) <= 3 and g["usd90"] >= 5000]
    if hits:
        for (c, col, debt), g in hits:
            print(f"  {col}/{debt}: n90={g['n90']} uniq={len(g['liqs'])} USD90=${g['usd90']:,.0f}")
    else:
        print("  NONE")
    tot90 = sum(g["usd90"] for g in mk.values())
    print(f"\ntotal liquidated collateral last 90d: ${tot90:,.0f} "
          f"(annualized ~${tot90 * 4:,.0f}); ~7% bonus pool ~${tot90 * 4 * 0.07:,.0f}/yr,"
          f" split across {len(set(x['liquidator'] for x in liqs))} liquidators")


def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "report"
    {"scan": scan, "report": report}.get(cmd, lambda: print(__doc__))()


if __name__ == "__main__":
    main()
