# Phase 5 — Alternative-topic exploration log (Polymarket)

Direction 9 of the parent edge-research program. Weather = NO (Phase 0.5, primary-data
confirmed — see `external/kelbic_review.md`). This log hunts for a Polymarket topic that
passes the filter weather died on.

## Pre-registered Gate-0 criteria (fixed BEFORE any topic backtest, 2026-07-05)

A topic advances from Gate-0 screen to full backtest only if it passes **all** of:

- **E1 — objective public resolution.** Settles on a public dataset (gov stat, price feed,
  game result), not UMA-DVM subjective judgement (else it's mode-6 "guessing" / P1, already NO).
- **E4 — capacity & count.** ≥50 resolved markets *and* enough with ≥$5k volume that a $2–10k
  book exists but $10k doesn't move the top of book.
- **E5 — no speed race.** The market lives long enough before resolution that a non-co-located
  independent can enter; edge is not a sub-second dash at data-release (mode 2).

And it must have a *plausible* (tested at backtest, not asserted) path to:

- **E2 — modeling advantage the MM lacks.** There exists a public signal that produces a better
  probability than the market price, which the Polymarket MM does **not** already incorporate.
  (Weather died here: MMs *are* the weather models.)
- **E3 — residual > spread.** That advantage clears the round-trip spread (~2%, measure per topic).

### Verdict rule for a topic's backtest (fixed before data)

Using a real (not perfect-foresight) public signal as the forecast:
- **GO** if mean PnL/trade ≥ **+3%** net of spread **AND** median ≥ 0 **AND** positive across
  ≥2 independent subsets (time period × market sub-family). (Matches kelbic's threshold + adds
  stability, the test GT failed on weather.)
- else **NO**, document, next topic. Max 5 topics; stop if all NO.

Perfect-foresight is used only as a *screen*: if it can't clear spread, stop cheaply (valid only
where settlement-horizon forecast skill ≈ perfect — true for weather, **false** for macro, see below).

## Gate-0 inventory screen (empirical, 2026-07-05)

Discovery via Gamma `tag_id`. Counts = resolved markets fetched (closed=true).

| Topic | tag | closed N | vol≥$5k | spread | E1 | E4 | E5 | E2 a-priori | Screen |
|---|---|---|---|---|---|---|---|---|---|
| **Macro Indicators** (CPI/inflation/jobs/GDP/Fed) | 102000 | 1014 | **568** | ~0.1% | ✅ BLS/BEA/INDEC | ✅ | ✅ | **open** | **ADVANCE** |
| GDP buckets (incl. non-US) | 370 | 148 | 67 | ~0.1% | ✅ | ✅ | ✅ | open (intl less-nowcasted) | advance (2nd) |
| MLB / sports | 100381 | 2000+ | 249 | ~0.1% | ✅ game result | ✅ | ✅ | ❌ sharp-book-dominated; beating closing line ≈ impossible for retail public-data | **NO (E2)** |
| BTC / ETH price | 102321/102322 | 18 / 8 | all | ~0.1% | ✅ price feed | ⚠️ daily stream but tag-thin | ✅ | ❌ liquid-crypto price = martingale; MM prices off Deribit options; no forecast edge | **NO (E2)** |
| SpaceX / "launches" | 1459/138 | 6 | 0 | ~100% | – | ❌ (tag is *crypto* launches, not SpaceX; illiquid) | – | – | **NO (E4)** |
| us-jobs (standalone tag) | 1625 | 13 | 13 | ~100% | ✅ | ❌ dead tag | ✅ | ❌ | **NO (E4)** — folded into Macro |

Macro `tag=102000` family split: CPI/inflation **391**, jobs/unemp **290**, other 194,
GDP 115, Fed 24. Top CPI market volume **$1.9M**. Resolution text confirms BLS CPI (US),
INDEC (Argentina) — objective, scheduled.

### Screen conclusion

Only **Macro Indicators** (and secondarily GDP) survive Gate-0 on E1+E4+E5. Crucially it is the
one family where **E2 is a genuine open question**, not an a-priori NO: unlike weather (MMs = ECMWF),
BTC (MMs = options market), and sports (MMs = Pinnacle), Polymarket's macro crowd is **crypto-native
and may not price in professional nowcasts** (Cleveland Fed / consensus). → run the full backtest here.

## Target topic #1: US CPI / inflation buckets — backtest spec (pre-registered)

**Edge hypothesis:** the pre-release Polymarket implied distribution across CPI buckets deviates
from the professional nowcast distribution by more than the spread, in a tradeable direction.

**Why perfect-foresight shortcut does NOT apply here** (unlike weather): at settlement horizon,
weather forecast skill ≈ observed (predictability ceiling) so perfect-foresight was a *tight* bound.
CPI 2 days pre-release still has real uncertainty (nowcast MoM error ~0.1%), so perfect-foresight
would be a *loose, misleading* bound. The test must use the **actual Cleveland Fed nowcast as of
the pre-release date** (no-lookahead), convert to bucket probs via that nowcast's historical error
std, and compare to the market.

**Data taps (de-risked 2026-07-05):**
- ✅ **Actual CPI** — FRED no-key CSV `CPIAUCNS` (not-seasonally-adjusted = matches resolution).
- ✅ **Pre-release market price** — CLOB `prices-history`. **Anchor entry to the BLS RELEASE date,
  not the market `endDate`** (endDate is a buffer *after* release; price snaps to 0/1 at release —
  observed: "≥2.8% March" already 0.989 two days before endDate). Detect release either from BLS
  release calendar or from the price-path discontinuity.
- ⚠️ **Nowcast history** — Cleveland Fed inflation nowcasting. Page 200; historical-archive data URL
  not yet located (guessed JSON endpoints 404). **This is the make-or-break tap** — need the
  as-of-date nowcast series to avoid lookahead. Fallbacks: Cleveland Fed downloadable historical
  spreadsheet; or a nowcast-free variant (test market self-calibration: is the pre-release price a
  biased predictor of outcome? a systematic bias = edge without external signal).

**Next executable step:** locate the Cleveland Fed nowcast archive (or run the nowcast-free
self-calibration variant first), build `scripts/backtest_cpi.py` reusing the CLOB client +
GT/BUCKET parser from `verify_weather_no.py`, apply the pre-registered verdict rule.

## Topic ledger

| # | Topic | Verdict | Note |
|---|---|---|---|
| — | Weather (temperature) | **NO** | Phase 0.5, primary-data confirmed. Gross edge ≈ 0 even w/ perfect forecast. |
| 1 | US CPI / inflation buckets | **NO** (naive public nowcast) — 1 live residual | See results below. Market well-calibrated (Brier 0.09); both year-subsets negative. YoY perfect-foresight ceiling high → Cleveland Fed nowcast = the one untested lever. |
| 2 | GDP buckets (intl) | queued | smaller N; run if #1 informative. |
| — | MLB / BTC / ETH / SpaceX | NO at screen | E2 or E4, see table. |

## CPI backtest — RESULT (2026-07-06)

`scripts/backtest_cpi.py`. Data path solved: CLOB `prices-history` is pruned for markets
older than ~2 weeks, but **`data-api.polymarket.com/trades` retains full trade history** →
reconstruct pre-release price from real trades. **N=153 usable US CPI bucket markets**, 13
distinct release months (2025-03 … 2026-05). Entry = VWAP of trades in [endDate−3d, endDate−1d]
(pre-release; each trade normalized to YES-equivalent — the `/trades` feed mixes YES+NO legs, a
bug that first produced impossible +31%/trade until fixed). Outcome = settlement. Signal = a
no-lookahead **public seasonal-MoM nowcast** from FRED CPIAUCNS (11/12 months known for YoY).

**Market self-calibration (the decisive diagnostic):** pre-release price is **well-calibrated** —
realized-YES rises monotonically 0.02 → 1.00 across price deciles; **Brier = 0.092** (vs 0.25
coin-flip). The market already prices public information efficiently.

**Public-nowcast strategy (the pre-registered test):**

| Subset | mean | median | hit | gross |
|---|---|---|---|---|
| ALL (n=153) | −2.05% | −3.48% | 41% | −0.05% |
| YoY (79) | +1.51% | −3.17% | 47% | +3.51% |
| MoM (74) | −5.84% | −4.46% | 34% | −3.84% |
| by year 2025 (99) | −0.1% | −3.3% | 45% | — |
| by year 2026 (54) | −5.6% | −4.0% | 31% | — |

**Verdict = NO** per pre-registered rule: gross ≈ 0, net negative after spread; **both year-subsets
negative** (fails ≥2-positive-subsets); YoY has +mean but −median (outlier-driven, unstable — same
trap as weather's GT +8.73%). YoY-by-year confirms instability: 2025 +6.0%/+5.6% but 2026
−5.5%/−3.6% = one good year, one bad = noise.

**Perfect-foresight ceiling (reference, not achievable):** YoY **+15.6% mean / +8.2% median /
hit 80%** (MoM only −4.4%). → the YoY bucket markets *are* exploitable IF one can nowcast YoY CPI
materially better than a naive seasonal model.

### The one live residual (conditional, not GO)

YoY inflation buckets: naive nowcast fails, but the perfect-foresight ceiling is high (+15.6%) and
YoY needs only a 1-month MoM nowcast (11/12 months public). The **untested lever** is a
professional nowcast — **Cleveland Fed inflation nowcasting** (~0.1pp MoM skill vs my ~0.2pp
seasonal) — which sits between naive (~0) and perfect (+15.6%). Blocker: its **vintage archive**
(nowcast-as-of-date) is not cleanly downloadable (page 403s WebFetch; no FRED series). This is a
Gate-1-style follow-up, analogous to the parent program's `prepare-tooling` verdicts — **not a
GO**. Absent that data, CPI stands at **NO** for a public-data retail modeler.

**Structural read (confirms lattice mode 6):** on Polymarket the pre-release macro market is
calibrated to public info; the only escape is a signal genuinely better than public — which for
macro means competing with professional nowcasters, and even *perfect* foresight nets only ~+4–6%
mean after spread. Same shape as weather (MM = the model), slightly less commoditized on YoY.

## Program status after Direction 9

Weather NO (primary data) + CPI NO (primary data, 1 conditional residual). Prediction-market
topics screened: weather, CPI, MLB, BTC/ETH, SpaceX — **no GO**. Remaining queued topic (GDP intl)
shares the CPI structure and is lower-liquidity; the residual worth any further spend is
**Cleveland-Fed-nowcast on YoY CPI buckets**, gated on obtaining the nowcast vintage archive.
Bot/capital (Phases 3-4) remain frozen — no GO.

---

## ❄️ FROZEN (2026-07-06): edge not found

User froze Direction 9. Topics screened by the pre-registered filter: weather, CPI, MLB, BTC/ETH,
SpaceX — **no GO**. Weather & CPI carried to full primary-data backtests (both NO). Lattice mode-6
confirmed for prediction markets. Sole un-closed residual (not reopened): Cleveland Fed nowcast on
YoY CPI buckets — reopen only with cheap vintage-nowcast access AND ≥+3% net / median≥0 on ≥2
subsets. Bot/capital not built. Parameters not relaxed further.
