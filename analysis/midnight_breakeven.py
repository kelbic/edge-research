"""M-T2: break-even-модель рампа Midnight post-maturity (Фаза B, план §4). Read-only.

Ядро тайминг-игры. Для позиции (долг D в loan-units, коллатерал, оракул, maxLif)
считает t* = min{t: π(t) > 0}, где

    seized(t)   = D · lif(t)/WAD · ORACLE_PRICE_SCALE/price      # из Midnight.sol:687
    proceeds(t) = exit_quote(collateral→loanToken, seized(t))    # РЕАЛЬНЫМ роутером
    π(t)        = proceeds(t) − D_loanwei − gas − flashCost       # НЕ оракульной ценой

Урок SVR Gate 1 встроен: exit считаем квотой роутера на размер ВСЕЙ позиции, никогда
оракулом (оракульный «gross» D·(lif−1) — верхняя граница, испаряется на импакте клипа).
Exit-роутер — Uniswap V3 QuoterV2 на Base (перебор маршрутов/fee-тиров по лучшему
выходу), порт `analysis/svr_gate1_replay.py` под Base-хабы (WETH/USDC).

Usage:
    python3 -m analysis.midnight_breakeven model <market_id> [debt_usd]  # t* для позиции
    python3 -m analysis.midnight_breakeven sweep [debt_usd]              # t* по всем рынкам
    python3 -m analysis.midnight_breakeven demo                          # оффлайн-демо формулы
"""
from __future__ import annotations

import itertools
import json
import sys

from analysis.keccak import keccak256
from analysis.midnight_day0 import (
    BASE_RPCS, CACHE, WAD, lif_at,
)
from analysis.rpc import Rpc

ORACLE_PRICE_SCALE = 10 ** 36  # Midnight/Blue: IOracle.price() масштаб 1e36
TIME_TO_MAX_LIF = 3600

# --- Base Uniswap V3 (проверены он-чейн 2026-07-12: QuoterV2/Factory код непуст,
#     пулы WETH/USDC и cbBTC/USDC существуют) + функциональная сверка квоты ниже ---
QUOTER_V2 = "0x3d4e44Eb1374240CE5F1B871ab261CD16335B76a"
V3_FACTORY = "0x33128a8fC17869897dcE68Ed026d694621f6FDfD"
WETH = "0x4200000000000000000000000000000000000006"
USDC = "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913"
FEE_TIERS = (100, 500, 3000, 10000)

SEL_GET_POOL = "0x" + keccak256(b"getPool(address,address,uint24)").hex()[:8]
SEL_QUOTE_INPUT = "0x" + keccak256(b"quoteExactInput(bytes,uint256)").hex()[:8]
SEL_PRICE = "0x" + keccak256(b"price()").hex()[:8]
SEL_DECIMALS = "0x" + keccak256(b"decimals()").hex()[:8]

STABLES = {USDC, "0x60a3e35cc302bfa44cb288bc5a4f316fdb1adb42"}  # USDC, EURC(~$)


# -- чистые функции (юнит-тестируемы офлайн) ------------------------------

def _w(x, width=64) -> str:
    return (hex(x)[2:] if isinstance(x, int) else x).rjust(width, "0")


def encode_path(tokens: list[str], fees: list[int]) -> str:
    assert len(fees) == len(tokens) - 1
    out = tokens[0][2:].lower()
    for i, fee in enumerate(fees):
        out += fee.to_bytes(3, "big").hex() + tokens[i + 1][2:].lower()
    return "0x" + out


def encode_quote_call(path_hex: str, amount_in: int) -> str:
    path_body = path_hex[2:]
    path_len = len(path_body) // 2
    padded = path_body + "0" * ((64 - len(path_body) % 64) % 64)
    return SEL_QUOTE_INPUT + _w(64) + _w(amount_in) + _w(path_len) + padded


def route_candidates(coll: str, loan: str) -> list[list[str]]:
    """Маршруты coll→loan: прямой, через WETH, через USDC, coll→WETH→USDC→loan."""
    coll, loan = coll.lower(), loan.lower()
    routes = [[coll, loan]]
    for h in (WETH.lower(), USDC):
        if h not in (coll, loan):
            routes.append([coll, h, loan])
    r = [coll, WETH.lower(), USDC, loan]
    if len(set(r)) == len(r):
        routes.append(r)
    uniq = []
    for rt in routes:
        if rt not in uniq and len(set(rt)) == len(rt):
            uniq.append(rt)
    return uniq


def seized_for_debt(debt_units: int, lif_wad: int, price_1e36: int) -> int:
    """seizedAssets = debt·lif/WAD·SCALE/price [Midnight.sol:687, mulDivDown-семантика]."""
    return debt_units * lif_wad // WAD * ORACLE_PRICE_SCALE // price_1e36


def breakeven_t(debt_units: int, max_lif_wad: int, price_1e36: int, quote_fn,
                gas_loanwei: int, flash_bps: int = 0,
                step_s: int = 60, horizon_s: int = TIME_TO_MAX_LIF) -> dict:
    """Наименьший Δt (сек после maturity), при котором proceeds(t) > D + gas + flash.
    quote_fn(seized_raw) -> proceeds в loan-wei (реальная exit-квота). Перебор рампа
    сеткой step_s до horizon (за horizon lif=maxLif, дальше без изменений).
    По рампу seized меняется лишь на бонус (≤~4%), так что quote_fn может кэшировать
    маршрут — см. BaseRouter.make_quote_fn."""
    curve = []
    t_star = None
    for dt in range(step_s, horizon_s + step_s, step_s):
        lif = lif_at(max_lif_wad, dt)
        seized = seized_for_debt(debt_units, lif, price_1e36)
        proceeds = quote_fn(seized)
        flash = debt_units * flash_bps // 10_000
        pi = proceeds - debt_units - gas_loanwei - flash
        curve.append({"dt": dt, "lif": lif, "seized": seized,
                      "proceeds": proceeds, "pi": pi})
        if t_star is None and pi > 0:
            t_star = dt
    return {"t_star_s": t_star, "curve": curve, "max_lif": max_lif_wad}


# -- exit-роутер Base (eth_call квоты) --------------------------------------

class BaseRouter:
    def __init__(self, rpc: Rpc):
        self.rpc = rpc
        self._pool_cache: dict[tuple, list[int]] = {}
        self._dec: dict[str, int] = {}

    def decimals(self, token: str) -> int:
        token = token.lower()
        if token not in self._dec:
            self._dec[token] = int(self.rpc.eth_call(token, SEL_DECIMALS), 16)
        return self._dec[token]

    def _pool_fees(self, a: str, b: str) -> list[int]:
        key = (a.lower(), b.lower())
        if key in self._pool_cache:
            return self._pool_cache[key]
        out = []
        for fee in FEE_TIERS:
            try:
                r = self.rpc.eth_call(V3_FACTORY,
                                      SEL_GET_POOL + _w(a[2:]) + _w(b[2:]) + _w(fee))
                if int("0x" + r[-40:], 16) != 0:
                    out.append(fee)
            except Exception:
                continue
        self._pool_cache[key] = out
        return out

    def quote_path(self, toks: list[str], fees: list[int], amount_in: int) -> int:
        """Единичная quoteExactInput по фиксированному пути (0 при ошибке)."""
        path = encode_path([t if t.startswith("0x") else "0x" + t for t in toks], fees)
        try:
            r = self.rpc.eth_call(QUOTER_V2, encode_quote_call(path, amount_in),
                                  gas=30_000_000)
            return int(r[2:2 + 64], 16)
        except Exception:
            return 0

    def quote(self, coll: str, loan: str, amount_in: int) -> dict:
        """Лучшая quoteExactInput coll→loan по маршрутам×fee-комбо (по макс. выходу)."""
        if amount_in <= 0:
            return {"out": 0, "route": None}
        best = {"out": 0, "route": None, "fees": None}
        for toks in route_candidates(coll, loan):
            toks = [t if t.startswith("0x") else "0x" + t for t in toks]
            per_hop, ok = [], True
            for i in range(len(toks) - 1):
                fees = self._pool_fees(toks[i], toks[i + 1])
                if not fees:
                    ok = False
                    break
                per_hop.append(fees)
            if not ok:
                continue
            for combo in itertools.product(*per_hop):
                out = self.quote_path(toks, list(combo), amount_in)
                if out > best["out"]:
                    best = {"out": out, "route": toks, "fees": list(combo)}
        return best

    def make_quote_fn(self, coll: str, loan: str, ref_amount: int):
        """Разрешает лучший маршрут ОДИН раз на ref_amount, возвращает дешёвую
        quote_fn(amount)->out по этому пути. По рампу amount меняется на ≤~4%,
        так что оптимальный путь стабилен — экономит ~10× RPC-вызовов."""
        best = self.quote(coll, loan, ref_amount)
        route, fees = best["route"], best["fees"]
        if not route:
            return (lambda amount: 0), best
        return (lambda amount: self.quote_path(route, fees, amount)), best

    def price_via_quote(self, coll: str, loan: str) -> float | None:
        """Спот-курс из малой квоты (1 ед. коллатерала) для сверки с оракулом."""
        one = 10 ** self.decimals(coll)
        q = self.quote(coll, loan, one)
        if not q["out"]:
            return None
        return q["out"] / 10 ** self.decimals(loan)


# -- прогон на реальных рынках ----------------------------------------------

def _oracle_price(rpc: Rpc, oracle: str) -> int | None:
    try:
        r = rpc.eth_call(oracle, SEL_PRICE)
        return int(r, 16) if r and r != "0x" else None
    except Exception:
        return None


def _gas_loanwei(router: BaseRouter, loan: str, gas_units: int = 300_000,
                 gas_price_gwei: float = 0.02) -> int:
    """Оценка газа в loan-wei: Base ~0.02 gwei базовый; конверт ETH→loan квотой."""
    gas_eth_wei = int(gas_units * gas_price_gwei * 1e9)
    if loan.lower() == WETH.lower():
        return gas_eth_wei
    q = router.quote(WETH, loan, gas_eth_wei)
    return q["out"] or 0


def model_market(market_id: str, debt_usd: float | None = None) -> None:
    blob = json.load(open(CACHE))
    m = next((x for x in blob["markets"] if x["id"].startswith(market_id)
              or x["id"] == market_id), None)
    if not m:
        print(f"рынок {market_id} не найден в кэше")
        return
    rpc = Rpc(BASE_RPCS)
    router = BaseRouter(rpc)
    loan = m["loanToken"]
    loan_dec = router.decimals(loan)
    loan_sym = blob["tokens"].get(loan, {}).get("symbol") or loan[:8]
    print(f"=== break-even рынка {m['id'][:14]}… loan {loan_sym} maturity {m['maturity']} ===")
    gas_lw = _gas_loanwei(router, loan)
    print(f"газ-оценка: {gas_lw / 10 ** loan_dec:.4f} {loan_sym} (300k gas @ 0.02 gwei)")

    for cp in m["collateralParams"]:
        coll = cp["token"]
        coll_sym = blob["tokens"].get(coll, {}).get("symbol") or coll[:6]
        price = _oracle_price(rpc, cp["oracle"])
        max_lif = cp["maxLif"]
        if price is None or max_lif is None:
            print(f"  коллатерал {coll_sym}: оракул/maxLif недоступен — пропуск")
            continue
        # долг: заданный $ или фактический totalUnits рынка
        if debt_usd is not None:
            # D loan-units ≈ debt_usd для стейбл-loan; иначе через оракул loan цены нет — берём как есть
            debt_units = int(debt_usd * 10 ** loan_dec)
        else:
            debt_units = m.get("totalUnits") or 0
        if debt_units <= 0:
            print(f"  коллатерал {coll_sym}: долг 0 — нет позиции (укажи debt_usd для paper-сценария)")
            continue

        # разрешаем маршрут один раз на максимальном seized (при maxLif)
        max_seized = seized_for_debt(debt_units, max_lif, price)
        qfn, route_best = router.make_quote_fn(coll, loan, max_seized)
        res = breakeven_t(debt_units, max_lif, price, qfn, gas_lw)
        # спот-сверка оракула с роутером (диагностика): человекочитаемая цена
        # 1 коллатерала в loan = price/1e36 · 10^(coll_dec − loan_dec)
        spot = router.price_via_quote(coll, loan)
        orc_px = price / ORACLE_PRICE_SCALE * 10 ** (router.decimals(coll) - loan_dec) \
            if price else None
        ts = res["t_star_s"]
        print(f"  коллатерал {coll_sym} (lltv {cp['lltv'] / WAD:.2f}, "
              f"maxLif {max_lif / WAD:.4f}, бонус {100 * (max_lif - WAD) / WAD:.2f}%):")
        print(f"    долг D = {debt_units / 10 ** loan_dec:,.2f} {loan_sym}; "
              f"оракул-цена ≈ {orc_px:.2f}, роутер-спот ≈ {spot}")
        if ts is None:
            last = res["curve"][-1]
            print(f"    t* = НЕ ДОСТИГНУТ за 60-мин рамп (при maxLif π = "
                  f"{last['pi'] / 10 ** loan_dec:+,.2f} {loan_sym}) — окно убыточно "
                  f"(exit-импакт+газ > бонус)")
        else:
            row = next(r for r in res["curve"] if r["dt"] == ts)
            print(f"    t* = +{ts}с ({ts / 60:.1f} мин) после maturity; "
                  f"π(t*) = {row['pi'] / 10 ** loan_dec:+,.2f} {loan_sym}, "
                  f"seized {row['seized'] / 10 ** router.decimals(coll):.6f} {coll_sym}")


def sweep(debt_usd: float | None = None) -> None:
    blob = json.load(open(CACHE))
    stable_markets = [m for m in blob["markets"] if m["loanToken"] in STABLES]
    print(f"=== sweep break-even ({len(stable_markets)} стейбл-loan рынков) "
          f"debt_usd={debt_usd or 'фактический totalUnits'} ===\n")
    for m in stable_markets[:6] if debt_usd else stable_markets:
        model_market(m["id"], debt_usd)
        print()


def demo() -> None:
    """Оффлайн-демонстрация формулы (без сети): синтетический линейный exit."""
    max_lif = 1_043_841_336_116_910_229  # ~4.38% (lltv .86, cursor .30)
    price = 3000 * ORACLE_PRICE_SCALE // 10 ** 12  # WETH@$3000, loan 6dec
    debt = 100_000 * 10 ** 6  # $100k USDC

    def qfn(seized, impact_bps=50):  # синтетика: 0.5% импакт на клип
        gross = seized * price // ORACLE_PRICE_SCALE
        return gross * (10_000 - impact_bps) // 10_000

    res = breakeven_t(debt, max_lif, price, qfn, gas_loanwei=5 * 10 ** 6)
    ts = res["t_star_s"]
    print(f"demo: D=$100k, maxLif 4.38%, exit-импакт 0.5%, газ $5")
    print(f"  t* = {ts}с ({ts / 60:.1f} мин) — бонус превышает импакт+газ" if ts
          else "  t* не достигнут")
    for r in res["curve"][::10]:
        print(f"  +{r['dt']:>4}с lif={r['lif'] / WAD:.4f} π={r['pi'] / 1e6:+.1f}$")


def main() -> None:
    args = sys.argv[1:]
    cmd = args[0] if args else "demo"
    if cmd == "model" and len(args) >= 2:
        model_market(args[1], float(args[2]) if len(args) > 2 else None)
    elif cmd == "sweep":
        sweep(float(args[1]) if len(args) > 1 else None)
    else:
        demo()


if __name__ == "__main__":
    main()
