#!/usr/bin/env python3
"""Нативный форвардный мониторинг Midnight (Фаза B §5) с отправкой в Telegram
ПРЯМЫМ Bot API — без сессии Claude и без облака. Ставится в OS-cron (crontab).

Прогоняет календарь/вотчеры/пороги из репо edge-research, детектит §5-триггеры,
шлёт сжатый отчёт в TG. Кэш возвращается к git-состоянию, если нет новых рынков
(чтобы не плодить diff). Токен читается из ~/.claude/channels/telegram/.env.

crontab (пн/чт 06:17 UTC):
  17 6 * * 1,4 /home/claude-agent/midnight-monitor/run.sh
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.parse
import urllib.request

REPO = "/home/claude-agent/edge-research"
STATE_FILE = "/home/claude-agent/midnight-monitor/state.json"
LOG_FILE = "/home/claude-agent/midnight-monitor/monitor.log"
ENV_FILE = os.path.expanduser("~/.claude/channels/telegram/.env")
CHAT_ID = "265715923"

sys.path.insert(0, REPO)
os.chdir(REPO)


def log(msg: str) -> None:
    from datetime import datetime, UTC
    line = f"{datetime.now(UTC).isoformat()} {msg}"
    print(line)
    try:
        with open(LOG_FILE, "a") as f:
            f.write(line + "\n")
    except OSError:
        pass


def bot_token() -> str:
    with open(ENV_FILE) as f:
        for ln in f:
            if ln.startswith("TELEGRAM_BOT_TOKEN="):
                return ln.split("=", 1)[1].strip()
    raise RuntimeError("нет TELEGRAM_BOT_TOKEN в .env")


def send_tg(text: str) -> bool:
    token = bot_token()
    data = urllib.parse.urlencode({"chat_id": CHAT_ID, "text": text,
                                   "disable_web_page_preview": "true"}).encode()
    req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage",
                                 data=data)
    try:
        r = json.loads(urllib.request.urlopen(req, timeout=30).read())
        return bool(r.get("ok"))
    except Exception as e:
        log(f"send_tg ошибка: {e}")
        return False


def git_reset_cache() -> None:
    subprocess.run(["git", "checkout", "data/midnight_markets.json"],
                   cwd=REPO, capture_output=True)
    subprocess.run(["git", "checkout", "--", "data/"], cwd=REPO, capture_output=True)


def load_state() -> dict:
    if os.path.exists(STATE_FILE):
        try:
            return json.load(open(STATE_FILE))
        except Exception:
            return {}
    return {}


def save_state(st: dict) -> None:
    json.dump(st, open(STATE_FILE, "w"))


def run() -> int:
    from analysis import midnight_calendar as cal
    from analysis import midnight_watchers as wat
    from analysis.midnight_day0 import CACHE, STABLES, WAD
    from analysis.rpc import Rpc
    from analysis.midnight_day0 import BASE_RPCS

    prior = load_state()
    # 1) инкрементальный апдейт кэша (новые рынки + свежий totalUnits-снимок)
    try:
        cal.update()
    except Exception as e:
        log(f"calendar.update ошибка: {e}")
        send_tg(f"⚠️ Midnight монитор: ошибка обновления кэша ({e}). "
                f"Проверьте RPC/сеть на VPS.")
        return 1

    blob = json.load(open(CACHE))
    rpc = Rpc(BASE_RPCS)
    now = int(rpc.get_block("latest")["timestamp"], 16)
    ticks = cal.build_ticks(blob, now)
    passed = [t for t in ticks if t["passed"]]
    future = [t for t in ticks if not t["passed"]]

    # position-сканер: точная боевая цель (borrower-уровень, не totalUnits рынка)
    top_pos = None
    try:
        from analysis import midnight_positions as pos
        pos.scan()
        pblob = json.load(open(pos.POS_CACHE))
        priced = [p for p in pblob["positions"] if p.get("usd") is not None]
        if priced:
            top_pos = max(priced, key=lambda p: p["usd"])
    except Exception as e:
        log(f"positions.scan ошибка: {e}")

    # 2) вотчер-снимок (возвращает dict)
    try:
        snap = wat.scan()
    except Exception as e:
        log(f"watchers.scan ошибка: {e}")
        snap = {}

    # 3) §5-сигналы
    n_markets = len(blob["markets"])
    new_markets = n_markets - prior.get("n_markets", n_markets)
    uniq_liq = len({lq["caller"] for lq in blob["liquidates"]})
    # borrow ≥$5k: максимум по стейбл-рынкам
    max_borrow = 0.0
    for m in blob["markets"]:
        tu, dec = m.get("totalUnits"), blob["tokens"].get(m["loanToken"], {}).get("decimals")
        if tu and dec is not None and m["loanToken"] in STABLES:
            max_borrow = max(max_borrow, tu / 10 ** dec)
    oev = snap.get("oracles", {}).get("oev_oracles", [])
    adapter_wip = snap.get("adapter_wip")
    addr_ok = snap.get("addresses_ts", {}).get("matches_deploy", True)
    gated = snap.get("gate", {}).get("gated", 0)

    # точная боевая цель на уровне позиции (borrower)
    top_usd = (top_pos or {}).get("usd") or 0.0
    max_borrow = max(max_borrow, top_usd)

    # триггеры эскалации (§5)
    esc = []
    if top_usd >= 5000:
        esc.append(f"БОЕВАЯ ЦЕЛЬ: позиция borrower {top_pos['borrower'][:12]}… "
                   f"на рынке {top_pos['market'][:12]}… долг ${top_usd:,.0f} ≥ $5k"
                   + (" (post-maturity, ВСЯ сейзабельна)" if top_pos.get("post_maturity") else
                      f" (maturity через {(top_pos['maturity']-now)/86400:.1f}д)"))
    elif max_borrow >= 5000:
        esc.append(f"borrow-поток появился: рынок с долгом ${max_borrow:,.0f} ≥ $5k")
    if adapter_wip is False:
        esc.append("vault-v2 Adapter больше НЕ WIP — бутстрап lender-потока")
    if oev:
        esc.append(f"OEV-фид (SVR/Atom) на оракуле: {', '.join(a[:10] for a in oev)} — KILL-маркер")
    if uniq_liq >= 10:
        esc.append(f"≥10 уникальных ликвидаторов ({uniq_liq}) — рой пришёл, KILL-маркер")
    if addr_ok is False:
        esc.append("addresses.ts ≠ известному деплою — возможный re-deploy")

    # 4) сборка отчёта
    from datetime import datetime, UTC
    date = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    nxt = future[0] if future else None
    lines = [f"🛰️ Midnight мониторинг — {date} (head {blob['head']})", ""]
    if esc:
        lines.insert(1, "⚠️ ЭСКАЛАЦИЯ:")
        for e in esc:
            lines.insert(2, f"  • {e}")
        lines.insert(2 + len(esc),
                     "  → прогнать M-T2 на живой позиции; если пороги §5 проходят — "
                     "Gate 1 (fork-replay) в monad-liquidator; решение о live-капитале за вами.")
        lines.insert(3 + len(esc), "")
    lines.append(f"📅 Рынки: {n_markets} (новых: {new_markets:+d}). "
                 + (f"Ближайшее окно: {nxt['iso']} ({(nxt['maturity'] - now) / 86400:+.1f}д), "
                    f"{nxt['n_markets']} рынк., borrow ${nxt['borrow_stable_usd']:,.0f}."
                    if nxt else "Будущих окон нет."))
    lines.append(f"🔍 gate≠0: {gated}/{n_markets} · SVR/Atom: {'⚠️ ЕСТЬ' if oev else 'нет'} · "
                 f"addresses.ts: {'ok' if addr_ok else '⚠️ ИЗМ'} · "
                 f"Adapter: {'WIP' if adapter_wip else '⚠️ ГОТОВ'} · "
                 f"аудиты: {'DRAFT' if (snap.get('audits_draft') or {}).get('all_draft') else '⚠️ финал'}")
    lines.append(f"⚖️ HANDOVER: {'НЕ достигнут' if max_borrow < 5000 else '⚠️ проверить'} "
                 f"(max borrow ${max_borrow:,.0f}, порог $5k) · "
                 f"ликвидаторов: {uniq_liq} · прошедших окон: {len(passed)}")
    if not esc:
        lines.append("")
        lines.append("Итог: без изменений, поле пусто, триггеров нет.")

    ok = send_tg("\n".join(lines))
    log(f"отчёт отправлен={ok}; new_markets={new_markets} max_borrow={max_borrow:.0f} "
        f"esc={len(esc)}")

    # 5) сохранить состояние; вернуть кэш к git, если новых рынков нет
    save_state({"n_markets": n_markets, "head": blob["head"], "uniq_liq": uniq_liq})
    if new_markets == 0:
        git_reset_cache()
        subprocess.run(["git", "checkout", "--", "data/midnight_positions.json"],
                       cwd=REPO, capture_output=True)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(run())
    except Exception as e:
        log(f"FATAL: {e}")
        try:
            send_tg(f"⚠️ Midnight монитор упал: {e}")
        except Exception:
            pass
        sys.exit(1)
