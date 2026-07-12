"""День-0 разведка Morpho Midnight на Base (Направление 8/B, план
docs/midnight_tooling_plan.md §2). Read-only.

Вопросы дня-0 (по убыванию решающести): (1) байткод соответствует тегу 2026-07-02?
(2) сколько рынков создано и с какими параметрами (maturity, гейты, оракулы, cursors)?
(3) доля liquidatorGate==0; (4) оракулы — есть ли SVR/Atom-маркер (kill §5);
(5) были ли Liquidate; (6) borrow-поток vs порог живости $10M; (7) активность
mempool/offer-флоу.

Usage:
    python3 -m analysis.midnight_day0 scan      # логи+код+стейт -> data/midnight_markets.json
    python3 -m analysis.midnight_day0 report    # таблица рынков + вердикт-вход §5
    python3 -m analysis.midnight_day0 code      # только байткод-хэши (быстро)

Байткод сравнивается отдельным шагом (см. docs/midnight_day0_report.md): локальная
сборка forge тега e6f2bf2 -> out/*.json deployedBytecode vs eth_getCode.
"""
from __future__ import annotations

import json
import os
import sys
from collections import Counter

from analysis.keccak import keccak256
from analysis.rpc import Rpc, get_logs_chunked

BASE_RPCS = [
    "https://mainnet.base.org",
    "https://base-rpc.publicnode.com",
    "https://base.drpc.org",
    "https://gateway.tenderly.co/public/base",
]

# [P] morpho-org/sdks packages/morpho-ts/src/addresses.ts @ main 2026-07-12 (sync-коммит
# 8baeac7 2026-07-07); блоки деплоя оттуда же (deployments-секция).
MIDNIGHT = "0xAdedD8ab6dE832766Fedf0FaC4992E5C4D3EA18A"
BUNDLES = "0x091183d729BE9f808c212b475E387A12E67850A7"   # деплой 48286997
MEMPOOL = "0xdD6DCE32e21f7b020898a8258dA37355b4017993"
EXTRA = {  # ратификаторы из того же реестра — сравниваем код заодно
    "ecrecoverRatifier": "0xd6e70365C8E8DDa9a4ca662C07bbE663b017755E",
    "ecrecoverAuthorizer": "0x292bEa9f1443d54E0E509120c919106765c6a493",
    "setterRatifier": "0x800B5F12A61B8198a5a6EfD794Cac6699B294d63",
}
DEPLOY_BLOCK = 48286884
CHUNK = 10_000        # публичные Base-RPC режут getLogs на ~10k блоков

WAD = 10 ** 18
TIME_TO_MAX_LIF = 3600  # ConstantsLib.TIME_TO_MAX_LIF = 60 minutes

# Сигнатуры из src/libraries/EventsLib.sol @ e6f2bf2 (topic0 ранее сверены
# offline-keccak двумя путями — docs/morpho_v2_mechanics.md).
SIG_MARKET_CREATED = ("MarketCreated((uint256,address,address,"
                      "(address,uint256,uint256,address)[],uint256,uint256,address,address),bytes32)")
SIG_LIQUIDATE = ("Liquidate(address,bytes32,address,uint256,uint256,address,bool,"
                 "address,address,uint256,uint256,uint256)")
# полная карта событий ядра — для сводки активности по topic0
EVENT_SIGS = {
    "Constructor": "Constructor(address)",
    "SetConfigurator": "SetConfigurator(address)",
    "SetFeeSetter": "SetFeeSetter(address)",
    "SetTickSpacingSetter": "SetTickSpacingSetter(address)",
    "EnableLltv": "EnableLltv(uint256)",
    "EnableLiquidationCursor": "EnableLiquidationCursor(uint256)",
    "SetMarketTickSpacing": "SetMarketTickSpacing(bytes32,uint256)",
    "SetMarketSettlementFee": "SetMarketSettlementFee(bytes32,uint256,uint256)",
    "SetDefaultSettlementFee": "SetDefaultSettlementFee(address,uint256,uint256)",
    "SetFeeClaimer": "SetFeeClaimer(address)",
    "SetMarketContinuousFee": "SetMarketContinuousFee(bytes32,uint256)",
    "SetDefaultContinuousFee": "SetDefaultContinuousFee(address,uint256)",
    "UpdatePosition": "UpdatePosition(bytes32,address,uint256,uint256,uint256)",
    "MarketCreated": SIG_MARKET_CREATED,
    "Take": ("Take(address,bytes32,bytes32,bool,address,bytes32,address,bytes,uint256,"
             "address,uint256,uint256,uint256,uint256,uint256,int256,address,address)"),
    "Withdraw": "Withdraw(address,bytes32,uint256,address,address,uint256)",
    "Repay": "Repay(address,bytes32,uint256,address,address)",
    "SupplyCollateral": "SupplyCollateral(address,bytes32,address,uint256,address)",
    "WithdrawCollateral": "WithdrawCollateral(address,bytes32,address,uint256,address,address)",
    "Liquidate": SIG_LIQUIDATE,
    "SetConsumed": "SetConsumed(address,bytes32,uint256,address)",
    "FlashLoan": "FlashLoan(address,address[],uint256[],address)",
    "SetIsAuthorized": "SetIsAuthorized(address,address,bool,address)",
    "ClaimContinuousFee": "ClaimContinuousFee(address,bytes32,uint256,address)",
    "ClaimSettlementFee": "ClaimSettlementFee(address,address,uint256,address)",
}

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
CACHE = os.path.join(DATA_DIR, "midnight_markets.json")

# Известные стейблы Base (для $-оценки totalUnits; всё прочее — репортим как unpriced)
STABLES = {
    "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913": ("USDC", 6),
    "0x60a3e35cc302bfa44cb288bc5a4f316fdb1adb42": ("EURC", 6),   # ~$1.1, считаем 1:1 консервативно
    "0xfde4c96c8593536e31f229ea8f37b2ada2699bb2": ("USDT", 6),
}


def topic0(sig: str) -> str:
    return "0x" + keccak256(sig.encode()).hex()


TOPIC_MARKET_CREATED = topic0(SIG_MARKET_CREATED)
TOPIC_LIQUIDATE = topic0(SIG_LIQUIDATE)
TOPIC_NAMES = {topic0(s): n for n, s in EVENT_SIGS.items()}

SEL_TOTAL_UNITS = "0x" + keccak256(b"totalUnits(bytes32)").hex()[:8]
SEL_PRICE = "0x" + keccak256(b"price()").hex()[:8]
SEL_DECIMALS = "0x" + keccak256(b"decimals()").hex()[:8]
SEL_SYMBOL = "0x" + keccak256(b"symbol()").hex()[:8]
SEL_DESCRIPTION = "0x" + keccak256(b"description()").hex()[:8]
SEL_TYPE_AND_VERSION = "0x" + keccak256(b"typeAndVersion()").hex()[:8]
# MorphoChainlinkOracleV2-геттеры (реюз Blue-экосистемы ожидаем) — читаем фиды насквозь
ORACLE_FEED_GETTERS = {
    "BASE_FEED_1": "0x" + keccak256(b"BASE_FEED_1()").hex()[:8],
    "BASE_FEED_2": "0x" + keccak256(b"BASE_FEED_2()").hex()[:8],
    "QUOTE_FEED_1": "0x" + keccak256(b"QUOTE_FEED_1()").hex()[:8],
    "QUOTE_FEED_2": "0x" + keccak256(b"QUOTE_FEED_2()").hex()[:8],
    "VAULT": "0x" + keccak256(b"VAULT()").hex()[:8],
}
# маркеры OEV-изъятия (kill-условие §5 плана): SVR-фиды Chainlink / RedStone Atom
OEV_MARKERS = ("svr", "atom", "redstone")


# ---------------------------------------------------------------- pure decode

def _words(data_hex: str) -> str:
    return data_hex[2:] if data_hex.startswith("0x") else data_hex


def _w(d: str, i: int) -> str:
    return d[i * 64:(i + 1) * 64]


def _addr(word: str) -> str:
    return ("0x" + word[24:]).lower()


def _u(word: str) -> int:
    return int(word, 16)


def max_lif(lltv: int, cursor: int) -> int:
    """ConstantsLib.maxLif: WAD*WAD / (WAD - cursor*(WAD-lltv)/WAD), mulDivDown."""
    return WAD * WAD // (WAD - cursor * (WAD - lltv) // WAD)


def lif_at(max_lif_wad: int, dt_sec: int) -> int:
    """Post-maturity рамп: lif(t) = min(maxLif, WAD + (maxLif-WAD)*dt/3600) [Midnight.sol:686]."""
    if dt_sec <= 0:
        return WAD
    return min(max_lif_wad, WAD + (max_lif_wad - WAD) * dt_sec // TIME_TO_MAX_LIF)


def decode_market_created(log: dict) -> dict:
    """MarketCreated(Market market, bytes32 indexed id_): data = ABI-кортеж Market
    (динамический из-за CollateralParams[])."""
    d = _words(log["data"])
    # word0 = offset до кортежа Market (обычно 0x20)
    t = _u(_w(d, 0)) * 2  # смещение в hex-символах
    tup = d[t:]
    market = {
        "chainId": _u(_w(tup, 0)),
        "midnight": _addr(_w(tup, 1)),
        "loanToken": _addr(_w(tup, 2)),
        "maturity": _u(_w(tup, 4)),
        "rcfThreshold": _u(_w(tup, 5)),
        "enterGate": _addr(_w(tup, 6)),
        "liquidatorGate": _addr(_w(tup, 7)),
    }
    arr_off = _u(_w(tup, 3)) * 2  # относительно начала кортежа
    arr = tup[arr_off:]
    n = _u(_w(arr, 0))
    cps = []
    for i in range(n):
        base = 1 + i * 4
        lltv = _u(_w(arr, base + 1))
        cursor = _u(_w(arr, base + 2))
        cps.append({
            "token": _addr(_w(arr, base)),
            "lltv": lltv,
            "liquidationCursor": cursor,
            "oracle": _addr(_w(arr, base + 3)),
            "maxLif": max_lif(lltv, cursor) if lltv < WAD or cursor < WAD else None,
        })
    market["collateralParams"] = cps
    market["id"] = log["topics"][1]
    market["block"] = int(log["blockNumber"], 16)
    return market


def decode_liquidate(log: dict) -> dict:
    """indexed: id_, collateral, borrower; data: caller, seized, repaidUnits,
    postMaturityMode, receiver, payer, badDebt, lossFactor, feeCredit."""
    d = _words(log["data"])
    return {
        "block": int(log["blockNumber"], 16),
        "tx": log.get("transactionHash"),
        "id": log["topics"][1],
        "collateral": ("0x" + log["topics"][2][26:]).lower(),
        "borrower": ("0x" + log["topics"][3][26:]).lower(),
        "caller": _addr(_w(d, 0)),
        "seizedAssets": _u(_w(d, 1)),
        "repaidUnits": _u(_w(d, 2)),
        "postMaturityMode": bool(_u(_w(d, 3))),
        "badDebt": _u(_w(d, 6)),
    }


def decode_string_result(hexres: str) -> str | None:
    """Результат eth_call для string-возврата (offset,len,bytes); None если не строка."""
    d = _words(hexres or "0x")
    if len(d) < 128:
        return None
    try:
        off = _u(_w(d, 0)) * 2
        ln = _u(d[off:off + 64]) * 2
        raw = bytes.fromhex(d[off + 64:off + 64 + ln])
        return raw.decode("utf-8", errors="replace")
    except (ValueError, IndexError):
        return None


# ---------------------------------------------------------------- on-chain probes

def probe_str(rpc: Rpc, addr: str, selector: str) -> str | None:
    try:
        return decode_string_result(rpc.eth_call(addr, selector))
    except Exception:
        return None


def probe_u256(rpc: Rpc, addr: str, selector: str) -> int | None:
    try:
        r = rpc.eth_call(addr, selector)
        return int(r, 16) if r and r != "0x" else None
    except Exception:
        return None


def probe_oracle(rpc: Rpc, oracle: str) -> dict:
    """Тип фида best-effort: price()-liveness, self-описания, фиды-компоненты
    MorphoChainlinkOracleV2 (и их описания). Флаг OEV по маркер-строкам."""
    out = {"address": oracle, "code_bytes": 0, "price": None, "descriptions": [], "oev_flag": False}
    try:
        code = rpc.get_code(oracle)
        out["code_bytes"] = max(0, (len(code) - 2) // 2)
    except Exception as e:
        out["error"] = f"getCode: {e}"
        return out
    if out["code_bytes"] == 0:
        return out
    out["price"] = probe_u256(rpc, oracle, SEL_PRICE)
    for label, sel in [("self.description", SEL_DESCRIPTION),
                       ("self.typeAndVersion", SEL_TYPE_AND_VERSION)]:
        s = probe_str(rpc, oracle, sel)
        if s:
            out["descriptions"].append(f"{label}={s}")
    for name, sel in ORACLE_FEED_GETTERS.items():
        v = probe_u256(rpc, oracle, sel)
        if v is None or v == 0:
            continue
        feed = "0x" + hex(v)[2:].rjust(64, "0")[24:]
        entry = f"{name}={feed}"
        for sel2, lab in [(SEL_DESCRIPTION, "desc"), (SEL_TYPE_AND_VERSION, "tv")]:
            s = probe_str(rpc, feed, sel2)
            if s:
                entry += f" {lab}='{s}'"
        out["descriptions"].append(entry)
    joined = " ".join(out["descriptions"]).lower()
    out["oev_flag"] = any(m in joined for m in OEV_MARKERS)
    return out


def erc20_meta(rpc: Rpc, token: str) -> dict:
    dec = probe_u256(rpc, token, SEL_DECIMALS)
    sym = probe_str(rpc, token, SEL_SYMBOL)
    if sym is None:  # bytes32-symbol (MKR-стиль)
        try:
            raw = rpc.eth_call(token, SEL_SYMBOL)
            b = bytes.fromhex(_words(raw))[:32].rstrip(b"\0")
            sym = b.decode("utf-8", errors="replace") or None
        except Exception:
            sym = None
    return {"symbol": sym, "decimals": dec}


# ---------------------------------------------------------------- scan / report

def scan() -> None:
    rpc = Rpc(BASE_RPCS)
    head = rpc.block_number()
    print(f"Base head {head}; сканирую {MIDNIGHT} c {DEPLOY_BLOCK} ({head - DEPLOY_BLOCK} блоков)")

    def prog(hi, to, n):
        if hi % 200_000 < CHUNK:
            print(f"  ...{hi}/{to} ({n} логов)")

    # ВСЕ логи ядра одним проходом (фильтр по topic0 — локально): даёт и рынки,
    # и ликвидации, и полную карту активности.
    logs = get_logs_chunked(rpc, MIDNIGHT, None, DEPLOY_BLOCK, head, chunk=CHUNK, on_progress=prog)
    mempool_logs = get_logs_chunked(rpc, MEMPOOL, None, DEPLOY_BLOCK, head, chunk=CHUNK)
    bundles_logs = get_logs_chunked(rpc, BUNDLES, None, DEPLOY_BLOCK, head, chunk=CHUNK)

    activity = Counter(TOPIC_NAMES.get(lg["topics"][0], lg["topics"][0]) for lg in logs if lg["topics"])
    markets = [decode_market_created(lg) for lg in logs
               if lg["topics"] and lg["topics"][0] == TOPIC_MARKET_CREATED]
    liqs = [decode_liquidate(lg) for lg in logs
            if lg["topics"] and lg["topics"][0] == TOPIC_LIQUIDATE]

    tokens, oracles = {}, {}
    for m in markets:
        m["totalUnits"] = probe_u256(
            rpc, MIDNIGHT, SEL_TOTAL_UNITS + m["id"][2:])
        for t in {m["loanToken"], *(cp["token"] for cp in m["collateralParams"])}:
            if t not in tokens:
                tokens[t] = erc20_meta(rpc, t)
        for cp in m["collateralParams"]:
            if cp["oracle"] not in oracles:
                oracles[cp["oracle"]] = probe_oracle(rpc, cp["oracle"])

    code_hashes = {}
    for name, addr in [("midnight", MIDNIGHT), ("midnightBundles", BUNDLES),
                       ("midnightMempool", MEMPOOL), *EXTRA.items()]:
        code = rpc.get_code(addr)
        b = bytes.fromhex(code[2:])
        code_hashes[name] = {"address": addr, "size": len(b),
                             "keccak256": "0x" + keccak256(b).hex()}

    os.makedirs(DATA_DIR, exist_ok=True)
    json.dump({"head": head, "deploy_block": DEPLOY_BLOCK,
               "scanned_at_utc_block": head,
               "activity": dict(activity), "markets": markets, "liquidates": liqs,
               "tokens": tokens, "oracles": oracles, "code": code_hashes,
               "mempool_logs": len(mempool_logs), "bundles_logs": len(bundles_logs),
               "raw_log_count": len(logs)},
              open(CACHE, "w"), indent=1)
    print(f"кэш: {len(markets)} рынков, {len(liqs)} Liquidate, "
          f"{len(logs)} логов ядра, mempool={len(mempool_logs)} -> {CACHE}")


def code() -> None:
    rpc = Rpc(BASE_RPCS)
    for name, addr in [("midnight", MIDNIGHT), ("midnightBundles", BUNDLES),
                       ("midnightMempool", MEMPOOL), *EXTRA.items()]:
        c = rpc.get_code(addr)
        b = bytes.fromhex(c[2:])
        print(f"{name:<22}{addr} {len(b):>7}B keccak {'0x' + keccak256(b).hex()}")


def _fmt_units(m: dict, tokens: dict) -> str:
    tu = m.get("totalUnits")
    if tu is None:
        return "?"
    meta = tokens.get(m["loanToken"], {})
    dec = meta.get("decimals")
    if dec is None:
        return str(tu)
    val = tu / 10 ** dec
    stable = m["loanToken"] in STABLES
    return f"{val:,.2f} {meta.get('symbol') or m['loanToken'][:8]}" + (" (~$)" if stable else "")


def report() -> None:
    blob = json.load(open(CACHE))
    markets, tokens, oracles = blob["markets"], blob["tokens"], blob["oracles"]
    liqs = blob["liquidates"]
    head = blob["head"]
    print(f"=== Midnight/Base день-0: head {head} (деплой {DEPLOY_BLOCK}) ===")
    print(f"активность ядра по событиям: {blob['activity'] or 'ПУСТО'}")
    print(f"mempool-логи: {blob['mempool_logs']}; bundles-логи: {blob['bundles_logs']}\n")

    n = len(markets)
    gate0 = [m for m in markets if int(m["liquidatorGate"], 16) == 0]
    print(f"рынков (MarketCreated): {n}; liquidatorGate==0: {len(gate0)}"
          f" ({len(gate0) / n:.0%})" if n else "рынков (MarketCreated): 0")
    for m in markets:
        lt = tokens.get(m["loanToken"], {})
        print(f"\n id {m['id'][:18]}… блок {m['block']}")
        print(f"   loan {lt.get('symbol') or m['loanToken']}  maturity {m['maturity']}"
              f"  rcfThreshold {m['rcfThreshold']}")
        print(f"   enterGate {m['enterGate']}  liquidatorGate {m['liquidatorGate']}"
              f"  totalUnits {_fmt_units(m, tokens)}")
        for cp in m["collateralParams"]:
            ct = tokens.get(cp["token"], {})
            ml = cp["maxLif"]
            orc = oracles.get(cp["oracle"], {})
            print(f"   coll {ct.get('symbol') or cp['token']:<10} lltv {cp['lltv'] / WAD:.3f}"
                  f" cursor {cp['liquidationCursor'] / WAD:.3f}"
                  f" maxLif {ml / WAD:.4f} (бонус {100 * (ml - WAD) / WAD:.2f}%)"
                  f" oracle {cp['oracle'][:10]}… oev={'⚠️ YES' if orc.get('oev_flag') else 'no'}")
            for s in orc.get("descriptions", []):
                print(f"        {s}")

    print(f"\nLiquidate-событий: {len(liqs)}"
          + (f"; post-maturity: {sum(1 for x in liqs if x['postMaturityMode'])}" if liqs else ""))

    # $-оценка borrow (только стейбл-loanToken; прочее — unpriced, честно флажим)
    usd = unpriced = 0
    for m in markets:
        tu, dec = m.get("totalUnits"), tokens.get(m["loanToken"], {}).get("decimals")
        if tu is None or dec is None:
            continue
        if m["loanToken"] in STABLES:
            usd += tu / 10 ** dec
        elif tu:
            unpriced += 1
    print(f"суммарный borrow (стейбл-рынки, units≈$ к maturity): ${usd:,.0f}"
          f"{f' + {unpriced} рынков c non-stable loanToken (unpriced)' if unpriced else ''}")
    print(f"порог живости $10M (pre-reg dir8): {'ПРОЙДЕН' if usd >= 10_000_000 else 'НЕ пройден'}")

    # входы вердикта §5 (сам вердикт — в docs/midnight_day0_report.md)
    shadow = [m for m in gate0 if (m.get("totalUnits") or 0) > 0]
    oev_hit = [o for o in oracles.values() if o.get("oev_flag")]
    print(f"\n§5-входы: рынков gate==0 с borrow>0: {len(shadow)} "
          f"(активация shadow {'ДА' if shadow else 'нет'}); "
          f"OEV-маркер на оракулах: {'⚠️ ' + str(len(oev_hit)) if oev_hit else 'нет'}")


def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "report"
    {"scan": scan, "report": report, "code": code}.get(cmd, lambda: print(__doc__))()


if __name__ == "__main__":
    main()
