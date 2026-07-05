# Phase 0.5 — Review of `kelbic/Polymarket-weather-edge-research`

**Reviewed:** 2026-07-05 · **Reviewer:** edge-research Direction 9 · **Repo:** https://github.com/kelbic/Polymarket-weather-edge-research (public, MIT, single commit, ~5h April-2026 study)

## What it did

Systematic retail-edge test on **10,464 resolved Polymarket weather markets**. Pipeline (all
free / no-auth):

- **Discovery**: Gamma API `closed=true`, strict weather regex, sports false-positive exclusion
  (learned the hard way: "Heat"/"Hurricanes" team names poison keyword filters; v1→v6 log).
- **Ground truth**: Open-Meteo archive (global daily `temperature_2m_max`) as the *observed* value.
- **Market price**: CLOB `prices-history` at real T-24h / T-48h / T-72h (12h fidelity for
  resolved markets; ±6h windowing).
- **Edge metric — "perfect-forecast upper bound"**: implied prob = Normal(observed, std=1.7°C)
  mass in the bucket; PnL = (outcome − entry) − 2% spread; direction by implied vs market.
  Using the *observed* value as the forecast deliberately **overestimates** retail edge — if a
  perfect forecast can't clear the spread, no real model can.

## Data sources used

| Layer | Source | Auth |
|---|---|---|
| Market discovery, question text, volume, outcomePrices, clobTokenIds | Polymarket Gamma API | none |
| Historical T-Xh mid price | Polymarket CLOB `prices-history` | none |
| Observed weather (max/min temp, precip) | Open-Meteo archive | none |

No ERA5/CDS, no paid feeds. (ERA5 would only be a swap for Open-Meteo as ground truth — same role.)

## Fixed results (their numbers)

| Hypothesis | Mean PnL/trade (net) | Verdict |
|---|---|---|
| H1 perfect forecast vs T-24h | **+0.71%** (median **−1.7%**, hit 40.2%) | FAIL (<+3% threshold, outlier-carried) |
| H2 T-48h / T-72h earlier entry | −1.87% / −1.16% | FAIL (edge does not grow earlier) |
| H3 thin markets ($1–5k vol) | −1.64% (gross +0.36%) | FAIL (MMs quote thin = liquid) |
| H4 precipitation | −2.20% (N=13) | FAIL (too few, negative trend) |

By kind: BUCKET −1.94% (N=426), GT **+8.73%** (N=98, hit **36.7%** — outlier lottery),
LT −2.02%, RANGE +3.38%. **No PnL, no Sharpe** reported — study stopped at the edge-existence
gate (correctly; there was nothing to trade).

## Is the negative result trustworthy? — adversarial check

The only thing that could hide a false NO is the positive-mean kinds (GT +8.73%, RANGE +3.38%).
Checked and **rejected correctly**:
- Overall **median PnL is −1.7%** and **hit rate 40.2% (<50%)** → the positive means are carried
  by a few outliers, not a stable edge. Their decision framework explicitly required stability
  across subsets + median≈mean; GT fails both (lowest hit rate of any kind).
- GT with std=1.7°C: near-threshold markets are genuine probabilistic bets that lose ~63% of the
  time individually and win big rarely → textbook high-variance, not income.

Methodology is sound; the "perfect-forecast upper bound" is the gold-standard form of a NO.

## Independent reproduction (this review, fresh data)

Ran `scripts/verify_weather_no.py` on a **fresh, non-overlapping sample**: 495 bucket markets
across 45 global cities, all resolved **2026-07-01** (discovered via Gamma **tag_id=84 "Weather"**
— a cleaner discovery path than the original regex-over-all-markets, which hit Gamma's ~250k
offset cap).

```
ALL     n=495  mean_pnl=-2.14%  median=-2.05%  hit=25.1%  gross(no-spread)=-0.14%
BUCKET  n=405  mean_pnl=-2.51%  median=-2.15%  hit=29.9%  gross=-0.51%
GT      n= 45  mean_pnl=-0.58%  median=-2.05%  hit= 2.2%  gross=+1.42%
LT      n= 45  mean_pnl=-0.36%  median=-1.85%  hit= 4.4%  gross=+1.64%
```

**Gross edge ≈ 0 (−0.14%) even with observed-as-forecast.** Reproduces and *exceeds* their NO.
Caveat: my Open-Meteo coords are city-centre, ~1–1.5°C off the exact resolution stations
(e.g. Incheon for "Seoul") — so this is really a *public-gridded-forecast* test, i.e. what a real
retail trader using Open-Meteo would get. Still negative. The tails' small positive **gross** with
2–4% hit rates is the same outlier lottery, negative net.

## Reusability for later phases

- ✅ **Discovery pattern** (Gamma tag → events → bucket markets → clobTokenIds) — reused directly.
- ✅ **CLOB T-Xh price client** (windowing, retries, disk cache) — reused in `verify_weather_no.py`,
  transfers to any Polymarket topic.
- ✅ **Question parser** GT/LT/BUCKET/RANGE + °C/°F — transfers to sports spreads / crypto thresholds.
- ✅ **"Perfect-forecast upper bound" test structure** — the right Gate-0 template for *any* topic:
  give yourself the true outcome as forecast; if that can't clear the spread, stop.
- ❌ Weather-specific parsers/coords — swap per topic.

## DECISION (per plan Phase 0.5 gate)

> *"Если kelbic уже доказал отсутствие edge на погоде — зафиксируй и пропусти Фазу 1 и 2
> (перейди сразу к Фазе 5)."*

**kelbic proved NO edge on weather; independently confirmed with fresh 2026-07-01 data.**
→ **Skip Phases 1–2. Proceed to Phase 5 (alternative topics).**

### Why weather is structurally efficient (carries into the Phase-5 lens)

1. **No information asymmetry** — MMs use the same public models (ECMWF/NWS/Open-Meteo) as retail.
2. **Predictability ceiling** — T-24h max-temp skill (~1.7°C) is near the atmospheric limit; the
   market already prices it.
3. **PDF-calibrated buckets** — MMs quote the full forecast distribution, not just the mean.
4. **2% spread absorbs any residual.**

**Generalized filter for Phase 5** (weather + STATE.md lattice mode 6 / E4): a retail edge needs
(E1) objective public resolution data, (E2) a modeling/data advantage the MM *lacks*, (E3) residual
edge > spread, (E4) ≥50 liquid markets for stable income, (E5) no speed race. Weather passed
E1/E4/E5 but died on **E2** (forecasting is commoditized) → **E3**. Phase 5 must hunt specifically
for a domain that passes **E2** — where turning public data into a good probability takes real work
the Polymarket MM does not do.
