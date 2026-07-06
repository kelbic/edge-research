"""Gate 1 (Направление 7, SVR-Ethereum): realized net через fork-replay квот свопа.
Read-only, $0 (ни одной транзакции — только eth_call-квоты на исторических блоках).
Критерий вердикта зафиксирован ДО данных: docs/svr_gate1_criteria.md (коммит 1700eb9).

Задача: gross из Gate 0 — по ценам AaveOracle (верхняя граница). Здесь меряем
realized net = фактическая swap-квота на ВЫХОД захваченного залога (на блоке N−1,
до собственного market-impact) − погашенный долг − газ(факт) − SVR-бид(факт).
realized-net-margin% = realized_net / gross_oracle. Сравнивается с порогом 40%.

Выборка (pre-registered): стресс-хвост gross≥$20k из data/svr_liquidations.json.gz;
подмножества (а) кит 0xf057…0004 и (б) остальные — вердикт по (б). Стресс-флаг =
Σgross в 24ч-окне ≥ $250k.

Квоты: Uniswap V3 QuoterV2 quoteExactInput(path, amountIn) архивным eth_call на
блоке N−1; пулы не хардкодятся — перебор fee-тиров через Factory.getPool; маршруты
[coll→debt], [coll→WETH→debt], [coll→WETH→USDC→debt], берётся max. Для stETH-семейства
(wstETH/weETH) дополнительно канонический LST-путь не строим отдельно — Uniswap-пулы
wstETH/WETH и weETH/WETH ликвидны и котируются напрямую (входят в маршрут через WETH).

Адреса (по два источника: известный деплой Uniswap + функциональная сверка
QuoterV2 WETH→USDC против AaveOracle, расхождение 0.01%, 2026-07-06):
  QuoterV2 0x61fFE014bA17989E743c5F6cB21bF9697530B21e
  V3Factory 0x1F98431c8aD98523631AE4a59f267346ea31F984

Usage:
    python3 -m analysis.svr_gate1_replay select    # отобрать выборку -> data/svr_gate1_sample.json
    python3 -m analysis.svr_gate1_replay quote      # квоты на форке -> data/svr_gate1_quotes.json.gz
    python3 -m analysis.svr_gate1_replay report     # медиана/p25 (а)/(б), вердикт vs 40%
    python3 -m analysis.svr_gate1_replay validate    # сверка методики: квота vs факт token-flow
"""
from __future__ import annotations

import gzip
import itertools
import json
import os
import sys
from collections import defaultdict

from analysis.keccak import keccak256
from analysis.oev_api3 import percentile
from analysis.rpc import Rpc

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
GATE0 = os.path.join(DATA_DIR, "svr_liquidations.json.gz")
SAMPLE = os.path.join(DATA_DIR, "svr_gate1_sample.json")
QUOTES = os.path.join(DATA_DIR, "svr_gate1_quotes.json.gz")

WHALE = "0xf0570ec48d03171a80ff796dceadf0d385a00004"
GROSS_MIN = 20_000.0
STRESS_WINDOW_S = 24 * 3600
STRESS_SUM = 250_000.0
THRESHOLD = 0.40  # pre-registered

QUOTER_V2 = "0x61fFE014bA17989E743c5F6cB21bF9697530B21e"
V3_FACTORY = "0x1F98431c8aD98523631AE4a59f267346ea31F984"
WETH = "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"
USDC = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"
FEE_TIERS = (100, 500, 3000, 10000)

# stETH-семейство: канонический выход через Curve stETH/ETH (глубокий пул),
# минуя тонкие Uni wstETH-пулы. Адреса — Lido wstETH + Curve stETH/ETH.
WSTETH = "0x7f39C581F595B53c5cb19bD0b3f8dA6c935E2Ca0"
CURVE_STETH = "0xDC24316b9AE028F1497c275EB9192a3Ea0f67022"  # coin0=ETH, coin1=stETH
SEL_GET_STETH = "0x" + keccak256(b"getStETHByWstETH(uint256)").hex()[:8]
SEL_GET_DY = "0x" + keccak256(b"get_dy(int128,int128,uint256)").hex()[:8]

SEL_GET_POOL = "0x" + keccak256(b"getPool(address,address,uint24)").hex()[:8]
SEL_QUOTE_INPUT = "0x" + keccak256(b"quoteExactInput(bytes,uint256)").hex()[:8]
SEL_GET_ASSET_PRICE = "0x" + keccak256(b"getAssetPrice(address)").hex()[:8]
SEL_DECIMALS = "0x" + keccak256(b"decimals()").hex()[:8]
ORACLE = "0x54586bE62E3c3580375aE3723C145253060Ca0C2"  # Aave v3 core (Gate 0)
ORACLE_LIDO = "0xE3C061981870C0C7b1f3C4F4bB36B95f1F260BE6"  # Aave Lido pool


# -- чистые функции (юнит-тестируемы офлайн) ------------------------------

def _w(x, width=64) -> str:
    return (hex(x)[2:] if isinstance(x, int) else x).rjust(width, "0")


def encode_path(tokens: list[str], fees: list[int]) -> str:
    """UniV3 path: token(20) fee(3) token(20) fee(3) ... — packed bytes hex."""
    assert len(fees) == len(tokens) - 1
    out = tokens[0][2:].lower()
    for i, fee in enumerate(fees):
        out += fee.to_bytes(3, "big").hex() + tokens[i + 1][2:].lower()
    return "0x" + out


def encode_quote_call(path_hex: str, amount_in: int) -> str:
    """quoteExactInput(bytes path, uint256 amountIn) — dynamic bytes ABI."""
    path_body = path_hex[2:]
    path_len = len(path_body) // 2
    padded = path_body + "0" * ((64 - len(path_body) % 64) % 64)
    return (SEL_QUOTE_INPUT + _w(64) + _w(amount_in) + _w(path_len) + padded)


USDT = "0xdAC17F958D2ee523a2206206994597C13D831ec7"


def route_candidates(coll: str, debt: str) -> list[list[str]]:
    """Токен-маршруты (без fee): прямой, через WETH, через стейбл-интермедиари
    (USDC/USDT — глубокие пулы BTC/USD), и двойной хоп coll→WETH→stable→debt."""
    coll, debt = coll.lower(), debt.lower()
    hubs = [WETH.lower(), USDC.lower(), USDT.lower()]
    routes = [[coll, debt]]
    for h in hubs:
        if h not in (coll, debt):
            routes.append([coll, h, debt])
    # двойной хаб coll→WETH→{USDC/USDT}→debt (BTC→WETH→USDC→USDT и т.п.)
    for stable in (USDC.lower(), USDT.lower()):
        r = [coll, WETH.lower(), stable, debt]
        if len(set(r)) == len(r):
            routes.append(r)
    uniq = []
    for r in routes:
        if r not in uniq and len(set(r)) == len(r):
            uniq.append(r)
    return uniq


def usd(raw: int, decimals: int, price_8dec: int) -> float:
    return raw / (10 ** decimals) * (price_8dec / 1e8)


def realized_net(swap_out_debt_raw: int, debt_decimals: int, debt_price: int,
                 debt_repaid_usd: float, gas_usd: float, bid_usd: float) -> float:
    proceeds_usd = usd(swap_out_debt_raw, debt_decimals, debt_price)
    return proceeds_usd - debt_repaid_usd - gas_usd - bid_usd


# -- сбор -------------------------------------------------------------------

def _load_gate0() -> list[dict]:
    return json.load(gzip.open(GATE0, "rt"))["events"]


def select() -> None:
    evs = [e for e in _load_gate0() if e.get("gross_usd") and e["gross_usd"] >= GROSS_MIN
           and e.get("eth_usd")]
    # стресс-флаг: Σgross в ±12ч
    ts_sorted = sorted(evs, key=lambda e: e["timestamp"])
    for e in evs:
        t = e["timestamp"]
        s = sum(x["gross_usd"] for x in ts_sorted
                if abs(x["timestamp"] - t) <= STRESS_WINDOW_S / 2)
        e["stress"] = s >= STRESS_SUM
        e["window_gross"] = s
    a = [e for e in evs if e["calls"][0]["liquidator"].lower() == WHALE]
    b = [e for e in evs if e["calls"][0]["liquidator"].lower() != WHALE]
    a.sort(key=lambda e: -e["gross_usd"])
    b.sort(key=lambda e: -e["gross_usd"])
    sample = a[:15] + b[:35]
    with open(SAMPLE, "w") as f:
        json.dump({"whale": a[:15], "newcomer": b[:35],
                   "counts": {"a_total": len(a), "b_total": len(b)}}, f)
    print(f"выборка: (а) кит {len(a[:15])}/{len(a)}, (б) новички {len(b[:35])}/{len(b)}; "
          f"стресс в выборке: {sum(1 for e in sample if e['stress'])}/{len(sample)}")


STABLES = {USDC.lower(), USDT.lower(), "0x6b175474e89094c44da98b954eedeac495271d0f"}  # +DAI


def _plausible_tiers(a: str, b: str) -> tuple[int, ...]:
    """Эвристика fee-тиров по типу пары — режет комбинаторику (глубокие пулы
    только в этих тирах): стейбл-стейбл → 0.01%; с участием стейбла/WETH →
    0.05%+0.3%; прочее → 0.05%+0.3%+1%."""
    a, b = a.lower(), b.lower()
    if a in STABLES and b in STABLES:
        return (100, 500)
    if WETH.lower() in (a, b) or a in STABLES or b in STABLES:
        return (500, 3000)
    return (500, 3000, 10000)


def _pool_fees(rpc: Rpc, a: str, b: str, tag: str) -> list[int]:
    """fee-тиры с существующим пулом среди правдоподобных для пары."""
    out = []
    for fee in _plausible_tiers(a, b):
        r = rpc.eth_call(V3_FACTORY, SEL_GET_POOL + _w(a[2:]) + _w(b[2:]) + _w(fee), tag)
        if int("0x" + r[-40:], 16) != 0:
            out.append(fee)
    return out


def curve_wsteth_weth(rpc: Rpc, amount_wsteth: int, tag: str) -> int:
    """wstETH → stETH (Lido view) → ETH (Curve get_dy) на блоке tag. ETH≈WETH 1:1.
    Возвращает выход в wei WETH-эквивалента (0 при ошибке)."""
    try:
        steth = int(rpc.eth_call(WSTETH, SEL_GET_STETH + _w(amount_wsteth), tag), 16)
        eth = int(rpc.eth_call(CURVE_STETH, SEL_GET_DY + _w(1) + _w(0) + _w(steth), tag), 16)
        return eth
    except Exception:
        return 0


def quote_one(rpc: Rpc, coll: str, debt: str, amount_in: int, tag: str,
              max_combos: int = 48) -> dict:
    """Лучшая квота coll→debt: перебор маршрутов И комбинаций fee-тиров по
    ЛУЧШЕМУ выходу (не по первому существующему пулу — тонкий 0.01%-пул давал
    абсурдно низкую квоту на крупных объёмах). Пулы кэшируются per-hop.
    Для wstETH дополнительно канонический Curve-путь (глубже тонких Uni-пулов)."""
    best = {"out": 0, "route": None, "fees": None}
    # Curve-путь для wstETH: wstETH→(Curve)→WETH, затем WETH→debt через Uni
    if coll.lower() == WSTETH.lower():
        weth_out = curve_wsteth_weth(rpc, amount_in, tag)
        if weth_out:
            if debt.lower() == WETH.lower():
                best = {"out": weth_out, "route": ["wstETH", "curve", "WETH"], "fees": None}
            else:
                leg2 = quote_one(rpc, WETH, debt, weth_out, tag)
                if leg2["out"]:
                    best = {"out": leg2["out"],
                            "route": ["wstETH", "curve", "WETH"] + (leg2["route"] or [])[1:],
                            "fees": leg2["fees"]}
    hop_fees: dict[tuple, list[int]] = {}
    for toks in route_candidates(coll, debt):
        toks = [t if t.startswith("0x") else "0x" + t for t in toks]
        per_hop = []
        ok = True
        for i in range(len(toks) - 1):
            key = (toks[i], toks[i + 1])
            if key not in hop_fees:
                hop_fees[key] = _pool_fees(rpc, toks[i], toks[i + 1], tag)
            if not hop_fees[key]:
                ok = False
                break
            per_hop.append(hop_fees[key])
        if not ok:
            continue
        combos = list(itertools.product(*per_hop))
        if len(combos) > max_combos:  # защита от взрыва (не встречается: ≤3 хопа × 4)
            combos = combos[:max_combos]
        for fees in combos:
            path = encode_path(toks, list(fees))
            try:
                r = rpc.eth_call(QUOTER_V2, encode_quote_call(path, amount_in), tag,
                                 gas=30_000_000)
                out = int(r[2:2 + 64], 16)
            except Exception:
                continue
            if out > best["out"]:
                best = {"out": out, "route": toks, "fees": list(fees)}
    return best


def quote() -> None:
    with open(SAMPLE) as f:
        s = json.load(f)
    rpc = Rpc(urls=["https://gateway.tenderly.co/public/mainnet",
                    "https://rpc.mevblocker.io", "https://eth.drpc.org"],
              backoff_429=0.5)
    # резюм: уже посчитанные события по txHash из частичного кэша
    done: dict[str, dict] = {}
    if os.path.exists(QUOTES):
        with gzip.open(QUOTES, "rt") as f:
            prev = json.load(f)
        for g in ("whale", "newcomer"):
            for r in prev.get(g, []):
                done[r["txHash"]] = r
        print(f"resume: {len(done)} событий из кэша", file=sys.stderr)
    dec_cache: dict[str, int] = {}

    def dec(addr: str) -> int:
        addr = addr.lower()
        if addr not in dec_cache:
            dec_cache[addr] = int(rpc.eth_call(addr, SEL_DECIMALS), 16)
        return dec_cache[addr]

    out = {"whale": [], "newcomer": []}

    def checkpoint():
        with gzip.open(QUOTES, "wt") as f:
            json.dump(out, f)

    for group in ("whale", "newcomer"):
        for i, e in enumerate(s[group]):
            if e["txHash"] in done:
                out[group].append(done[e["txHash"]])
                continue
            block = e["block"]
            tag = hex(block - 1)  # состояние ДО ликвидационной tx
            oracle = ORACLE_LIDO if e["pool"] == "prime" else ORACLE
            rec = {"txHash": e["txHash"], "block": block, "timestamp": e["timestamp"],
                   "winner": e["calls"][0]["liquidator"].lower(),
                   "gross_usd": e["gross_usd"], "stress": e["stress"],
                   "window_gross": e["window_gross"],
                   "bid_usd": (e.get("bid_eth") or 0.0) * e["eth_usd"],
                   "gas_usd": e["gas_eth"] * e["eth_usd"],
                   "legs": [], "proceeds_usd": 0.0, "debt_usd": 0.0, "no_route": False}
            for c in e["calls"]:
                coll = c["collateralAsset"]
                debt = c["debtAsset"]
                seized = c["liquidatedCollateral"]
                repaid = c["debtToCover"]
                try:
                    p_coll = int(rpc.eth_call(oracle, SEL_GET_ASSET_PRICE + _w(coll[2:]), tag), 16)
                    p_debt = int(rpc.eth_call(oracle, SEL_GET_ASSET_PRICE + _w(debt[2:]), tag), 16)
                    q = quote_one(rpc, coll, debt, seized, tag)
                    proceeds = usd(q["out"], dec(debt), p_debt) if q["out"] else 0.0
                    debt_usd = usd(repaid, dec(debt), p_debt)
                    # референс малого клипа (1/20 объёма, ×20) — почти-спот-цена:
                    # отделяет market-impact крупного клипа от реальной неликвидности залога
                    qref = quote_one(rpc, coll, debt, max(seized // 20, 1), tag)
                    ref_proceeds = usd(qref["out"] * 20, dec(debt), p_debt) if qref["out"] else 0.0
                    coll_oracle_usd = usd(seized, dec(coll), p_coll)
                    rec["proceeds_usd"] += proceeds
                    rec["ref_proceeds_usd"] = rec.get("ref_proceeds_usd", 0.0) + ref_proceeds
                    rec["coll_oracle_usd"] = rec.get("coll_oracle_usd", 0.0) + coll_oracle_usd
                    rec["debt_usd"] += debt_usd
                    rec["legs"].append({"coll": coll, "debt": debt, "seized": seized,
                                        "route": q["route"], "fees": q["fees"],
                                        "out_raw": q["out"], "proceeds_usd": proceeds,
                                        "ref_proceeds_usd": ref_proceeds,
                                        "coll_oracle_usd": coll_oracle_usd,
                                        "debt_usd": debt_usd})
                    if not q["out"]:
                        rec["no_route"] = True
                except Exception as ex:
                    rec["no_route"] = True
                    rec["legs"].append({"coll": coll, "debt": debt, "error": str(ex)[:80]})
            rec["realized_net"] = (rec["proceeds_usd"] - rec["debt_usd"]
                                   - rec["gas_usd"] - rec["bid_usd"])
            rec["margin_pct"] = (rec["realized_net"] / rec["gross_usd"]
                                 if rec["gross_usd"] else 0.0)
            # impact-adjusted net: near-spot proceeds (малый клип) − долг − газ − бид.
            # Верхняя оценка для warehousing/CEX-exit (без крупного AMM-импакта).
            rec["ref_net"] = (rec.get("ref_proceeds_usd", 0.0) - rec["debt_usd"]
                              - rec["gas_usd"] - rec["bid_usd"])
            rec["ref_margin_pct"] = (rec["ref_net"] / rec["gross_usd"]
                                     if rec["gross_usd"] else 0.0)
            # market-impact полного клипа = 1 − proceeds/ref (доля потерь от размера)
            rec["price_impact"] = (1 - rec["proceeds_usd"] / rec["ref_proceeds_usd"]
                                   if rec.get("ref_proceeds_usd") else None)
            out[group].append(rec)
            print(f"  {group}[{i}] blk={block} gross=${rec['gross_usd']:,.0f} "
                  f"proceeds=${rec['proceeds_usd']:,.0f} net=${rec['realized_net']:,.0f} "
                  f"margin={rec['margin_pct']:.0%} {'NO-ROUTE' if rec['no_route'] else ''}",
                  file=sys.stderr)
            checkpoint()
    with gzip.open(QUOTES, "wt") as f:
        json.dump(out, f)
    print(f"cached -> {QUOTES}")


# -- отчёт ------------------------------------------------------------------

def _stats(recs: list[dict]) -> dict:
    priced = [r for r in recs if not r["no_route"]]
    margins = sorted(r["margin_pct"] for r in priced)
    nets = [r["realized_net"] for r in priced]
    return {"n": len(recs), "n_priced": len(priced), "no_route": len(recs) - len(priced),
            "median_margin": percentile(margins, 0.5) if margins else None,
            "p25_margin": percentile(margins, 0.25) if margins else None,
            "p75_margin": percentile(margins, 0.75) if margins else None,
            "median_net": percentile(sorted(nets), 0.5) if nets else None,
            "p25_net": percentile(sorted(nets), 0.25) if nets else None,
            "total_net": sum(nets)}


def report() -> None:
    with gzip.open(QUOTES, "rt") as f:
        q = json.load(f)
    for group in ("whale", "newcomer"):
        recs = q[group]
        st = _stats(recs)
        stress = [r for r in recs if r["stress"] and not r["no_route"]]
        st_stress = _stats(stress) if stress else None
        label = "(а) КИТ" if group == "whale" else "(б) НОВИЧОК — вердикт-подмножество"
        print(f"\n=== {label} ===")
        print(f"  n={st['n']} priced={st['n_priced']} no-route={st['no_route']}")
        if st["median_margin"] is not None:
            print(f"  realized-net-margin: median={st['median_margin']:.1%} "
                  f"p25={st['p25_margin']:.1%} p75={st['p75_margin']:.1%}")
            print(f"  realized-net$: median=${st['median_net']:,.0f} "
                  f"p25=${st['p25_net']:,.0f} total=${st['total_net']:,.0f}")
        if st_stress and st_stress["median_margin"] is not None:
            print(f"  СТРЕСС-подмножество (n={st_stress['n_priced']}): "
                  f"median margin={st_stress['median_margin']:.1%} "
                  f"p25 net=${st_stress['p25_net']:,.0f}")

    print("\n== ПО-СОБЫТИЙНО (б) ==")
    print(f"{'блок':>10} {'gross$':>11} {'net$':>11} {'margin':>7} {'stress':>6} {'залог→долг'}")
    for r in sorted(q["newcomer"], key=lambda r: -r["gross_usd"]):
        legs = "+".join(f"{l.get('coll','?')[:6]}→{l.get('debt','?')[:6]}" for l in r["legs"][:2])
        flag = "NO-ROUTE" if r["no_route"] else ("СТРЕСС" if r["stress"] else "")
        print(f"{r['block']:>10} {r['gross_usd']:>11,.0f} {r['realized_net']:>11,.0f} "
              f"{r['margin_pct']:>7.0%} {flag:>6} {legs}")

    b = _stats(q["newcomer"])
    med = b["median_margin"]
    p25net = b["p25_net"]
    print("\n== ВЕРДИКТ vs pre-registered порог 40% (подмножество б) ==")
    print(f"  медиана realized-net-margin (б): {med:.1%} (порог {THRESHOLD:.0%})")
    print(f"  p25 realized-net$ (б): ${p25net:,.0f} (требуется >0)")
    dist = (med - THRESHOLD) * 100
    if abs(dist) <= 5:
        print(f"  → ПОГРАНИЧНОЕ (медиана в ±5пп от порога, dist={dist:+.1f}пп): расширить выборку")
    elif med >= THRESHOLD and p25net > 0:
        print(f"  → GO-сторона по медиане (dist={dist:+.1f}пп), проверить стресс-хвост и p25")
    else:
        print(f"  → NO (dist={dist:+.1f}пп либо p25≤0)")


def validate() -> None:
    """Сверка методики: для событий, где победитель свопнул залог АТОМАРНО в той же
    tx, сравнить нашу квоту proceeds с фактическим token-flow (Transfer'ы долга
    победителю в receipt). Расхождение объяснить."""
    from analysis.keccak import keccak256 as k
    with open(SAMPLE) as f:
        s = json.load(f)
    rpc = Rpc()
    T_XFER = "0x" + k(b"Transfer(address,address,uint256)").hex()
    checked = 0
    for e in (s["newcomer"] + s["whale"]):
        if len(e["calls"]) != 1:
            continue
        rec = rpc.receipt(e["txHash"])
        c = e["calls"][0]
        debt = c["debtAsset"].lower()
        winner = c["liquidator"].lower()
        # Transfer долга В адрес победителя = фактические proceeds свопа
        got = 0
        for lg in rec["logs"]:
            if (lg["address"].lower() == debt and lg["topics"][0] == T_XFER
                    and len(lg["topics"]) >= 3
                    and ("0x" + lg["topics"][2][-40:]) == winner):
                got += int(lg["data"], 16)
        if got == 0:
            continue
        tag = hex(e["block"] - 1)
        q = quote_one(rpc, c["collateralAsset"], c["debtAsset"],
                      c["liquidatedCollateral"], tag)
        print(f"{e['txHash'][:16]} блок={e['block']} факт-proceeds(raw)={got} "
              f"наша-квота(raw)={q['out']} расхожд={((q['out']-got)/got if got else 0):+.1%}")
        checked += 1
        if checked >= 3:
            break
    if not checked:
        print("не найдено атомарных single-call свопов долга победителю в выборке")


def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "report"
    {"select": select, "quote": quote, "report": report, "validate": validate}[cmd]()


if __name__ == "__main__":
    main()
