"""T5 (Направление 6, ePBS/Glamsterdam): сенсор-скрипт S1–S7 по таблице §2
`docs/epbs_t4t5_execution_plan.md`. Каденс: раз в 2 недели до анонса публичного
тестнета (триггер S2), далее еженедельно; после mainnet-эпохи — ежедневно.
Выход — ЛОКАЛЬНЫЙ печатный отчёт + снапшот data/epbs_sensors_<дата>.json,
дифф против предыдущего снапшота. Никаких вебхуков наружу. Read-only, stdlib.

Датчики (источник -> ловим -> триггер):
  S1  raw eip-7773.md            SFI/CFI-список, де-скоуп 7732, 8282 в мета
                                 -> де-скоуп = парковка направления
  S2  activation-таблица 7773 +  GLOAS_FORK_EPOCH конфигов mainnet/Sepolia/
      Hoodi/Holešky              -> эпоха установлена = старт T1–T3 (после OK
                                 пользователя)
  S3  релизы flashbots/mev-boost{,-relay} (grep gloas/epbs) + rbuilder PR #855
      как «инженерный сигнал» (НЕ триггер) -> нет Gloas-софта relay-пути к S2 =
                                 эскалация H3 в prepare-tooling
  S4  последние релизы consensus-клиентов (информационно)
  S5  consensus-specs specs/gloas/ — PTC-стимулы (хэш + PTC_*-константы)
                                 -> митигации вошли = пересчитать H1/H2
  S6  тайминг-константы Gloas из configs/mainnet.yaml + последний alpha-тег
      (класс сдвигов «9s->6s», PAYLOAD_DUE_BPS/ATTESTATION_DUE bps,
      builder withdrawability)  -> сдвиг = обновить параметры H2-модели
  S7  статус 2780/8038/7904 в CFI-списке 7773 + devnet-конфиги ethpandaops
                                 -> вход репрайсинг-CFI в SFI = расширить
                                 CFI-ветвь T4 до основной

Graceful degradation: сетевая ошибка по источнику = {"status": "UNAVAILABLE"},
не крэш; триггеры считаются только по доступным источникам.

Usage:
    python3 -m analysis.epbs_sensors scan     # fetch -> снапшот -> дифф -> отчёт
    python3 -m analysis.epbs_sensors report   # отчёт по последнему снапшоту, без сети
"""
from __future__ import annotations

import glob
import hashlib
import json
import os
import re
import sys
import time
import urllib.request

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
SNAP_GLOB = os.path.join(DATA_DIR, "epbs_sensors_*.json")

UNSET_EPOCH = 18446744073709551615  # 2**64-1 = «не запланировано»

RAW_EIPS = "https://raw.githubusercontent.com/ethereum/EIPs/master/EIPS/eip-7773.md"
RAW_SPECS = "https://raw.githubusercontent.com/ethereum/consensus-specs/master"
GH_API = "https://api.github.com"

CONFIG_URLS = {  # GLOAS_FORK_EPOCH: mainnet из consensus-specs, тестнеты из eth-clients
    "mainnet": f"{RAW_SPECS}/configs/mainnet.yaml",
    "sepolia": "https://raw.githubusercontent.com/eth-clients/sepolia/main/metadata/config.yaml",
    "hoodi": "https://raw.githubusercontent.com/eth-clients/hoodi/main/metadata/config.yaml",
    "holesky": "https://raw.githubusercontent.com/eth-clients/holesky/main/metadata/config.yaml",
}
CLIENT_REPOS = ["sigp/lighthouse", "prysmaticlabs/prysm", "Consensys/teku",
                "status-im/nimbus-eth2", "grandinetech/grandine"]
GLOAS_SPEC_FILES = ["beacon-chain.md", "validator.md", "builder.md"]
TIMING_KEYS = ["PAYLOAD_DUE_BPS", "PAYLOAD_ATTESTATION_DUE_BPS", "ATTESTATION_DUE_BPS_GLOAS",
               "AGGREGATE_DUE_BPS_GLOAS", "SYNC_MESSAGE_DUE_BPS_GLOAS",
               "CONTRIBUTION_DUE_BPS_GLOAS", "MIN_BUILDER_WITHDRAWABILITY_DELAY"]
CFI_WATCH = ["2780", "8038", "7904"]  # репрайсинг-кандидаты для T4-ветви
DEVNETS_REPO = "ethpandaops/glamsterdam-devnets"


def fetch(url: str, timeout: int = 30) -> str | None:
    """GET url -> текст; None при любой сетевой ошибке (graceful degradation)."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (sensors)"})
        return urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8", "replace")
    except Exception:
        return None


def fetch_json(url: str, timeout: int = 30):
    txt = fetch(url, timeout)
    if txt is None:
        return None
    try:
        return json.loads(txt)
    except ValueError:
        return None


# ---------------------------------------------------------- чистые экстракторы

def parse_eip7773(md: str) -> dict:
    """Разбор мета-EIP: списки SFI/CFI/Proposed/Declined + activation-таблица."""
    sections, cur = {}, None
    for line in md.splitlines():
        if line.startswith("#"):
            head = line.strip("# ").lower()
            cur = ("sfi" if "scheduled" in head else
                   "cfi" if "considered" in head else
                   "proposed" if "proposed" in head else
                   "declined" if "declined" in head else
                   "activation" if "activation" in head else None)
            continue
        if cur:
            sections.setdefault(cur, []).append(line)
    out = {}
    for k in ("sfi", "cfi", "proposed", "declined"):
        out[k] = sorted(set(re.findall(r"EIP-(\d+)", "\n".join(sections.get(k, [])))), key=int)
    rows = [l for l in sections.get("activation", [])
            if l.strip().startswith("|") and "---" not in l and "Network Name" not in l]
    out["activation"] = {}
    for r in rows:
        cells = [c.strip() for c in r.strip("| \t").split("|")]
        if cells and cells[0]:
            out["activation"][cells[0]] = [c for c in cells[1:] if c]  # непустые ячейки
    return out


def parse_fork_epoch(yaml_text: str) -> int | None:
    m = re.search(r"^GLOAS_FORK_EPOCH:\s*(\d+)", yaml_text, re.M)
    return int(m.group(1)) if m else None


def parse_timing(yaml_text: str) -> dict:
    out = {}
    for k in TIMING_KEYS:
        m = re.search(rf"^{k}:\s*(\d+)", yaml_text, re.M)
        out[k] = int(m.group(1)) if m else None
    return out


def grep_releases(releases: list, pattern: str = r"gloas|epbs|7732") -> list:
    """[{tag, date, match}] по последним релизам; match = grep по имени+телу."""
    out = []
    for r in releases or []:
        blob = " ".join(str(r.get(k) or "") for k in ("tag_name", "name", "body"))
        out.append({"tag": r.get("tag_name"), "date": (r.get("published_at") or "")[:10],
                    "match": bool(re.search(pattern, blob, re.I))})
    return out


# ------------------------------------------------------------------- сенсоры

def sensor_s1() -> dict:
    md = fetch(RAW_EIPS)
    if md is None:
        return {"status": "UNAVAILABLE"}
    p = parse_eip7773(md)
    return {"status": "OK", "sha": hashlib.sha256(md.encode()).hexdigest()[:16],
            "sfi": p["sfi"], "cfi": p["cfi"],
            "epbs_7732_in_sfi": "7732" in p["sfi"],
            "eip8282_mentioned": "8282" in (p["sfi"] + p["cfi"] + p["proposed"]),
            "activation": p["activation"]}


def sensor_s2(s1: dict) -> dict:
    out = {"status": "OK", "fork_epoch": {}, "activation_rows_filled": {}}
    for net, url in CONFIG_URLS.items():
        txt = fetch(url)
        out["fork_epoch"][net] = ("UNAVAILABLE" if txt is None else parse_fork_epoch(txt))
    if all(v == "UNAVAILABLE" for v in out["fork_epoch"].values()) and s1.get("status") != "OK":
        out["status"] = "UNAVAILABLE"
    for net, cells in (s1.get("activation") or {}).items():
        out["activation_rows_filled"][net] = bool(cells)
    out["epoch_set_somewhere"] = any(
        isinstance(v, int) and v != UNSET_EPOCH for v in out["fork_epoch"].values())
    return out


def sensor_s3() -> dict:
    boost = fetch_json(f"{GH_API}/repos/flashbots/mev-boost/releases?per_page=10")
    relay = fetch_json(f"{GH_API}/repos/flashbots/mev-boost-relay/releases?per_page=10")
    pr = fetch_json(f"{GH_API}/repos/flashbots/rbuilder/pulls/855")
    out = {"status": "OK" if (boost is not None or relay is not None) else "UNAVAILABLE",
           "mev_boost": grep_releases(boost) if boost is not None else "UNAVAILABLE",
           "mev_boost_relay": grep_releases(relay) if relay is not None else "UNAVAILABLE"}
    out["gloas_software_exists"] = any(
        r["match"] for k in ("mev_boost", "mev_boost_relay")
        if isinstance(out[k], list) for r in out[k])
    out["rbuilder_pr855"] = ({"title": pr.get("title"), "state": pr.get("state"),
                              "draft": pr.get("draft"), "updated": pr.get("updated_at")}
                             if isinstance(pr, dict) else "UNAVAILABLE")
    return out


def sensor_s4() -> dict:
    out, ok = {"status": "OK", "clients": {}}, False
    for repo in CLIENT_REPOS:
        r = fetch_json(f"{GH_API}/repos/{repo}/releases/latest")
        if isinstance(r, dict) and r.get("tag_name"):
            out["clients"][repo] = {"tag": r["tag_name"],
                                    "date": (r.get("published_at") or "")[:10]}
            ok = True
        else:
            out["clients"][repo] = "UNAVAILABLE"
    out["status"] = "OK" if ok else "UNAVAILABLE"
    return out


# имена PTC/тайминг-констант, чьё появление ИЛИ значение в specs/gloas = материя для H1/H2
_PTC_NAME_RE = r"\b(PTC_[A-Z_]+|PAYLOAD_TIMELY_[A-Z_]+|PAYLOAD_ATTESTATION_[A-Z_]+)\b"


def const_values(txt: str, names: list[str]) -> dict:
    """Значения именованных констант из markdown-таблиц спеки (| `NAME` | `VALUE` |).
    Позволяет отличить смену ЗНАЧЕНИЯ стимула (материя) от правки прозы/комментария:
    последняя двигает sha файла, но значения констант не трогает."""
    vals = {}
    for name in names:
        m = re.search(r"\|\s*`?" + re.escape(name) + r"`?\s*\|\s*([^|\n]+?)\s*\|", txt)
        if m:
            vals[name] = m.group(1).strip().strip("`")
    return vals


def sensor_s5() -> dict:
    out, ok = {"status": "OK", "files": {}}, False
    for f in GLOAS_SPEC_FILES:
        txt = fetch(f"{RAW_SPECS}/specs/gloas/{f}")
        if txt is None:
            out["files"][f] = "UNAVAILABLE"
            continue
        ok = True
        names = sorted(set(re.findall(_PTC_NAME_RE, txt)))
        out["files"][f] = {
            "sha": hashlib.sha256(txt.encode()).hexdigest()[:16],
            "ptc_constants": names,
            "ptc_values": const_values(txt, names),
        }
    out["status"] = "OK" if ok else "UNAVAILABLE"
    return out


def sensor_s6() -> dict:
    txt = fetch(CONFIG_URLS["mainnet"])
    rel = fetch_json(f"{GH_API}/repos/ethereum/consensus-specs/releases?per_page=1")
    out = {"status": "OK" if txt is not None else "UNAVAILABLE"}
    out["timing"] = parse_timing(txt) if txt is not None else "UNAVAILABLE"
    out["latest_spec_tag"] = (rel[0].get("tag_name")
                              if isinstance(rel, list) and rel else "UNAVAILABLE")
    return out


def sensor_s7(s1: dict) -> dict:
    out = {"status": "OK", "cfi_watch": {}}
    sfi, cfi = s1.get("sfi") or [], s1.get("cfi") or []
    for eip in CFI_WATCH:
        out["cfi_watch"][eip] = ("SFI" if eip in sfi else "CFI" if eip in cfi
                                 else "absent" if s1.get("status") == "OK" else "UNAVAILABLE")
    dirs = fetch_json(f"{GH_API}/repos/{DEVNETS_REPO}/contents/network-configs")
    if isinstance(dirs, list):
        names = sorted(d["name"] for d in dirs if isinstance(d, dict) and "name" in d)
        out["devnets"] = names
        latest = names[-1] if names else None
        if latest:
            cfg = fetch(f"https://raw.githubusercontent.com/{DEVNETS_REPO}/master/"
                        f"network-configs/{latest}/metadata/config.yaml")
            out["latest_devnet"] = {
                "name": latest,
                "gloas_fork_epoch": parse_fork_epoch(cfg) if cfg else "UNAVAILABLE"}
    else:
        out["devnets"] = "UNAVAILABLE"
    out["repricing_in_sfi"] = any(v == "SFI" for v in out["cfi_watch"].values())
    if s1.get("status") != "OK" and out["devnets"] == "UNAVAILABLE":
        out["status"] = "UNAVAILABLE"
    return out


# ---------------------------------------------------------- снапшот/дифф/отчёт

def flatten(obj, prefix=""):
    """Плоский {путь: значение} для диффа снапшотов."""
    out = {}
    if isinstance(obj, dict):
        for k, v in sorted(obj.items()):
            out.update(flatten(v, f"{prefix}.{k}" if prefix else str(k)))
    elif isinstance(obj, list):
        out[prefix] = json.dumps(obj, ensure_ascii=False, sort_keys=True)
    else:
        out[prefix] = obj
    return out


def is_schema_growth(fp: dict, fc: dict, key: str) -> bool:
    """True, если путь `key` «изменился» ТОЛЬКО потому, что сам сенсор научился его собирать,
    а не потому, что источник изменился.

    Зачем: 19.07 добавление поля `ptc_values` дало `<нет> -> uint64(2**9)` по PTC_SIZE и
    выстрелило S5-триггером «PTC-константы изменились» — хотя в спеке ничего не менялось
    (значения совпали с зафиксированными в карте перехода 05.07). Любое расширение схемы
    иначе порождает ложный триггер, а ложные триггеры обесценивают алертинг.

    Признак роста схемы: раньше РОДИТЕЛЬ был листом с пустым значением (None/{}/[]), а теперь
    у него появились дети — либо наоборот, лист исчез, потому что развернулся в поддерево.
    Смена ЗНАЧЕНИЯ уже существовавшего пути под это не подпадает и остаётся материей."""
    def _empty(v) -> bool:
        return v is None or v in ("", "{}", "[]", "null")

    if key in fp:
        return False              # путь уже собирался -> это смена значения, материя
    parent = key.rsplit(".", 1)[0] if "." in key else ""
    if not parent:
        return False
    if parent in fp and _empty(fp[parent]):
        return True               # родитель был пустым листом и развернулся в поддерево
    # ключевой случай 19.07: ветки `…​.ptc_values` в прошлом снапшоте НЕ БЫЛО ВОВСЕ — поля
    # тогда не существовало. Если же ветка была (например, в ptc_values уже лежали константы),
    # то появление новой константы внутри неё — это спека, а не схема, и остаётся материей.
    return not (parent in fp or any(k.startswith(parent + ".") for k in fp))


def diff_snapshots(prev: dict, cur: dict) -> list[str]:
    fp, fc = flatten(prev.get("sensors", {})), flatten(cur.get("sensors", {}))
    lines = []
    for k in sorted(set(fp) | set(fc)):
        if fp.get(k) != fc.get(k):
            grown = " [новое поле сенсора, не изменение источника]" \
                if is_schema_growth(fp, fc, k) else ""
            lines.append(f"  {k}: {fp.get(k, '<нет>')} -> {fc.get(k, '<нет>')}{grown}")
    return lines


def previous_snapshot(exclude: str) -> dict | None:
    files = sorted(f for f in glob.glob(SNAP_GLOB) if os.path.abspath(f) != exclude)
    return json.load(open(files[-1])) if files else None


def triggers(sensors: dict, changed: list[str]) -> list[str]:
    """Триггеры по букве плана §2; считаются только по доступным источникам."""
    out = []
    s1, s2, s3 = sensors["S1"], sensors["S2"], sensors["S3"]
    s7 = sensors["S7"]
    if s1.get("status") == "OK" and not s1["epbs_7732_in_sfi"]:
        out.append("S1 ТРИГГЕР: EIP-7732 вне SFI (де-скоуп) -> ПАРКОВКА направления 6.")
    s2_fired = s2.get("status") == "OK" and (
        s2.get("epoch_set_somewhere") or any(s2.get("activation_rows_filled", {}).values()))
    if s2_fired:
        out.append("S2 ТРИГГЕР: тестнет-эпоха Gloas установлена -> T1–T3 открываются "
                   "(ТОЛЬКО после отдельного OK пользователя).")
        if s3.get("status") == "OK" and not s3.get("gloas_software_exists"):
            out.append("S3 ТРИГГЕР: к моменту S2 нет Gloas-софта relay-пути -> "
                       "эскалация H3 в prepare-tooling.")
    if s7.get("status") == "OK" and s7.get("repricing_in_sfi"):
        out.append("S7 ТРИГГЕР: репрайсинг-EIP (2780/8038/7904) вошёл в SFI -> "
                   "CFI-ветвь T4 становится основной.")
    if any(k.startswith("S6.timing") for k in map(str.strip, changed)):
        out.append("S6 СИГНАЛ: тайминг-константы слота изменились -> обновить "
                   "bps-параметры H2-модели (класс «9s->6s»).")
    # строки с меткой роста схемы триггеров не поднимают: сенсор научился читать поле —
    # это не изменение источника (19.07: ptc_values дало ложный S5-триггер)
    changed = [k for k in changed if "новое поле сенсора" not in k]
    if any(("ptc_constants" in k or "ptc_values" in k) for k in map(str.strip, changed)):
        out.append("S5 СИГНАЛ: PTC-константы/значения в specs/gloas изменились -> "
                   "проверить PTC-стимулы, при митигациях free option пересчитать H1/H2.")
    return out


def print_report(snap: dict, prev: dict | None) -> None:
    s = snap["sensors"]
    print(f"=== ePBS-сенсоры S1–S7, снапшот {snap['date']} (локальный отчёт) ===")
    s1 = s["S1"]
    if s1.get("status") == "OK":
        print(f"S1 [OK] SFI={len(s1['sfi'])} EIP (7732 в SFI: {s1['epbs_7732_in_sfi']}); "
              f"CFI={len(s1['cfi'])}; 8282 в мета: {s1['eip8282_mentioned']}")
    else:
        print("S1 [UNAVAILABLE]")
    s2 = s["S2"]
    if s2.get("status") == "OK":
        eps = ", ".join(
            f"{n}={'не задана (2^64-1)' if v == UNSET_EPOCH else 'нет в конфиге' if v is None else v}"
            for n, v in s2["fork_epoch"].items())
        print(f"S2 [OK] GLOAS_FORK_EPOCH: {eps}; activation-строки: "
              f"{s2['activation_rows_filled'] or 'пусто'}")
    else:
        print("S2 [UNAVAILABLE]")
    s3 = s["S3"]
    if s3.get("status") == "OK":
        mb = s3["mev_boost"]
        tag = mb[0]["tag"] if isinstance(mb, list) and mb else "?"
        print(f"S3 [OK] gloas-софт relay-пути: {s3['gloas_software_exists']} "
              f"(mev-boost последний {tag}); rbuilder PR#855 (инженерный сигнал, "
              f"не триггер): {s3['rbuilder_pr855']}")
    else:
        print("S3 [UNAVAILABLE]")
    s4 = s["S4"]
    if s4.get("status") == "OK":
        cl = ", ".join(f"{r.split('/')[1]} {v['tag']}" if isinstance(v, dict) else
                       f"{r.split('/')[1]} n/a" for r, v in s4["clients"].items())
        print(f"S4 [OK] (информационно) {cl}")
    else:
        print("S4 [UNAVAILABLE]")
    s5 = s["S5"]
    if s5.get("status") == "OK":
        n = sum(len(v["ptc_constants"]) for v in s5["files"].values() if isinstance(v, dict))
        print(f"S5 [OK] specs/gloas: {len(s5['files'])} файлов, PTC-констант: {n}")
    else:
        print("S5 [UNAVAILABLE]")
    s6 = s["S6"]
    if s6.get("status") == "OK":
        t = s6["timing"]
        print(f"S6 [OK] {s6['latest_spec_tag']}: PAYLOAD_DUE_BPS={t['PAYLOAD_DUE_BPS']} "
              f"PAYLOAD_ATTESTATION_DUE_BPS={t['PAYLOAD_ATTESTATION_DUE_BPS']} "
              f"ATTESTATION_DUE_BPS_GLOAS={t['ATTESTATION_DUE_BPS_GLOAS']} "
              f"MIN_BUILDER_WITHDRAWABILITY_DELAY={t['MIN_BUILDER_WITHDRAWABILITY_DELAY']}")
    else:
        print("S6 [UNAVAILABLE]")
    s7 = s["S7"]
    if s7.get("status") == "OK":
        print(f"S7 [OK] репрайсинг-CFI: {s7['cfi_watch']}; devnets: "
              f"{s7.get('devnets')}; последний: {s7.get('latest_devnet')}")
    else:
        print("S7 [UNAVAILABLE]")

    changed = diff_snapshots(prev, snap) if prev else []
    if prev:
        print(f"\n--- дифф против снапшота {prev.get('date')} ---")
        print("\n".join(changed) if changed else "  без изменений")
    else:
        print("\n--- предыдущего снапшота нет (первый прогон) ---")
    print("\n--- триггеры (по букве плана §2) ---")
    trg = triggers(s, changed)
    print("\n".join(f"  {t}" for t in trg) if trg else
          "  нет сработавших триггеров; T1–T3 остаются запертыми; каденс 2 недели.")


def scan() -> None:
    date = time.strftime("%Y-%m-%d")
    s1 = sensor_s1()
    sensors = {"S1": s1, "S2": sensor_s2(s1), "S3": sensor_s3(), "S4": sensor_s4(),
               "S5": sensor_s5(), "S6": sensor_s6(), "S7": sensor_s7(s1)}
    snap = {"date": date, "sensors": sensors}
    os.makedirs(DATA_DIR, exist_ok=True)
    path = os.path.join(DATA_DIR, f"epbs_sensors_{date}.json")
    prev = previous_snapshot(os.path.abspath(path))
    json.dump(snap, open(path, "w"), ensure_ascii=False, indent=1)
    print(f"снапшот -> {path}\n")
    print_report(snap, prev)


def report() -> None:
    files = sorted(glob.glob(SNAP_GLOB))
    if not files:
        print("снапшотов нет — сначала: python3 -m analysis.epbs_sensors scan")
        return
    snap = json.load(open(files[-1]))
    prev = json.load(open(files[-2])) if len(files) > 1 else None
    print_report(snap, prev)


def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "report"
    {"scan": scan, "report": report}.get(cmd, lambda: print(__doc__))()


if __name__ == "__main__":
    main()
