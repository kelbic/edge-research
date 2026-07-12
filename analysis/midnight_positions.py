"""Position-сканер Midnight: энумерация заёмщиков и поиск СЕЙЗАБЕЛЬНЫХ позиций
(фундамент боевого бота + заострение мониторинга). Read-only.

Заёмщики восстанавливаются из событий SupplyCollateral.onBehalf и UpdatePosition.user
(union), затем per (market, borrower) читается debt()/collateralBitmap()/collateral().
Классификация:
  - post-maturity сейзабельная: now > maturity ∧ debt > 0 ⇒ ВСЯ позиция сейзабельна
    (RCF off), стоимость ≈ debt-units (для стейбл-loan ≈ $ к maturity);
  - approaching: maturity в ближайшие `soon_days` дней с debt > 0 (кандидат окна).

Usage:
    python3 -m analysis.midnight_positions scan       # энумерация -> data/midnight_positions.json
    python3 -m analysis.midnight_positions report [thr_usd]   # сейзабельные ≥ порога
"""
from __future__ import annotations

import json
import os
import sys

from analysis.keccak import keccak256
from analysis.midnight_day0 import (
    BASE_RPCS, CACHE, CHUNK, DATA_DIR, MIDNIGHT, STABLES, WAD,
)
from analysis.rpc import Rpc, get_logs_chunked

POS_CACHE = os.path.join(DATA_DIR, "midnight_positions.json")
SEIZE_THRESHOLD_USD = 5_000.0   # порог живости §5 (сейзабельный долг)

SIG_SUPPLY_COLLATERAL = "SupplyCollateral(address,bytes32,address,uint256,address)"
SIG_UPDATE_POSITION = "UpdatePosition(bytes32,address,uint256,uint256,uint256)"
TOPIC_SUPPLY = "0x" + keccak256(SIG_SUPPLY_COLLATERAL.encode()).hex()
TOPIC_UPDATE = "0x" + keccak256(SIG_UPDATE_POSITION.encode()).hex()

SEL_DEBT = "0x" + keccak256(b"debt(bytes32,address)").hex()[:8]
SEL_COLL_BITMAP = "0x" + keccak256(b"collateralBitmap(bytes32,address)").hex()[:8]
SEL_COLLATERAL = "0x" + keccak256(b"collateral(bytes32,address,uint256)").hex()[:8]


def _w(x, width=64) -> str:
    return (hex(x)[2:] if isinstance(x, int) else x).rjust(width, "0")


def enumerate_borrowers(rpc: Rpc, from_block: int, to_block: int) -> dict[str, set]:
    """{market_id: {borrower,...}} из SupplyCollateral.onBehalf (topics[3]) и
    UpdatePosition.user (topics[2])."""
    out: dict[str, set] = {}
    sc = get_logs_chunked(rpc, MIDNIGHT, [TOPIC_SUPPLY], from_block, to_block, chunk=CHUNK)
    for lg in sc:
        mid = lg["topics"][1]
        borrower = "0x" + lg["topics"][3][26:]
        out.setdefault(mid, set()).add(borrower.lower())
    up = get_logs_chunked(rpc, MIDNIGHT, [TOPIC_UPDATE], from_block, to_block, chunk=CHUNK)
    for lg in up:
        mid = lg["topics"][1]
        user = "0x" + lg["topics"][2][26:]
        out.setdefault(mid, set()).add(user.lower())
    return out


def read_debt(rpc: Rpc, market_id: str, borrower: str) -> int:
    r = rpc.eth_call(MIDNIGHT, SEL_DEBT + market_id[2:] + _w(borrower[2:]))
    return int(r, 16) if r and r != "0x" else 0


def scan() -> None:
    blob = json.load(open(CACHE))
    rpc = Rpc(BASE_RPCS)
    head = rpc.block_number()
    now = int(rpc.get_block("latest")["timestamp"], 16)
    mkt = {m["id"]: m for m in blob["markets"]}
    borrowers = enumerate_borrowers(rpc, blob["deploy_block"], head)
    positions = []
    for mid, bset in borrowers.items():
        m = mkt.get(mid)
        if not m:
            continue
        loan_dec = blob["tokens"].get(m["loanToken"], {}).get("decimals")
        for b in bset:
            debt = read_debt(rpc, mid, b)
            if debt <= 0:
                continue
            usd = debt / 10 ** loan_dec if (loan_dec is not None
                                            and m["loanToken"] in STABLES) else None
            positions.append({
                "market": mid, "borrower": b, "debt": debt,
                "loan": m["loanToken"], "loan_dec": loan_dec,
                "maturity": m["maturity"], "usd": usd,
                "post_maturity": now > m["maturity"],
                "gate0": int(m["liquidatorGate"], 16) == 0,
            })
    os.makedirs(DATA_DIR, exist_ok=True)
    json.dump({"head": head, "now": now, "positions": positions,
               "n_borrowers": sum(len(s) for s in borrowers.values())},
              open(POS_CACHE, "w"), indent=1)
    seiz = [p for p in positions if p["post_maturity"]]
    print(f"заёмщиков просканировано: {sum(len(s) for s in borrowers.values())}; "
          f"позиций с debt>0: {len(positions)}; из них post-maturity: {len(seiz)}")
    print(f"кэш -> {POS_CACHE}")


def report(threshold_usd: float = SEIZE_THRESHOLD_USD) -> None:
    if not os.path.exists(POS_CACHE):
        print("нет кэша — сначала `scan`")
        return
    blob = json.load(open(POS_CACHE))
    now = blob["now"]
    positions = blob["positions"]
    print(f"=== position-сканер: {len(positions)} позиций с debt>0, "
          f"порог сейза ${threshold_usd:,.0f} ===")
    # сейзабельные post-maturity
    seiz = sorted([p for p in positions if p["post_maturity"]],
                  key=lambda p: -(p["usd"] or 0))
    print(f"\npost-maturity сейзабельные ({len(seiz)}):")
    over = []
    for p in seiz[:20]:
        usd = f"${p['usd']:,.2f}" if p["usd"] is not None else f"{p['debt']} units"
        flag = "  ⚠️ ≥ПОРОГА" if (p["usd"] or 0) >= threshold_usd else ""
        if (p["usd"] or 0) >= threshold_usd:
            over.append(p)
        print(f"  {p['market'][:12]}… borrower {p['borrower'][:10]}… debt {usd}"
              f" gate0={p['gate0']}{flag}")
    if not seiz:
        print("  нет (все прошедшие окна очищены/пусты)")
    # приближающиеся окна с долгом
    soon = sorted([p for p in positions if not p["post_maturity"]
                   and 0 < (p["maturity"] - now) < 14 * 86400],
                  key=lambda p: p["maturity"])
    print(f"\nдолг на окнах в ближайшие 14д ({len(soon)}):")
    for p in soon[:20]:
        usd = f"${p['usd']:,.2f}" if p["usd"] is not None else f"{p['debt']} units"
        dd = (p["maturity"] - now) / 86400
        print(f"  {p['market'][:12]}… debt {usd} maturity +{dd:.1f}д gate0={p['gate0']}")

    print(f"\n=== ИТОГ: сейзабельных ≥${threshold_usd:,.0f} прямо сейчас: {len(over)} ===")
    if over:
        print("  ⚠️ ЭСКАЛАЦИЯ — есть боевая цель; прогнать M-T2 t* + realized-net гард.")
    else:
        tot = sum(p["usd"] or 0 for p in positions)
        print(f"  нет боевых целей (суммарный debt ≈ ${tot:,.0f}, всё < порога) — поле пусто")


def main() -> None:
    args = sys.argv[1:]
    cmd = args[0] if args else "report"
    if cmd == "scan":
        scan()
    elif cmd == "report":
        report(float(args[1]) if len(args) > 1 else SEIZE_THRESHOLD_USD)
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
