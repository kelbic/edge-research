"""Gate 1: Nexus Mutual repricing latency — how fast do pool managers move targetPrice
after public risk events? See docs/gate1_repricing_architecture.md.

Usage:
    python3 -m analysis.nexus_repricing scan     # fetch/extend log cache (read-only RPC)
    python3 -m analysis.nexus_repricing report   # global stats + incident case studies

Scan is address-wide with NO topic filter (proxy upgrades may have emitted other event
shapes over the years); decode groups by topic0 and reports unknown ones loudly.
"""
from __future__ import annotations

import json
import os
import statistics
import sys
from datetime import datetime, timezone

from analysis.keccak import keccak256
from analysis.rpc import Rpc, get_logs_chunked

STAKING_PRODUCTS = "0xcafea573fBd815B5f59e8049E71E554bde3477E4"  # verified 2x, see docs
V2_START_BLOCK = 16_700_000  # ~2023-02-22, before V2 launch (first pools Mar 2023)

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
LOGS_CACHE = os.path.join(DATA_DIR, "staking_products_logs.json")
PRODUCTS_FILE = os.path.join(DATA_DIR, "nexus_products.json")

SIG_PRODUCT_UPDATED = "ProductUpdated(uint256,uint8,uint96)"


def topic0(signature: str) -> str:
    return "0x" + keccak256(signature.encode()).hex()


TOPIC_PRODUCT_UPDATED = topic0(SIG_PRODUCT_UPDATED)

# T0 = first public knowledge of the incident, UTC (public reporting; ±hours is fine
# against the >48h/<12h criterion fixed in the architecture doc).
INCIDENTS = [
    # (name, T0 iso-utc, product-name keywords, notes)
    ("Euler hack", "2023-03-13T09:00:00+00:00", ["euler"], "$197M, first attack tx ~08:5x UTC"),
    ("Curve/Vyper reentrancy", "2023-07-30T14:00:00+00:00", ["curve"], "pools drained through the day"),
    ("KyberSwap hack", "2023-11-22T23:00:00+00:00", ["kyber"], "$48M evening UTC"),
    ("Bybit hack", "2025-02-21T15:00:00+00:00", ["bybit"], "$1.4B custody, afternoon UTC"),
    ("Arcadia hack", "2025-07-15T02:00:00+00:00", ["arcadia"], "$3.5M on Base; Nexus paid ~$250K"),
    ("Balancer v2 hack", "2025-11-03T08:00:00+00:00", ["balancer"], "$116M morning UTC"),
    ("Stream Finance loss", "2025-11-04T18:00:00+00:00", ["stream", "xusd"], "$93M disclosed; Nexus paid ~$95K"),
]


# ---------------------------------------------------------------- pure functions

def decode_product_updated(log: dict) -> dict | None:
    """ProductUpdated has no indexed params: data = productId, targetWeight, targetPrice."""
    if (log.get("topic0") or (log.get("topics") or [None])[0]) != TOPIC_PRODUCT_UPDATED:
        return None
    data = log["data"][2:] if log["data"].startswith("0x") else log["data"]
    if len(data) < 3 * 64:
        return None
    return {
        "bn": log["bn"],
        "ts": log["ts"],
        "tx": log["tx"],
        "productId": int(data[0:64], 16),
        "targetWeight": int(data[64:128], 16),
        "targetPrice": int(data[128:192], 16),  # basis points, TARGET_PRICE_DENOMINATOR=10000
    }


def bucket_by_topic0(logs: list[dict]) -> dict[str, int]:
    out: dict[str, int] = {}
    for lg in logs:
        t0 = lg.get("topic0") or (lg.get("topics") or ["<none>"])[0]
        out[t0] = out.get(t0, 0) + 1
    return out


def per_product_series(events: list[dict]) -> dict[int, list[dict]]:
    """Group decoded events by productId, sorted by timestamp."""
    out: dict[int, list[dict]] = {}
    for ev in events:
        out.setdefault(ev["productId"], []).append(ev)
    for evs in out.values():
        evs.sort(key=lambda e: e["ts"])
    return out


def real_changes(series: list[dict]) -> list[dict]:
    """Drop consecutive no-op events (same weight AND price) and the initialization event:
    the first event for a product is the listing itself, not a manager reaction."""
    out = []
    prev = None
    for ev in series:
        if prev is not None and (ev["targetPrice"] != prev["targetPrice"]
                                 or ev["targetWeight"] != prev["targetWeight"]):
            out.append(ev)
        prev = ev
    return out


def update_gaps_days(series: list[dict]) -> list[float]:
    """Gaps between consecutive events for one product, in days."""
    return [(b["ts"] - a["ts"]) / 86400.0 for a, b in zip(series, series[1:])]


def first_event_after(events: list[dict], t0: float, product_ids: set[int]) -> dict | None:
    """Earliest event for any of product_ids strictly after t0."""
    cand = [e for e in events if e["productId"] in product_ids and e["ts"] > t0]
    return min(cand, key=lambda e: e["ts"]) if cand else None


def match_product_ids(products: list[dict], keywords: list[str]) -> dict[int, str]:
    out = {}
    for p in products:
        name = (p.get("name") or "").lower()
        if any(k in name for k in keywords):
            out[p["id"]] = p.get("name")
    return out


def iso_to_ts(iso: str) -> float:
    return datetime.fromisoformat(iso).timestamp()


def fmt_ts(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")


# ---------------------------------------------------------------- scan

def load_cache() -> dict:
    if os.path.exists(LOGS_CACHE):
        with open(LOGS_CACHE) as f:
            return json.load(f)
    return {"scanned_to": V2_START_BLOCK - 1, "logs": []}


def scan() -> None:
    rpc = Rpc()
    cache = load_cache()
    head = rpc.block_number() - 12  # small reorg margin
    frm = cache["scanned_to"] + 1
    if frm > head:
        print(f"cache up to date at block {cache['scanned_to']}")
        return
    print(f"scanning {STAKING_PRODUCTS} blocks {frm}..{head} ({head - frm + 1:,})")

    def progress(done_to, target, n):
        print(f"  ..{done_to:,}/{target:,} logs={n}", flush=True)

    raw = get_logs_chunked(rpc, STAKING_PRODUCTS, None, frm, head, chunk=100_000,
                           on_progress=progress)
    # timestamps for new blocks only
    known_ts = {lg["bn"]: lg["ts"] for lg in cache["logs"]}
    new_blocks = sorted({int(lg["blockNumber"], 16) for lg in raw} - set(known_ts))
    print(f"fetching {len(new_blocks)} block timestamps")
    for i, bn in enumerate(new_blocks):
        known_ts[bn] = int(rpc.get_block(bn)["timestamp"], 16)
        if (i + 1) % 200 == 0:
            print(f"  ..{i + 1}/{len(new_blocks)}", flush=True)
    for lg in raw:
        bn = int(lg["blockNumber"], 16)
        cache["logs"].append({
            "bn": bn,
            "ts": known_ts[bn],
            "topic0": (lg.get("topics") or [None])[0],
            "topics": lg.get("topics"),
            "data": lg.get("data"),
            "tx": lg.get("transactionHash"),
        })
    cache["scanned_to"] = head
    cache["logs"].sort(key=lambda x: (x["bn"], x["tx"]))
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(LOGS_CACHE, "w") as f:
        json.dump(cache, f)
    print(f"cached {len(cache['logs'])} logs up to block {head} -> {LOGS_CACHE}")


# ---------------------------------------------------------------- report

def load_products() -> list[dict]:
    with open(PRODUCTS_FILE) as f:
        return json.load(f)


def report() -> None:
    cache = load_cache()
    logs = cache["logs"]
    if not logs:
        print("no logs cached — run scan first")
        return
    products = load_products()
    names = {p["id"]: p.get("name", "?") for p in products}

    print(f"=== Nexus StakingProducts log census (blocks ..{cache['scanned_to']:,}) ===")
    for t0h, n in sorted(bucket_by_topic0(logs).items(), key=lambda kv: -kv[1]):
        label = "ProductUpdated" if t0h == TOPIC_PRODUCT_UPDATED else "UNKNOWN"
        print(f"  {t0h}  x{n:<6} {label}")

    events = [e for e in (decode_product_updated(lg) for lg in logs) if e]
    series = per_product_series(events)
    print(f"\ndecoded ProductUpdated: {len(events)} events, {len(series)} distinct products")

    # global laziness stats: exclude per-product initialization event
    changes = {pid: real_changes(evs) for pid, evs in series.items()}
    n_never = sum(1 for c in changes.values() if not c)
    per_year: dict[int, int] = {}
    for c in changes.values():
        for ev in c:
            y = datetime.fromtimestamp(ev["ts"], tz=timezone.utc).year
            per_year[y] = per_year.get(y, 0) + 1
    print(f"products never re-touched after listing: {n_never}/{len(series)}")
    print("real changes per year:", dict(sorted(per_year.items())))
    all_gaps = [g for evs in series.values() for g in update_gaps_days(evs)]
    if all_gaps:
        print(f"gaps between consecutive product events (days): "
              f"median={statistics.median(all_gaps):.1f} "
              f"p10={statistics.quantiles(all_gaps, n=10)[0]:.1f} "
              f"p90={statistics.quantiles(all_gaps, n=10)[-1]:.1f} n={len(all_gaps)}")

    print("\n=== incident case studies ===")
    for name, iso, keywords, notes in INCIDENTS:
        t0 = iso_to_ts(iso)
        matched = match_product_ids(products, keywords)
        print(f"\n-- {name} (T0 {iso}, {notes})")
        if not matched:
            print(f"   no current product name matches {keywords} — listing may be delisted;"
                  f" check manually")
            continue
        print(f"   matched products: {sorted(matched.items())}")
        ev = first_event_after(events, t0, set(matched))
        if ev is None:
            print("   NO ProductUpdated after T0 for matched products (never repriced)")
        else:
            days = (ev["ts"] - t0) / 86400.0
            print(f"   first ProductUpdated after T0: +{days:.1f} days ({fmt_ts(ev['ts'])}) "
                  f"product={ev['productId']} '{names.get(ev['productId'])}' "
                  f"targetPrice={ev['targetPrice']}bp weight={ev['targetWeight']} tx={ev['tx']}")
        # context: activity in the week before (pre-emptive?) and 30d after
        pre = [e for e in events if e["productId"] in matched and t0 - 7 * 86400 <= e["ts"] <= t0]
        post = [e for e in events if e["productId"] in matched and t0 < e["ts"] <= t0 + 30 * 86400]
        print(f"   events in [T0-7d, T0]: {len(pre)}; in (T0, T0+30d]: {len(post)}")
        for e in post[:8]:
            print(f"     +{(e['ts'] - t0) / 86400.0:5.1f}d p{e['productId']:<4} "
                  f"'{names.get(e['productId'], '?')}' price={e['targetPrice']}bp "
                  f"w={e['targetWeight']}")


def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "report"
    if cmd == "scan":
        scan()
    elif cmd == "report":
        report()
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
