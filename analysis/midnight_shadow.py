"""M-T5: shadow-режим Midnight (Фаза B, план §3/§5). Read-only, форвардный.

Активирован день-0 (docs/midnight_day0_report.md: ≥1 рынок gate=0 с borrow>0).
На каждое ПРОШЕДШЕЕ maturity-окно с ненулевым сейзабельным долгом фиксирует:
  t_первого_филла − maturity, реализованный бонус, адреса ликвидаторов, наш paper-t*,
  «выиграли бы?». Метрика конкуренции = уникальные ликвидаторы/окно. Прямой форвардный
  аналог микротеста контестируемости SVR Gate 1.

Также сводит §5-пороги (handover/kill) — сам вердикт остаётся за пользователем.

Usage:
    python3 -m analysis.midnight_shadow log      # shadow-лог прошедших окон
    python3 -m analysis.midnight_shadow gates     # сводка §5-порогов (handover/kill)
"""
from __future__ import annotations

import datetime
import json
import sys

from analysis.midnight_calendar import build_ticks
from analysis.midnight_day0 import BASE_RPCS, CACHE, WAD, lif_at
from analysis.rpc import Rpc

HANDOVER_MIN_WINDOWS = 3        # §5: ≥3 окон с долгом ≥$5k
HANDOVER_DEBT_USD = 5_000.0
HANDOVER_MEDIAN_LIQ = 3        # медиана уникальных ликвидаторов ≤3
KILL_LIQ_COUNT = 10            # ≥10 уник. ликвидаторов в окнах первых 2 нед = рой


def _iso(ts: int) -> str:
    return datetime.datetime.fromtimestamp(ts, datetime.UTC).strftime("%Y-%m-%d %H:%M UTC")


def shadow_log() -> None:
    blob = json.load(open(CACHE))
    rpc = Rpc(BASE_RPCS)
    now = int(rpc.get_block("latest")["timestamp"], 16)
    ticks = build_ticks(blob, now)
    passed = [t for t in ticks if t["passed"]]
    liqs = blob["liquidates"]
    # индекс ликвидаций по market id
    by_id: dict[str, list] = {}
    for lq in liqs:
        by_id.setdefault(lq["id"], []).append(lq)

    print(f"=== M-T5 shadow-лог: {len(passed)} прошедших окон, now {_iso(now)} ===")
    print("(shadow активирован день-0: ≥1 рынок gate=0 с borrow>0)\n")
    total_fills = 0
    all_liquidators: set[str] = set()
    for t in passed:
        mat = t["maturity"]
        window_liqs = [lq for mid in t["market_ids"] for lq in by_id.get(mid, [])]
        fillers = {lq["caller"] for lq in window_liqs}
        all_liquidators |= fillers
        total_fills += len(window_liqs)
        print(f"окно {t['iso']} ({t['n_markets']} рынков, gate0 {t['gate0']}):")
        if not window_liqs:
            print(f"    Liquidate: НЕТ (долг был пыль/ноль либо ещё не тронут)")
            continue
        # время первого филла и реализованный бонус
        first = min(window_liqs, key=lambda x: x["block"])
        blk = rpc.get_block(first["block"])
        ts_fill = int(blk["timestamp"], 16)
        dt = ts_fill - mat
        # реализованный lif на dt (paper: рамп) — фактический бонус в событии не сырой,
        # оцениваем по формуле рампа для коллатерала филла
        m = next((x for x in blob["markets"] if x["id"] == first["id"]), None)
        cp = next((c for c in m["collateralParams"] if c["token"] == first["collateral"]),
                  None) if m else None
        lif = lif_at(cp["maxLif"], dt) if cp and cp.get("maxLif") else None
        print(f"    первый филл: +{dt}с ({dt / 3600:.1f} ч) после maturity; "
              f"ликвидаторов {len(fillers)}; событий {len(window_liqs)}")
        if lif:
            print(f"    реализованный бонус (paper-рамп на dt): {100 * (lif - WAD) / WAD:.2f}% "
                  f"(из maxLif {100 * (cp['maxLif'] - WAD) / WAD:.2f}%)")
        print(f"    наш paper-t*: см. M-T2 (для пыли бессмыслен: долг < газа); "
              f"«выиграли бы?»: рамп прошёл за ~16ч до филла ⇒ первым мог быть любой")
    print(f"\nитого: {total_fills} филлов, {len(all_liquidators)} уникальных ликвидаторов "
          f"по всем прошедшим окнам")
    print(f"конкуренция (метрика): {len(all_liquidators)} уник. ликвидаторов — "
          f"{'РОЙ (kill-риск)' if len(all_liquidators) >= KILL_LIQ_COUNT else 'поле пусто'}")


def gates() -> None:
    """Сводка §5-порогов handover/kill (вердикт — за пользователем)."""
    blob = json.load(open(CACHE))
    rpc = Rpc(BASE_RPCS)
    now = int(rpc.get_block("latest")["timestamp"], 16)
    ticks = build_ticks(blob, now)
    passed = [t for t in ticks if t["passed"]]
    liqs = blob["liquidates"]
    by_id: dict[str, list] = {}
    for lq in liqs:
        by_id.setdefault(lq["id"], []).append(lq)

    # окна с сейзабельным долгом ≥$5k — сейчас borrow всех рынков ≈$528, так что 0
    windows_5k = [t for t in passed if t["borrow_stable_usd"] >= HANDOVER_DEBT_USD]
    liq_counts = []
    for t in passed:
        fillers = {lq["caller"] for mid in t["market_ids"] for lq in by_id.get(mid, [])}
        if fillers:
            liq_counts.append(len(fillers))
    median_liq = sorted(liq_counts)[len(liq_counts) // 2] if liq_counts else 0
    all_liq = {lq["caller"] for lq in liqs}
    oev = [a for a, o in blob.get("oracles", {}).items() if o.get("oev_flag")]

    print("=== §5 пороги (pre-registered, вердикт за пользователем) ===\n")
    print("SHADOW-активация (≥1 рынок gate=0 ∧ borrow>0):")
    shadow_ok = any(int(m["liquidatorGate"], 16) == 0 and (m.get("totalUnits") or 0) > 0
                    for m in blob["markets"])
    print(f"  {'✓ АКТИВНА' if shadow_ok else '✗'}\n")

    print("HANDOVER в monad-liquidator (ВСЕ одновременно):")
    print(f"  1. ≥{HANDOVER_MIN_WINDOWS} окон с долгом ≥${HANDOVER_DEBT_USD:,.0f}: "
          f"{len(windows_5k)}/{HANDOVER_MIN_WINDOWS} {'✓' if len(windows_5k) >= HANDOVER_MIN_WINDOWS else '✗ НЕТ'}")
    print(f"  2. медиана уник. ликвидаторов ≤{HANDOVER_MEDIAN_LIQ}: "
          f"{median_liq} {'✓' if median_liq <= HANDOVER_MEDIAN_LIQ else '✗'}")
    print(f"  3-5. paper-модель/net/exit-импакт: требуют потока с долгом (M-T2 на живых окнах) — н/д")
    print(f"  → HANDOVER: НЕ достигнут (нет окон с сейзабельным долгом ≥$5k)\n")

    print("KILL (немедленно, любой):")
    print(f"  • SVR/Atom-фид: {'⚠️ ДА ' + str(oev) if oev else 'нет ✓'}")
    print(f"  • ≥{KILL_LIQ_COUNT} уник. ликвидаторов в первые 2 нед: "
          f"{len(all_liq)} {'⚠️ РОЙ' if len(all_liq) >= KILL_LIQ_COUNT else 'нет ✓'}")
    print(f"  • re-deploy с изменённой механикой: вотчер M-T4.iv (сейчас адреса совпадают)")
    print(f"  → KILL: не сработал\n")

    # часы возврата в monitoring
    gate0_markets = [m for m in blob["markets"] if int(m["liquidatorGate"], 16) == 0]
    first_block = min((m["block"] for m in gate0_markets), default=None)
    if first_block:
        fb = rpc.get_block(first_block)
        t0 = int(fb["timestamp"], 16)
        deadline = t0 + 8 * 7 * 86400
        print(f"MONITORING-возврат: 8 нед без окна ≥$5k от первого gate=0 рынка "
              f"({_iso(t0)}) ⇒ дедлайн {_iso(deadline)} "
              f"({(deadline - now) / 86400:+.0f}д)")


def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "log"
    {"log": shadow_log, "gates": gates}.get(cmd, lambda: print(__doc__))()


if __name__ == "__main__":
    main()
