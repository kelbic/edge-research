#!/usr/bin/env python3
"""
backtest_cpi.py — Direction 9, Phase-5 topic #1: US CPI / inflation buckets.

Edge question (E2): can a retail modeler using ONLY public data beat the pre-release
Polymarket price on US CPI bucket markets — by more than the ~2% spread?

Design (no-lookahead, fully reproducible):
  outcome      : market settlement (Gamma outcomePrices) — binary.
  entry price  : VWAP of REAL trades (data-api /trades) in [endDate-3d, endDate-1d],
                 i.e. pre-release (monthly CPI releases ~ endDate; price snaps at release).
  signal       : a PUBLIC seasonal nowcast from FRED CPIAUCNS, computed with only
                 data available before release:
                   MoM[M]  ~ Normal( mean seasonal MoM over years < target_year , std )
                   YoY[M]  built from actual CPI[M-1], CPI[M-12] (both public pre-release)
                            and the MoM nowcast for the single unknown month M.
  strategy     : trade YES if nowcast_prob > price, NO if <, PnL = outcome - entry - 2%.
Also reports: perfect-foresight ceiling (actual value as signal) + market self-calibration.
A weak public nowcast is a CONSERVATIVE probe: if even it beats the market -> GO signal
(then upgrade to Cleveland Fed nowcast); if not -> market is >= a naive public model = efficient.
read-only, zero capital.
"""
import requests, json, time, math, os, re, statistics as st
from datetime import datetime, timezone
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__)); DATA = os.path.join(HERE, "..", "data")
os.makedirs(os.path.join(DATA, "trades"), exist_ok=True)
GAMMA="https://gamma-api.polymarket.com"; DAPI="https://data-api.polymarket.com"; FREDCSV="https://fred.stlouisfed.org/graph/fredgraph.csv"
SPREAD=0.02
SESS=requests.Session(); SESS.headers["User-Agent"]="edge-research-dir9/1.0"
MONTHS={m:i+1 for i,m in enumerate(["january","february","march","april","may","june","july","august","september","october","november","december"])}

def get(url,params=None,tries=6,timeout=25):
    for i in range(tries):
        try:
            r=SESS.get(url,params=params,timeout=timeout)
            if r.status_code==200: return r
            if r.status_code in (429,500,502,503,504): time.sleep(1.5*(i+1)); continue
            return None
        except Exception: time.sleep(1.5*(i+1))
    return None

# ---------- FRED CPI ----------
def load_cpi():
    cp=os.path.join(DATA,"fred_cpiaucns.json")
    if os.path.exists(cp): return json.load(open(cp))
    r=get(FREDCSV,{"id":"CPIAUCNS","cosd":"2008-01-01"})
    cpi={}
    for line in r.text.strip().splitlines()[1:]:
        d,v=line.split(",")
        if v not in (".",""): cpi[d[:7]]=float(v)
    json.dump(cpi,open(cp,"w")); return cpi

def prev_month(ym,k=1):
    y,m=int(ym[:4]),int(ym[5:7])
    m-=k
    while m<=0: m+=12; y-=1
    return f"{y:04d}-{m:02d}"

def seasonal_mom(cpi, ym):
    """No-lookahead: mean/std of MoM for calendar-month(ym) over years strictly before ym's year."""
    tgt_y=int(ym[:4]); m=int(ym[5:7]); vals=[]
    for y in range(2009,tgt_y):
        cur=f"{y:04d}-{m:02d}"; pm=prev_month(cur)
        if cur in cpi and pm in cpi: vals.append(cpi[cur]/cpi[pm]-1)
    if len(vals)<4: return None
    return st.mean(vals), max(st.pstdev(vals),0.0008)   # floor std at 0.08pp

def nowcast(cpi, kind_time, ym):
    """Return (mu, sigma) for the target statistic. kind_time in {'MoM','YoY'}."""
    sm=seasonal_mom(cpi, ym)
    if not sm: return None
    mom_mu, mom_sd = sm
    if kind_time=="MoM":
        return mom_mu, mom_sd
    # YoY: needs CPI[M-1] and CPI[M-12], both public before release of M
    pm, pm12 = prev_month(ym), prev_month(ym,12)
    if pm not in cpi or pm12 not in cpi: return None
    cpi_M_hat = cpi[pm]*(1+mom_mu)
    yoy_mu = cpi_M_hat/cpi[pm12]-1
    yoy_sd = (cpi[pm]/cpi[pm12])*mom_sd
    return yoy_mu, yoy_sd

def actual_stat(cpi, kind_time, ym):
    if ym not in cpi: return None
    if kind_time=="MoM":
        pm=prev_month(ym); return cpi[ym]/cpi[pm]-1 if pm in cpi else None
    pm12=prev_month(ym,12); return cpi[ym]/cpi[pm12]-1 if pm12 in cpi else None

def ncdf(x): return 0.5*(1+math.erf(x/math.sqrt(2)))
def bucket_prob(kind, lo, hi, mu, sd):
    z=lambda v: ncdf((v-mu)/sd)
    if kind=="GT": return 1-z(lo)
    if kind=="LT": return z(hi)
    return max(z(hi)-z(lo), 0.0)   # BUCKET/RANGE

# ---------- question parsing (US CPI) ----------
def parse(q, end_ym):
    ql=q.lower()
    if "annual inflation" in ql or ("inflation" in ql and "monthly" not in ql and "reach" not in ql):
        tstat="YoY"
    elif "monthly inflation" in ql: tstat="MoM"
    else: return None
    # target month
    mo=None
    for name,idx in MONTHS.items():
        if f" in {name}" in ql or f"{name} 20" in ql: mo=idx; break
    if mo is None: return None
    # year: from explicit or infer from endDate (data month is release_month-? -> use endDate month-1 heuristic corrected by named month)
    ym_year=int(end_ym[:4])
    # if named month > endDate month, it's prior year
    if mo> int(end_ym[5:7]): ym_year-=1
    ym=f"{ym_year:04d}-{mo:02d}"
    # value(s) as fraction
    nums=[float(x) for x in re.findall(r"(-?\d+(?:\.\d+)?)\s*%", q)]
    if not nums: return None
    if "between" in ql and len(nums)>=2:
        lo,hi=sorted(nums[:2]); return ("RANGE",lo/100,hi/100,tstat,ym)
    v=nums[0]/100
    if any(t in ql for t in ("≥","or more","or higher","more than","at least",">=")):
        return ("GT", v, None, tstat, ym)
    if any(t in ql for t in ("≤","or less","or lower","less than","<=")):
        return ("LT", None, v, tstat, ym)
    # exact bucket, 0.1%-wide
    return ("BUCKET", v-0.0005, v+0.0005, tstat, ym)

# ---------- trades -> pre-release VWAP ----------
def entry_price(cond, end_ts):
    cp=os.path.join(DATA,"trades",f"{cond[-16:]}.json")
    if os.path.exists(cp): tr=json.load(open(cp))
    else:
        tr=[]; off=0
        while True:
            r=get(DAPI+"/trades",{"market":cond,"limit":500,"offset":off})
            if not r: break
            b=r.json()
            if not b: break
            tr+=b; off+=500
            if len(b)<500 or off>=4000: break
        json.dump(tr,open(cp,"w"))
    lo,hi=end_ts-3*86400, end_ts-1*86400
    win=[t for t in tr if lo<=t.get("timestamp",0)<hi and 0<t.get("price",0)<1]
    if len(win)<3: return None,len(tr)
    # /trades mixes YES and NO trades — normalize every trade to the YES-equivalent price
    def yes_px(t): return t["price"] if str(t.get("outcome","")).lower()=="yes" else 1-t["price"]
    num=sum(yes_px(t)*t["size"] for t in win); den=sum(t["size"] for t in win)
    return (num/den if den else None), len(tr)

# ---------- main ----------
def main():
    cpi=load_cpi()
    print(f"[fred] CPIAUCNS {len(cpi)} months, latest {sorted(cpi)[-1]}")
    # discover US CPI markets
    cache=os.path.join(DATA,"macro_tag102000.json")
    if os.path.exists(cache): mk=json.load(open(cache))
    else:
        mk=[]
        for off in range(0,1500,100):
            r=get(GAMMA+"/markets",{"tag_id":102000,"closed":"true","limit":100,"offset":off,"order":"volumeNum","ascending":"false"})
            if not r: break
            b=r.json(); b=b if isinstance(b,list) else b.get("data",[])
            if not b: break
            mk+=b
            if len(b)<100: break
        json.dump(mk,open(cache,"w"))
    infl=[m for m in mk if "inflation" in m.get("question","").lower()
          and "argentin" not in (m.get("description") or "").lower()
          and "indec" not in (m.get("description") or "").lower()]
    print(f"[discover] {len(infl)} US inflation markets")
    rows=[]; skipped=defaultdict(int)
    for i,m in enumerate(infl):
        end=m.get("endDate");
        if not end: skipped["no_end"]+=1; continue
        end_ym=end[:7]; end_ts=int(datetime.fromisoformat(end.replace("Z","+00:00")).timestamp())
        p=parse(m.get("question",""), end_ym)
        if not p: skipped["parse"]+=1; continue
        kind,lo,hi,tstat,ym=p
        try:
            op=json.loads(m.get("outcomePrices") or "[]"); outcome=1.0 if float(op[0])>0.5 else 0.0
        except Exception: skipped["outcome"]+=1; continue
        nc=nowcast(cpi,tstat,ym); act=actual_stat(cpi,tstat,ym)
        if nc is None or act is None: skipped["no_cpi"]+=1; continue
        price,ntr=entry_price(m.get("conditionId"), end_ts)
        if price is None or price<=0.01 or price>=0.99: skipped["no_price_or_extreme"]+=1; continue
        mu,sd=nc
        ip=bucket_prob(kind,lo if lo is not None else -9,hi if hi is not None else 9,mu,sd)
        # perfect-foresight reference prob (actual as mean, tiny sd)
        pf=bucket_prob(kind, lo if lo is not None else -9, hi if hi is not None else 9, act, max(sd*0.15,0.0003))
        def pnl(prob):
            if prob>price:  return (outcome-price)-SPREAD
            if prob<price:  return ((1-outcome)-(1-price))-SPREAD
            return None
        pn=pnl(ip); pfpn=pnl(pf)
        if pn is None: skipped["no_edge_tie"]+=1; continue
        rows.append(dict(q=m["question"][:60],ym=ym,tstat=tstat,kind=kind,
                         nowcast=round(mu*100,3),nc_sd=round(sd*100,3),actual=round(act*100,3),
                         implied=round(ip,3),price=round(price,3),outcome=outcome,
                         pnl=round(pn,4),pf_pnl=round(pfpn,4) if pfpn is not None else None,
                         vol=m.get("volumeNum"),ntrades=ntr))
        if (i+1)%25==0: print(f"  ..{i+1}/{len(infl)} usable={len(rows)}",flush=True)
    # save + report
    out=os.path.join(DATA,"backtest_cpi_results.csv")
    if rows:
        import csv
        with open(out,"w",newline="") as f:
            w=csv.DictWriter(f,fieldnames=list(rows[0].keys())); w.writeheader(); w.writerows(rows)
    def stats(rs,key="pnl"):
        rs=[r for r in rs if r.get(key) is not None]; n=len(rs)
        if not n: return "n=0"
        v=sorted(r[key] for r in rs); mean=sum(v)/n; med=v[n//2]
        hit=sum(1 for x in v if x>0)/n; gross=sum(r[key]+SPREAD for r in rs)/n
        return f"n={n:4d}  mean={mean:+.2%}  median={med:+.2%}  hit={hit:.0%}  gross={gross:+.2%}"
    print("\n=========== CPI BACKTEST (public seasonal nowcast vs pre-release market) ===========")
    print("skipped:",dict(skipped))
    print("\n-- PUBLIC-NOWCAST STRATEGY --")
    print("ALL     ",stats(rows))
    for t in ("YoY","MoM"):
        print(f"{t:8s}",stats([r for r in rows if r['tstat']==t]))
    for k in ("GT","LT","BUCKET","RANGE"):
        s=[r for r in rows if r['kind']==k]
        if s: print(f"  {k:6s}",stats(s))
    print("\n-- PERFECT-FORESIGHT CEILING (actual value as signal) --")
    print("ALL     ",stats(rows,"pf_pnl"))
    # market self-calibration: reliability of pre-release price
    print("\n-- MARKET SELF-CALIBRATION (pre-release price vs realized outcome) --")
    bins=defaultdict(lambda:[0,0])
    for r in rows:
        b=int(r["price"]*10)/10; bins[b][0]+=1; bins[b][1]+=r["outcome"]
    for b in sorted(bins):
        n,s=bins[b]; print(f"  price~[{b:.1f},{b+.1:.1f}) n={n:3d}  realized_yes={s/n:.2f}")
    brier=sum((r['price']-r['outcome'])**2 for r in rows)/len(rows) if rows else float('nan')
    print(f"  Brier(pre-release price)= {brier:.4f}   (0=perfect, 0.25=coinflip-at-0.5)")
    print(f"\nsaved -> {out}")
    print("verdict rule: GO if nowcast mean>=+3% net AND median>=0 AND positive in >=2 subsets; else NO.")

if __name__=="__main__": main()
