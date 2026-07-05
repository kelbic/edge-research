"""Офлайн-тесты чистых функций analysis/oev_svr.py (stdlib-only, без сети)."""
import unittest

from analysis.oev_svr import (SEL_DECIMALS, SEL_GET_ASSET_PRICE,
                              SEL_LATEST_ROUND_DATA, TOPIC_LIQ, TOPIC_UPD,
                              decode_liq_call, event_gross_usd, find_refund,
                              join_adjacent, to_common_schema, usd_amount)

CORE = "0x87870bca3f3fd6335c3f4ce8392d69350b4fa4e2"


class TestSelectors(unittest.TestCase):
    """Селекторы/topic0 — offline-keccak против известных значений
    (LiquidationCall и SecondaryRoundIdUpdated сверены с живым блоком 25430607)."""

    def test_topics(self):
        self.assertEqual(TOPIC_UPD,
                         "0x8d530b9ddc4b318d28fdd4c3a21fcfecece54c1a72a824f262985b99afef009b")
        self.assertEqual(TOPIC_LIQ,
                         "0xe413a321e8681d831f4dbccbca790d2952b56f977908e45be37335533e005286")

    def test_selectors(self):
        self.assertEqual(SEL_GET_ASSET_PRICE, "0xb3596f07")
        self.assertEqual(SEL_DECIMALS, "0x313ce567")
        self.assertEqual(SEL_LATEST_ROUND_DATA, "0xfeaf968c")


def upd_log(block, tx_index):
    return {"blockNumber": hex(block), "transactionIndex": hex(tx_index)}


def liq_log(block, tx_index, tx_hash="0xabc", coll="0x" + "11" * 20,
            debt="0x" + "22" * 20, debt_cover=1000, coll_amount=1100,
            liquidator="0x" + "33" * 20):
    data = ("0x" + hex(debt_cover)[2:].rjust(64, "0")
            + hex(coll_amount)[2:].rjust(64, "0")
            + liquidator[2:].rjust(64, "0")
            + "0".rjust(64, "0"))
    return {"blockNumber": hex(block), "transactionIndex": hex(tx_index),
            "transactionHash": tx_hash, "address": CORE,
            "topics": ["0xt0", "0x" + coll[2:].rjust(64, "0"),
                       "0x" + debt[2:].rjust(64, "0"), "0x" + "0" * 64],
            "data": data}


class TestJoin(unittest.TestCase):
    def test_adjacent_joined(self):
        evs = join_adjacent([upd_log(100, 3)], [liq_log(100, 4)])
        self.assertEqual(len(evs), 1)
        self.assertEqual(evs[0]["block"], 100)
        self.assertEqual(evs[0]["pool"], "core")

    def test_nonadjacent_dropped(self):
        self.assertEqual(join_adjacent([upd_log(100, 3)], [liq_log(100, 7)]), [])
        self.assertEqual(join_adjacent([upd_log(100, 3)], [liq_log(101, 4)]), [])

    def test_catchup_without_liq_dropped(self):
        self.assertEqual(join_adjacent([upd_log(100, 3)], []), [])

    def test_multiple_calls_one_tx_grouped(self):
        logs = [liq_log(100, 4, tx_hash="0xsame"), liq_log(100, 4, tx_hash="0xsame")]
        evs = join_adjacent([upd_log(100, 3)], logs)
        self.assertEqual(len(evs), 1)
        self.assertEqual(len(evs[0]["calls"]), 2)


class TestDecode(unittest.TestCase):
    def test_decode(self):
        c = decode_liq_call(liq_log(1, 1, debt_cover=5_000_000, coll_amount=123,
                                    liquidator="0x" + "ab" * 20))
        self.assertEqual(c["debtToCover"], 5_000_000)
        self.assertEqual(c["liquidatedCollateral"], 123)
        self.assertEqual(c["liquidator"], "0x" + "ab" * 20)
        self.assertEqual(c["collateralAsset"], "0x" + "11" * 20)
        self.assertEqual(c["debtAsset"], "0x" + "22" * 20)


class TestRefund(unittest.TestCase):
    SAFE = "0x149b41b1e4c00b5f9aa34b14fd9f84cfd2f014e5"

    def tx(self, idx, to, frm, value_wei):
        return {"transactionIndex": hex(idx), "to": to, "from": frm,
                "value": hex(value_wei)}

    def test_found_after_liq(self):
        txs = [self.tx(5, self.SAFE, "0xminer", 10 ** 18)]
        self.assertEqual(find_refund(txs, "0xMINER", 4), 1.0)

    def test_before_liq_ignored(self):
        txs = [self.tx(3, self.SAFE, "0xminer", 10 ** 18)]
        self.assertIsNone(find_refund(txs, "0xminer", 4))

    def test_wrong_sender_ignored(self):
        txs = [self.tx(5, self.SAFE, "0xother", 10 ** 18)]
        self.assertIsNone(find_refund(txs, "0xminer", 4))


class TestUsd(unittest.TestCase):
    def test_usd_amount(self):
        # 2 WBTC (8 dec) по $100k (8 dec оракула) = $200k
        self.assertAlmostEqual(usd_amount(2 * 10 ** 8, 8, 100_000 * 10 ** 8), 200_000.0)

    def test_event_gross(self):
        calls = [decode_liq_call(liq_log(1, 1, debt_cover=1_000 * 10 ** 6,
                                         coll_amount=105 * 10 ** 7))]
        coll, debt = "0x" + "11" * 20, "0x" + "22" * 20
        # coll: 10.5 ед. по $100 = $1050; debt: 1000 USDC по $1 = $1000 -> gross $50
        g = event_gross_usd(calls, {coll: 100 * 10 ** 8, debt: 1 * 10 ** 8},
                            {coll: 8, debt: 6})
        self.assertAlmostEqual(g, 50.0)


class TestSchema(unittest.TestCase):
    def test_to_common_schema(self):
        ev = {"txHash": "0x1", "timestamp": 123, "pool": "core",
              "calls": [{"liquidator": "0xLL"}], "bid_eth": 0.5, "gas_eth": 0.1,
              "gross_usd": 3000.0, "tx_from": "0xeoa"}
        rows = to_common_schema([ev], [2000.0])
        r = rows[0]
        self.assertEqual(r["sender"], "0xLL")
        self.assertAlmostEqual(int(r["incentiveUsd"]) / 1e18, 3000.0)
        self.assertAlmostEqual(int(r["bidAmountUsd"]) / 1e18, 1000.0)
        self.assertAlmostEqual(int(r["gasCostUsd"]) / 1e18, 200.0)


if __name__ == "__main__":
    unittest.main()
