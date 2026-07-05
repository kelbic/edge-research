"""Offline unit tests for the Felix SP gain scanner — run: python3 -m analysis.test_felix_sp"""
import unittest

from analysis.felix_sp import (
    TOPIC_LIQ,
    concentration,
    decode_liq,
    gain_usd,
    realized_apr,
)

WHYPE_TM = "0x3100f4e7bda2ed2452d9a57eb30260ab071bbe62"


def _word(v):
    return format(int(v), "064x")


def _liqlog(debt, coll, price, bn=100, tm=WHYPE_TM):
    # 10 words: debtOffsetSP, _, _, _, collSentSP, _, _, _, _, price
    words = [debt, 0, 0, 0, coll, 0, 0, 0, 0, price]
    return {"blockNumber": hex(bn), "address": tm,
            "data": "0x" + "".join(_word(int(w * 1e18)) for w in words)}


class TestDecode(unittest.TestCase):
    def test_topic0_pinned_offline_keccak(self):
        self.assertEqual(
            TOPIC_LIQ,
            "0x7243af9a1cff94d3429b2ee00b78c1c10589259f20dc167cb67704f38f9e824e")

    def test_decode_fields_and_branch(self):
        ev = decode_liq(_liqlog(10_000, 167.6, 63.5, bn=42))
        self.assertEqual(ev["branch"], "WHYPE")
        self.assertEqual(ev["bn"], 42)
        self.assertAlmostEqual(ev["debtOffsetSP"], 10_000, places=3)
        self.assertAlmostEqual(ev["price"], 63.5, places=3)

    def test_gain_is_5pct_of_debt(self):
        ev = decode_liq(_liqlog(10_000, 167.6, 63.5))
        self.assertAlmostEqual(gain_usd(ev), 500.0, places=3)   # 5% of $10k


class TestAggregation(unittest.TestCase):
    def test_realized_apr(self):
        # one $10k-debt liquidation -> $500 gain; pool $50k over 365d -> 1% APR
        evs = [decode_liq(_liqlog(10_000, 100, 1))]
        self.assertAlmostEqual(realized_apr(evs, 50_000, 365), 0.01, places=6)

    def test_realized_apr_guards(self):
        self.assertEqual(realized_apr([], 0, 0), 0.0)
        self.assertEqual(realized_apr([decode_liq(_liqlog(1, 1, 1))], 100, 0), 0.0)

    def test_concentration_fat_tail(self):
        evs = [decode_liq(_liqlog(d, 1, 1)) for d in (100_000, 1_000, 1_000, 1_000)]
        c = concentration(evs)
        self.assertEqual(c["n"], 4)
        self.assertGreater(c["top1_share"], 0.9)   # one giant dominates


if __name__ == "__main__":
    unittest.main()
