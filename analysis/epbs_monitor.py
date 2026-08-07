#!/usr/bin/env python3
"""T5 ePBS-монитор (Направление 6): обёртка над analysis.epbs_sensors с TG-алертом
ТОЛЬКО на МАТЕРИАЛЬНОЕ изменение или триггер (§2). Тихо в остальное время. Для cron.

Отправка — прямой Telegram Bot API (токен из ~/.claude/channels/telegram/.env), как
midnight-монитор; без облака, без сессии Claude. Локальный лог — всегда.

Что считается материальным (→ TG):
  - любой триггер из epbs_sensors.triggers() (де-скоуп 7732 / тестнет-эпоха / repricing
    в SFI / сигналы S5/S6);
  - изменение ключей ВНЕ SUPPRESS (ниже). SUPPRESS = рутинный шум (версии клиентов,
    CFI-перетасовка, хэши файлов, tag-бампы mev-boost/rbuilder, список девнетов) — только
    в лог, НЕ в TG. Материя: S1.sfi/7732/8282, S2.* (эпохи/активация), S3.gloas_software,
    S5.* (specs/PTC), S6.timing, S7.repricing/cfi_watch/latest_devnet.

Устойчивость: если источник сенсора временно недоступен (status!=OK), берём последнее
известное значение из state (carry-forward) — временная недоступность НЕ даёт ложный
OK<->UNAVAILABLE алерт.

cron (раз в 3 дня, 07:00 UTC):
  0 7 */3 * * flock -n /tmp/epbs_monitor.lock python3 \
    /home/claude-agent/edge-research/analysis/epbs_monitor.py \
    >> /home/claude-agent/edge-research/data/epbs_monitor.log 2>&1
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.parse
import urllib.request

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from analysis import epbs_sensors as es  # noqa: E402

STATE_FILE = os.path.join(REPO, "data", "epbs_monitor_state.json")
LOG_FILE = os.path.join(REPO, "data", "epbs_monitor.log")
ENV_FILE = os.path.expanduser("~/.claude/channels/telegram/.env")
CHAT_ID = os.environ.get("MN_CHAT_ID", "265715923")

# префиксы плоских ключей, изменение которых НЕ алертим (рутина/шум) — только лог
SUPPRESS = ("S4", "S1.sha", "S1.cfi", "S3.mev_boost", "S3.rbuilder", "S7.devnets",
            "S7.latest_devnet")


def is_noise(key: str) -> bool:
    """True = рутина/шум: только лог, без TG. Голые content-sha спеки (правка прозы/
    комментария) и прогресс девнета материей сами по себе не являются — материей
    делает изменение констант/значений (ptc_*) или статусов (repricing/эпохи)."""
    return key.endswith(".sha") or any(key.startswith(p) for p in SUPPRESS)


def log(msg: str) -> None:
    line = f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} {msg}"
    print(line)
    try:
        with open(LOG_FILE, "a") as f:
            f.write(line + "\n")
    except OSError:
        pass


def bot_token() -> str | None:
    try:
        with open(ENV_FILE) as f:
            for ln in f:
                if ln.startswith("TELEGRAM_BOT_TOKEN="):
                    return ln.split("=", 1)[1].strip()
    except OSError:
        pass
    return None


def send_tg(text: str) -> bool:
    token = bot_token()
    if not token:
        log("send_tg: нет TELEGRAM_BOT_TOKEN")
        return False
    data = urllib.parse.urlencode({"chat_id": CHAT_ID, "text": text,
                                   "disable_web_page_preview": "true"}).encode()
    req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage", data=data)
    try:
        r = json.loads(urllib.request.urlopen(req, timeout=30).read())
        return bool(r.get("ok"))
    except Exception as e:
        log(f"send_tg ошибка: {e}")
        return False


def build_snapshot() -> dict:
    s1 = es.sensor_s1()
    return {"date": time.strftime("%Y-%m-%d"),
            "sensors": {"S1": s1, "S2": es.sensor_s2(s1), "S3": es.sensor_s3(),
                        "S4": es.sensor_s4(), "S5": es.sensor_s5(),
                        "S6": es.sensor_s6(), "S7": es.sensor_s7(s1)}}


def carry_forward(cur: dict, prev: dict | None) -> dict:
    """Для сенсоров с status!=OK — взять последнее известное OK-значение из prev, чтобы
    временная недоступность источника не порождала ложный дифф (и не затирала state)."""
    if not prev:
        return cur
    for k, v in cur["sensors"].items():
        if isinstance(v, dict) and v.get("status") != "OK":
            pv = prev.get("sensors", {}).get(k)
            if isinstance(pv, dict) and pv.get("status") == "OK":
                cur["sensors"][k] = pv
                log(f"{k} недоступен — carry-forward последнего известного")
    return cur


def changed_keys(prev: dict | None, cur: dict) -> list[str]:
    if not prev:
        return []
    fp = es.flatten(prev.get("sensors", {}))
    fc = es.flatten(cur["sensors"])
    return [k for k in sorted(set(fp) | set(fc)) if fp.get(k) != fc.get(k)]


def schema_growth_keys(prev: dict | None, cur: dict) -> set[str]:
    """Пути, «изменившиеся» лишь потому, что сенсор начал их собирать. Ни материей, ни
    триггером они быть не могут — источник их не менял (см. es.is_schema_growth)."""
    if not prev:
        return set()
    fp, fc = es.flatten(prev.get("sensors", {})), es.flatten(cur["sensors"])
    return {k for k in set(fp) | set(fc)
            if fp.get(k) != fc.get(k) and es.is_schema_growth(fp, fc, k)}


def notation_only_keys(prev: dict | None, cur: dict) -> set[str]:
    """Пути, где апстрим переписал ЗАПИСЬ значения, не тронув смысл (25.07: репо-широкое
    `uint*`->`Uint*` в consensus-specs сдвинуло текст каждой константы, не сдвинув ни одного
    числа). Ни материя, ни триггер — но в лог попадают (см. es.is_notation_only)."""
    if not prev:
        return set()
    fp, fc = es.flatten(prev.get("sensors", {})), es.flatten(cur["sensors"])
    return {k for k in set(fp) | set(fc)
            if fp.get(k) != fc.get(k) and es.is_notation_only(fp, fc, k)}


def trigger_fingerprints(sensors: dict) -> dict:
    """Отпечаток СОСТОЯНИЯ, породившего устойчивый триггер, по сенсорам.

    Триггеры S1/S2/S3/S7 сформулированы как свойства состояния («7732 вне SFI»,
    «эпоха установлена», «репрайсинг в SFI»), а не как переход. Раз войдя, такое
    состояние истинно ВСЕГДА — без защёлки алерт повторяется каждый прогон крона
    (S7 с 07.08 — навсегда; S2 после установки эпохи — в самый неподходящий момент).
    Отпечаток = то, что должно СМЕНИТЬСЯ, чтобы тревога зазвонила снова: эскалация
    (третий EIP вошёл в SFI, эпоха переехала) даёт новый отпечаток и новый звонок.

    Сенсора нет в словаре, если его источник недоступен: пропуск НЕ снимает защёлку
    (просвет — не ре-арм). Триггеры S5/S6 сюда не входят: они и так поднимаются
    только по факту ИЗМЕНЕНИЯ ключей, то есть уже являются переходами."""
    out = {}
    s1, s2, s3, s7 = sensors["S1"], sensors["S2"], sensors["S3"], sensors["S7"]
    if s1.get("status") == "OK":
        out["S1"] = json.dumps({"in_sfi": s1.get("epbs_7732_in_sfi")}, sort_keys=True)
    if s2.get("status") == "OK":
        out["S2"] = json.dumps({"epoch": s2.get("fork_epoch"),
                                "rows": s2.get("activation_rows_filled")},
                               sort_keys=True, ensure_ascii=False)
        if s3.get("status") == "OK":   # S3 звонит только вместе с S2 — отпечаток общий
            out["S3"] = json.dumps({"s2": out["S2"],
                                    "sw": s3.get("gloas_software_exists")}, sort_keys=True)
    if s7.get("status") == "OK":
        out["S7"] = json.dumps(s7.get("cfi_watch"), sort_keys=True)
    return out


def split_triggers(trg: list[str], fps: dict, latched: dict) -> tuple[list, list]:
    """(новые -> TG, повторные -> только лог). Повтор = отпечаток состояния совпал
    с уже отзвонившим. Нет отпечатка (источник недоступен) — считаем новым, чтобы
    отсутствие данных не глушило тревогу."""
    new, repeat = [], []
    for t in trg:
        sensor = t[:2]
        fp = fps.get(sensor)
        (repeat if fp is not None and latched.get(sensor) == fp else new).append(t)
    return new, repeat


def key_line(prev: dict, cur: dict, k: str) -> str:
    fp, fc = es.flatten(prev.get("sensors", {})), es.flatten(cur["sensors"])
    return f"  {k}: {fp.get(k, '<нет>')} -> {fc.get(k, '<нет>')}"


def status_line(sensors: dict) -> str:
    s1, s2 = sensors["S1"], sensors["S2"]
    s6, s7 = sensors["S6"], sensors["S7"]
    epoch = "установлена!" if (s2.get("status") == "OK" and (
        s2.get("epoch_set_somewhere") or any(s2.get("activation_rows_filled", {}).values()))) \
        else "не задана"
    sfi = len(s1["sfi"]) if s1.get("status") == "OK" else "?"
    tag = s6.get("latest_spec_tag", "?") if s6.get("status") == "OK" else "?"
    rep = s7.get("cfi_watch") if s7.get("status") == "OK" else "?"
    return (f"S1 SFI={sfi} (7732 в SFI: {s1.get('epbs_7732_in_sfi')}) · "
            f"S2 тестнет-эпоха: {epoch} · S6 {tag} · S7 репрайсинг {rep}")


def main() -> int:
    prev = None
    if os.path.exists(STATE_FILE):
        try:
            prev = json.load(open(STATE_FILE))
        except Exception:
            prev = None
    try:
        cur = build_snapshot()
    except Exception as e:
        log(f"scan FATAL: {e}")
        send_tg(f"⚠️ ePBS-монитор: скан упал ({e}). Проверьте сеть/источники.")
        return 1
    cur = carry_forward(cur, prev)

    changed = changed_keys(prev, cur)
    grown = schema_growth_keys(prev, cur)          # рост схемы сенсора != изменение источника
    notation = notation_only_keys(prev, cur)       # переписанная запись != изменение значения
    inert = grown | notation                       # источник не менялся -> ни TG, ни триггер
    material = [k for k in changed if not is_noise(k) and k not in inert]
    trg = es.triggers(cur["sensors"],              # triggers ждёт строки-«  key:…»
                      [f"  {k}:" for k in changed if k not in inert])
    first_run = prev is None

    # защёлка: устойчивый триггер звонит в TG один раз на состояние, дальше — в лог
    fps = trigger_fingerprints(cur["sensors"])
    latched = (prev or {}).get("trigger_latch", {})
    trg, repeat_trg = split_triggers(trg, fps, latched)
    cur["trigger_latch"] = {**latched,
                            **{t[:2]: fps[t[:2]] for t in trg if t[:2] in fps}}

    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    json.dump(cur, open(STATE_FILE, "w"), ensure_ascii=False, indent=1)

    date = cur["date"]
    log(f"скан {date}: изменений={len(changed)} материальных={len(material)} "
        f"триггеров={len(trg)} повторных={len(repeat_trg)} "
        f"suppressed={len(changed) - len(material)} "
        f"(из них рост схемы={len(grown)}, нотация={len(notation)})")
    for t in repeat_trg:               # состояние то же — тревога в силе, но без звонка
        log(f"  ТРИГГЕР В СИЛЕ (состояние не менялось, TG молчит): {t}")
    if grown:                                  # видно в логе, но без TG и без триггеров
        log(f"  новые поля сенсора (база): {', '.join(sorted(grown)[:8])}"
            f"{' …' if len(grown) > 8 else ''}")
    if notation:                               # тоже видно в логе, но без TG и без триггеров
        log(f"  переписана запись, значение то же: {', '.join(sorted(notation)[:8])}"
            f"{' …' if len(notation) > 8 else ''}")

    if first_run:
        msg = ("📡 ePBS-монитор запущен (baseline, S1–S7).\n"
               + status_line(cur["sensors"])
               + "\n\nДальше — ТИХО: алерт в TG только на материальное изменение или триггер "
                 "(де-скоуп 7732 / тестнет-эпоха / repricing в SFI / сдвиг тайминга). Рутина "
                 "(версии клиентов, CFI-перетасовка) — только в лог. Каденс: раз в 3 дня.")
        ok = send_tg(msg)
        log(f"baseline отправлен: {ok}")
        return 0

    if not trg and not material:
        log("без материальных изменений — TG молчит" +
            (f" (шум: {', '.join(changed)})" if changed else ""))
        return 0

    lines = [f"🛰️ ePBS-сенсоры — {date}", ""]
    if trg:
        lines.append("⚠️ ТРИГГЕР:")
        lines += [f"  • {t}" for t in trg]
        lines.append("")
    if material:
        lines.append("🔀 Материальные изменения:")
        lines += [key_line(prev, cur, k) for k in material]
        lines.append("")
    lines.append(status_line(cur["sensors"]))
    if any("ТРИГГЕР" in t for t in trg):
        lines.append("→ действие: см. план T4/T5; T1–T3 только после отдельного OK.")
    ok = send_tg("\n".join(lines))
    log(f"алерт отправлен: {ok} (материальных={len(material)}, триггеров={len(trg)})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
