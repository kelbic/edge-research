#!/usr/bin/env python3
"""
verify_weather_no.py  —  Direction 9 (Polymarket weather), independent Gate-0 verification.

Purpose: reproduce, on a FRESH sample of recently-resolved Polymarket temperature
markets, the core negative result of kelbic/Polymarket-weather-edge-research:
    "even a PERFECT forecast yields ~0 edge before the ~2% spread".

Method (perfect-forecast upper bound, per that repo):
  - discover resolved temperature events via Gamma tag_id=84 (Weather)
  - for each bucket market: real T-24h CLOB mid price + binary settlement outcome
  - observed daily max temp from Open-Meteo archive (free, no auth) as the "forecast"
  - implied prob = Normal(observed, std) mass in the bucket; std = 1.7C / 3.0F (T-24h skill)
  - trade YES if implied>mkt+edge_min, NO if implied<mkt-edge_min; PnL = outcome-entry-spread
If perfect foresight can't clear the spread, no real model can. read-only, zero capital.
"""
import requests, json, time, math, os, re, sys
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "..", "data")
os.makedirs(os.path.join(DATA, "prices"), exist_ok=True)
GAMMA = "https://gamma-api.polymarket.com"
CLOB  = "https://clob.polymarket.com"
GEO   = "https://geocoding-api.open-meteo.com/v1/search"
ARCH  = "https://archive-api.open-meteo.com/v1/archive"
SPREAD = 0.02          # 2% round-trip, per prior repo (conservative-friendly to finding edge)
EDGE_MIN = 0.0         # trade on ANY positive edge (most generous)
STD_C = 1.7            # NWS T-24h max-temp skill, deg C
SESS = requests.Session()
SESS.headers["User-Agent"] = "edge-research-dir9/1.0"

def get(url, params=None, tries=6):
    for i in range(tries):
        try:
            r = SESS.get(url, params=params, timeout=25)
            if r.status_code == 200:
                return r.json()
            if r.status_code in (429, 500, 502, 503, 504):
                time.sleep(1.5 * (i + 1)); continue
            return None
        except Exception:
            time.sleep(1.5 * (i + 1))
    return None

# ---------- discovery ----------
def discover_events(date_lo, date_hi, max_events=50, cache="events_tag84.json"):
    cpath = os.path.join(DATA, cache)
    if os.path.exists(cpath):
        allev = json.load(open(cpath))
    else:
        allev = []
        for off in range(0, 900, 100):
            r = get(GAMMA + "/events",
                    {"tag_id": 84, "closed": "true", "limit": 100, "offset": off,
                     "order": "endDate", "ascending": "false"})
            if not r:
                break
            ev = r if isinstance(r, list) else r.get("data", [])
            if not ev:
                break
            allev += ev
            # ascending=false → once we've paged past date_lo we can stop
            if min(e.get("endDate", "9") for e in ev) < date_lo:
                break
        json.dump(allev, open(cpath, "w"))
    picked = [e for e in allev
              if date_lo <= e.get("endDate", "") <= date_hi
              and "temperature" in e.get("title", "").lower()]
    return picked[:max_events]

# ---------- geocoding ----------
_geo = {}
def city_latlon(city):
    if city in _geo:
        return _geo[city]
    r = get(GEO, {"name": city, "count": 1})
    res = (r or {}).get("results")
    ll = (res[0]["latitude"], res[0]["longitude"]) if res else None
    _geo[city] = ll
    return ll

def observed_max(lat, lon, date, unit_f):
    key = f"{lat:.2f}_{lon:.2f}_{date}_{'F' if unit_f else 'C'}"
    cp = os.path.join(DATA, "prices", "om_" + key + ".json")
    if os.path.exists(cp):
        return json.load(open(cp))
    p = {"latitude": lat, "longitude": lon, "start_date": date, "end_date": date,
         "daily": "temperature_2m_max", "timezone": "auto"}
    if unit_f:
        p["temperature_unit"] = "fahrenheit"
    r = get(ARCH, p)
    val = None
    try:
        val = r["daily"]["temperature_2m_max"][0]
    except Exception:
        val = None
    json.dump(val, open(cp, "w"))
    return val

# ---------- CLOB T-24h ----------
def price_at(token_id, target_ts, end_ts, tol=8 * 3600):
    cp = os.path.join(DATA, "prices", f"clob_{token_id[-16:]}.json")
    if os.path.exists(cp):
        hist = json.load(open(cp))
    else:
        hist = []
        for params in ({"market": token_id, "startTs": end_ts - 5 * 86400, "endTs": end_ts, "fidelity": 60},
                       {"market": token_id, "interval": "1m", "fidelity": 60}):
            r = get(CLOB + "/prices-history", params)
            hist = (r or {}).get("history", [])
            if hist:
                break
        json.dump(hist, open(cp, "w"))
    if not hist:
        return None
    best = min(hist, key=lambda p: abs(p["t"] - target_ts))
    return best["p"] if abs(best["t"] - target_ts) <= tol else None

# ---------- question parsing ----------
CITY_RE = re.compile(r"temperature in (.+?) (?:be|on|reach)", re.I)
def parse_city(title):
    m = re.search(r"temperature in (.+?) on ", title, re.I)
    return m.group(1).strip() if m else None

def parse_bucket(q):
    """Return (kind, thr, unit_f) for a bucket sub-market question."""
    unit_f = "°f" in q.lower() or re.search(r"\d\s*f\b", q.lower()) is not None
    # normalize
    m = re.search(r"(-?\d+(?:\.\d+)?)\s*°?\s*([cf])?\s*(or higher|or above|or below|or lower)?", q, re.I)
    if not m:
        return None
    thr = float(m.group(1))
    tail = (m.group(3) or "").lower()
    if "higher" in tail or "above" in tail:
        kind = "GT"
    elif "below" in tail or "lower" in tail:
        kind = "LT"
    else:
        kind = "BUCKET"
    return kind, thr, unit_f

def norm_cdf(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))

def implied_prob(kind, thr, observed, std):
    z = lambda v: norm_cdf((v - observed) / std)
    if kind == "BUCKET":
        return z(thr + 0.5) - z(thr - 0.5)
    if kind == "GT":
        return 1 - z(thr - 0.5)
    if kind == "LT":
        return z(thr + 0.5)
    return None

# ---------- main ----------
def main():
    date_lo, date_hi = "2026-06-18", "2026-07-02"
    events = discover_events(date_lo, date_hi, max_events=45)
    print(f"[discover] {len(events)} resolved temperature events in [{date_lo},{date_hi}]", flush=True)
    rows = []
    for ei, e in enumerate(events):
        title = e.get("title", "")
        city = parse_city(title)
        end = e.get("endDate")
        if not city or not end:
            continue
        date = end[:10]
        end_ts = int(datetime.fromisoformat(end.replace("Z", "+00:00")).timestamp())
        t24 = end_ts - 86400
        ll = city_latlon(city)
        if not ll:
            print(f"  [skip] no geocode for {city}"); continue
        lat, lon = ll
        for m in e.get("markets", []):
            q = m.get("question", "")
            pb = parse_bucket(q)
            if not pb:
                continue
            kind, thr, unit_f = pb
            try:
                op = json.loads(m.get("outcomePrices") or "[]")
                outcome = 1.0 if float(op[0]) > 0.5 else 0.0
            except Exception:
                continue
            std = STD_C * (1.8 if unit_f else 1.0)
            obs = observed_max(lat, lon, date, unit_f)
            if obs is None:
                continue
            ids = json.loads(m.get("clobTokenIds") or "[]")
            if not ids:
                continue
            mkt = price_at(ids[0], t24, end_ts)
            if mkt is None or mkt <= 0.0 or mkt >= 1.0:
                continue
            ip = implied_prob(kind, thr, obs, std)
            if ip is None:
                continue
            # trade direction on the YES token
            if ip > mkt + EDGE_MIN:
                pnl = (outcome - mkt) - SPREAD          # buy YES at mkt
                side = "YES"
            elif ip < mkt - EDGE_MIN:
                pnl = ((1 - outcome) - (1 - mkt)) - SPREAD  # buy NO at (1-mkt)
                side = "NO"
            else:
                continue
            rows.append(dict(city=city, date=date, kind=kind, thr=thr, unit=("F" if unit_f else "C"),
                             observed=obs, implied=round(ip, 4), mkt=round(mkt, 4),
                             outcome=outcome, side=side, pnl=round(pnl, 4),
                             gross=round(pnl + SPREAD, 4), vol=m.get("volumeNum")))
        print(f"  [{ei+1}/{len(events)}] {title[:44]:44s}  rows={len(rows)}", flush=True)
    # summary
    out = os.path.join(DATA, "verify_weather_results.csv")
    if rows:
        import csv
        with open(out, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys())); w.writeheader(); w.writerows(rows)
    def stats(rs):
        n = len(rs)
        if not n: return "n=0"
        pnls = sorted(r["pnl"] for r in rs)
        gross = sum(r["gross"] for r in rs) / n
        mean = sum(pnls) / n
        med = pnls[n // 2]
        hit = sum(1 for p in pnls if p > 0) / n
        return f"n={n:4d}  mean_pnl={mean:+.3%}  median={med:+.3%}  hit={hit:.1%}  gross(no-spread)={gross:+.3%}"
    print("\n==================  VERIFY WEATHER (perfect-forecast upper bound)  ==================")
    print("ALL       ", stats(rows))
    for k in ("BUCKET", "GT", "LT"):
        print(f"{k:10s}", stats([r for r in rows if r["kind"] == k]))
    print(f"\nsaved -> {out}")
    print("verdict rule: mean_pnl < +1% (and median<=0) => reproduces NO (structurally efficient)")

if __name__ == "__main__":
    main()
