"""Offline unit tests for the HyperEVM liquidation scanner — run: python3 -m analysis.test_hyperlend_liq"""
import unittest

from analysis.hyperlend_liq import (
    TOPIC_LIQ,
    decode_liq,
    market_stats,
    sym,
    usd_value,
)


def _w(v):
    return format(v, "064x")


def _addr_topic(a):
    return "0x" + "0" * 24 + a[2:].lower()


def _log(collateral, debt, user, liq_collateral, liquidator, contract="0xCEcc", bn=100):
    return {
        "blockNumber": hex(bn),
        "address": contract,
        "topics": [TOPIC_LIQ, _addr_topic(collateral), _addr_topic(debt), _addr_topic(user)],
        "data": "0x" + _w(1) + _w(liq_collateral) + _addr_topic(liquidator)[2:] + _w(1),
    }


WHYPE = "0x5555555555555555555555555555555555555555"
USDC = "0xb88339cb7199b77e23db6e890353e22632ba630f"
USER = "0x000000000000000000000000000000000000dEaD"
BOT1 = "0x1111111111111111111111111111111111111111"
BOT2 = "0x2222222222222222222222222222222222222222"


class TestDecode(unittest.TestCase):
    def test_topic0_pinned_offline_keccak(self):
        self.assertEqual(
            TOPIC_LIQ,
            "0xe413a321e8681d831f4dbccbca790d2952b56f977908e45be37335533e005286")

    def test_decode_fields(self):
        ev = decode_liq(_log(WHYPE, USDC, USER, 5 * 10 ** 18, BOT1))
        self.assertEqual(ev["collateral"], WHYPE)
        self.assertEqual(ev["debt"], USDC)
        self.assertEqual(ev["liqCollateral"], 5 * 10 ** 18)
        self.assertEqual(ev["liquidator"], BOT1)


class TestValuation(unittest.TestCase):
    def test_usd_value_decimals(self):
        self.assertAlmostEqual(usd_value(WHYPE, 2 * 10 ** 18), 80.0)   # 2 WHYPE * $40
        self.assertAlmostEqual(usd_value(USDC, 1500 * 10 ** 6), 1500.0)  # 6 decimals
        self.assertEqual(usd_value("0xdeadbeef", 10 ** 18), 0.0)       # unknown -> 0

    def test_sym(self):
        self.assertEqual(sym(WHYPE), "WHYPE")
        self.assertEqual(sym("0xabc123"), "0xabc123")


class TestMarketStats(unittest.TestCase):
    def test_unique_liquidator_counting_and_recency(self):
        head = 1_000_000
        recent_bn = head - 10 * 86400        # inside 90d
        old_bn = head - 200 * 86400          # outside 90d
        liqs = [
            decode_liq(_log(WHYPE, USDC, USER, 10 ** 18, BOT1, bn=recent_bn)),
            decode_liq(_log(WHYPE, USDC, USER, 10 ** 18, BOT2, bn=recent_bn)),
            decode_liq(_log(WHYPE, USDC, USER, 10 ** 18, BOT1, bn=old_bn)),
        ]
        mk = market_stats(liqs, head, recent_days=90)
        key = ("0xcecc"[:6], "WHYPE", "USDC")
        g = mk[key]
        self.assertEqual(g["n"], 3)
        self.assertEqual(g["n90"], 2)               # only the two recent ones
        self.assertEqual(len(g["liqs"]), 2)         # BOT1, BOT2
        self.assertAlmostEqual(g["usd90"], 80.0)    # 2 * 1 WHYPE * $40


if __name__ == "__main__":
    unittest.main()
