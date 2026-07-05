"""Офлайн-тесты чистых функций analysis/oev_api3.py (stdlib-only, без сети)."""
import unittest

from analysis.oev_api3 import (MONTH_SECONDS, concentration, fp18, margin_usd,
                               month_index, monthly_rows, percentile,
                               qualified_entrants, margin_stats, recapture_rate,
                               tx_aggregate)


def ev(ts, sender, incentive, bid=0.0, gas=0.0, typ="OEV", tx="0xdefault"):
    return {"blockTimestamp": ts, "sender": sender, "type": typ, "txHash": tx,
            "incentiveUsd": str(int(incentive * 1e18)),
            "bidAmountUsd": str(int(bid * 1e18)),
            "gasCostUsd": str(int(gas * 1e18))}


END = 1_000_000_000


class TestFp18(unittest.TestCase):
    def test_parse(self):
        self.assertAlmostEqual(fp18("1748240360246652015"), 1.7482403602, places=6)
        self.assertEqual(fp18(None), 0.0)
        self.assertEqual(fp18("0"), 0.0)


class TestMargin(unittest.TestCase):
    def test_margin(self):
        e = ev(END, "a", 100.0, bid=73.0, gas=2.0)
        self.assertAlmostEqual(margin_usd(e), 25.0, places=9)

    def test_margin_negative(self):
        e = ev(END, "a", 10.0, bid=9.0, gas=2.0)
        self.assertAlmostEqual(margin_usd(e), -1.0, places=9)


class TestMonthIndex(unittest.TestCase):
    def test_buckets(self):
        self.assertEqual(month_index(END, END), 0)
        self.assertEqual(month_index(END - MONTH_SECONDS + 1, END), 0)
        self.assertEqual(month_index(END - MONTH_SECONDS, END), 1)
        self.assertEqual(month_index(END - 5 * MONTH_SECONDS - 1, END), 5)


class TestPercentile(unittest.TestCase):
    def test_median_odd(self):
        self.assertEqual(percentile([1.0, 2.0, 10.0], 0.5), 2.0)

    def test_median_even_interpolates(self):
        self.assertEqual(percentile([1.0, 3.0], 0.5), 2.0)

    def test_empty(self):
        self.assertEqual(percentile([], 0.5), 0.0)


class TestConcentration(unittest.TestCase):
    def test_top_shares(self):
        events = [ev(END, "A", 70), ev(END, "b", 20), ev(END, "c", 10)]
        c = concentration(events)
        self.assertEqual(c["senders"], 3)
        self.assertAlmostEqual(c["top1_usd"], 0.7)
        self.assertAlmostEqual(c["top3_usd"], 1.0)
        self.assertAlmostEqual(c["top1_n"], 1 / 3)
        # HHI: 70^2+20^2+10^2 = 5400
        self.assertAlmostEqual(c["hhi_usd"], 5400.0)

    def test_case_insensitive_sender(self):
        events = [ev(END, "0xAB", 50), ev(END, "0xab", 50)]
        c = concentration(events)
        self.assertEqual(c["senders"], 1)
        self.assertAlmostEqual(c["top1_usd"], 1.0)


class TestEntrants(unittest.TestCase):
    def test_old_incumbent_not_entrant(self):
        events = [ev(END - 20 * MONTH_SECONDS, "old", 100),
                  ev(END, "old", 100)]
        q = qualified_entrants(events, END)
        self.assertEqual(q["entrants_total"], 0)
        self.assertEqual(q["qualified"], {})

    def test_new_entrant_with_share_qualifies(self):
        # инкумбент 10 побед/мес (история глубже 12 мес); новичок берёт 3 из 13
        events = []
        for mi in range(14):
            for _ in range(10):
                events.append(ev(END - mi * MONTH_SECONDS - 1000, "inc", 10))
        for _ in range(3):
            events.append(ev(END - 1 * MONTH_SECONDS - 1000, "new", 10))
        q = qualified_entrants(events, END)
        self.assertEqual(q["entrants_total"], 1)
        self.assertIn("new", q["qualified"])
        self.assertGreaterEqual(q["qualified"]["new"], 0.05)

    def test_dust_entrant_not_qualified(self):
        events = []
        for mi in range(14):
            for _ in range(50):
                events.append(ev(END - mi * MONTH_SECONDS - 1000, "inc", 10))
        events.append(ev(END - 1000, "dust", 1))  # 1 из 51 = ~2% < 5%
        q = qualified_entrants(events, END)
        self.assertEqual(q["entrants_total"], 1)
        self.assertEqual(q["qualified"], {})

    def test_qualifies_by_usd_share(self):
        # по числу 1/11 < 5%, но по $ 50/150 = 33%
        events = [ev(END - 1000, "inc", 10) for _ in range(10)]
        events.append(ev(END - 1000, "whale", 50))
        q = qualified_entrants(events, END)
        self.assertIn("whale", q["qualified"])


class TestMonthlyRows(unittest.TestCase):
    def test_rows_ordered_and_summed(self):
        events = [ev(END - 1000, "a", 100, bid=60, gas=10),
                  ev(END - MONTH_SECONDS - 1000, "b", 50, bid=20, gas=5)]
        rows = monthly_rows(events, END, months=3)
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[-1]["months_ago"], 0)
        self.assertEqual(rows[-1]["n"], 1)
        self.assertAlmostEqual(rows[-1]["margin_total"], 30.0)
        self.assertEqual(rows[-2]["n"], 1)
        self.assertAlmostEqual(rows[-2]["gross"], 50.0)


class TestStatsAndSanity(unittest.TestCase):
    def test_margin_stats(self):
        events = [ev(END, "a", 100, bid=50, gas=0),   # 50
                  ev(END, "b", 10, bid=12, gas=0),    # -2
                  ev(END, "c", 20, bid=10, gas=0)]    # 10
        m = margin_stats(events)
        self.assertEqual(m["n"], 3)
        self.assertAlmostEqual(m["total"], 58.0)
        self.assertAlmostEqual(m["median"], 10.0)
        self.assertAlmostEqual(m["negative_share"], 1 / 3)
        self.assertAlmostEqual(m["top5_share"], 1.0)

    def test_recapture(self):
        events = [ev(END, "a", 100, bid=73), ev(END, "b", 100, bid=73)]
        self.assertAlmostEqual(recapture_rate(events), 0.73)


class TestTxAggregate(unittest.TestCase):
    def test_dedups_bid_per_tx(self):
        # одна tx: 3 события с ОДИНАКОВЫМ бидом (дубли дашборда) -> бид один раз
        events = [ev(END, "a", 10, bid=8, gas=1, tx="0x1"),
                  ev(END, "a", 5, bid=8, gas=0.5, tx="0x1"),
                  ev(END, "a", 2, bid=8, gas=0.5, tx="0x1"),
                  ev(END, "b", 4, bid=3, gas=0.2, tx="0x2")]
        rows = tx_aggregate(events)
        self.assertEqual(len(rows), 2)
        t1 = next(r for r in rows if r["sender"] == "a")
        self.assertAlmostEqual(t1["inc"], 17.0)
        self.assertAlmostEqual(t1["bid"], 8.0)   # не 24
        self.assertAlmostEqual(t1["gas"], 2.0)
        t2 = next(r for r in rows if r["sender"] == "b")
        self.assertAlmostEqual(t2["bid"], 3.0)


if __name__ == "__main__":
    unittest.main()
