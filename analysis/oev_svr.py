"""Gate 0 (Направление 7, OEV-сёрчер): он-чейн реконструкция выигранных SVR-аукционов
Chainlink на Ethereum mainnet. Read-only. Критерии — docs/oev_gate0_criteria.md
(коммит b277aa2), карта механики — docs/oev_mechanics_map.md.

Метод (фингерпринт верифицирован на живом блоке 25430607 ДО скана):
выигранный SVR-аукцион = в одном блоке (1) tx с событием SecondaryRoundIdUpdated
на DualAggregator SVR-фида (topic0 offline-keccak), (2) СЛЕДУЮЩАЯ по индексу tx
с Aave LiquidationCall (topic0 offline-keccak) — бандл сёрчера, (3) рефанд-tx
builder(=miner) → SVR fee Safe 0x149b…14e5, value = ~90% бида сёрчера (MEV-Share
refund; далее «бид» = наблюдаемый рефанд). Невыигранные апдейты (catch-up)
падают без смежной ликвидации — отсекаются правилом смежности.

Адреса: 19 DualAggregator'ов — reference-data-directory.vercel.app/feeds-mainnet.json,
кросс-сверены с bgd-labs/aave-address-book (разведка 2026-07-05); пулы и оракулы
Aave — из aave-address-book (Core + Prime/Lido).

Gross-бонус ликвидации: цены обоих активов через AaveOracle.getAssetPrice
(USD, 8 дес.) архивным eth_call на блоке события; decimals() токенов кэшируются.
margin = (collUSD − debtUSD) − бид − газ.

Usage:
    python3 -m analysis.oev_svr scan            # 12 мес: логи+джойн+блоки+цены -> кэш
    python3 -m analysis.oev_svr report          # M1-M5 по критериям
"""
from __future__ import annotations

import gzip
import json
import os
import sys
from collections import defaultdict

from analysis.keccak import keccak256
from analysis.oev_api3 import (concentration, margin_stats, monthly_rows,
                               percentile, qualified_entrants, tx_aggregate)
from analysis.rpc import Rpc, get_logs_chunked

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
CACHE = os.path.join(DATA_DIR, "svr_liquidations.json.gz")

# DualAggregator'ы SVR-фидов Ethereum (path -> address), снимок 2026-07-05
DUAL_AGGREGATORS = {
    "btc-usd-svr": "0xdc715c751f1cc129A6b47fEDC87D9918a4580502",
    "eth-usd-svr": "0x7c7FdFCa295a787ded12Bb5c1A49A8D2cC20E3F8",
    "link-usd-svr": "0x64c67984A458513C6BAb23a815916B1b1075cf3a",
    "aave-usd-svr": "0xcd07B31D85756098334eDdC92DE755dEae8FE62f",
    "usdc-usd-svr": "0xe13fafe4FB769e0f4a1cB69D35D21EF99188EFf7",
    "usdt-usd-svr": "0x9df238BE059572d7211F1a1a5fEe609F979AAD2d",
    "btc-usd-shared-svr": "0x6f3F8d82694d52E6B6171A7b26A88c9554e7999b",
    "eth-usd-shared-svr": "0xad88fc1A810379Ef4EFbF2D97EdE57e306178e5a",
    "link-usd-shared-svr": "0x4F3EBf190f8889734424aE71Ac0B00e1A8013f3C",
    "comp-usd-shared-svr": "0x458138Fc0D67027E9A6778ef40a6ffC318c69061",
    "usdc-usd-shared-svr": "0x7d06199061Da586dAFc5D18fd1AeeAf18ae7593b",
    "usdt-usd-shared-svr": "0x757EB2AF32c76621FEAE483c6458C04ba19906Ba",
    "tslaon-usd-shared-svr": "0x95dC7c293ad1706C80bCde068B609CA61B3FF78C",
    "tslaon-usd-calculated-kalman-shared-svr": "0x9F6B06e826d3DF391285c695749F8f921F6972D9",
    "spyon-usd-shared-svr": "0x9dDb5fBA9A737860c7ccEd0D9177Af56AB16c183",
    "spyon-usd-calculated-kalman-shared-svr": "0x2053257478bA1FeDF7F99dEF0C412006753aC9Bf",
    "qqqon-usd-shared-svr": "0xe660B4DC23430BdF2eC30b961fcAf6CCac8276a3",
    "qqqon-usd-calculated-kalman-shared-svr": "0x320E22c489e4bb634aC1aa5822543014A6fbB292",
    "flhyon-usd-calculated-shared-svr": "0xc1E84F39413eb3641a208dcf784Ab477a6e1336F",
}

POOLS = {  # aave-address-book
    "0x87870bca3f3fd6335c3f4ce8392d69350b4fa4e2": "core",
    "0x4e033931ad43597d96d6bcc25c280717730b58b1": "prime",
}
ORACLES = {"core": "0x54586bE62E3c3580375aE3723C145253060Ca0C2",
           "prime": "0xE3C061981870C0C7b1f3C4F4bB36B95f1F260BE6"}
SVR_FEE_SAFE = "0x149b41b1e4c00b5f9aa34b14fd9f84cfd2f014e5"

TOPIC_UPD = "0x" + keccak256(b"SecondaryRoundIdUpdated(uint32)").hex()
TOPIC_LIQ = "0x" + keccak256(
    b"LiquidationCall(address,address,address,uint256,uint256,address,bool)").hex()
# селекторы — offline-keccak, не руками
SEL_GET_ASSET_PRICE = "0x" + keccak256(b"getAssetPrice(address)").hex()[:8]
SEL_DECIMALS = "0x" + keccak256(b"decimals()").hex()[:8]
SEL_LATEST_ROUND_DATA = "0x" + keccak256(b"latestRoundData()").hex()[:8]

BLOCKS_12MO = 2_628_000  # ~12 с/блок


# -- чистые функции (юнит-тестируемы офлайн) ------------------------------

def join_adjacent(upd_logs: list[dict], liq_logs: list[dict]) -> list[dict]:
    """Смежность в блоке: ликвидационная tx идёт сразу за update-tx.
    Возвращает по одной записи на ликвидационную TX (событий может быть >1)."""
    upd_idx = defaultdict(set)
    for lg in upd_logs:
        upd_idx[int(lg["blockNumber"], 16)].add(int(lg["transactionIndex"], 16))
    by_tx: dict[tuple, dict] = {}
    for lg in liq_logs:
        bn = int(lg["blockNumber"], 16)
        ti = int(lg["transactionIndex"], 16)
        if ti - 1 not in upd_idx.get(bn, ()):  # noqa: SIM201 — set lookup
            continue
        key = (bn, lg["transactionHash"])
        rec = by_tx.setdefault(key, {"block": bn, "txHash": lg["transactionHash"],
                                     "txIndex": ti, "pool": POOLS[lg["address"].lower()],
                                     "calls": []})
        rec["calls"].append(decode_liq_call(lg))
    return sorted(by_tx.values(), key=lambda r: (r["block"], r["txIndex"]))


def decode_liq_call(lg: dict) -> dict:
    """LiquidationCall: topics = collateralAsset, debtAsset, user;
    data = debtToCover, liquidatedCollateralAmount, liquidator, receiveAToken."""
    d = lg["data"][2:]
    return {"collateralAsset": "0x" + lg["topics"][1][-40:],
            "debtAsset": "0x" + lg["topics"][2][-40:],
            "debtToCover": int(d[0:64], 16),
            "liquidatedCollateral": int(d[64:128], 16),
            "liquidator": "0x" + d[128 + 24:192],
            }


def find_refund(block_txs: list[dict], miner: str, liq_tx_index: int) -> float | None:
    """Рефанд билдера в SVR fee Safe в том же блоке ПОСЛЕ ликвидационной tx.
    Возвращает value в ETH (наблюдаемый бид) или None."""
    for tx in block_txs:
        if (int(tx["transactionIndex"], 16) > liq_tx_index
                and (tx.get("to") or "").lower() == SVR_FEE_SAFE
                and tx["from"].lower() == miner.lower()):
            return int(tx["value"], 16) / 1e18
    return None


def usd_amount(raw: int, decimals: int, price_8dec: int) -> float:
    return raw / (10 ** decimals) * (price_8dec / 1e8)


def event_gross_usd(calls: list[dict], prices: dict, decimals: dict) -> float:
    """Gross-бонус tx: sum(collateralUSD - debtUSD) по всем LiquidationCall в tx."""
    g = 0.0
    for c in calls:
        coll, debt = c["collateralAsset"].lower(), c["debtAsset"].lower()
        g += (usd_amount(c["liquidatedCollateral"], decimals[coll], prices[coll])
              - usd_amount(c["debtToCover"], decimals[debt], prices[debt]))
    return g


def to_common_schema(events: list[dict], eth_usd_by_ev: list[float]) -> list[dict]:
    """Приведение SVR-строк к схеме oev_api3 (для реюза M1/M2/M3-функций).
    sender = liquidator-контракт первой LiquidationCall (стабильнее EOA)."""
    out = []
    for ev, ethusd in zip(events, eth_usd_by_ev):
        bid_usd = (ev.get("bid_eth") or 0.0) * ethusd
        gas_usd = (ev.get("gas_eth") or 0.0) * ethusd
        out.append({"txHash": ev["txHash"], "blockTimestamp": ev["timestamp"],
                    "sender": ev["calls"][0]["liquidator"],
                    "type": "OEV",
                    "incentiveUsd": str(int(max(ev.get("gross_usd") or 0.0, 0) * 1e18)),
                    "bidAmountUsd": str(int(bid_usd * 1e18)),
                    "gasCostUsd": str(int(gas_usd * 1e18)),
                    "tx_from": ev.get("tx_from"), "pool": ev["pool"]})
    return out


# -- сбор -------------------------------------------------------------------

def scan() -> None:
    blocks = int(sys.argv[2]) if len(sys.argv) > 2 else BLOCKS_12MO
    rpc = Rpc()
    head = rpc.block_number()
    lo = head - blocks
    print(f"window: {lo}..{head}", file=sys.stderr)

    def prog(tag):
        def f(done, total, n):
            print(f"  {tag}: block {done}/{total}, logs {n}", file=sys.stderr)
        return f

    upd = get_logs_chunked(rpc, list(DUAL_AGGREGATORS.values()), [TOPIC_UPD],
                           lo, head, chunk=50_000, on_progress=prog("upd"))
    liq = get_logs_chunked(rpc, [a for a in POOLS], [TOPIC_LIQ],
                           lo, head, chunk=50_000, on_progress=prog("liq"))
    print(f"update logs: {len(upd)}, liquidation logs: {len(liq)}", file=sys.stderr)
    events = join_adjacent(upd, liq)
    print(f"SVR-joined liquidation txs: {len(events)}", file=sys.stderr)

    # обогащение: блок (timestamp, refund, from), receipt (газ)
    for i, ev in enumerate(events):
        blk = rpc.get_block(ev["block"], full=True)
        ev["timestamp"] = int(blk["timestamp"], 16)
        ev["bid_eth"] = find_refund(blk["transactions"], blk["miner"], ev["txIndex"])
        tx = next(t for t in blk["transactions"]
                  if t["hash"].lower() == ev["txHash"].lower())
        ev["tx_from"] = tx["from"].lower()
        rec = rpc.receipt(ev["txHash"])
        ev["gas_eth"] = int(rec["gasUsed"], 16) * int(rec["effectiveGasPrice"], 16) / 1e18
        if i % 100 == 0:
            print(f"  enrich {i}/{len(events)}", file=sys.stderr)

    # цены активов на блоке события через AaveOracle (архивный eth_call) + decimals
    dec_cache: dict[str, int] = {}
    eth_feed = "0x5424384B256154046E9667dDFaaa5e550145215e"  # ETH/USD SVR proxy (для конверсии бида)
    for i, ev in enumerate(events):
        oracle = ORACLES[ev["pool"]]
        tag = hex(ev["block"])
        prices, decs = {}, {}
        try:
            for c in ev["calls"]:
                for a in (c["collateralAsset"].lower(), c["debtAsset"].lower()):
                    if a not in prices:
                        r = rpc.eth_call(oracle, SEL_GET_ASSET_PRICE + a[2:].rjust(64, "0"), tag)
                        prices[a] = int(r, 16)
                    if a not in dec_cache:
                        rd = rpc.eth_call(a, SEL_DECIMALS)
                        dec_cache[a] = int(rd, 16)
                    decs[a] = dec_cache[a]
            ev["gross_usd"] = event_gross_usd(ev["calls"], prices, decs)
        except Exception as e:  # архив может не отдать старый стейт — фиксируем
            ev["gross_usd"] = None
            ev["price_error"] = str(e)[:80]
        try:
            r = rpc.eth_call(eth_feed, SEL_LATEST_ROUND_DATA, tag)
            ev["eth_usd"] = int(r[2 + 64:2 + 128], 16) / 1e8
        except Exception:
            ev["eth_usd"] = None
        if i % 100 == 0:
            print(f"  price {i}/{len(events)}", file=sys.stderr)

    os.makedirs(DATA_DIR, exist_ok=True)
    with gzip.open(CACHE, "wt") as f:
        json.dump({"window": [lo, head], "events": events}, f)
    print(f"cached {len(events)} SVR liquidation txs -> {CACHE}")


def _load() -> dict:
    with gzip.open(CACHE, "rt") as f:
        return json.load(f)


def report() -> None:
    data = _load()
    events = data["events"]
    priced = [e for e in events if e.get("gross_usd") is not None and e.get("eth_usd")]
    with_bid = [e for e in events if e.get("bid_eth") is not None]
    print(f"SVR liquidation txs (12-мес окно {data['window']}): {len(events)}")
    print(f"  с найденным рефандом (бид читаем): {len(with_bid)} ({len(with_bid)/len(events):.0%})")
    print(f"  с восстановленным gross (цены на блоке): {len(priced)} ({len(priced)/len(events):.0%})")
    by_pool = defaultdict(int)
    for e in events:
        by_pool[e["pool"]] += 1
    print(f"  по пулам: {dict(by_pool)}")

    rows = to_common_schema(priced, [e["eth_usd"] for e in priced])
    end_ts = max(e["blockTimestamp"] for e in rows)

    print("\n== M1 концентрация (liquidator-контракт; priced-подвыборка) ==")
    c = concentration(rows)
    print(f"  сущностей={c['senders']} n={c['n_total']} gross=${c['usd_total']:,.0f}")
    print(f"  top-1: {c['top1_n']:.1%} по числу / {c['top1_usd']:.1%} по $")
    print(f"  top-3: {c['top3_n']:.1%} / {c['top3_usd']:.1%}")
    print(f"  top-10: {c['top10_n']:.1%} / {c['top10_usd']:.1%}   HHI($)={c['hhi_usd']:,.0f}")
    for a, u, n in c["top_usd"]:
        print(f"    {a} ${u:>12,.2f} n={n}")
    rows_eoa = [dict(r, sender=r["tx_from"]) for r in rows if r.get("tx_from")]
    c2 = concentration(rows_eoa)
    print(f"  (по EOA tx.from: сущностей={c2['senders']}, top-3 {c2['top3_usd']:.1%} по $)")

    print("\n== M1 последние 6 мес ==")
    rows6 = [r for r in rows if end_ts - r["blockTimestamp"] <= 180 * 86400]
    c6 = concentration(rows6)
    print(f"  сущностей={c6['senders']} n={c6['n_total']} gross=${c6['usd_total']:,.0f}"
          f" top-3 {c6['top3_n']:.1%}/{c6['top3_usd']:.1%} HHI={c6['hhi_usd']:,.0f}")

    print("\n== M2 контестируемость (порог 5%, 3-мес окно; внимание: окно данных 12 мес =")
    print("   'первая победа за 12 мес' совпадает с окном — трактовать консервативно) ==")
    q = qualified_entrants(rows, end_ts)
    print(f"  адресов с первой победой в окне: {q['entrants_total']};"
          f" квалифицированных ≥5%: {len(q['qualified'])}")
    for s, v in sorted(q["qualified"].items(), key=lambda kv: -kv[1])[:15]:
        print(f"    {s} best-3мес-доля={v:.1%} first_ts={q['first_win'][s]}")

    print("\n== M3 остаточная маржа (gross − бид − газ; priced-подвыборка) ==")
    m = margin_stats(rows)
    print(f"  n={m['n']} total=${m['total']:,.0f} median=${m['median']:,.2f}"
          f" p25=${m['p25']:,.2f} p75=${m['p75']:,.2f}")
    print(f"  отрицательных: {m['negative_share']:.1%}; топ-5 событий = {m['top5_share']:.1%} суммы")
    gross = sum(int(r["incentiveUsd"]) / 1e18 for r in rows)
    bids = sum(int(r["bidAmountUsd"]) / 1e18 for r in rows)
    gas = sum(int(r["gasCostUsd"]) / 1e18 for r in rows)
    print(f"  pooled: gross=${gross:,.0f} bids=${bids:,.0f} gas=${gas:,.0f}"
          f" recapture={bids / gross:.1%}")

    print("\n== M3/M4 помесячно (30-дн окна) ==")
    print(f"{'мес назад':>9} {'n':>5} {'gross$':>12} {'bids$':>11} {'маржа$':>11} {'медиана$':>9} {'сущн.':>6}")
    for r in monthly_rows(rows, end_ts):
        print(f"{r['months_ago']:>9} {r['n']:>5} {r['gross']:>12,.0f} {r['bids']:>11,.0f}"
              f" {r['margin_total']:>11,.0f} {r['margin_median']:>9,.2f} {r['senders']:>6}")

    print("\n== M5 санити ==")
    bids_all_eth = sum(e["bid_eth"] for e in with_bid)
    print(f"  сумма наблюдаемых рефандов за окно: {bids_all_eth:,.2f} ETH"
          f" (сверить с внешним якорем в отчёте; рефанд ≈ 90% бида, т.е. х1.11 для полного)")
    no_bid = [e for e in events if e.get("bid_eth") is None]
    print(f"  событий без найденного рефанда: {len(no_bid)} — вероятные не-SVR совпадения"
          f" или иной путь рефанда; исключены из recapture, включены в счёт потока")


def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "report"
    {"scan": scan, "report": report}[cmd]()


if __name__ == "__main__":
    main()
