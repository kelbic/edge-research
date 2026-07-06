# era5_polymarket — Direction 9: Polymarket forecast edge

Living diary. Part of the parent `edge-research` program (see `../STATE.md`).
Goal: find a Polymarket topic with a real, retail-scale forecast edge and (only if proven)
build a bot for $2–10k. **Discipline inherited from parent: Gate-0 first, read-only, zero
capital until edge is proven on data, honest NO cheaper than false GO.**

## Status @ 2026-07-05

| Phase | State |
|---|---|
| 0 — Discovery (Polymarket weather markets) | ✅ Gamma tag_id=84 discovery working; sample cached `data/events_tag84.json` |
| 0.5 — Review kelbic/Polymarket-weather-edge-research | ✅ `external/kelbic_review.md` |
| 1–2 — Weather features/model/backtest | ⏭️ **SKIPPED** — kelbic already proved NO; independently confirmed |
| 5 — Alternative topics | 🔬 screen done; **CPI/inflation** backtested → **NO** (1 conditional residual) |
| 3–4 — Bot / live capital | 🔒 GATED behind a GO verdict (none yet) |

## Headline result so far

**Polymarket WEATHER = NO edge (confirmed with fresh primary data).**
A *perfect* forecast (observed temperature as the forecast) yields **gross edge ≈ 0** across
495 markets / 45 global cities (2026-07-01 resolutions); net of the ~2% spread it is firmly
negative. The market maker already *is* the weather model (ECMWF/NWS/Open-Meteo), and T-24h temp
skill is at the atmospheric predictability ceiling. See `external/kelbic_review.md` + reproduction
`scripts/verify_weather_no.py` → `data/verify_weather_results.csv`.

```
ALL     n=495  mean_pnl=-2.14%  median=-2.05%  hit=25.1%  gross(no-spread)=-0.14%
```

## Phase-5 screen (why weather's NO is a *lattice*, not bad luck)

A retail edge on Polymarket needs: E1 objective public resolution · E2 a modeling advantage the
MM lacks · E3 residual > spread · E4 ≥50 liquid markets · E5 no speed race. Weather passed
E1/E4/E5, died on **E2** (forecasting is commoditized). Screening the plan's candidates:

- **Macro Indicators** (CPI/inflation/jobs/GDP, tag 102000, 568 liquid markets) — **survives**;
  E2 is the one genuinely *open* question (Polymarket's crypto-native crowd may not price in pro
  nowcasts). → target #1.
- **MLB/sports** — NO (E2: sharp-sportsbook-dominated, closing line ≈ unbeatable for retail).
- **BTC/ETH price** — NO (E2: liquid-crypto price is a martingale priced off options).
- **SpaceX/launches, us-jobs tag** — NO (E4: no real inventory).

Full log + pre-registered criteria: `research/exploration_log.md`.

## CPI/inflation backtest — done → NO (`scripts/backtest_cpi.py`)

N=153 US CPI bucket markets (2025-03…2026-05). Pre-release price reconstructed from real trades
(`data-api/trades`, since CLOB prices-history is pruned >~2wk). Signal = no-lookahead public
seasonal nowcast (FRED).

- **Market is well-calibrated** (Brier 0.092; realized-YES monotonic 0.02→1.00).
- **Public-nowcast strategy: NO** — gross ≈ 0 (−0.05%), net −2.05%, **both year-subsets negative**.
- **Perfect-foresight ceiling: YoY +15.6% / MoM −4.4%** — YoY buckets *are* exploitable only with a
  materially-better-than-naive nowcast.

**One conditional residual (not GO):** a professional nowcast — **Cleveland Fed** — on YoY buckets,
which sits between naive (~0) and perfect (+15.6%). Blocked on its vintage archive (403 / no FRED
series). This is the only lever worth further spend on prediction markets.

## Direction-9 conclusion

Prediction markets (weather, CPI, MLB, BTC/ETH, SpaceX) — **no GO**; lattice mode-6 confirmed with
primary data. The pre-release market prices public info efficiently; the only escape is a signal
better than public, and even *perfect* foresight nets ~+4–6% mean after spread. Bot/capital frozen.

## Layout

```
data/            cached markets, Open-Meteo, CLOB, verify results
scripts/         verify_weather_no.py  (+ backtest_cpi.py, next)
external/        kelbic_weather_edge/ (cloned) + kelbic_review.md
research/        exploration_log.md
config.yaml      params; status=research, live keys absent by design
```
