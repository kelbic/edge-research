"""Offline unit tests for the Gate-1 repricing scanner — run: python3 -m analysis.test_nexus_repricing"""
import unittest

from analysis.nexus_repricing import (
    TOPIC_PRODUCT_UPDATED,
    bucket_by_topic0,
    decode_product_updated,
    first_event_after,
    match_product_ids,
    per_product_series,
    real_changes,
    update_gaps_days,
)


def _word(v: int) -> str:
    return format(v, "064x")


def _log(bn=100, ts=1000.0, product_id=7, weight=50, price=200, topic=TOPIC_PRODUCT_UPDATED):
    return {
        "bn": bn, "ts": ts, "tx": "0xabc",
        "topics": [topic],
        "topic0": topic,
        "data": "0x" + _word(product_id) + _word(weight) + _word(price),
    }


class TestDecode(unittest.TestCase):
    def test_topic0_pinned_offline_keccak(self):
        # offline-keccak pin: ProductUpdated(uint256,uint8,uint96)
        self.assertEqual(
            TOPIC_PRODUCT_UPDATED,
            "0xa53c9996cef049b1d8795f6b98a35963de73d7096d884d7eb32fa2a64526b12e")

    def test_decode_roundtrip(self):
        ev = decode_product_updated(_log(product_id=128, weight=100, price=1_50))
        self.assertEqual(ev["productId"], 128)
        self.assertEqual(ev["targetWeight"], 100)
        self.assertEqual(ev["targetPrice"], 150)

    def test_decode_rejects_other_topics(self):
        self.assertIsNone(decode_product_updated(_log(topic="0x" + "11" * 32)))

    def test_decode_rejects_short_data(self):
        lg = _log()
        lg["data"] = "0x" + _word(1)
        self.assertIsNone(decode_product_updated(lg))


class TestSeries(unittest.TestCase):
    def _ev(self, ts, pid=1, price=100, weight=50):
        return {"bn": 1, "ts": ts, "tx": "0x", "productId": pid,
                "targetWeight": weight, "targetPrice": price}

    def test_series_sorted_and_grouped(self):
        evs = [self._ev(30, pid=2), self._ev(10, pid=1), self._ev(20, pid=1)]
        s = per_product_series(evs)
        self.assertEqual([e["ts"] for e in s[1]], [10, 20])
        self.assertEqual(len(s[2]), 1)

    def test_real_changes_drops_init_and_noops(self):
        evs = [self._ev(10, price=100), self._ev(20, price=100),  # noop
               self._ev(30, price=250), self._ev(40, price=250, weight=0)]  # price, then weight
        ch = real_changes(evs)
        self.assertEqual([e["ts"] for e in ch], [30, 40])

    def test_gaps_days(self):
        evs = [self._ev(0), self._ev(86400), self._ev(3 * 86400)]
        self.assertEqual(update_gaps_days(evs), [1.0, 2.0])

    def test_first_event_after_filters_products_and_time(self):
        evs = [self._ev(10, pid=1), self._ev(20, pid=2), self._ev(30, pid=1)]
        hit = first_event_after(evs, 15, {1})
        self.assertEqual(hit["ts"], 30)
        self.assertIsNone(first_event_after(evs, 30, {1}))

    def test_bucket_by_topic0(self):
        logs = [_log(), _log(), _log(topic="0x" + "22" * 32)]
        b = bucket_by_topic0(logs)
        self.assertEqual(b[TOPIC_PRODUCT_UPDATED], 2)
        self.assertEqual(b["0x" + "22" * 32], 1)


class TestMatching(unittest.TestCase):
    def test_match_by_keyword_case_insensitive(self):
        products = [{"id": 1, "name": "Euler v2"}, {"id": 2, "name": "Curve All Pools"},
                    {"id": 3, "name": "Aave v3"}]
        self.assertEqual(match_product_ids(products, ["euler"]), {1: "Euler v2"})
        self.assertEqual(set(match_product_ids(products, ["curve", "aave"])), {2, 3})

    def test_match_handles_missing_name(self):
        self.assertEqual(match_product_ids([{"id": 9}], ["x"]), {})


if __name__ == "__main__":
    unittest.main()
