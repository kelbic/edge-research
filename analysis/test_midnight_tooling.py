"""Тесты тулинга Midnight M-T1/M-T2/M-T4/M-T5 (Фаза B): чистые функции календаря,
break-even-модели (seized/рамп/маршруты), сводки вотчеров/гейтов на синтетике."""
import unittest

from analysis.midnight_breakeven import (
    ORACLE_PRICE_SCALE, WAD, breakeven_t, encode_path, encode_quote_call,
    route_candidates, seized_for_debt, WETH, USDC,
)
from analysis.midnight_calendar import build_ticks, _iso
from analysis.midnight_day0 import lif_at, max_lif


class TestCalendarTicks(unittest.TestCase):
    def _blob(self):
        return {
            "tokens": {"0xusdc": {"symbol": "USDC", "decimals": 6}},
            "markets": [
                {"id": "0x01", "maturity": 2000, "liquidatorGate": "0x" + "00" * 20,
                 "loanToken": "0xusdc", "totalUnits": 5_000_000, "collateralParams": []},
                {"id": "0x02", "maturity": 1000, "liquidatorGate": "0x" + "00" * 20,
                 "loanToken": "0xusdc", "totalUnits": 3_000_000, "collateralParams": []},
                {"id": "0x03", "maturity": 1000, "liquidatorGate": "0x" + "11" * 20,
                 "loanToken": "0xusdc", "totalUnits": 0, "collateralParams": []},
            ],
        }

    def test_sorted_and_grouped(self):
        from analysis import midnight_day0
        midnight_day0.STABLES["0xusdc"] = ("USDC", 6)
        ticks = build_ticks(self._blob(), now_ts=1500)
        self.assertEqual([t["maturity"] for t in ticks], [1000, 2000])
        # окно 1000 объединяет 2 рынка, gate0=1 из 2
        w1000 = ticks[0]
        self.assertEqual(w1000["n_markets"], 2)
        self.assertEqual(w1000["gate0"], 1)
        self.assertTrue(w1000["passed"])           # 1000 < now 1500
        self.assertFalse(ticks[1]["passed"])        # 2000 > 1500
        # borrow: рынок 0x02 (3 USDC) gate0, 0x03 gated 0 units → сумма 3.0
        self.assertAlmostEqual(w1000["borrow_stable_usd"], 3.0)

    def test_iso(self):
        self.assertEqual(_iso(1783555200), "2026-07-09 00:00 UTC")


class TestSeizedAndRamp(unittest.TestCase):
    def test_seized_matches_contract_formula(self):
        # seized = debt·lif/WAD·SCALE/price (Midnight.sol:687 mulDivDown)
        debt = 50_000 * 10 ** 6
        lif = int(1.0006e18)
        # cbBTC@$63700: price_1e36 · 10^(coll8-loan6): p=63700·1e34
        price = 63700 * 10 ** 34
        seized = seized_for_debt(debt, lif, price)
        # ≈ 50000·1.0006/63700 cbBTC = 0.7852 cbBTC (8 dec)
        self.assertAlmostEqual(seized / 10 ** 8, 50000 * 1.0006 / 63700, places=4)

    def test_seized_integer_semantics(self):
        debt, lif, price = 123456789, int(1.03e18), 7 * 10 ** 38
        expect = debt * lif // WAD * ORACLE_PRICE_SCALE // price
        self.assertEqual(seized_for_debt(debt, lif, price), expect)

    def test_breakeven_finds_tstar(self):
        # синтетический линейный exit c 0.5% импактом; долг $100k, газ $5
        max_l = max_lif(int(0.86e18), int(0.30e18))
        price = 3000 * ORACLE_PRICE_SCALE // 10 ** 12
        debt = 100_000 * 10 ** 6

        def qfn(seized):
            gross = seized * price // ORACLE_PRICE_SCALE
            return gross * 9950 // 10000
        res = breakeven_t(debt, max_l, price, qfn, gas_loanwei=5 * 10 ** 6, step_s=60)
        self.assertIsNotNone(res["t_star_s"])
        # π монотонно растёт по рампу → после t* всё положительно
        after = [r["pi"] for r in res["curve"] if r["dt"] >= res["t_star_s"]]
        self.assertTrue(all(p > 0 for p in after))
        before = [r["pi"] for r in res["curve"] if r["dt"] < res["t_star_s"]]
        self.assertTrue(all(p <= 0 for p in before))

    def test_breakeven_never_when_impact_exceeds_bonus(self):
        # импакт 10% >> maxLif-бонус 4.38% → t* недостижим весь рамп
        max_l = max_lif(int(0.86e18), int(0.30e18))
        price = 3000 * ORACLE_PRICE_SCALE // 10 ** 12
        debt = 100_000 * 10 ** 6

        def qfn(seized):
            gross = seized * price // ORACLE_PRICE_SCALE
            return gross * 9000 // 10000
        res = breakeven_t(debt, max_l, price, qfn, gas_loanwei=0, step_s=60)
        self.assertIsNone(res["t_star_s"])


class TestRouterEncoding(unittest.TestCase):
    def test_encode_path_single_hop(self):
        p = encode_path(["0x" + "aa" * 20, "0x" + "bb" * 20], [500])
        # 20 + 3 + 20 bytes = 43 bytes = 86 hex
        self.assertEqual(p, "0x" + "aa" * 20 + "0001f4" + "bb" * 20)

    def test_encode_quote_call_selector(self):
        c = encode_quote_call("0x" + "aa" * 20 + "0001f4" + "bb" * 20, 1000)
        from analysis.keccak import keccak256
        self.assertTrue(c.startswith("0x" + keccak256(b"quoteExactInput(bytes,uint256)").hex()[:8]))

    def test_route_candidates_dedup_and_hubs(self):
        routes = route_candidates("0x" + "cc" * 20, USDC)
        # прямой + через WETH (USDC-хаб=loan, пропущен как дубль)
        self.assertIn(["0x" + "cc" * 20, USDC], routes)
        self.assertTrue(any(WETH.lower() in r for r in routes))
        for r in routes:  # без повторов токенов в маршруте
            self.assertEqual(len(set(r)), len(r))


class TestGateAndOevSummaries(unittest.TestCase):
    def test_gate_stats_and_oev(self):
        from analysis.midnight_watchers import _gate_stats, _oracle_flags
        blob = {
            "markets": [
                {"liquidatorGate": "0x" + "00" * 20},
                {"liquidatorGate": "0x" + "11" * 20},
            ],
            "oracles": {
                "0xa": {"descriptions": ["BTC / USD"], "oev_flag": False},
                "0xb": {"descriptions": ["ETH / USD SVR"], "oev_flag": False},
            },
        }
        g = _gate_stats(blob)
        self.assertEqual(g["gated"], 1)
        self.assertAlmostEqual(g["gated_frac"], 0.5)
        o = _oracle_flags(blob)
        # 'svr' в описании должен поднять флаг
        self.assertIn("0xb", o["oev_oracles"])
        self.assertNotIn("0xa", o["oev_oracles"])


if __name__ == "__main__":
    unittest.main()
