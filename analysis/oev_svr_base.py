"""Gate 0 (Направление 7, OEV-сёрчер): реконструкция SVR/Atlas-ликвидаций Aave v3
на Base (наша домашняя цепь). Read-only. Критерии — docs/oev_gate0_criteria.md
(коммит b277aa2); карта механики — docs/oev_mechanics_map.md.

Механика Base отличается от Ethereum: аукцион Atlas, Chainlink-нода — бандлер,
апдейт фида и ликвидация исполняются ОДНОЙ tx (metacall на Atlas v1.6.4
0x583d…9B77). Фингерпринт (верифицирован на живой tx 0xd7f75480…a85dd0 ДО скана):
receipt ликвидационной tx содержит и LiquidationCall (Aave Pool), и
SecondaryRoundIdUpdated (DualAggregator), и SolverTxResult Atlas'а —
`SolverTxResult(address solverTo, address solverFrom, address dAppControl,
address bidToken, uint256 bidAmount, bool executed, bool success, uint256 result)`
(topic0 offline-keccak) — откуда читается ПОБЕДИТЕЛЬ (solverTo, он же liquidator
в LiquidationCall) и БИД (bidAmount; bidToken=0 → нативный ETH).

Скан: все LiquidationCall на Aave-Base Pool за окно → receipt каждой tx →
классификация SVR/не-SVR → бид/победитель из SolverTxResult → gross через
AaveOracle.getAssetPrice (архивный eth_call, проверен на mainnet.base.org).

Aave v3 Base: Pool 0xA238Dd80C259a72e81d7e4664a9801593F98d1c5, Oracle
0x2Cc0Fc26eD4563A5ce5e8bdcfe1A2878676Ae156 (bgd-labs/aave-address-book).
SVR на Aave-Base live с ~конца марта 2026 (AIP #461) → окно по умолчанию 105 дн.

Usage:
    python3 -m analysis.oev_svr_base scan [blocks]   # дефолт ~105 дней
    python3 -m analysis.oev_svr_base report
"""
from __future__ import annotations

import gzip
import json
import os
import sys
from collections import defaultdict

from analysis.keccak import keccak256
from analysis.oev_api3 import concentration, margin_stats, monthly_rows, percentile
from analysis.oev_svr import SEL_GET_ASSET_PRICE, SEL_DECIMALS, decode_liq_call
from analysis.rpc import Rpc, get_logs_chunked

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
CACHE = os.path.join(DATA_DIR, "svr_base_liquidations.json.gz")

RPCS = ["https://mainnet.base.org", "https://base.drpc.org"]  # оба archive-OK (проверено)
POOL = "0xA238Dd80C259a72e81d7e4664a9801593F98d1c5"
ORACLE = "0x2Cc0Fc26eD4563A5ce5e8bdcfe1A2878676Ae156"
WETH = "0x4200000000000000000000000000000000000006"

TOPIC_UPD = "0x" + keccak256(b"SecondaryRoundIdUpdated(uint32)").hex()
TOPIC_LIQ = "0x" + keccak256(
    b"LiquidationCall(address,address,address,uint256,uint256,address,bool)").hex()
TOPIC_SOLVER = "0x" + keccak256(
    b"SolverTxResult(address,address,address,address,uint256,bool,bool,uint256)").hex()

BLOCKS_DEFAULT = 4_540_000  # ~105 дней по 2 с/блок — покрывает go-live SVR (~23-30.03.2026)


# -- чистые функции ---------------------------------------------------------

def classify_receipt(logs: list[dict]) -> dict:
    """SVR-ликвидация = в receipt есть SecondaryRoundIdUpdated; бид/победитель —
    из успешного SolverTxResult (executed && success)."""
    is_svr = any(lg["topics"] and lg["topics"][0] == TOPIC_UPD for lg in logs)
    out = {"is_svr": is_svr, "solver": None, "bid_wei": None, "bid_token": None}
    for lg in logs:
        if lg["topics"] and lg["topics"][0] == TOPIC_SOLVER:
            f = solver_fields(lg["data"])
            if f["executed"] and f["success"]:
                out["solver"] = "0x" + lg["topics"][1][-40:]
                out["bid_token"] = f["bidToken"]
                out["bid_wei"] = f["bidAmount"]
    return out


def solver_fields(data_hex: str) -> dict:
    """data SolverTxResult (не-indexed): bidToken(w0), bidAmount(w1),
    executed(w2), success(w3), result(w4)."""
    d = data_hex[2:]
    return {"bidToken": "0x" + d[24:64], "bidAmount": int(d[64:128], 16),
            "executed": int(d[128:192], 16) == 1, "success": int(d[192:256], 16) == 1}


# -- сбор -------------------------------------------------------------------

def scan() -> None:
    blocks = int(sys.argv[2]) if len(sys.argv) > 2 else BLOCKS_DEFAULT
    rpc = Rpc(urls=RPCS)
    head = rpc.block_number()
    lo = head - blocks
    print(f"window: {lo}..{head}", file=sys.stderr)
    liq = get_logs_chunked(rpc, [POOL], [TOPIC_LIQ], lo, head, chunk=10_000,
                           on_progress=lambda d, t, n: (d % 500_000 < 10_000) and print(
                               f"  liq: {d}/{t} logs={n}", file=sys.stderr))
    print(f"liquidation logs: {len(liq)}", file=sys.stderr)

    by_tx: dict[str, dict] = {}
    for lg in liq:
        rec = by_tx.setdefault(lg["transactionHash"],
                               {"txHash": lg["transactionHash"],
                                "block": int(lg["blockNumber"], 16), "calls": []})
        rec["calls"].append(decode_liq_call(lg))
    events = sorted(by_tx.values(), key=lambda r: r["block"])
    print(f"liquidation txs: {len(events)}", file=sys.stderr)

    dec_cache: dict[str, int] = {}
    for i, ev in enumerate(events):
        rec = rpc.receipt(ev["txHash"])
        cls = classify_receipt(rec["logs"])
        # SolverTxResult в data: bidToken(word0), bidAmount(word1) — поправка
        ev.update(cls)
        ev["tx_from"] = rec["from"].lower()
        ev["gas_wei"] = int(rec["gasUsed"], 16) * int(rec["effectiveGasPrice"], 16)
        blk = rpc.get_block(ev["block"])
        ev["timestamp"] = int(blk["timestamp"], 16)
        tag = hex(ev["block"])
        try:
            prices, decs = {}, {}
            for c in ev["calls"]:
                for a in (c["collateralAsset"].lower(), c["debtAsset"].lower()):
                    if a not in prices:
                        r = rpc.eth_call(ORACLE, SEL_GET_ASSET_PRICE + a[2:].rjust(64, "0"), tag)
                        prices[a] = int(r, 16)
                    if a not in dec_cache:
                        dec_cache[a] = int(rpc.eth_call(a, SEL_DECIMALS), 16)
                    decs[a] = dec_cache[a]
            from analysis.oev_svr import event_gross_usd
            ev["gross_usd"] = event_gross_usd(ev["calls"], prices, decs)
            r = rpc.eth_call(ORACLE, SEL_GET_ASSET_PRICE + WETH[2:].rjust(64, "0"), tag)
            ev["eth_usd"] = int(r, 16) / 1e8
        except Exception as e:
            ev["gross_usd"] = None
            ev["eth_usd"] = None
            ev["price_error"] = str(e)[:80]
        if i % 50 == 0:
            print(f"  enrich {i}/{len(events)}", file=sys.stderr)

    os.makedirs(DATA_DIR, exist_ok=True)
    with gzip.open(CACHE, "wt") as f:
        json.dump({"window": [lo, head], "events": events}, f)
    n_svr = sum(1 for e in events if e["is_svr"])
    print(f"cached {len(events)} liq txs ({n_svr} SVR) -> {CACHE}")


def _load() -> dict:
    with gzip.open(CACHE, "rt") as f:
        return json.load(f)


def report() -> None:
    data = _load()
    events = data["events"]
    svr = [e for e in events if e["is_svr"]]
    non = [e for e in events if not e["is_svr"]]
    print(f"Aave-Base liq txs за окно {data['window']}: {len(events)};"
          f" SVR={len(svr)} ({len(svr) / len(events):.0%}), открытая гонка={len(non)}")

    rows = []
    for e in svr:
        if e.get("gross_usd") is None or not e.get("eth_usd"):
            continue
        bid_usd = (e.get("bid_wei") or 0) / 1e18 * e["eth_usd"]
        gas_usd = e["gas_wei"] / 1e18 * e["eth_usd"]
        rows.append({"txHash": e["txHash"], "blockTimestamp": e["timestamp"],
                     "sender": e.get("solver") or e["calls"][0]["liquidator"],
                     "type": "OEV",
                     "incentiveUsd": str(int(max(e["gross_usd"], 0) * 1e18)),
                     "bidAmountUsd": str(int(bid_usd * 1e18)),
                     "gasCostUsd": str(int(gas_usd * 1e18))})
    print(f"  priced SVR rows: {len(rows)}")
    if not rows:
        return
    end_ts = max(r["blockTimestamp"] for r in rows)

    print("\n== M1 концентрация (solver-контракт, SVR-Base) ==")
    c = concentration(rows)
    print(f"  сущностей={c['senders']} n={c['n_total']} gross=${c['usd_total']:,.0f}")
    print(f"  top-1 {c['top1_n']:.1%}/{c['top1_usd']:.1%}  top-3 {c['top3_n']:.1%}/{c['top3_usd']:.1%}"
          f"  HHI($)={c['hhi_usd']:,.0f}")
    for a, u, n in c["top_usd"]:
        print(f"    {a} ${u:>10,.2f} n={n}")

    print("\n== M3 остаточная маржа (gross − бид − газ) ==")
    m = margin_stats(rows)
    print(f"  n={m['n']} total=${m['total']:,.0f} median=${m['median']:,.2f}"
          f" p25=${m['p25']:,.2f} p75=${m['p75']:,.2f} отриц.={m['negative_share']:.1%}"
          f" топ-5={m['top5_share']:.1%}")
    gross = sum(int(r["incentiveUsd"]) / 1e18 for r in rows)
    bids = sum(int(r["bidAmountUsd"]) / 1e18 for r in rows)
    print(f"  pooled: gross=${gross:,.0f} bids=${bids:,.0f} recapture={bids / gross:.1%}")

    print("\n== помесячно (30-дн окна) ==")
    for r in monthly_rows(rows, end_ts, months=4):
        print(f"  {r['months_ago']} мес назад: n={r['n']} gross=${r['gross']:,.0f}"
              f" bids=${r['bids']:,.0f} маржа=${r['margin_total']:,.0f} сущностей={r['senders']}")

    print("\n== не-SVR остаток (открытая гонка, наш legacy-канал) ==")
    ngross = sum(e["gross_usd"] for e in non if e.get("gross_usd"))
    print(f"  txs={len(non)} gross=${ngross:,.0f}"
          f" (сравнить с SVR-каналом gross=${gross:,.0f})")


def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "report"
    {"scan": scan, "report": report}[cmd]()


if __name__ == "__main__":
    main()
