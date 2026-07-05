"""Gate 1 (Направление 8, challenger-bounties): частота РЕАЛЬНЫХ slashing-событий
на EigenLayer mainnet — главное измерение направления. Read-only. Критерии
зафиксированы ДО данных: docs/dir8_dir_morpho_preregistration.md (коммит 673f0d8).

Метод: полный скан событий AllocationManager (прокси
0x948a420b8CC1d6BFd0B6087C2E7c344a2CD0bc39 — адрес сверен по двум источникам
Layr-Labs: script/configs/mainnet/mainnet-addresses.config.json и README v1.6.0)
с блока 21,000,000 (до запуска слэшинга 04.2025) до головы. Сигнатуры событий —
из src/contracts/interfaces/IAllocationManager.sol (main), topic0 offline-keccak.
OperatorSet — структ (address avs, uint32 id), в сигнатуре разворачивается
в (address,uint32).

Замер 2026-07-05 (см. docs/dir8_morpho_report.md): 15 OperatorSlashed за ~14 мес
на ВСЁМ мейннете; из них 2 теста «slash 10%», 10 — само-слэшинг EigenYields
(desc='👉 eigenyields.xyz/vaults'), 2 пустых desc + 1 'AlephAVS' (тот же кластер).
ВАЖНО: классификация 'promo-spam' — по описанию, экономически это НЕ пыль:
EigenYields через 100%-слэш своего оператора увёл ~28.8k LST (~$90-120M)
делегаторского стейка в свои воулты (redistribution-свип, форум-инцидент
t/14799). Adversarial-фолтов и challenger-выплат: 0. Контекст: 99 operator-sets
(20 AVS), slasher настроен у 21, redistribution у 14.

Usage:
    python3 -m analysis.dir8_slashing scan     # полный скан -> data/eigenlayer_slashing.json
    python3 -m analysis.dir8_slashing report   # декод, помесячно, классификация
"""
from __future__ import annotations

import datetime
import json
import os
import sys
from collections import Counter

from analysis.keccak import keccak256
from analysis.rpc import Rpc, get_logs_chunked

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
CACHE = os.path.join(DATA_DIR, "eigenlayer_slashing.json")

ALLOCATION_MANAGER = "0x948a420b8CC1d6BFd0B6087C2E7c344a2CD0bc39"
FROM_BLOCK = 21_000_000  # ~ноябрь 2024, до ELIP-002/слэшинга

SIGS = {
    "OperatorSlashed": b"OperatorSlashed(address,(address,uint32),address[],uint256[],string)",
    "OperatorSetCreated": b"OperatorSetCreated((address,uint32))",
    "SlasherUpdated": b"SlasherUpdated((address,uint32),address,uint32)",
    "RedistributionAddressSet": b"RedistributionAddressSet((address,uint32),address)",
}
TOPICS = {k: "0x" + keccak256(v).hex() for k, v in SIGS.items()}


# -- чистые функции (юнит-тестируемы офлайн) ------------------------------

def _word(data: str, i: int) -> str:
    return data[2 + 64 * i:2 + 64 * (i + 1)]


def decode_operator_slashed(data: str) -> dict:
    """OperatorSlashed(address operator, (address avs, uint32 id),
    address[] strategies, uint256[] wadSlashed, string description) — все поля
    в data (без indexed)."""
    off_strat = int(_word(data, 3), 16)
    off_wad = int(_word(data, 4), 16)
    off_desc = int(_word(data, 5), 16)
    n_wad = int(data[2 + off_wad * 2:2 + off_wad * 2 + 64], 16)
    wads = [int(data[2 + off_wad * 2 + 64 + 64 * i:2 + off_wad * 2 + 128 + 64 * i], 16) / 1e18
            for i in range(n_wad)]
    n_strat = int(data[2 + off_strat * 2:2 + off_strat * 2 + 64], 16)
    desc_len = int(data[2 + off_desc * 2:2 + off_desc * 2 + 64], 16)
    desc = bytes.fromhex(
        data[2 + off_desc * 2 + 64:2 + off_desc * 2 + 64 + desc_len * 2]).decode(errors="replace")
    return {"operator": "0x" + _word(data, 0)[24:],
            "avs": "0x" + _word(data, 1)[24:],
            "operator_set_id": int(_word(data, 2), 16),
            "n_strategies": n_strat, "wads": wads, "description": desc}


def classify_event(desc: str, wads: list[float]) -> str:
    """Грубая классификация: тест / промо-спам / неизвестно.
    Промо-спам = URL/эмодзи в description при 100%-само-слэше."""
    d = desc.lower()
    if "slash 10%" in d or d.strip() in ("test", "testing"):
        return "test"
    if (".xyz" in d or ".com" in d or "http" in d or "👉" in desc) and wads and min(wads) >= 0.99:
        return "promo-spam"
    if not desc.strip():
        return "no-description"
    return "other"


def month_key(ts: int) -> str:
    return datetime.datetime.fromtimestamp(ts, datetime.UTC).strftime("%Y-%m")


# -- сбор/отчёт ------------------------------------------------------------

def scan() -> None:
    rpc = Rpc()
    head = rpc.block_number()
    out = {"window": [FROM_BLOCK, head], "events": {}}
    for name, t0 in TOPICS.items():
        logs = get_logs_chunked(rpc, [ALLOCATION_MANAGER], [t0], FROM_BLOCK, head,
                                chunk=400_000)
        out["events"][name] = logs
        print(f"{name}: {len(logs)}", file=sys.stderr)
    # timestamps для slashed-событий
    for lg in out["events"]["OperatorSlashed"]:
        blk = rpc.get_block(int(lg["blockNumber"], 16))
        lg["timestamp"] = int(blk["timestamp"], 16)
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(CACHE, "w") as f:
        json.dump(out, f)
    print(f"cached -> {CACHE}")


def report() -> None:
    with open(CACHE) as f:
        data = json.load(f)
    evs = data["events"]
    slashed = evs["OperatorSlashed"]
    print(f"окно: блоки {data['window'][0]}..{data['window'][1]}")
    print(f"operator-sets создано: {len(evs['OperatorSetCreated'])}; "
          f"slasher настроен (SlasherUpdated): {len(evs['SlasherUpdated'])}; "
          f"redistribution настроен: {len(evs['RedistributionAddressSet'])}")
    print(f"\nOperatorSlashed ВСЕГО: {len(slashed)}")
    cls = Counter()
    monthly = Counter()
    for lg in slashed:
        d = decode_operator_slashed(lg["data"])
        c = classify_event(d["description"], d["wads"])
        cls[c] += 1
        monthly[month_key(lg["timestamp"])] += 1
        ts = datetime.datetime.fromtimestamp(lg["timestamp"], datetime.UTC).strftime("%Y-%m-%d")
        print(f"  {ts} avs={d['avs'][:12]} set={d['operator_set_id']} "
              f"op={d['operator'][:12]} wad={[round(w, 3) for w in d['wads'][:2]]} "
              f"[{c}] desc={d['description'][:50]!r}")
    print(f"\nклассификация: {dict(cls)}")
    print(f"помесячно: {dict(sorted(monthly.items()))}")
    real = len(slashed) - cls["test"] - cls["promo-spam"]
    print(f"\nВЕРХНЯЯ ГРАНИЦА реальных фолт-слэшингов за всю историю: {real} "
          f"(включая {cls['no-description']} без описания и {cls['other']} прочих)")


def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "report"
    {"scan": scan, "report": report}[cmd]()


if __name__ == "__main__":
    main()
