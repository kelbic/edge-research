"""Midnight live-signing executor — автономный боевой цикл (Направление 8/B).
Профиль: латентность+капитал расслаблены. Стреляет САМ в позиции ≥$5k; вхолостую
не ждёт ручного шага. Read-loop через analysis/* (read-only RPC); подпись+бродкаст —
через `cast send` (foundry keystore, ключ пользователя, не в git, не в ps).

БЕЗОПАСНОСТЬ (защита капитала, не «дисциплина»):
  - DRY_RUN=1 по умолчанию — только логирует cast-команду, НЕ шлёт. Пользователь
    флипает в 0, когда ключ+контракт готовы.
  - realized-net гард (M-T2 живым роутером): не стреляет, если net ≤ MIN_PROFIT_USD.
  - minLoanOut в calldata = repaidUnits+minProfit ⇒ своп+контракт РЕВЕРТЯТ в убыток
    (worst-case = потерянный газ на реверте, не потеря позиции).
  - только gate=0 рынки; только post-maturity (вся позиция сейзабельна, RCF off).

Конфиг (env):
  MN_CONTRACT     адрес задеплоенного MidnightLiquidator (обязателен для live)
  MN_ACCOUNT      имя foundry-keystore (cast wallet import), деф. "midnight-bot"
  MN_PASSFILE     файл пароля keystore, деф. ~/.midnight-bot/pw
  MN_RPC          write-RPC Base, деф. https://mainnet.base.org
  MN_THRESHOLD    порог цели $, деф. 5000
  MN_MIN_PROFIT   мин. realized-net $ для выстрела, деф. 50
  MN_POLL_SEC     интервал опроса, деф. 45
  DRY_RUN         1 (деф.) = не слать; 0 = боевой

Usage:
    DRY_RUN=1 python3 -m bot.executor once     # один проход (диагностика)
    DRY_RUN=0 MN_CONTRACT=0x.. python3 -m bot.executor loop   # боевой цикл
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.parse
import urllib.request

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from analysis.midnight_day0 import BASE_RPCS, CACHE, STABLES, lif_at  # noqa: E402
from analysis.midnight_breakeven import (  # noqa: E402
    BaseRouter, breakeven_t, encode_path, seized_for_debt, _oracle_price, _gas_loanwei,
)
from analysis.midnight_positions import enumerate_borrowers, read_debt  # noqa: E402
from analysis.rpc import Rpc  # noqa: E402

CONTRACT = os.environ.get("MN_CONTRACT", "")
ACCOUNT = os.environ.get("MN_ACCOUNT", "midnight-bot")
PASSFILE = os.path.expanduser(os.environ.get("MN_PASSFILE", "~/.midnight-bot/pw"))
RPC_WRITE = os.environ.get("MN_RPC", "https://mainnet.base.org")
THRESHOLD_USD = float(os.environ.get("MN_THRESHOLD", "5000"))
MIN_PROFIT_USD = float(os.environ.get("MN_MIN_PROFIT", "50"))
POLL_SEC = int(os.environ.get("MN_POLL_SEC", "45"))
DRY_RUN = os.environ.get("DRY_RUN", "1") != "0"
ENV_FILE = os.path.expanduser("~/.claude/channels/telegram/.env")
CHAT_ID = "265715923"
RUN_SIG = "runLiquidation(bytes32,uint256,uint256,address,bytes,uint256)"


def alert(text: str) -> None:
    try:
        token = None
        with open(ENV_FILE) as f:
            for ln in f:
                if ln.startswith("TELEGRAM_BOT_TOKEN="):
                    token = ln.split("=", 1)[1].strip()
        if not token:
            return
        data = urllib.parse.urlencode({"chat_id": CHAT_ID, "text": text}).encode()
        urllib.request.urlopen(
            urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage",
                                   data=data), timeout=20)
    except Exception as e:
        print(f"alert fail: {e}")


def find_targets(rpc: Rpc, blob: dict, now: int) -> list[dict]:
    """Post-maturity сейзабельные позиции ≥ порога на gate=0 рынках."""
    mkt = {m["id"]: m for m in blob["markets"]}
    borrowers = enumerate_borrowers(rpc, blob["deploy_block"], rpc.block_number())
    out = []
    for mid, bset in borrowers.items():
        m = mkt.get(mid)
        if not m or int(m["liquidatorGate"], 16) != 0 or now <= m["maturity"]:
            continue
        dec = blob["tokens"].get(m["loanToken"], {}).get("decimals")
        if dec is None or m["loanToken"] not in STABLES:
            continue  # v1: только стейбл-loan (оценка $ надёжна)
        for b in bset:
            debt = read_debt(rpc, mid, b)
            usd = debt / 10 ** dec if debt else 0
            if usd >= THRESHOLD_USD:
                out.append({"market": mid, "borrower": b, "debt": debt,
                            "usd": usd, "m": m, "loan_dec": dec})
    return out


def evaluate(router: BaseRouter, rpc: Rpc, t: dict, now: int) -> dict | None:
    """t* + realized-net гард. Возвращает calldata-параметры или None (не стрелять)."""
    m = t["m"]
    # выбираем коллатерал с максимальной позицией заёмщика — упрощение v1:
    # берём коллатерал index 0 (в проде — перебор по collateralBitmap).
    cp = m["collateralParams"][0]
    coll, loan = cp["token"], m["loanToken"]
    price = _oracle_price(rpc, cp["oracle"])
    if not price:
        return None
    debt = t["debt"]
    gas_lw = _gas_loanwei(router, loan)
    max_seized = seized_for_debt(debt, cp["maxLif"], price)
    qfn, best = router.make_quote_fn(coll, loan, max_seized)
    if not best["route"]:
        return None
    res = breakeven_t(debt, cp["maxLif"], price, qfn, gas_lw, step_s=30)
    # текущий Δt после maturity
    dt_now = now - m["maturity"]
    lif_now = lif_at(cp["maxLif"], dt_now)
    seized_now = seized_for_debt(debt, lif_now, price)
    proceeds_now = qfn(seized_now)
    net_now = proceeds_now - debt - gas_lw
    net_usd = net_now / 10 ** t["loan_dec"]
    swap_path = encode_path(best["route"], best["fees"])
    min_out = debt + int(MIN_PROFIT_USD * 10 ** t["loan_dec"])
    return {"coll": coll, "loan": loan, "price": price, "cp_index": 0,
            "t_star_s": res["t_star_s"], "dt_now": dt_now, "net_usd": net_usd,
            "fire": net_now > int(MIN_PROFIT_USD * 10 ** t["loan_dec"]),
            "swap_path": swap_path, "min_out": min_out, "repaid": debt}


def fire(t: dict, ev: dict) -> None:
    """Подпись+бродкаст через cast send (или dry-лог)."""
    args = ["cast", "send", CONTRACT, RUN_SIG,
            t["market"], str(ev["cp_index"]), str(ev["repaid"]),
            t["borrower"], ev["swap_path"], str(ev["min_out"]),
            "--rpc-url", RPC_WRITE, "--account", ACCOUNT, "--password-file", PASSFILE]
    if DRY_RUN or not CONTRACT:
        msg = (f"🧪 DRY_RUN: цель ${t['usd']:,.0f} borrower {t['borrower'][:10]}… "
               f"net ${ev['net_usd']:+,.1f}; НЕ отправлено (DRY_RUN/нет контракта).\n"
               f"cast: {' '.join(a if not a.startswith('0x') or len(a)<20 else a[:14]+'…' for a in args)}")
        print(msg)
        alert(msg)
        return
    alert(f"🔫 ВЫСТРЕЛ: ликвидация ${t['usd']:,.0f} borrower {t['borrower'][:10]}… "
          f"paper-net ${ev['net_usd']:+,.1f}, отправляю tx…")
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=120)
        ok = r.returncode == 0
        tail = (r.stdout or r.stderr)[-400:]
        alert(f"{'✅ tx отправлена' if ok else '❌ реверт/ошибка'}: {tail}")
    except Exception as e:
        alert(f"❌ cast ошибка: {e}")


def once() -> int:
    blob = json.load(open(CACHE))
    rpc = Rpc(BASE_RPCS)
    router = BaseRouter(rpc)
    now = int(rpc.get_block("latest")["timestamp"], 16)
    targets = find_targets(rpc, blob, now)
    print(f"[{time.strftime('%H:%M:%S')}] целей ≥${THRESHOLD_USD:,.0f}: {len(targets)}"
          f" (DRY_RUN={'on' if DRY_RUN else 'OFF'}, contract={'set' if CONTRACT else 'нет'})")
    for t in targets:
        ev = evaluate(router, rpc, t, now)
        if not ev:
            continue
        print(f"  цель ${t['usd']:,.0f}: t*={ev['t_star_s']} dt_now={ev['dt_now']}с "
              f"net=${ev['net_usd']:+,.1f} fire={ev['fire']}")
        if ev["fire"] and ev["dt_now"] >= (ev["t_star_s"] or 0):
            fire(t, ev)
    return len(targets)


def loop() -> None:
    alert(f"▶️ Midnight executor запущен (DRY_RUN={'on' if DRY_RUN else 'OFF'}, "
          f"порог ${THRESHOLD_USD:,.0f}, contract={'set' if CONTRACT else 'НЕТ'}).")
    while True:
        try:
            once()
        except Exception as e:
            print(f"loop err: {e}")
        time.sleep(POLL_SEC)


def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "once"
    {"once": once, "loop": loop}.get(cmd, lambda: print(__doc__))()


if __name__ == "__main__":
    main()
