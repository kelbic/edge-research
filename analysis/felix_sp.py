"""Gate 1 (Направление 5, кандидат F2-B): реализованный liquidation-gain стабильности-пула
Felix (Liquity V2 форк на HyperEVM). Read-only. См. STATE.md.

Тезис: с капиталом не-гоночная роль = депонировать feUSD в StabilityPool, ликвидации
абсорбируются pro-rata, получаешь коллатерал с 5% дисконтом БЕЗ латентной гонки. Вопрос
Gate 1: реализованный liquidation-gain APR (отдельно от базового процента) устойчиво > базы,
или социализация + приток капитала съели edge?

Вывод замера 2026-07-05 (см. STATE.md): NO. Полная история 400 ликвидаций даёт blended
~7% APR, НО он фат-тейл (топ-5 = 46%) и весь в раннем периоде; форвардно (trailing-90d на
выросший пул $10.2M) = ~0.4%. Капитал притёк и схлопнул edge — killer #4 (социализация) + #7.

Usage:
    python3 -m analysis.felix_sp scan     # full-history Liquidation cache
    python3 -m analysis.felix_sp report    # per-branch realized gain APR, full vs trailing-90d
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.request
from collections import Counter, defaultdict

from analysis.keccak import keccak256

RPC = "https://rpc.hyperlend.finance"          # no getLogs range cap; aggressive 429s
TIP_RPC = "https://rpc.hyperliquid.xyz/evm"    # head only (1000-block getLogs cap)
BLOCKS_PER_DAY = 86_400                          # HyperEVM ~1s/block

# TroveManager per branch (verified via felixprotocol/felix-contracts addresses/999.json)
TROVE_MANAGERS = {
    "0x3100f4e7bda2ed2452d9a57eb30260ab071bbe62": "WHYPE",
    "0xbbe5f227275f24b64bd290a91f55723a00214885": "UBTC",
    "0x7c07bb77b1cf9a5b40d92f805c10d90c90957e4a": "KHYPE",
    "0x58446c58caa8a6f6cc8be343f812ebf0b997c001": "WSTHYPE",
}
# StabilityPool feUSD size (feUSD.balanceOf(SP)), snapshot 2026-07-05
SP_SIZE_USD = {"WHYPE": 6_937_878, "UBTC": 2_060_737, "KHYPE": 1_131_724, "WSTHYPE": 80_838}
LIQUIDATION_PENALTY_SP = 0.05  # SP receives collateral worth debt*1.05 (verified economically)

SIG_LIQ = ("Liquidation(uint256,uint256,uint256,uint256,uint256,"
           "uint256,uint256,uint256,uint256,uint256)")

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
CACHE = os.path.join(DATA_DIR, "felix_liquidations.json")


def topic0(sig: str) -> str:
    return "0x" + keccak256(sig.encode()).hex()


TOPIC_LIQ = topic0(SIG_LIQ)


def _rpc(url, method, params, timeout=120):
    for _ in range(25):
        try:
            body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
            req = urllib.request.Request(url, data=body,
                                         headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"})
            d = json.loads(urllib.request.urlopen(req, timeout=timeout).read())
            if isinstance(d.get("error"), dict) and d["error"].get("code") == -32005:
                time.sleep(3)
                continue
            return d
        except Exception:
            time.sleep(1.2)
    return {"error": "giveup"}


# ---------------------------------------------------------------- pure functions

def decode_liq(log: dict) -> dict:
    """Liquidation event: 10 non-indexed uint256 words. word0=_debtOffsetBySP,
    word4=_collSentToSP, word9=_price (all 1e18). SP $-gain = debt * penalty."""
    d = log["data"][2:] if log["data"].startswith("0x") else log["data"]
    w = [int(d[i * 64:(i + 1) * 64], 16) for i in range(10)]
    return {
        "bn": int(log["blockNumber"], 16),
        "branch": TROVE_MANAGERS[log["address"].lower()],
        "debtOffsetSP": w[0] / 1e18,   # feUSD ~ $1
        "collSentSP": w[4] / 1e18,
        "price": w[9] / 1e18,
    }


def gain_usd(ev: dict) -> float:
    """Realized $ gain to the stability pool = 5% penalty on the SP-offset debt.
    (collateral value = debt*1.05, so gain = debt*0.05; validated on real logs.)"""
    return ev["debtOffsetSP"] * LIQUIDATION_PENALTY_SP


def realized_apr(events: list[dict], sp_size: float, window_days: float) -> float:
    if not sp_size or window_days <= 0:
        return 0.0
    return sum(gain_usd(e) for e in events) / sp_size / (window_days / 365.0)


def concentration(events: list[dict]) -> dict:
    g = sorted((gain_usd(e) for e in events), reverse=True)
    tot = sum(g) or 1.0
    return {
        "n": len(g),
        "total": tot,
        "top1_share": g[0] / tot if g else 0,
        "top5_share": sum(g[:5]) / tot if g else 0,
        "median": sorted(gain_usd(e) for e in events)[len(events) // 2] if events else 0,
    }


# ---------------------------------------------------------------- scan / report

def scan() -> None:
    tip = int(_rpc(TIP_RPC, "eth_blockNumber", [])["result"], 16)
    logs = []
    b, step = tip, 8_000_000
    while b > 0:
        a = max(1, b - step + 1)
        r = _rpc(RPC, "eth_getLogs", [{"address": list(TROVE_MANAGERS), "topics": [TOPIC_LIQ],
                                       "fromBlock": hex(a), "toBlock": hex(b)}])
        lg = r.get("result")
        if lg is None:
            time.sleep(2)
            b = a - 1
            continue
        logs.extend(lg)
        print(f"  chunk {a}-{b}: {len(lg)} (cum {len(logs)})", flush=True)
        b = a - 1
        time.sleep(0.8)
    os.makedirs(DATA_DIR, exist_ok=True)
    json.dump({"tip": tip, "logs": logs}, open(CACHE, "w"))
    print(f"cached {len(logs)} Liquidation logs up to block {tip} -> {CACHE}")


def report() -> None:
    blob = json.load(open(CACHE))
    tip, logs = blob["tip"], blob["logs"]
    evs = [decode_liq(lg) for lg in logs]
    evs.sort(key=lambda e: e["bn"])
    print(f"=== Felix stability-pool liquidation-gain (head {tip}) ===")
    print(f"total liquidations: {len(evs)}; by branch: {dict(Counter(e['branch'] for e in evs))}")

    cut90 = tip - 90 * BLOCKS_PER_DAY
    # full-history window = blocks since first liquidation
    first_bn = evs[0]["bn"] if evs else tip
    hist_days = (tip - first_bn) / BLOCKS_PER_DAY

    by_branch = defaultdict(list)
    for e in evs:
        by_branch[e["branch"]].append(e)
    print(f"\n{'branch':<9}{'nLiq':>5}{'gain$':>11}{'SP$':>11}{'APR_full':>10}{'APR_90d':>9}")
    tot_gain = 0.0
    for br in ["WHYPE", "UBTC", "KHYPE", "WSTHYPE"]:
        es = by_branch[br]
        sp = SP_SIZE_USD[br]
        g = sum(gain_usd(e) for e in es)
        tot_gain += g
        apr_full = realized_apr(es, sp, hist_days)
        apr_90 = realized_apr([e for e in es if e["bn"] >= cut90], sp, 90)
        print(f"{br:<9}{len(es):>5}{g:>11,.0f}{sp:>11,.0f}{apr_full:>9.2%}{apr_90:>9.2%}")

    tot_sp = sum(SP_SIZE_USD.values())
    apr_full = realized_apr(evs, tot_sp, hist_days)
    apr_90 = realized_apr([e for e in evs if e["bn"] >= cut90], tot_sp, 90)
    c = concentration(evs)
    print(f"\nBLENDED realized liquidation-gain APR: full={apr_full:.2%} (over {hist_days:.0f}d), "
          f"trailing-90d={apr_90:.2%}")
    print(f"concentration: top1={c['top1_share']:.0%} top5={c['top5_share']:.0%} "
          f"median-gain=${c['median']:,.0f} (fat tail => APR is episodic, not steady)")
    print("VERDICT: non-racing mechanism is real, but forward edge (trailing-90d on the grown "
          "$10.2M pool) is ~dust; the ~7% full-history APR is early-period survivorship, "
          "competed away by capital inflow (killer #4 socialization + #7).")


def main() -> None:
    {"scan": scan, "report": report}.get(sys.argv[1] if len(sys.argv) > 1 else "report",
                                         lambda: print(__doc__))()


if __name__ == "__main__":
    main()
