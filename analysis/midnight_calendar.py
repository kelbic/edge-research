"""M-T1: maturity-календарь Midnight (Фаза B, план §3). Read-only.

Инкрементальный сканер `MarketCreated` → реестр рынков (все параметры) + tick-лист
maturity-окон, отсортированный по времени. Кэш дозаписывается с последнего
просканированного блока (не пересканирует всё). Питает M-T4 (гейт/оракул-доли) и
M-T5 (shadow-лог прошедших окон).

Usage:
    python3 -m analysis.midnight_calendar update    # дозаписать кэш до головы
    python3 -m analysis.midnight_calendar ticks      # tick-лист maturity-окон
    python3 -m analysis.midnight_calendar registry    # реестр рынков (компактно)
"""
from __future__ import annotations

import datetime
import json
import os
import sys

from analysis.midnight_day0 import (
    BASE_RPCS, CACHE, CHUNK, DEPLOY_BLOCK, MIDNIGHT, TOPIC_MARKET_CREATED, WAD,
    decode_market_created, erc20_meta, probe_oracle, probe_u256, SEL_TOTAL_UNITS,
)
from analysis.rpc import Rpc, get_logs_chunked


def _load() -> dict:
    if os.path.exists(CACHE):
        return json.load(open(CACHE))
    return {"head": DEPLOY_BLOCK - 1, "deploy_block": DEPLOY_BLOCK, "markets": [],
            "liquidates": [], "tokens": {}, "oracles": {}, "activity": {},
            "code": {}, "mempool_logs": 0, "bundles_logs": 0, "raw_log_count": 0}


def update() -> None:
    """Дозаписать MarketCreated с (head+1) до текущей головы; обновить totalUnits
    существующих рынков (снапшот на новой голове)."""
    blob = _load()
    rpc = Rpc(BASE_RPCS)
    head = rpc.block_number()
    last = blob.get("head", DEPLOY_BLOCK - 1)
    if head <= last:
        print(f"нет новых блоков (кэш на {last}, голова {head})")
    else:
        new_logs = get_logs_chunked(rpc, MIDNIGHT, [TOPIC_MARKET_CREATED],
                                    last + 1, head, chunk=CHUNK)
        existing = {m["id"] for m in blob["markets"]}
        added = 0
        for lg in new_logs:
            m = decode_market_created(lg)
            if m["id"] in existing:
                continue
            for t in {m["loanToken"], *(cp["token"] for cp in m["collateralParams"])}:
                blob["tokens"].setdefault(t, erc20_meta(rpc, t))
            for cp in m["collateralParams"]:
                blob["oracles"].setdefault(cp["oracle"], probe_oracle(rpc, cp["oracle"]))
            blob["markets"].append(m)
            added += 1
        print(f"добавлено рынков: {added} (блоки {last + 1}..{head})")
    # обновить снапшот totalUnits всех рынков на текущей голове
    for m in blob["markets"]:
        m["totalUnits"] = probe_u256(rpc, MIDNIGHT, SEL_TOTAL_UNITS + m["id"][2:])
    blob["head"] = head
    json.dump(blob, open(CACHE, "w"), indent=1)
    print(f"кэш обновлён до блока {head}: {len(blob['markets'])} рынков -> {CACHE}")


def _iso(ts: int) -> str:
    return datetime.datetime.fromtimestamp(ts, datetime.UTC).strftime("%Y-%m-%d %H:%M UTC")


def build_ticks(blob: dict, now_ts: int | None = None) -> list[dict]:
    """tick-лист: по одному окну на уникальный maturity; агрегирует рынки окна,
    помечает прошедшие/будущие, суммирует borrow (units) по стейбл-loanToken."""
    from analysis.midnight_day0 import STABLES
    by_mat: dict[int, list[dict]] = {}
    for m in blob["markets"]:
        by_mat.setdefault(m["maturity"], []).append(m)
    ticks = []
    for mat in sorted(by_mat):
        ms = by_mat[mat]
        gate0 = sum(1 for m in ms if int(m["liquidatorGate"], 16) == 0)
        borrow_stable = 0.0
        for m in ms:
            tu = m.get("totalUnits")
            dec = blob["tokens"].get(m["loanToken"], {}).get("decimals")
            if tu and dec is not None and m["loanToken"] in STABLES:
                borrow_stable += tu / 10 ** dec
        ticks.append({
            "maturity": mat, "iso": _iso(mat), "n_markets": len(ms),
            "gate0": gate0, "borrow_stable_usd": borrow_stable,
            "passed": (now_ts is not None and mat < now_ts),
            "market_ids": [m["id"] for m in ms],
        })
    return ticks


def ticks() -> None:
    blob = _load()
    rpc = Rpc(BASE_RPCS)
    now = int(rpc.get_block("latest")["timestamp"], 16)
    tk = build_ticks(blob, now)
    print(f"=== maturity tick-лист ({len(tk)} окон), now {_iso(now)} ===")
    print(f"{'maturity (UTC)':<22}{'рынков':>7}{'gate0':>6}{'borrow$':>10}  статус")
    for t in tk:
        st = "ПРОШЛО" if t["passed"] else "будущее"
        d_days = (t["maturity"] - now) / 86400
        when = f"{d_days:+.1f}д"
        print(f"{t['iso']:<22}{t['n_markets']:>7}{t['gate0']:>6}"
              f"{t['borrow_stable_usd']:>10,.0f}  {st} ({when})")
    nxt = [t for t in tk if not t["passed"]]
    if nxt:
        print(f"\nближайшее окно: {nxt[0]['iso']} ({(nxt[0]['maturity'] - now) / 86400:+.1f}д), "
              f"{nxt[0]['n_markets']} рынков, borrow ${nxt[0]['borrow_stable_usd']:,.0f}")


def registry() -> None:
    blob = _load()
    print(f"=== реестр рынков ({len(blob['markets'])}), кэш на блоке {blob['head']} ===")
    for m in sorted(blob["markets"], key=lambda m: m["maturity"]):
        lt = blob["tokens"].get(m["loanToken"], {}).get("symbol") or m["loanToken"][:8]
        colls = "+".join(
            (blob["tokens"].get(cp["token"], {}).get("symbol") or cp["token"][:6])
            + f"({cp['lltv'] / WAD:.2f})" for cp in m["collateralParams"])
        gate = "open" if int(m["liquidatorGate"], 16) == 0 else "GATED"
        print(f" {m['id'][:14]}… {_iso(m['maturity'])} loan {lt:<6} [{gate}] {colls}")


def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "ticks"
    {"update": update, "ticks": ticks, "registry": registry}.get(
        cmd, lambda: print(__doc__))()


if __name__ == "__main__":
    main()
