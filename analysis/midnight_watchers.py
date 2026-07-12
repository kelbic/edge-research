"""M-T4: вотчеры Midnight (Фаза B, план §3, каденс еженедельный). Read-only.

Пять датчиков (снимок → сравнение с прошлым → триггеры по §5 плана):
  (i)   доля gate≠0 рынков (мера огораживания поля; на borrow-весе при потоке);
  (ii)  оракул-фиды рынков — SVR/Atom/RedStone-маркер = KILL §5 (OEV-изъятие, режим 1);
  (iii) Adapter WIP→готов (vault-v2 README «Market V2 Adapter. WIP») = бутстрап lender;
  (iv)  diff `addresses.ts` (re-deploy-детектор — при DRAFT-аудитах критичен);
  (v)   audits/ не-DRAFT (финализация аудитов).
Источники (ii) он-чейн; (i) — из кэша day0/calendar; (iii)(iv)(v) — GitHub raw.
Триггеры печатаются локально, вебхуков наружу нет.

Usage:
    python3 -m analysis.midnight_watchers scan     # снимок -> data/midnight_watch_<date>.json
    python3 -m analysis.midnight_watchers report    # снимок + дифф с прошлым + триггеры
"""
from __future__ import annotations

import glob
import json
import os
import sys
import urllib.request

from analysis.midnight_day0 import (
    CACHE, DATA_DIR, MIDNIGHT, BUNDLES, MEMPOOL, OEV_MARKERS, WAD,
)

RAW = "https://raw.githubusercontent.com/morpho-org"
# ⚠️ реестр переехал в монорепо sdks (день-0): старый morpho-ts 404
ADDRESSES_TS = f"{RAW}/sdks/main/packages/morpho-ts/src/addresses.ts"
VAULT_V2_README = f"{RAW}/vault-v2/main/README.md"
AUDITS_API = "https://api.github.com/repos/morpho-org/midnight/contents/audits"

WATCH_GLOB = os.path.join(DATA_DIR, "midnight_watch_*.json")


def _fetch(url: str, timeout: float = 25.0) -> str | None:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0",
                                                   "Accept": "application/vnd.github.raw"})
        return urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8", "replace")
    except Exception as e:
        print(f"  [!] fetch {url[:60]}…: {e}", file=sys.stderr)
        return None


def _gate_stats(blob: dict) -> dict:
    ms = blob["markets"]
    gate0 = sum(1 for m in ms if int(m["liquidatorGate"], 16) == 0)
    return {"n_markets": len(ms), "gate0": gate0,
            "gated": len(ms) - gate0,
            "gated_frac": (len(ms) - gate0) / len(ms) if ms else 0.0}


def _oracle_flags(blob: dict) -> dict:
    """(ii): пере-собрать OEV-флаг из кэшированных описаний оракулов (day0 их снял).
    Свежий пере-зонд — в day0 scan; здесь дёшево читаем кэш + маркер-строки."""
    hits = []
    for addr, o in blob.get("oracles", {}).items():
        joined = " ".join(o.get("descriptions", [])).lower()
        if o.get("oev_flag") or any(m in joined for m in OEV_MARKERS):
            hits.append(addr)
    return {"n_oracles": len(blob.get("oracles", {})), "oev_oracles": hits}


def scan() -> dict:
    blob = json.load(open(CACHE))
    snap = {
        "cache_head": blob.get("head"),
        "gate": _gate_stats(blob),
        "oracles": _oracle_flags(blob),
        "addresses_ts": None,   # хэш блока Base-адресов
        "adapter_wip": None,
        "audits_draft": None,
    }
    # (iv) addresses.ts — вырезаем Base-блок Midnight-адресов, хэшируем
    ts = _fetch(ADDRESSES_TS)
    if ts:
        import re
        from analysis.keccak import keccak256
        # ⚠️ midnight-поля есть у нескольких чейнов (тестовый 31337 = заглушки
        # 0x..05/06/07). Берём ТОЛЬКО блок BaseMainnet: от «[ChainId.BaseMainnet]:»
        # до следующего «[ChainId.».
        base_seg = ts
        mb = re.search(r'\[ChainId\.BaseMainnet\]\s*:', ts)
        if mb:
            rest = ts[mb.end():]
            nxt = re.search(r'\[ChainId\.\w+\]\s*:', rest)
            base_seg = rest[:nxt.start()] if nxt else rest
        addrs = dict(re.findall(r'(midnight\w*)\s*:\s*"(0x[0-9a-fA-F]{40})"', base_seg))
        snap["addresses_ts"] = {
            "midnight_addrs": addrs,
            "matches_deploy": (addrs.get("midnight", "").lower() == MIDNIGHT.lower()
                               and addrs.get("midnightBundles", "").lower() == BUNDLES.lower()
                               and addrs.get("midnightMempool", "").lower() == MEMPOOL.lower()),
            "hash": "0x" + keccak256(json.dumps(addrs, sort_keys=True).encode()).hex()[:16],
        }
    # (iii) vault-v2 Adapter WIP?
    rm = _fetch(VAULT_V2_README)
    if rm is not None:
        snap["adapter_wip"] = ("Market V2 Adapter. WIP" in rm
                               or "Market V2 Adapter — WIP" in rm)
    # (v) audits — сколько DRAFT
    au = _fetch(AUDITS_API)
    if au:
        try:
            files = json.loads(au)
            names = [f["name"] for f in files if isinstance(f, dict) and "name" in f]
            snap["audits_draft"] = {
                "files": names,
                "n": len(names),
                "all_draft": all("draft" in n.lower() for n in names if n.lower().endswith(".pdf")),
            }
        except Exception:
            snap["audits_draft"] = {"error": "parse"}
    return snap


def _latest_prior() -> dict | None:
    files = sorted(glob.glob(WATCH_GLOB))
    if not files:
        return None
    return json.load(open(files[-1]))


def report() -> None:
    from datetime import datetime, UTC
    snap = scan()
    print("=== M-T4 вотчеры Midnight (снимок) ===")
    g = snap["gate"]
    print(f"(i)  gate: {g['n_markets']} рынков, gate≠0 (KYC): {g['gated']} "
          f"({g['gated_frac']:.0%}); поле {'ОГОРОЖЕНО' if g['gated_frac'] > 0.8 else 'открыто'}")
    o = snap["oracles"]
    print(f"(ii) оракулы: {o['n_oracles']}; OEV-маркер (SVR/Atom): "
          f"{'⚠️ ' + ', '.join(a[:10] for a in o['oev_oracles']) if o['oev_oracles'] else 'нет'}")
    a = snap["addresses_ts"]
    if a:
        print(f"(iv) addresses.ts: адреса {'совпадают с деплоем' if a['matches_deploy'] else '⚠️ ИЗМЕНИЛИСЬ'} "
              f"(hash {a['hash']})")
    print(f"(iii) vault-v2 Adapter WIP: {snap['adapter_wip']} "
          f"{'(бутстрап lender ещё не готов)' if snap['adapter_wip'] else '⚠️ (Adapter ГОТОВ — предвестник borrow)'}")
    ad = snap["audits_draft"]
    if ad and "n" in ad:
        print(f"(v)  audits: {ad['n']} файлов, все DRAFT: {ad.get('all_draft')} "
              f"{'⚠️ (аудиты финализированы)' if ad.get('all_draft') is False else ''}")

    # дифф с прошлым снимком + триггеры §5
    prior = _latest_prior()
    print("\n--- дифф с прошлым снимком + триггеры (§5 плана) ---")
    triggers = []
    if snap["oracles"]["oev_oracles"]:
        triggers.append("KILL: SVR/Atom-фид на рынке (OEV-изъятие, режим 1)")
    if snap["gate"]["gated_frac"] > 0.8:
        triggers.append("MONITORING: >80% рынков gate≠0 (поле огорожено)")
    if a and not a["matches_deploy"]:
        triggers.append("RE-DEPLOY?: адреса в addresses.ts ≠ известному деплою — сверить механику (M-T3)")
    if snap["adapter_wip"] is False:
        triggers.append("BOOTSTRAP: Market V2 Adapter больше не WIP — ждать рост borrow")
    if ad and ad.get("all_draft") is False:
        triggers.append("AUDITS: появились не-DRAFT аудиты")
    if prior:
        if prior.get("addresses_ts", {}).get("hash") != (a or {}).get("hash"):
            triggers.append("addresses.ts ИЗМЕНИЛСЯ с прошлого снимка")
        if prior.get("gate", {}).get("n_markets") != g["n_markets"]:
            print(f"  рынков: {prior['gate']['n_markets']} → {g['n_markets']}")
        if prior.get("adapter_wip") != snap["adapter_wip"]:
            print(f"  Adapter WIP: {prior.get('adapter_wip')} → {snap['adapter_wip']}")
    else:
        print("  (прошлого снимка нет — первый прогон)")
    if triggers:
        for t in triggers:
            print(f"  ⚠️ {t}")
    else:
        print("  нет сработавших триггеров; каденс еженедельный")

    # сохранить снимок (дата из кэша head, без Date.now — берём из имён/аргумента)
    date = sys.argv[2] if len(sys.argv) > 2 else _today_from_env()
    path = os.path.join(DATA_DIR, f"midnight_watch_{date}.json")
    json.dump(snap, open(path, "w"), indent=1)
    print(f"\nснимок сохранён -> {path}")


def _today_from_env() -> str:
    # дата берётся из переменной окружения или дефолт (детерминизм для реплея)
    return os.environ.get("WATCH_DATE", "2026-07-12")


def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "report"
    if cmd == "scan":
        snap = scan()
        date = sys.argv[2] if len(sys.argv) > 2 else _today_from_env()
        path = os.path.join(DATA_DIR, f"midnight_watch_{date}.json")
        json.dump(snap, open(path, "w"), indent=1)
        print(f"снимок -> {path}")
    else:
        report()


if __name__ == "__main__":
    main()
