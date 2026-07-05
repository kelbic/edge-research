"""Gate 0 (Направление 7, OEV-сёрчер): метрики API3 OEV по живому бэкенду их дашборда.
Read-only. Критерии вердикта зафиксированы ДО данных: docs/oev_gate0_criteria.md
(коммит b277aa2). См. STATE.md.

Контекст источника: публичный роллап OEV Network (chain 4913) выключен в ноябре 2025,
реплей PlacedBid/AwardedBid невозможен (RPC/explorer/bridge — NXDOMAIN, проверено
2026-07-05). Живой источник — бэкенд официального дашборда oev-dashboard.api3.org:
по-событийная история ликвидаций по каждому dApp с полями sender/type/incentiveUsd/
bidAmountUsd/gasCostUsd (18-дес. fixed point). type: OEV = выигранный аукцион,
MEV = открытая гонка по signed-API данным, BFL = ликвидация по базовому фиду.
Это САМООТЧЁТ API3 (их индексер) → обязательный спот-чек txHash'ей на реальных
чейнах: подкоманда verify (Ethereum, morpho-api3-ethereum).

Метрики по критериям: M1 концентрация (top-1/3/10, HHI), M2 контестируемость
(новые входы ≥5% в скользящем 3-мес окне), M3 остаточная маржа победителя
(incentive − bid − gas), M4 поток, M5 санити (recapture-rate, protocolFee).

Usage:
    python3 -m analysis.oev_api3 scan      # выгрузка всех dApp в data/api3_oev_liquidations.json
    python3 -m analysis.oev_api3 report    # метрики M1-M5 по критериям
    python3 -m analysis.oev_api3 verify    # спот-чек 8 OEV-событий против Ethereum RPC
"""
from __future__ import annotations

import gzip
import json
import os
import random
import sys
import urllib.request
from collections import Counter, defaultdict

BACKEND = "https://oev-dashboard-backend-aws.api3.org"
DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
CACHE = os.path.join(DATA_DIR, "api3_oev_liquidations.json.gz")  # ~51MB сырым — жмём

KEEP_FIELDS = ("txHash", "blockNumber", "blockTimestamp", "logIndex", "sender", "type",
               "incentiveUsd", "bidAmountUsd", "gasCostUsd", "protocolFeeUsd",
               "collateralSeizedUsd", "debtRepaidUsd")

MONTH_SECONDS = 30 * 86_400  # аналитические "месяцы" — окна по 30 дней от конца датасета

# Граница эр: публичный роллап выключен, средства выведены до конца ноября 2025
# (api3-docs коммит 6eb2607). После — закрытые private-аукционы partnered searchers,
# биды в дашборде в основном не публикуются (72% нулей) => M3 считаем по open-эре.
SHUTDOWN_TS = 1_764_547_200  # 2025-12-01 UTC


def _get(url: str, timeout: float = 90.0):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0",
                                               "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


# -- чистые функции (юнит-тестируемы офлайн) ------------------------------

def fp18(v) -> float:
    """18-дес. fixed-point строка бэкенда -> float USD."""
    if v is None:
        return 0.0
    return int(v) / 1e18


def margin_usd(ev: dict) -> float:
    """Остаточная маржа победителя: gross-инцентив − бид − газ (M3)."""
    return fp18(ev["incentiveUsd"]) - fp18(ev.get("bidAmountUsd", 0)) - fp18(ev.get("gasCostUsd", 0))


def month_index(ts: int, end_ts: int) -> int:
    """0 = последние 30 дней перед end_ts, 1 = предыдущие 30, и т.д."""
    return (end_ts - ts) // MONTH_SECONDS


def percentile(sorted_vals: list[float], q: float) -> float:
    if not sorted_vals:
        return 0.0
    idx = q * (len(sorted_vals) - 1)
    lo = int(idx)
    hi = min(lo + 1, len(sorted_vals) - 1)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (idx - lo)


def concentration(events: list[dict]) -> dict:
    """M1: top-1/3/10 по числу и по gross-$, HHI по $ (0..10000)."""
    n_by = Counter()
    usd_by = defaultdict(float)
    for ev in events:
        s = ev["sender"].lower()
        n_by[s] += 1
        usd_by[s] += fp18(ev["incentiveUsd"])
    n_total = sum(n_by.values())
    usd_total = sum(usd_by.values())
    top_n = n_by.most_common()
    top_usd = sorted(usd_by.items(), key=lambda kv: -kv[1])
    def share_n(k):
        return sum(c for _, c in top_n[:k]) / n_total if n_total else 0.0
    def share_usd(k):
        return sum(u for _, u in top_usd[:k]) / usd_total if usd_total else 0.0
    hhi = sum((u / usd_total * 100) ** 2 for _, u in top_usd) if usd_total else 0.0
    return {"senders": len(n_by), "n_total": n_total, "usd_total": usd_total,
            "top1_n": share_n(1), "top3_n": share_n(3), "top10_n": share_n(10),
            "top1_usd": share_usd(1), "top3_usd": share_usd(3), "top10_usd": share_usd(10),
            "hhi_usd": hhi,
            "top_usd": [(a, round(u, 2), n_by[a]) for a, u in top_usd[:10]]}


def qualified_entrants(events: list[dict], end_ts: int,
                       share_threshold: float = 0.05,
                       entry_window_months: int = 12,
                       rolling_months: int = 3) -> dict:
    """M2: «новый вход» = первая победа в последних 12 мес И ≥5% побед (по числу
    ИЛИ по $) в каком-либо скользящем 3-мес окне после входа. Пороги — из
    pre-registration (docs/oev_gate0_criteria.md)."""
    first_win = {}
    for ev in sorted(events, key=lambda e: e["blockTimestamp"]):
        s = ev["sender"].lower()
        first_win.setdefault(s, ev["blockTimestamp"])
    entrants = {s for s, ts in first_win.items()
                if end_ts - ts <= entry_window_months * MONTH_SECONDS}
    max_mi = max((month_index(e["blockTimestamp"], end_ts) for e in events), default=-1)
    qualified = {}
    for start in range(0, max_mi + 1):  # окно = месяцы [start, start+rolling)
        lo = end_ts - (start + rolling_months) * MONTH_SECONDS
        hi = end_ts - start * MONTH_SECONDS
        win_events = [e for e in events if lo < e["blockTimestamp"] <= hi]
        if not win_events:
            continue
        n_tot = len(win_events)
        usd_tot = sum(fp18(e["incentiveUsd"]) for e in win_events)
        n_by = Counter(e["sender"].lower() for e in win_events)
        usd_by = defaultdict(float)
        for e in win_events:
            usd_by[e["sender"].lower()] += fp18(e["incentiveUsd"])
        for s in entrants:
            if s in n_by and first_win[s] <= hi:
                sh_n = n_by[s] / n_tot
                sh_u = (usd_by[s] / usd_tot) if usd_tot else 0.0
                best = max(sh_n, sh_u)
                if best >= share_threshold:
                    prev = qualified.get(s, 0.0)
                    qualified[s] = max(prev, best)
    return {"entrants_total": len(entrants),
            "qualified": {s: round(v, 4) for s, v in qualified.items()},
            "first_win": {s: first_win[s] for s in entrants}}


def margin_stats(events: list[dict]) -> dict:
    """M3: распределение остаточной маржи + доля топ-5 событий в суммарной марже."""
    margins = sorted(margin_usd(e) for e in events)
    total = sum(margins)
    top5 = sum(sorted(margins, reverse=True)[:5])
    return {"n": len(margins), "total": total,
            "median": percentile(margins, 0.5),
            "p25": percentile(margins, 0.25), "p75": percentile(margins, 0.75),
            "negative_share": (sum(1 for m in margins if m < 0) / len(margins)) if margins else 0.0,
            "top5_share": (top5 / total) if total > 0 else 0.0}


def monthly_rows(events: list[dict], end_ts: int, months: int = 12) -> list[dict]:
    """Помесячная динамика (30-дн окна от конца датасета): поток, gross, bid, маржа."""
    buckets = defaultdict(list)
    for e in events:
        mi = month_index(e["blockTimestamp"], end_ts)
        if 0 <= mi < months:
            buckets[mi].append(e)
    rows = []
    for mi in range(months - 1, -1, -1):
        evs = buckets.get(mi, [])
        margins = sorted(margin_usd(e) for e in evs)
        rows.append({"months_ago": mi, "n": len(evs),
                     "gross": sum(fp18(e["incentiveUsd"]) for e in evs),
                     "bids": sum(fp18(e.get("bidAmountUsd", 0)) for e in evs),
                     "margin_total": sum(margins),
                     "margin_median": percentile(margins, 0.5),
                     "senders": len({e["sender"].lower() for e in evs})})
    return rows


def tx_aggregate(events: list[dict]) -> list[dict]:
    """Агрегация OEV-событий по txHash: один аукцион = одна tx. Бид в дашборде
    ДУБЛИРУЕТСЯ на каждое событие одной tx (проверено на данных: multi-event tx
    несут одинаковый ненулевой bidAmountUsd) => бид берём один раз (max),
    инцентивы/газ суммируем. Возвращает по-tx строки для честной M3."""
    by_tx: dict[str, dict] = {}
    for e in events:
        t = by_tx.setdefault(e["txHash"], {"inc": 0.0, "gas": 0.0, "bid": 0.0,
                                           "sender": e["sender"].lower(),
                                           "blockTimestamp": e["blockTimestamp"]})
        t["inc"] += fp18(e["incentiveUsd"])
        t["gas"] += fp18(e.get("gasCostUsd", 0))
        t["bid"] = max(t["bid"], fp18(e.get("bidAmountUsd", 0)))
    return list(by_tx.values())


def recapture_rate(events: list[dict]) -> float:
    """M5: доля gross-инцентива, изъятая бидом (bid / incentive), по сумме."""
    gross = sum(fp18(e["incentiveUsd"]) for e in events)
    bids = sum(fp18(e.get("bidAmountUsd", 0)) for e in events)
    return bids / gross if gross else 0.0


# -- сбор/отчёт ------------------------------------------------------------

def scan() -> None:
    landing = _get(f"{BACKEND}/landing-page-data")
    dapps = sorted(landing.keys())
    out = {"source": f"{BACKEND}/liquidations", "dapps": {}}
    for k in dapps:
        d = _get(f"{BACKEND}/liquidations?dapp={k}")
        evs = [{f: e.get(f) for f in KEEP_FIELDS} for e in d.get("liquidations", [])]
        out["dapps"][k] = {"dbLastBlock": d.get("dbLastBlock"), "events": evs}
        print(f"{k}: {len(evs)} events", file=sys.stderr)
    os.makedirs(DATA_DIR, exist_ok=True)
    with gzip.open(CACHE, "wt") as f:
        json.dump(out, f)
    n = sum(len(v["events"]) for v in out["dapps"].values())
    print(f"cached {n} events across {len(dapps)} dapps -> {CACHE}")


def _load() -> dict:
    with gzip.open(CACHE, "rt") as f:
        return json.load(f)


def report() -> None:
    data = _load()
    all_events = []
    for k, v in data["dapps"].items():
        for e in v["events"]:
            e["dapp"] = k
            all_events.append(e)
    end_ts = max(e["blockTimestamp"] for e in all_events)
    oev = [e for e in all_events if e["type"] == "OEV"]
    print(f"dataset end (max blockTimestamp): {end_ts}")
    print(f"events total: {len(all_events)}; by type: {Counter(e['type'] for e in all_events)}")

    print("\n== доля канала OEV в потоке инцентивов (последние 12 мес, по $) ==")
    recent = [e for e in all_events if month_index(e["blockTimestamp"], end_ts) < 12]
    by_type = defaultdict(float)
    for e in recent:
        by_type[e["type"]] += fp18(e["incentiveUsd"])
    tot = sum(by_type.values())
    for t, u in sorted(by_type.items(), key=lambda kv: -kv[1]):
        print(f"  {t}: ${u:,.0f} ({u / tot:.1%})")

    print("\n== по-dApp сводка OEV-событий (вся история) ==")
    print(f"{'dapp':26} {'nOEV':>6} {'gross$':>12} {'bids$':>11} {'margin$':>11} {'recap':>6} {'senders':>7}")
    for k in sorted(data["dapps"]):
        evs = [e for e in oev if e["dapp"] == k]
        if not evs:
            continue
        gross = sum(fp18(e["incentiveUsd"]) for e in evs)
        bids = sum(fp18(e.get("bidAmountUsd", 0)) for e in evs)
        marg = sum(margin_usd(e) for e in evs)
        print(f"{k:26} {len(evs):>6} {gross:>12,.0f} {bids:>11,.0f} {marg:>11,.0f}"
              f" {bids / gross if gross else 0:>6.1%} {len({e['sender'].lower() for e in evs}):>7}")

    print("\n== M1 концентрация (все OEV-события, вся история, pooled) ==")
    c = concentration(oev)
    print(f"  senders={c['senders']}  n={c['n_total']}  gross=${c['usd_total']:,.0f}")
    print(f"  top-1: {c['top1_n']:.1%} по числу / {c['top1_usd']:.1%} по $")
    print(f"  top-3: {c['top3_n']:.1%} / {c['top3_usd']:.1%}")
    print(f"  top-10: {c['top10_n']:.1%} / {c['top10_usd']:.1%}   HHI($)={c['hhi_usd']:,.0f}")
    for a, u, n in c["top_usd"]:
        print(f"    {a} ${u:>10,.2f} n={n}")

    print("\n== M1 концентрация (последние 6 мес) ==")
    oev6 = [e for e in oev if month_index(e["blockTimestamp"], end_ts) < 6]
    c6 = concentration(oev6)
    print(f"  senders={c6['senders']}  n={c6['n_total']}  gross=${c6['usd_total']:,.0f}")
    print(f"  top-3: {c6['top3_n']:.1%} по числу / {c6['top3_usd']:.1%} по $   HHI($)={c6['hhi_usd']:,.0f}")

    print("\n== M2 контестируемость (новые входы за 12 мес, порог 5% в 3-мес окне) ==")
    q = qualified_entrants(oev, end_ts)
    print(f"  адресов с первой OEV-победой за 12 мес: {q['entrants_total']}")
    print(f"  квалифицированных (≥5% доли): {len(q['qualified'])}")
    for s, v in sorted(q["qualified"].items(), key=lambda kv: -kv[1]):
        print(f"    {s} best-3мес-доля={v:.1%} (первая победа ts={q['first_win'][s]})")

    print("\n== M3 остаточная маржа победителя (OEV, вся история) ==")
    m = margin_stats(oev)
    print(f"  n={m['n']} total=${m['total']:,.0f} median=${m['median']:,.2f}"
          f" p25=${m['p25']:,.2f} p75=${m['p75']:,.2f}")
    print(f"  доля событий с маржой <0: {m['negative_share']:.1%}; топ-5 событий = {m['top5_share']:.1%} суммарной маржи")

    print("\n== M3/M4 помесячно (30-дн окна от конца датасета; OEV-события) ==")
    print(f"{'мес назад':>9} {'n':>5} {'gross$':>10} {'bids$':>9} {'маржа$':>9} {'медиана$':>9} {'senders':>7}")
    for r in monthly_rows(oev, end_ts):
        print(f"{r['months_ago']:>9} {r['n']:>5} {r['gross']:>10,.0f} {r['bids']:>9,.0f}"
              f" {r['margin_total']:>9,.0f} {r['margin_median']:>9,.2f} {r['senders']:>7}")

    print("\n== разрез эр: open (до 2025-12-01) vs partnered (после) ==")
    for name, es in (("open", [e for e in oev if e["blockTimestamp"] < SHUTDOWN_TS]),
                     ("partnered", [e for e in oev if e["blockTimestamp"] >= SHUTDOWN_TS])):
        zero = sum(1 for e in es if fp18(e.get("bidAmountUsd", 0)) == 0)
        print(f"  {name}: событий={len(es)} gross=${sum(fp18(e['incentiveUsd']) for e in es):,.0f}"
              f" bids(сырые)=${sum(fp18(e.get('bidAmountUsd', 0)) for e in es):,.0f}"
              f" событий-с-нулевым-бидом={zero} ({zero / len(es) if es else 0:.0%})")

    print("\n== M3 по-tx (дедуп бида; один аукцион = одна tx), OPEN-эра ==")
    open_oev = [e for e in oev if e["blockTimestamp"] < SHUTDOWN_TS]
    txs = tx_aggregate(open_oev)
    margins = sorted(t["inc"] - t["bid"] - t["gas"] for t in txs)
    gross = sum(t["inc"] for t in txs)
    bids = sum(t["bid"] for t in txs)
    total_m = sum(margins)
    months_span = (max(t["blockTimestamp"] for t in txs) - min(t["blockTimestamp"] for t in txs)) / MONTH_SECONDS
    print(f"  аукционов={len(txs)} gross=${gross:,.0f} bids=${bids:,.0f}"
          f" recapture={bids / gross:.1%}")
    print(f"  маржа: total=${total_m:,.0f} (≈${total_m / months_span:,.0f}/мес на ВСЮ площадку,"
          f" окно {months_span:.1f} мес)")
    print(f"  per-auction: median=${percentile(margins, 0.5):.2f}"
          f" p25=${percentile(margins, 0.25):.2f} p75=${percentile(margins, 0.75):.2f}"
          f" отриц.={sum(1 for m in margins if m < 0) / len(margins):.1%}")
    by_sender = defaultdict(lambda: [0.0, 0])
    for t in txs:
        by_sender[t["sender"]][0] += t["inc"] - t["bid"] - t["gas"]
        by_sender[t["sender"]][1] += 1
    top = sorted(by_sender.items(), key=lambda kv: -kv[1][0])[:5]
    pos = sum(1 for _, (m, _) in by_sender.items() if m > 0)
    print(f"  сендеров={len(by_sender)}, с положительной кумулятивной маржой={pos};"
          f" топ-5 по кумулятивной марже:")
    for a, (m, n) in top:
        print(f"    {a} margin=${m:,.0f} n={n} (≈${m / months_span:,.0f}/мес)")

    print("\n== M5 санити ==")
    print(f"  pooled recapture-rate по-событийно (искажён дублями бида): {recapture_rate(oev):.1%}")
    print(f"  честный по-tx recapture open-эры: {bids / gross:.1%}")
    over = [(k, v) for k, v in
            (lambda per: per.items())(_per_dapp_recapture(oev)) if v > 1.0]
    if over:
        print("  ⚠ dApp'ы с recapture>100% (по-tx, вся история) — переплата не объяснена"
              " (см. STATE: MEV-докап тем же сендером опровергнут, 0 пересечений):")
        for k, v in over:
            print(f"    {k}: {v:.0%}")
    fee = sum(fp18(e.get("protocolFeeUsd", 0)) for e in oev)
    bids_raw = sum(fp18(e.get("bidAmountUsd", 0)) for e in oev)
    print(f"  protocolFee/bids: {fee / bids_raw if bids_raw else 0:.1%}"
          f" (семантика поля protocolFeeUsd НЕ ДОКУМЕНТИРОВАНА — не используется в M3)")


def _per_dapp_recapture(oev: list[dict]) -> dict:
    out = {}
    by_dapp = defaultdict(list)
    for e in oev:
        by_dapp[e["dapp"]].append(e)
    for k, es in by_dapp.items():
        txs = tx_aggregate(es)
        g = sum(t["inc"] for t in txs)
        b = sum(t["bid"] for t in txs)
        if g > 0:
            out[k] = b / g
    return out


def verify(sample: int = 8) -> None:
    """Спот-чек самоотчёта API3 против Ethereum mainnet RPC: tx существует,
    from == sender, блок совпадает. Проверяем morpho-api3-ethereum (chain 1)."""
    from analysis.rpc import Rpc
    data = _load()
    evs = [e for e in data["dapps"].get("morpho-api3-ethereum", {}).get("events", [])]
    if not evs:
        print("morpho-api3-ethereum: нет событий в кэше")
        return
    rng = random.Random(7)  # фиксированный сид — реплеябельно
    picked = rng.sample(evs, min(sample, len(evs)))
    rpc = Rpc()
    ok = 0
    for e in picked:
        tx = rpc.call("eth_getTransactionByHash", [e["txHash"]])
        if tx is None:
            print(f"  MISSING tx {e['txHash']}")
            continue
        sender_ok = tx["from"].lower() == e["sender"].lower()
        block_ok = int(tx["blockNumber"], 16) == int(e["blockNumber"])
        status = "OK" if sender_ok and block_ok else f"MISMATCH from={tx['from']} block={int(tx['blockNumber'], 16)}"
        if sender_ok and block_ok:
            ok += 1
        print(f"  {e['txHash'][:18]}… type={e['type']} sender_ok={sender_ok} block_ok={block_ok} -> {status}")
    print(f"verified {ok}/{len(picked)}")


def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "report"
    {"scan": scan, "report": report, "verify": verify}[cmd]()


if __name__ == "__main__":
    main()
