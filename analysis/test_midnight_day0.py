"""Тесты день-0 разведки Midnight: ABI-декодер MarketCreated/Liquidate на синтетических
логах, формулы maxLif/рампа против кода (ConstantsLib/Midnight.sol@e6f2bf2), topic0."""
import unittest

from analysis.midnight_day0 import (
    TOPIC_LIQUIDATE, TOPIC_MARKET_CREATED, WAD, decode_liquidate,
    decode_market_created, decode_string_result, lif_at, max_lif,
)


def w(x: int) -> str:
    return hex(x)[2:].rjust(64, "0")


def waddr(a: str) -> str:
    return a[2:].rjust(64, "0")


def encode_market_created(chain_id, midnight, loan, cps, maturity, rcf, enter, liq_gate):
    """Ручная ABI-кодировка data события MarketCreated (эталон для декодера)."""
    # head: offset до кортежа
    tup = [w(chain_id), waddr(midnight), waddr(loan), w(8 * 32),  # offset массива внутри кортежа
           w(maturity), w(rcf), waddr(enter), waddr(liq_gate)]
    arr = [w(len(cps))]
    for t, lltv, cur, orc in cps:
        arr += [waddr(t), w(lltv), w(cur), waddr(orc)]
    return "0x" + w(32) + "".join(tup) + "".join(arr)


class TestDecodeMarketCreated(unittest.TestCase):
    def test_two_collaterals(self):
        cps = [("0x" + "aa" * 20, int(0.86e18), int(0.25e18), "0x" + "cc" * 20),
               ("0x" + "bb" * 20, int(0.5e18), int(0.5e18), "0x" + "dd" * 20)]
        data = encode_market_created(8453, "0x" + "11" * 20, "0x" + "22" * 20, cps,
                                     1767225600, 10 ** 6, "0x" + "00" * 20, "0x" + "00" * 20)
        log = {"data": data, "topics": [TOPIC_MARKET_CREATED, "0x" + "ee" * 32],
               "blockNumber": hex(48286900)}
        m = decode_market_created(log)
        self.assertEqual(m["chainId"], 8453)
        self.assertEqual(m["loanToken"], "0x" + "22" * 20)
        self.assertEqual(m["maturity"], 1767225600)
        self.assertEqual(m["rcfThreshold"], 10 ** 6)
        self.assertEqual(m["liquidatorGate"], "0x" + "00" * 20)
        self.assertEqual(len(m["collateralParams"]), 2)
        cp = m["collateralParams"][0]
        self.assertEqual(cp["token"], "0x" + "aa" * 20)
        self.assertEqual(cp["lltv"], int(0.86e18))
        self.assertEqual(cp["oracle"], "0x" + "cc" * 20)
        self.assertEqual(m["id"], "0x" + "ee" * 32)
        self.assertEqual(m["block"], 48286900)

    def test_empty_collaterals(self):
        data = encode_market_created(8453, "0x" + "11" * 20, "0x" + "22" * 20, [],
                                     0, 0, "0x" + "00" * 20, "0x" + "ff" * 20)
        m = decode_market_created({"data": data,
                                   "topics": [TOPIC_MARKET_CREATED, "0x" + "00" * 32],
                                   "blockNumber": "0x1"})
        self.assertEqual(m["collateralParams"], [])
        self.assertEqual(m["liquidatorGate"], "0x" + "ff" * 20)


class TestDecodeLiquidate(unittest.TestCase):
    def test_fields(self):
        data = "0x" + waddr("0x" + "01" * 20) + w(5 * 10 ** 18) + w(7 * 10 ** 6) + w(1) \
            + waddr("0x" + "02" * 20) + waddr("0x" + "03" * 20) + w(0) + w(0) + w(0)
        log = {"data": data,
               "topics": [TOPIC_LIQUIDATE, "0x" + "aa" * 32,
                          "0x" + "00" * 12 + "bb" * 20, "0x" + "00" * 12 + "cc" * 20],
               "blockNumber": hex(100), "transactionHash": "0xdead"}
        x = decode_liquidate(log)
        self.assertEqual(x["caller"], "0x" + "01" * 20)
        self.assertEqual(x["seizedAssets"], 5 * 10 ** 18)
        self.assertEqual(x["repaidUnits"], 7 * 10 ** 6)
        self.assertTrue(x["postMaturityMode"])
        self.assertEqual(x["collateral"], "0x" + "bb" * 20)
        self.assertEqual(x["borrower"], "0x" + "cc" * 20)
        self.assertEqual(x["badDebt"], 0)


class TestFormulas(unittest.TestCase):
    def test_max_lif_docs_example(self):
        # пример из доков SDK (mechanics §1): cursor 0.25e18, lltv 0.86 → бонус ~3.6%
        ml = max_lif(int(0.86e18), int(0.25e18))
        self.assertAlmostEqual((ml - WAD) / WAD, 0.0363, places=3)

    def test_max_lif_monotone_in_cursor(self):
        lltv = int(0.9e18)
        self.assertLess(max_lif(lltv, int(0.1e18)), max_lif(lltv, int(0.5e18)))

    def test_max_lif_mulDivDown_semantics(self):
        # интегерная семантика: WAD*WAD // (WAD - cursor*(WAD-lltv)//WAD)
        lltv, cur = 123456789 * 10 ** 10, 987654321 * 10 ** 8
        expect = WAD * WAD // (WAD - cur * (WAD - lltv) // WAD)
        self.assertEqual(max_lif(lltv, cur), expect)

    def test_ramp_boundaries(self):
        ml = max_lif(int(0.86e18), int(0.25e18))
        self.assertEqual(lif_at(ml, 0), WAD)              # в момент maturity бонус 0
        self.assertEqual(lif_at(ml, 3600), ml)            # через час — maxLif
        self.assertEqual(lif_at(ml, 7200), ml)            # капируется
        mid = lif_at(ml, 1800)                            # линейность: середина = половина бонуса
        self.assertEqual(mid - WAD, (ml - WAD) * 1800 // 3600)

    def test_topics_pinned(self):
        # topic0 сверены offline-keccak двумя путями (docs/morpho_v2_mechanics.md)
        self.assertEqual(TOPIC_MARKET_CREATED,
                         "0xdbf3e95a2290945645820c722294f678e0b3522a7dcf3cf2e2870268bf6c9472")
        self.assertEqual(TOPIC_LIQUIDATE,
                         "0xb137b989b9fd54b984273db8f16364f52f383aaca56076a320c1896e9fc2dad9")


class TestStringDecode(unittest.TestCase):
    def test_string_result(self):
        s = b"BTC/USD SVR"
        payload = "0x" + w(32) + w(len(s)) + s.hex().ljust(64, "0")
        self.assertEqual(decode_string_result(payload), "BTC/USD SVR")

    def test_not_a_string(self):
        self.assertIsNone(decode_string_result("0x" + w(1)))
        self.assertIsNone(decode_string_result("0x"))
        self.assertIsNone(decode_string_result(None))


if __name__ == "__main__":
    unittest.main()
